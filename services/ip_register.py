"""rgIP 业务：登记一条上游代理 + 部署到一台 VPS 的某个端口。

业务身份：egress_ip。一条 rgIP = 同时写 IPRecord + ProxyRecord 两张表。

主流程见 todo/TODO_IP_PROXY.md §2。按 CLAUDE.md 业务函数契约：
- 返回 dict 含 status，吃掉所有底层异常
- 业务事件用 logger.info 口语化叙述（services.ip_register logger）
- 失败时回滚 xray config（rollback_proxy_binding），不让脏配置遗留
"""

from __future__ import annotations

from datetime import date as date_class

from sqlalchemy import or_

import config as project_config
from core import (
    AuthFailedError,
    ConnectTimeoutError,
    ConnectRefusedError,
    VPSSession,
    open_tcp_port_range,
    FirewallOpenError,
    test_socks_proxy,
)
from core.geoip import lookup_egress
from db import (
    IPRecord,
    ProxyRecord,
    VPSRecord,
    XrayStatus,
    session_scope,
)
from log import get_logger
from xray import (
    XrayManager,
    OutboundTagConflictError,
    PortAlreadyBoundError,
    PortConflictError,
    build_proxy_outbound,
    generate_random_auth,
    SUPPORTED_PROTOCOLS,
)
from xray.config import (
    ConfigValidationError,
    ConfigWriteError,
)
from xray.service import ReloadFailedError


logger = get_logger("services.ip_register")


PERSIST_MAX_RETRIES = 3


class _PersistFailedError(RuntimeError):
    """写库 3 次重试后仍失败的内部异常，业务层捕获后回滚 xray + 返回 failed。"""


# ============================================================
# 公开业务函数
# ============================================================

def register_ip(
    *,
    entry_host: str,
    entry_port: int,
    username: str,
    password: str,
    protocol: str,
    egress_ip: str,
    provider_domain: str = "",
    expire_date: date_class | None = None,
) -> dict:
    """登记一条上游代理 + 部署到一台 VPS 的某个端口作为对外出口。

    业务返回 status 全集（8 种业务结果 + 4 种 SSH 连接错）：
        ok / ok_security_group_blocked /
        already_exists / no_available_vps / no_available_port /
        egress_mismatch / failed /
        auth_failed / timeout / refused

    其中只有 ok / ok_security_group_blocked 会真正落库（IPRecord + ProxyRecord）。
    其他业务返回 status 不写任何表。
    """
    logger.info(
        "开始登记 IP：egress=%s entry=%s:%s protocol=%s",
        egress_ip, entry_host, entry_port, protocol,
    )

    # 入参校验
    if protocol not in SUPPORTED_PROTOCOLS:
        msg = f"协议 {protocol!r} 不支持，仅支持 {list(SUPPORTED_PROTOCOLS)}"
        logger.warning("入参校验失败：%s", msg)
        return {"status": "failed", "message": msg}

    # ① 查 IP 表（不分 active/expired，已存在就拒收，留给续费业务处理）
    existing_short_circuit = _check_existing(egress_ip)
    if existing_short_circuit is not None:
        return existing_short_circuit

    # ②.5 geoip 上移（用于拼 outbound tag = proxy-{country_code}-{egress_ip}）
    logger.info("查 IP 归属地：egress=%s", egress_ip)
    geo = lookup_egress(egress_ip)
    country_code = (geo.get("country_code") or "").strip() or "XX"

    # ② 挑 VPS
    vps_snapshot = _pick_vps()
    if vps_snapshot is None:
        logger.info("没合适 VPS：需要 xray running + is_active=1 + idle_port_count>0")
        return {
            "status": "no_available_vps",
            "message": "没有可用的 VPS（要求：xray=running + is_active=1 + idle_port_count>0）",
        }
    vps_id = vps_snapshot["id"]
    vps_ip = vps_snapshot["ip"]
    logger.info(
        "选中 VPS：id=%s ip=%s 闲端口计数=%s",
        vps_id, vps_ip, vps_snapshot["idle_port_count"],
    )

    # ③-⑦ SSH 进 VPS：算端口 + 部署 xray + 内 ping
    vps_record = _load_vps_record(vps_id)
    new_config: dict | None = None
    vps_port: int | None = None
    inbound_user = inbound_pwd = ""

    try:
        with VPSSession.from_record(vps_record) as vps:
            xm = XrayManager(vps.client)

            # ④ 算端口：只查 proxy_record（不 SSH 跑 ss -tln，proxy_record 是真相源）
            used_ports = _proxy_used_ports(vps_id)
            all_ports = set(range(
                project_config.PROXY_PORT_RANGE_START,
                project_config.PROXY_PORT_RANGE_END + 1,
            ))
            available = all_ports - used_ports
            logger.info(
                "端口审计（只查 proxy_record）：已用 %s 真闲 %d 个 %s",
                sorted(used_ports), len(available), sorted(available),
            )

            if not available:
                logger.info("真闲端口为空，本次不复用过期端口（顶替机制以后做）")
                return {
                    "status": "no_available_port",
                    "message": "选中的 VPS 业务端口全部已分配（暂不支持过期端口顶替）",
                }

            # ⑤ 选 min
            vps_port = min(available)
            logger.info("挑端口：%d（真闲集里最小那个，行为可预测）", vps_port)

            # ⑥ 生成客户端账密
            inbound_user, inbound_pwd = generate_random_auth()

            # ⑦ 拼上游 outbound + apply
            # tag 必须跨多次部署唯一，所以加 vps_port 后缀
            # （即使同一条 egress_ip 重复部署或上次失败留了脏 outbound 也不冲突）
            outbound_tag = f"proxy-{country_code}-{egress_ip}-{vps_port}"
            proxy_outbound = build_proxy_outbound(
                host=entry_host, port=entry_port,
                user=username, pwd=password,
                protocol=protocol, tag=outbound_tag,
            )
            proxy_outbound["_meta"] = {
                "egress_ip": egress_ip,
                "egress_country": country_code,
            }

            try:
                new_config = xm.apply_proxy_binding(
                    vps_port=vps_port,
                    proxy_outbound=proxy_outbound,
                    inbound_user=inbound_user,
                    inbound_pwd=inbound_pwd,
                )
                logger.info(
                    "xray 配置已应用：vps_port=%s outbound_tag=%s",
                    vps_port, outbound_tag,
                )
            except PortAlreadyBoundError as exc:
                logger.warning("端口已被 xray config 占用（罕见竞态）：%s", exc)
                return {
                    "status": "no_available_port",
                    "message": f"端口 {vps_port} 在 xray config 里已被占用",
                }
            except (PortConflictError, OutboundTagConflictError) as exc:
                logger.warning("xray config 冲突：%s", exc)
                return {"status": "failed", "message": str(exc)}
            except (ConfigWriteError, ConfigValidationError, ReloadFailedError) as exc:
                logger.warning("xray 配置应用失败：%s", exc)
                return {"status": "failed", "message": str(exc)}

            # ⑧ 内 ping（带 inbound 账密——rgIP 部署的 inbound 是 auth=password）
            logger.info("内部 ping：从服务器内走 vps_port=%s 出去", vps_port)
            internal_ping = xm.test_internal_socks(
                port=vps_port, user=inbound_user, pwd=inbound_pwd,
            )
            logger.info(
                "内部 ping 结果：通=%s http=%s 实测出口=%s",
                internal_ping["ok"],
                internal_ping.get("http_code"),
                (internal_ping.get("body") or "")[:60],
            )

            if not internal_ping["ok"]:
                logger.warning("内部 ping 不通，回滚 xray 配置")
                _rollback_quiet(xm, vps_port, new_config)
                return {
                    "status": "failed",
                    "message": (
                        "内部 ping 不通：服务器内走代理失败。"
                        "常见原因：上游入口地址/端口错 / 上游账号密码错。"
                        "建议：核对入口 host/port/user/pwd 后重试。"
                    ),
                    "internal_ping": internal_ping,
                }

            actual_egress = (internal_ping.get("body") or "").strip()
            if actual_egress != egress_ip:
                logger.warning(
                    "egress 不匹配：用户填 %s 实测 %s，回滚 xray 配置",
                    egress_ip, actual_egress,
                )
                _rollback_quiet(xm, vps_port, new_config)
                return {
                    "status": "egress_mismatch",
                    "message": (
                        f"实测出口 IP={actual_egress} 跟用户填的 egress_ip={egress_ip} 不一致。"
                        "常见原因：上游凭据填错（用了别人的账号）/ 用户填错了 egress_ip。"
                    ),
                    "internal_ping": internal_ping,
                }

    except AuthFailedError as exc:
        logger.warning("SSH 登录失败（vps_ip=%s）：%s", vps_ip, exc)
        return {"status": "auth_failed", "message": str(exc)}
    except ConnectTimeoutError as exc:
        logger.warning("SSH 连接超时（vps_ip=%s）：%s", vps_ip, exc)
        return {"status": "timeout", "message": str(exc)}
    except ConnectRefusedError as exc:
        logger.warning("SSH 连接被拒（vps_ip=%s）：%s", vps_ip, exc)
        return {"status": "refused", "message": str(exc)}
    except ConnectionError as exc:
        logger.warning("SSH 连接失败（vps_ip=%s）：%s", vps_ip, exc)
        return {"status": "failed", "message": str(exc)}

    # ⑨ 写库（3 次重试，全失败回滚 xray）
    logger.info("内 ping 通过 + egress 匹配，开始写库（最多 3 次重试）")
    try:
        ip_id, proxy_id = _persist_with_retry(
            ip_form={
                "entry_host": entry_host,
                "entry_port": entry_port,
                "username": username,
                "password": password,
                "protocol": protocol,
                "egress_ip": egress_ip,
                "provider_domain": provider_domain,
                "expire_date": expire_date,
                "geo": geo,
            },
            proxy_form={
                "vps_id": vps_id,
                "vps_port": vps_port,
                "inbound_user": inbound_user,
                "inbound_pwd": inbound_pwd,
                "upstream_host": entry_host,
                "egress_ip": egress_ip,
                "egress_country": country_code,
                "protocol": protocol,
            },
        )
        logger.info("入库成功：ip_id=%s proxy_id=%s", ip_id, proxy_id)
    except _PersistFailedError as exc:
        logger.warning(
            "写库 3 次重试全失败，回滚 xray 配置（你拿了什么垃圾进来就都带走）：%s",
            exc,
        )
        _rollback_via_new_session(vps_id, vps_port, new_config)
        return {
            "status": "failed",
            "message": f"写库失败 ({PERSIST_MAX_RETRIES} 次重试)：{exc}。xray 配置已回滚。",
        }

    # ⑩ 外 ping（带 inbound 账密）
    logger.info("外部 ping：从本机走 %s:%s（带账密）", vps_ip, vps_port)
    external_ping = test_socks_proxy(
        vps_ip, vps_port, user=inbound_user, pwd=inbound_pwd,
    )
    logger.info(
        "外部 ping 结果：通=%s http=%s",
        external_ping["ok"], external_ping.get("status_code"),
    )

    sg_blocked = False
    if not external_ping["ok"]:
        # 自动开 VPS 本地防火墙重测一次
        logger.info("外 ping 不通，尝试在 VPS 本地开 %d 端口防火墙后重测", vps_port)
        try:
            with VPSSession.from_record(_load_vps_record(vps_id)) as vps:
                open_tcp_port_range(vps.client, vps_port, vps_port)
        except FirewallOpenError as exc:
            logger.warning("本机防火墙开放失败（不阻塞主流程）：%s", exc)
        except (AuthFailedError, ConnectTimeoutError, ConnectRefusedError,
                ConnectionError) as exc:
            logger.warning("二次 SSH 失败（不阻塞主流程）：%s", exc)

        external_ping = test_socks_proxy(
            vps_ip, vps_port, user=inbound_user, pwd=inbound_pwd,
        )
        logger.info(
            "外部 ping 重测：通=%s",
            external_ping["ok"],
        )
        if not external_ping["ok"]:
            sg_blocked = True
            logger.warning(
                "外 ping 仍不通：云端安全组应该没放行 %d 入方向", vps_port,
            )

    # ⑪ 返回完整节点信息
    status = "ok_security_group_blocked" if sg_blocked else "ok"
    message = (
        f"节点已部署完成，但本机外 ping {vps_port} 不通。"
        f"请到云服务商控制台『安全组』里放行入方向 TCP {vps_port}。"
        if sg_blocked
        else "节点已部署，全链路通"
    )

    logger.info(
        "登记完成：status=%s 节点=%s:%s 出口=%s",
        status, vps_ip, vps_port, egress_ip,
    )

    return {
        "status": status,
        "message": message,
        "node": {
            "protocol": "socks5",
            "host": vps_ip,
            "port": vps_port,
            "username": inbound_user,
            "password": inbound_pwd,
            "country_code": geo.get("country_code", ""),
            "city": geo.get("city", ""),
        },
        "binding": {"vps_id": vps_id, "ip_id": ip_id, "proxy_id": proxy_id},
        "ping": {
            "internal": "ok",
            "external": "ok" if external_ping["ok"] else "blocked",
        },
    }


# ============================================================
# 内部辅助
# ============================================================

def _existing_info(rec: IPRecord) -> dict:
    """把一条 IPRecord 转成 already_exists 返回里的 existing 字典。

    不返回敏感字段（username / password_encrypted）。
    """
    return {
        "id": rec.id,
        "egress_ip": rec.egress_ip,
        "country_code": rec.country_code,
        "city": rec.city,
        "provider_domain": rec.provider_domain,
        "expire_date": rec.expire_date.isoformat() if rec.expire_date else None,
        "is_active": rec.is_active,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


def _check_existing(egress_ip: str) -> dict | None:
    """查 IP 表，已存在直接返回 already_exists；不存在返回 None 让主流程继续。

    不区分 active/expired —— 续费走独立业务（后续 ip_renew）。
    """
    with session_scope() as s:
        existing = s.query(IPRecord).filter_by(egress_ip=egress_ip).first()
        if existing is None:
            return None

        logger.info(
            "数据库里已有这条 IP：egress=%s is_active=%s expire_date=%s",
            existing.egress_ip, existing.is_active, existing.expire_date,
        )
        return {
            "status": "already_exists",
            "message": (
                f"该 IP 已登记，到期 {existing.expire_date}，"
                f"is_active={existing.is_active}。"
                "如需续费或检查状态，请走对应的查询 / 续费业务。"
            ),
            "existing": _existing_info(existing),
        }


def _pick_vps() -> dict | None:
    """挑一台合适的 VPS 部署。返回 dict snapshot（避免离开 session 后 lazy load）。

    条件：xray_status='running' + is_active=1 + idle_port_count>0 + 未过期
    排序：idle_port_count DESC, id ASC
    """
    today = date_class.today()
    with session_scope() as s:
        vps = (
            s.query(VPSRecord)
            .filter(VPSRecord.is_active == 1)
            .filter(VPSRecord.xray_status == XrayStatus.RUNNING)
            .filter(VPSRecord.idle_port_count > 0)
            .filter(or_(
                VPSRecord.expire_date.is_(None),
                VPSRecord.expire_date >= today,
            ))
            .order_by(VPSRecord.idle_port_count.desc(), VPSRecord.id.asc())
            .first()
        )
        if vps is None:
            return None
        return {
            "id": vps.id, "ip": vps.ip,
            "idle_port_count": vps.idle_port_count,
        }


def _load_vps_record(vps_id: int) -> VPSRecord:
    """从 vps_id 加载 detach 的 VPSRecord（给 VPSSession.from_record 用）。"""
    with session_scope() as s:
        rec = s.query(VPSRecord).filter_by(id=vps_id).one()
        s.expunge(rec)
        return rec


def _proxy_used_ports(vps_id: int) -> set[int]:
    """这台 VPS 上 proxy_record 已分配的端口（含 expired 行——端口仍在 xray config 里）。

    方案 B 单一真相源：proxy_record 是端口分配的最终事实，不去 SSH 跑 ss -tln。
    """
    with session_scope() as s:
        ports = (
            s.query(ProxyRecord.vps_port)
            .filter_by(vps_id=vps_id)
            .all()
        )
        return {p[0] for p in ports}


def _persist_deployment(*, ip_form: dict, proxy_form: dict) -> tuple[int, int]:
    """一个事务里写 IPRecord + ProxyRecord + VPSRecord.idle_port_count -= 1。

    任一失败整体回滚（SQLAlchemy session_scope 自带 try/except + rollback）。
    """
    with session_scope() as s:
        ip_rec = IPRecord.from_form(**ip_form)
        s.add(ip_rec)
        s.flush()  # 拿 id

        proxy_rec = ProxyRecord.from_new_deployment(
            **proxy_form,
            ip_id=ip_rec.id,
        )
        s.add(proxy_rec)
        s.flush()  # 拿 id

        vps_rec = s.query(VPSRecord).filter_by(id=proxy_form["vps_id"]).one()
        vps_rec.idle_port_count = max(0, vps_rec.idle_port_count - 1)

        return ip_rec.id, proxy_rec.id


def _persist_with_retry(
    *,
    ip_form: dict,
    proxy_form: dict,
    max_retries: int = PERSIST_MAX_RETRIES,
) -> tuple[int, int]:
    """写库 max_retries 次，全失败抛 _PersistFailedError。

    业务约定："你拿了什么垃圾进来就都带走"：3 次写库失败后，业务层捕获
    本异常，回滚 xray 配置（这次部署的 binding 撤掉），返回 failed。
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return _persist_deployment(ip_form=ip_form, proxy_form=proxy_form)
        except Exception as exc:  # noqa: BLE001 — DB 异常类型多变（Integrity/Operational/Connection）
            last_exc = exc
            logger.warning(
                "写库第 %d/%d 次失败：%s (%s)",
                attempt, max_retries, type(exc).__name__, exc,
            )
    raise _PersistFailedError(
        f"重试 {max_retries} 次后写库仍失败，最后一次异常：{last_exc}"
    )


def _rollback_quiet(
    xm: XrayManager,
    vps_port: int,
    last_config: dict,
) -> None:
    """业务内 ping / egress 失败时回滚 xray 配置（在 with VPSSession 内调用）。

    回滚动作失败不阻塞主流程（脏配置可下次手动清理）——只 log。
    """
    try:
        xm.rollback_proxy_binding(vps_port=vps_port, last_config=last_config)
        logger.info("xray 配置已回滚：vps_port=%s", vps_port)
    except Exception as exc:  # noqa: BLE001 — 回滚失败要继续返回业务 status
        logger.warning(
            "xray 配置回滚失败（手动清理：登服务器删 vps_port=%s 的 inbound/outbound）：%s",
            vps_port, exc,
        )


def _rollback_via_new_session(
    vps_id: int,
    vps_port: int,
    last_config: dict,
) -> None:
    """写库失败时回滚 xray 配置（已离开原 SSH session，要新开一个）。"""
    try:
        with VPSSession.from_record(_load_vps_record(vps_id)) as vps:
            xm = XrayManager(vps.client)
            xm.rollback_proxy_binding(vps_port=vps_port, last_config=last_config)
            logger.info("xray 配置已回滚（写库失败兜底）：vps_port=%s", vps_port)
    except Exception as exc:  # noqa: BLE001 — 兜底回滚失败仍要让业务函数返回
        logger.warning(
            "回滚 xray 配置失败（手动清理：登服务器删 vps_port=%s 的 inbound/outbound）：%s",
            vps_port, exc,
        )
