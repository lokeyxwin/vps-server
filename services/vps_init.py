"""在已注册的 VPS 上安装并启用 xray。

可独立使用（xrayinit CLI），也被 register_vps 链式调用。
"""

from datetime import datetime

import config
from db import ProxyRecord, VPSRecord, XrayStatus, session_scope
from log import get_logger
from core import (
    AuthFailedError,
    ConnectTimeoutError,
    ConnectRefusedError,
    VPSSession,
    open_tcp_port_range,
    FirewallOpenError,
    test_socks_proxy,
    EXTERNAL_UNREACHABLE_MESSAGE,
)
from xray import (
    XrayManager,
    XrayError,
    DEFAULT_PORT as XRAY_DEFAULT_PORT,
)


# 防火墙开放范围从 config 取（XRAY_DEFAULT_PORT 18440 + PROXY 业务范围 18441-18450）
FIREWALL_OPEN_START = config.FIREWALL_OPEN_START
FIREWALL_OPEN_END = config.FIREWALL_OPEN_END


logger = get_logger(__name__)


def init_vps_xray(ip: str) -> dict:
    """在已注册的 VPS 上安装并启用 xray（含自启）。

    流程：
        ① 查 DB → 不存在 return not_registered
        ② 状态短路：running → already_running；installing → in_progress
        ③ 标记 installing
        ④ SSH 连（错误细分）
        ⑤ XrayManager.ensure_installed_and_running（已装跳过装，未装则装；起服务；设自启）
        ⑥ 写入 DB，return ok / imported

    返回的 status 全集：
        ok / imported / already_running / in_progress / not_registered /
        auth_failed / timeout / refused / failed /
        install_failed / verify_failed / service_not_active / enable_failed
    """
    logger.info("开始处理 xray：ip=%s", ip)

    # ① ② 查 DB + 前置状态
    with session_scope() as session:
        record = session.query(VPSRecord).filter_by(ip=ip).first()
        if record is None:
            logger.warning("这台 VPS 还没登记，先去跑 rgvps：ip=%s", ip)
            return {
                "status": "not_registered",
                "message": f"IP {ip} 未在数据库中。请先执行 rgvps 注册 VPS 或 rgip 注册代理。",
            }
        if record.xray_status == XrayStatus.RUNNING:
            logger.info("xray 已经在跑了，跳过这次处理：ip=%s", ip)
            return {"status": "already_running", "ip": ip, "version": record.xray_version}
        if record.xray_status == XrayStatus.INSTALLING:
            logger.info("当前有另一个进程正在装 xray，跳过：ip=%s", ip)
            return {"status": "in_progress", "message": "另一进程正在安装中"}

    # ③ 标记 installing
    _update_xray_state(ip, XrayStatus.INSTALLING, "正在执行 xray 全流程")

    # ④ ⑤ SSH 连 + XrayManager 高层动作 + 端口审计 + 防火墙 + 内部 ping
    internal_check = None
    port_audit: dict | None = None
    try:
        with VPSSession.from_record(_load_record(ip)) as vps:
            manager = XrayManager(vps.client)
            logger.info("检查 xray 安装情况，没装就装、没起就起、没自启就设")
            try:
                result = manager.ensure_installed_and_running()
            except XrayError as exc:
                _save_failure_with_context(ip, manager, exc)
                logger.warning("xray 处理失败（%s）：ip=%s", exc.code, ip)
                return {"status": exc.code, "message": str(exc)}

            # ⑤.4 端口审计：
            #   a. 抄录已部署在 xray 里的客户端 inbound 绑定（rgvps 重装场景兜底）
            #   b. 扫业务端口区间 18441-18450 的可用集合（已扣 OS 占用 + 默认排除）
            #   c. 把可用端口数写到 VPS 表的 idle_port_count
            #   d. 每条 existing_binding 抄录到 proxy 表（schema 还没定，先 stub log）
            existing_bindings = manager.import_existing_bindings()
            available_ports = vps.get_available_ports(
                config.PROXY_PORT_RANGE_START,
                config.PROXY_PORT_RANGE_END,
            )
            # 已绑定端口虽然在 ss -tln 里就会显示占用，这里冗余扣一次保险
            available_ports -= {b["port"] for b in existing_bindings}

            port_audit = {
                "available_count": len(available_ports),
                "available_ports": sorted(available_ports),
                "existing_bindings": existing_bindings,
            }
            logger.info(
                "端口审计：业务端口区间内可用 %d/%d 个，已被 xray 绑定 %d 个，可用列表=%s",
                len(available_ports),
                config.PROXY_PORT_RANGE_END - config.PROXY_PORT_RANGE_START + 1,
                len(existing_bindings),
                sorted(available_ports),
            )

            # ⑤.4.c 写 VPS 表的 idle_port_count（独立短事务，跟 _update_xray_state 解耦）
            _set_idle_port_count(ip, len(available_ports))
            logger.info("可用端口数已写入数据库：%d", len(available_ports))

            # ⑤.4.d 把每条已部署的 binding upsert 到 proxy_record
            #   按 (vps_id, vps_port) 唯一键命中：
            #     - 已有该行 → 更新字段（重装场景，配置可能改了）
            #     - 没有 → 新插
            upserted = _upsert_proxy_bindings(ip, existing_bindings)
            if upserted:
                logger.info(
                    "已抄录 %d 条 xray 端口绑定到 proxy 表：ports=%s",
                    upserted, sorted(b["port"] for b in existing_bindings),
                )

            # ⑤.5 开服务器本地防火墙 18440-18450（best-effort，失败只警告不阻塞）
            logger.info("处理服务器本地防火墙，放行端口范围 %d-%d", FIREWALL_OPEN_START, FIREWALL_OPEN_END)
            try:
                fw = open_tcp_port_range(vps.client, FIREWALL_OPEN_START, FIREWALL_OPEN_END)
                logger.info(
                    "本机防火墙处理完毕：类型=%s 已尝试放行 %d-%d/tcp",
                    fw, FIREWALL_OPEN_START, FIREWALL_OPEN_END,
                )
            except FirewallOpenError as exc:
                logger.warning(
                    "本机防火墙开放失败但不阻塞主流程，继续走（原因：%s）", exc,
                )

            # ⑥ 内部 ping：服务器内 curl localhost:18440
            logger.info("从服务器内部走 socks5 测试 xray 通不通")
            internal_check = manager.test_internal_socks(port=XRAY_DEFAULT_PORT)
            logger.info(
                "内部 ping 结果：通=%s 状态码=%s 出口 IP=%s",
                internal_check["ok"], internal_check["http_code"],
                internal_check["body"][:60],
            )
            if not internal_check["ok"]:
                msg = (
                    f"内部 ping 失败：xray 服务在跑但 socks5 不响应。"
                    f"原因：xray config 可能损坏 / 进程异常。详情：{internal_check['error']}"
                )
                _update_xray_state(
                    ip, XrayStatus.INSTALL_FAILED, msg,
                    version=result["version"],
                    installed_at=datetime.now(),
                )
                logger.warning("内部 ping 不通，本次 xray 处理失败（ip=%s）", ip)
                return {"status": "internal_check_failed", "message": msg}
    except AuthFailedError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("登录失败：账号或密码不对（ip=%s）", ip)
        return {"status": "auth_failed", "message": str(exc)}
    except ConnectTimeoutError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("连接超时：可能 SSH 端口被防火墙挡了（ip=%s）", ip)
        return {"status": "timeout", "message": str(exc)}
    except ConnectRefusedError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("连接被拒：SSH 端口没开或端口号不对（ip=%s）", ip)
        return {"status": "refused", "message": str(exc)}
    except ConnectionError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("连接失败：未知错误（ip=%s）", ip)
        return {"status": "failed", "message": str(exc)}

    # ⑦ 外部 ping：从本机走 socks5 → VPS_IP:18440
    logger.info("从本机这边外部走 socks5 测试 vps_ip:%d 通不通", XRAY_DEFAULT_PORT)
    external_check = test_socks_proxy(ip, XRAY_DEFAULT_PORT)
    logger.info(
        "外部 ping 结果：通=%s 状态码=%s 出口 IP=%s",
        external_check["ok"], external_check["status_code"],
        external_check["body"][:60],
    )

    # ⑧ 收尾：根据内外 ping 综合判断最终 status
    version = result["version"]
    was_already = result["was_already_installed"]
    actions = result["actions_taken"]

    base_msg = "已在服务器上安装，本次仅纳管同步" if was_already else ""

    if external_check["ok"]:
        # 内通外通 = 完美
        final_status = "imported" if was_already else "ok"
        message = base_msg or "全链路通：内部 + 外部 socks5 均正常"
    else:
        # 内通外不通 = 安全组没开
        final_status = "external_unreachable"
        message = (
            f"{EXTERNAL_UNREACHABLE_MESSAGE} "
            f"详情：{external_check['error'] or external_check['status_code']}"
        )

    _update_xray_state(
        ip,
        XrayStatus.RUNNING,
        message,
        version=version,
        installed_at=datetime.now(),
    )

    logger.info(
        "xray 全部搞定（ip=%s）：状态=%s 版本=%s 本次做的操作=%s",
        ip, final_status, version, actions,
    )
    return {
        "status": final_status,
        "ip": ip,
        "version": version,
        "actions": actions,
        "internal_ping": internal_check,
        "external_ping": external_check,
        "port_audit": port_audit,
        "message": message if final_status == "external_unreachable" else None,
    }


# ============================================================
# 内部辅助
# ============================================================

def _load_record(ip: str) -> VPSRecord:
    """加载 + detach 一条 VPS 记录（给 VPSSession.from_record 用）。"""
    with session_scope() as session:
        record = session.query(VPSRecord).filter_by(ip=ip).one()
        session.expunge(record)
        return record


def _save_failure_with_context(ip: str, manager: XrayManager, exc: XrayError) -> None:
    """xray 失败时主动查一次 manager 拿 version/is_installed，避免 DB 字段缺失。

    场景：xray 已装但服务起不来（如 config 损坏），message 里说"version=...."
    但 DB 的 xray_version 字段空——矛盾。这个辅助把已探测到的信息写进 DB。
    """
    partial_version = ""
    partial_installed = False
    try:
        partial_version = manager.version()
    except Exception:  # noqa: BLE001 — 失败信息收集是 best-effort
        pass
    try:
        partial_installed = manager.is_installed()
    except Exception:  # noqa: BLE001 — 失败信息收集是 best-effort
        pass

    _update_xray_state(
        ip,
        XrayStatus.INSTALL_FAILED,
        str(exc),
        version=partial_version if partial_version else None,
        installed_at=datetime.now() if partial_installed else None,
    )


def _upsert_proxy_bindings(ip: str, bindings: list[dict]) -> int:
    """把 extract_port_bindings 抠出的 binding 列表 upsert 到 proxy_record。

    匹配键：(vps_id, vps_port)
    - 已存在该行：更新所有字段（重装场景：配置可能改了，比如换了账密或上游）
    - 不存在：from_extracted_binding 造新行 add

    返回实际处理的行数（含新插 + 已更新）。
    """
    if not bindings:
        return 0

    from core.security import encrypt_password

    with session_scope() as session:
        vps_id = session.query(VPSRecord.id).filter_by(ip=ip).scalar()
        if vps_id is None:
            # 走到这里说明 VPS 都已经在 DB 里了，理论上不会缺；防御性 0 返回
            return 0

        for b in bindings:
            existing = (
                session.query(ProxyRecord)
                .filter_by(vps_id=vps_id, vps_port=b["port"])
                .one_or_none()
            )
            if existing is None:
                session.add(ProxyRecord.from_extracted_binding(vps_id, b))
            else:
                # 重装场景：原地刷新字段（密码也重新加密一份）
                existing.protocol = b.get("protocol", "socks5")
                existing.inbound_user = b.get("inbound_user", "")
                existing.inbound_pwd_encrypted = encrypt_password(b.get("inbound_pwd", ""))
                existing.upstream_host = b.get("upstream_host", "")
                existing.egress_ip = b.get("egress_ip", "")
                existing.egress_country = b.get("egress_country", "")

    return len(bindings)


def _set_idle_port_count(ip: str, count: int) -> None:
    """单独把端口审计算出的可用端口数写到 VPS 表。

    跟 _update_xray_state 分开是因为：
    - xray 状态机字段（status/version/installed_at/...）跟端口数是两件事
    - 端口审计可能频繁触发（每次 rgvps / 巡检），不该牵连 xray_status 更新
    """
    with session_scope() as session:
        record = session.query(VPSRecord).filter_by(ip=ip).one()
        record.idle_port_count = count


def _update_xray_state(
    ip: str,
    status: str,
    message: str = "",
    version: str | None = None,
    installed_at: datetime | None = None,
) -> None:
    """更新 VPS 的 xray_* 字段。每次调用都是独立短事务，立刻 commit。"""
    with session_scope() as session:
        record = session.query(VPSRecord).filter_by(ip=ip).one()
        record.xray_status = status
        record.xray_status_message = message
        record.xray_last_checked_at = datetime.now()
        if version is not None:
            record.xray_version = version
        if installed_at is not None and record.xray_installed_at is None:
            # 仅首次设置，不覆盖
            record.xray_installed_at = installed_at
