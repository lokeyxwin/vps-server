"""在已注册的 VPS 上安装并启用 xray。

可独立使用（xrayinit CLI），也被 register_vps 链式调用。
"""

from datetime import datetime

import config
from db import VPSRecord, XrayStatus, session_scope
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
    logger.info("init_vps_xray 开始 ip=%s", ip)

    # ① ② 查 DB + 前置状态
    with session_scope() as session:
        record = session.query(VPSRecord).filter_by(ip=ip).first()
        if record is None:
            logger.warning("init_vps_xray 失败 ip=%s status=not_registered", ip)
            return {
                "status": "not_registered",
                "message": f"IP {ip} 未在数据库中。请先执行 rgvps 注册 VPS 或 rgip 注册代理。",
            }
        if record.xray_status == XrayStatus.RUNNING:
            logger.info("init_vps_xray 跳过 ip=%s 原因=已 running", ip)
            return {"status": "already_running", "ip": ip, "version": record.xray_version}
        if record.xray_status == XrayStatus.INSTALLING:
            logger.info("init_vps_xray 跳过 ip=%s 原因=另一进程正在安装", ip)
            return {"status": "in_progress", "message": "另一进程正在安装中"}

    # ③ 标记 installing
    _update_xray_state(ip, XrayStatus.INSTALLING, "正在执行 xray 全流程")

    # ④ ⑤ SSH 连 + XrayManager 高层动作 + 防火墙 + 内部 ping
    internal_check = None
    try:
        with VPSSession.from_record(_load_record(ip)) as vps:
            manager = XrayManager(vps.client)
            try:
                result = manager.ensure_installed_and_running()
            except XrayError as exc:
                _save_failure_with_context(ip, manager, exc)
                logger.warning(
                    "init_vps_xray 失败 ip=%s status=%s", ip, exc.code,
                )
                return {"status": exc.code, "message": str(exc)}

            # ⑤.5 开服务器本地防火墙 18440-18450（best-effort，失败只警告不阻塞）
            try:
                fw = open_tcp_port_range(vps.client, FIREWALL_OPEN_START, FIREWALL_OPEN_END)
                logger.info(
                    "init_vps_xray 防火墙处理完成 ip=%s 防火墙=%s 范围=%d-%d",
                    ip, fw, FIREWALL_OPEN_START, FIREWALL_OPEN_END,
                )
            except FirewallOpenError as exc:
                logger.warning(
                    "init_vps_xray 防火墙开放失败（继续走完后续步骤）ip=%s reason=%s",
                    ip, exc,
                )

            # ⑥ 内部 ping：服务器内 curl localhost:18440
            internal_check = manager.test_internal_socks(port=XRAY_DEFAULT_PORT)
            logger.info(
                "init_vps_xray 内部 ping ip=%s ok=%s http_code=%s body=%s",
                ip, internal_check["ok"], internal_check["http_code"],
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
                logger.warning(
                    "init_vps_xray 失败 ip=%s status=internal_check_failed", ip,
                )
                return {"status": "internal_check_failed", "message": msg}
    except AuthFailedError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("init_vps_xray 失败 ip=%s status=auth_failed", ip)
        return {"status": "auth_failed", "message": str(exc)}
    except ConnectTimeoutError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("init_vps_xray 失败 ip=%s status=timeout", ip)
        return {"status": "timeout", "message": str(exc)}
    except ConnectRefusedError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("init_vps_xray 失败 ip=%s status=refused", ip)
        return {"status": "refused", "message": str(exc)}
    except ConnectionError as exc:
        _update_xray_state(ip, XrayStatus.INSTALL_FAILED, str(exc))
        logger.warning("init_vps_xray 失败 ip=%s status=failed", ip)
        return {"status": "failed", "message": str(exc)}

    # ⑦ 外部 ping：从本机走 socks5 → VPS_IP:18440
    external_check = test_socks_proxy(ip, XRAY_DEFAULT_PORT)
    logger.info(
        "init_vps_xray 外部 ping ip=%s ok=%s status_code=%s body=%s",
        ip, external_check["ok"], external_check["status_code"],
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
        "init_vps_xray 完成 ip=%s status=%s version=%s actions=%s",
        ip, final_status, version, actions,
    )
    return {
        "status": final_status,
        "ip": ip,
        "version": version,
        "actions": actions,
        "internal_ping": internal_check,
        "external_ping": external_check,
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
