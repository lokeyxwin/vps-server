"""MCP 工具调的所有业务函数集合 (ADR-0008 §决策 §2).

读写都在: 本文件层面不区分 read-only / write, 权限隔离靠 MCP admin/user 分层
(CLAUDE.local.md §14.1) 兜.

本任务 (T-18) 范围内只搬 3 个 read-only 查询函数, 内容跟原 services 一致, 签名不变.
未来加写入函数 (update_*) 时, 必须遵循 CLAUDE.local.md §14.3 ABCD 4 条规则:
主键精准 / 白名单字段 patch / 整对象不允许覆盖 / 工具命名反映约束.

任务表 (vps_task / ip_task) 永不暴露写入函数 (CLAUDE.local.md §14.2).

谁调我: tools/*.py handler (MCP 协议适配层).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_

from db.models import (
    IPRecord,
    IPTask,
    ProxyRecord,
    ProxyStatus,
    TaskStatus,
    VPSRecord,
    VPSTask,
)
from db.session import session_scope
from log import get_logger


logger = get_logger("db.queries")


# ============================================================
# VPS 注册进度查询
# ============================================================

def query_vps_status(
    vps_id: int | None = None,
    task_id: int | None = None,
) -> dict:
    """查 VPS 装机进度. vps_id 或 task_id 二选一(vps_id 优先).

    返回形状 (test/mcp_tools/spec.md §6.3):
      {"status": "ok",
       "vps":  {"id", "ip", "stage", "xray_version", "is_active"},
       "task": {"id", "status", "last_error_code", "last_error_msg",
                "completed_at"} | None}
      {"status": "not_found"}

    "最新一条 task" = ORDER BY created_at DESC LIMIT 1.
    没 task 时 task 字段为 None (新 VPS 可能 SSHWorker 还没派 task 就被查).
    """
    if vps_id is None and task_id is None:
        return {"status": "not_found"}

    with session_scope() as s:
        if vps_id is None:
            task = s.get(VPSTask, task_id)
            if task is None:
                return {"status": "not_found"}
            vps_id = task.vps_id

        vps = s.get(VPSRecord, vps_id)
        if vps is None:
            return {"status": "not_found"}

        latest_task = (
            s.query(VPSTask)
            .filter(VPSTask.vps_id == vps_id)
            .order_by(VPSTask.created_at.desc())
            .first()
        )

        task_dict: dict | None = None
        if latest_task is not None:
            task_dict = {
                "id": latest_task.id,
                "status": latest_task.status,
                "last_error_code": latest_task.last_error_code,
                "last_error_msg": latest_task.last_error_msg,
                "completed_at": (
                    latest_task.completed_at.isoformat()
                    if latest_task.completed_at is not None
                    else None
                ),
            }

        return {
            "status": "ok",
            "vps": {
                "id": vps.id,
                "ip": vps.ip,
                "stage": vps.stage,
                "xray_version": vps.xray_version,
                "is_active": vps.is_active,
            },
            "task": task_dict,
        }


# ============================================================
# IP 注册进度查询 (一条龙: ip + task + proxy_node)
# ============================================================

def query_ip_status(
    ip_id: int | None = None,
    task_id: int | None = None,
) -> dict:
    """查 IP 配置进度. ip_id 或 task_id 二选一(ip_id 优先).

    返回形状 (test/mcp_tools/spec.md §6.4, ⭐ 一条龙):
      {"status": "ok",
       "ip":   {"id", "egress_ip", "country_code", "expire_date"},
       "task": {"id", "status", "last_error_code", "last_error_msg",
                "completed_at"} | None,
       "proxy_node": {"vps_id", "vps_ip", "vps_port", "protocol",
                      "inbound_user", "inbound_pwd", "status"} | None}
      {"status": "not_found"}

    proxy_node 字段只在 task.status=done 且对应 proxy_record 存在时填, 否则 None.
    """
    if ip_id is None and task_id is None:
        return {"status": "not_found"}

    with session_scope() as s:
        if ip_id is None:
            task = s.get(IPTask, task_id)
            if task is None:
                return {"status": "not_found"}
            ip_id = task.ip_id

        ip = s.get(IPRecord, ip_id)
        if ip is None:
            return {"status": "not_found"}

        latest_task = (
            s.query(IPTask)
            .filter(IPTask.ip_id == ip_id)
            .order_by(IPTask.created_at.desc())
            .first()
        )

        task_dict: dict | None = None
        proxy_node_dict: dict | None = None
        if latest_task is not None:
            task_dict = {
                "id": latest_task.id,
                "status": latest_task.status,
                "last_error_code": latest_task.last_error_code,
                "last_error_msg": latest_task.last_error_msg,
                "completed_at": (
                    latest_task.completed_at.isoformat()
                    if latest_task.completed_at is not None
                    else None
                ),
            }

            if latest_task.status == TaskStatus.DONE:
                proxy_node_dict = _build_proxy_node(s, ip_id)

        return {
            "status": "ok",
            "ip": {
                "id": ip.id,
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
                "expire_date": (
                    ip.expire_date.isoformat()
                    if ip.expire_date is not None
                    else None
                ),
            },
            "task": task_dict,
            "proxy_node": proxy_node_dict,
        }


def _build_proxy_node(s, ip_id: int) -> dict | None:
    """task.status=done 时, 用 ip_id 找 proxy_record + join vps 拼一条龙 proxy_node dict.

    没找到 proxy_record (理论上 done 必有, 但兜底) → 返 None.
    """
    proxy = (
        s.query(ProxyRecord)
        .filter(ProxyRecord.ip_id == ip_id)
        .order_by(ProxyRecord.created_at.desc())
        .first()
    )
    if proxy is None:
        return None
    vps = s.get(VPSRecord, proxy.vps_id)
    if vps is None:
        return None

    return {
        "vps_id": vps.id,
        "vps_ip": vps.ip,
        "vps_port": proxy.vps_port,
        "protocol": proxy.protocol,
        "inbound_user": proxy.inbound_user,
        "inbound_pwd": proxy.get_inbound_pwd(),
        "status": proxy.status,
    }


# ============================================================
# 可用代理节点查询
# ============================================================

def list_available_proxies(country_code: str = "") -> list[dict]:
    """列出所有可用的代理节点.

    "可用" 定义:
    - proxy.status = USING
    - proxy.ip_id IS NOT NULL (rgvps 端口审计反推的孤儿 binding 不算)
    - vps.is_active = 1 且 (vps.expire_date 为空 或 vps.expire_date >= today)
    - ip.is_active = 1 且 (ip.expire_date 为空 或 ip.expire_date >= today)

    参数:
        country_code: 可选, 按国家代码过滤(如 "SG" / "US"), 空串=不过滤

    返回 list[dict], 每个 dict 形如:
        {
            "proxy_id": int,
            "vps_id": int,
            "ip_id": int,
            "protocol": "socks5",
            "host": "203.0.113.10",        # VPS 入口 IP
            "port": 18441,                    # VPS 上的端口
            "username": "xxx",                # inbound 账号
            "password": "yyy",                # inbound 明文密码
            "egress_ip": "198.51.100.10",       # 真正出口 IP
            "country_code": "SG",
            "country_name": "Singapore",
            "city": "Singapore",
        }

    无匹配返回 [].
    """
    today = date.today()
    cc_filter_msg = f" country_code={country_code!r}" if country_code else ""
    logger.info("查询可用代理节点: today=%s%s", today, cc_filter_msg)

    with session_scope() as s:
        query = (
            s.query(ProxyRecord, VPSRecord, IPRecord)
            .join(VPSRecord, ProxyRecord.vps_id == VPSRecord.id)
            .join(IPRecord, ProxyRecord.ip_id == IPRecord.id)
            .filter(ProxyRecord.status == ProxyStatus.USING)
            .filter(ProxyRecord.ip_id.isnot(None))
            .filter(VPSRecord.is_active == 1)
            .filter(or_(VPSRecord.expire_date.is_(None), VPSRecord.expire_date >= today))
            .filter(IPRecord.is_active == 1)
            .filter(or_(IPRecord.expire_date.is_(None), IPRecord.expire_date >= today))
            .order_by(IPRecord.country_code, VPSRecord.ip, ProxyRecord.vps_port)
        )

        if country_code:
            query = query.filter(IPRecord.country_code == country_code)

        rows = query.all()

        results = [
            {
                "proxy_id": p.id,
                "vps_id": v.id,
                "ip_id": ip.id,
                "protocol": p.protocol,
                "host": v.ip,
                "port": p.vps_port,
                "username": p.inbound_user,
                "password": p.get_inbound_pwd(),
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
                "country_name": ip.country_name,
                "city": ip.city,
            }
            for p, v, ip in rows
        ]

    logger.info("查询完成: 命中 %d 条可用节点", len(results))
    return results


# ============================================================
# 全量已登记 IP 查询 (过期 + 未过期, 单表不 join)
# ============================================================

def get_registered_ips() -> list[dict]:
    """列出全部已登记 IP(过期 + 未过期), 单表 ip_record, 不 join, 不过滤.

    给 agent 补到期日批量场景用: 拿到过期/没挂 IP 的 ip_id, 配合
    update_ip_expire_date 精准改. 跟 list_available_proxies(只列 USING 可用
    代理节点)区分: 本函数能看到过期/没挂的 IP. 绝不返上游密码/凭据.

    排序: is_active 升序(过期=0 排前, 方便补到期日) → country_code → egress_ip.

    返回 list[dict], 每条 (test/mcp_tools/spec.md §6.7):
        {"ip_id": int, "egress_ip": str,
         "country_code": str, "country_name": str, "city": str,
         "expire_date": "2026-06-18" | None, "is_active": 1 | 0}
    空库返 [].
    """
    logger.info("查询全部已登记 IP(过期+未过期)")

    with session_scope() as s:
        rows = (
            s.query(IPRecord)
            .order_by(
                IPRecord.is_active.asc(),
                IPRecord.country_code,
                IPRecord.egress_ip,
            )
            .all()
        )
        results = [
            {
                "ip_id": ip.id,
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
                "country_name": ip.country_name,
                "city": ip.city,
                "expire_date": (
                    ip.expire_date.isoformat()
                    if ip.expire_date is not None
                    else None
                ),
                "is_active": ip.is_active,
            }
            for ip in rows
        ]

    logger.info("查询完成: 命中 %d 条已登记 IP", len(results))
    return results


# ============================================================
# 全量已登记 VPS 查询 (装/未装、忙/闲、过期/未过期, 单表不 join)
# ============================================================

def get_registered_vps() -> list[dict]:
    """列出全部已登记 VPS(装/未装、忙/闲、过期/未过期), 单表 vps_record, 不 join, 不过滤.

    运维 / agent 看 VPS 池全貌(谁装了 xray / 忙闲 stage / 挂了几条代理 / 到期 /
    是否过期) + 拿 vps_id (备未来 update_vps_expire_date 用). 跟
    get_vps_registration_status(按 id 查单台装机进度)区分: 本函数是列全量、不带 task
    进度. **绝不返 SSH 凭据(密码 / 端口 / 登录名)**.

    排序: is_active 升序(过期=0 排前) → ip.

    返回 list[dict], 每条 (test/mcp_tools/spec.md §6.8):
        {"vps_id": int, "ip": str,
         "os_name": str, "os_version": str,
         "xray_version": str,            # ""=还没装 xray
         "stage": str,                   # connectable=空闲 / running=有工人在用
         "used_port_count": int,         # 挂了几条业务代理
         "expire_date": "2026-06-18" | None,
         "is_active": 1 | 0,             # 1可用/0过期
         "provider_domain": str}
    空库返 [].
    """
    logger.info("查询全部已登记 VPS(装/未装、忙/闲、过期/未过期)")

    with session_scope() as s:
        rows = (
            s.query(VPSRecord)
            .order_by(
                VPSRecord.is_active.asc(),
                VPSRecord.ip,
            )
            .all()
        )
        results = [
            {
                "vps_id": v.id,
                "ip": v.ip,
                "os_name": v.os_name,
                "os_version": v.os_version,
                "xray_version": v.xray_version,
                "stage": v.stage,
                "used_port_count": v.used_port_count,
                "expire_date": (
                    v.expire_date.isoformat()
                    if v.expire_date is not None
                    else None
                ),
                "is_active": v.is_active,
                "provider_domain": v.provider_domain,
            }
            for v in rows
        ]

    logger.info("查询完成: 命中 %d 台已登记 VPS", len(results))
    return results


# ============================================================
# IP 到期日写入 (白名单 patch, ADR-0008 §3.3 ABCD)
# ============================================================

def update_ip_expire_date(ip_id: int, expire_date: str) -> dict:
    """白名单 patch: 只改 ip_record.expire_date 单列 (CLAUDE.local.md §14.3 ABCD).

    规则 A 主键精准: 按 ip_id 主键定位单行.
    规则 B/C 白名单单列: 只写 expire_date, 不碰 is_active / egress_ip 等任何其他字段,
    不整对象覆盖.
    只校验日期格式 (YYYY-MM-DD), 不拦过去日期 (用户拍"允许任意合法日期").

    返回形状 (test/mcp_tools/spec.md §6.6):
      {"status": "ok", "ip": {"id", "egress_ip", "country_code",
                              "expire_date", "is_active"}}
      {"status": "not_found"}
      {"status": "invalid_date", "expire_date": <原值>}
    """
    try:
        parsed = date.fromisoformat(expire_date)
    except (ValueError, TypeError):
        logger.info(
            "update_ip_expire_date: ip_id=%s 日期非法 %r → invalid_date",
            ip_id, expire_date,
        )
        return {"status": "invalid_date", "expire_date": expire_date}

    with session_scope() as s:
        ip = s.get(IPRecord, ip_id)
        if ip is None:
            logger.info("update_ip_expire_date: ip_id=%s 不存在 → not_found", ip_id)
            return {"status": "not_found"}

        ip.expire_date = parsed
        s.flush()
        logger.info(
            "update_ip_expire_date: ip_id=%s → expire_date=%s",
            ip_id, parsed.isoformat(),
        )
        return {
            "status": "ok",
            "ip": {
                "id": ip.id,
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
                "expire_date": ip.expire_date.isoformat(),
                "is_active": ip.is_active,
            },
        }
