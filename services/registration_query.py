"""注册进度查询业务: query_vps_status / query_ip_status (read-only).

给 MCP 状态查询工具 (get_vps_registration_status / get_ip_registration_status)
当业务后端用. handler 不写 SQL, 走这里.

位置说明 (ADR-0007 §影响清单 + 实现者拍板):
  跟现有 services/proxy_query.py 同位, read-only 查询保留在 services/ 下.
  虽然 ADR-0001 §决策 §5 写"新代码不 import services/", 但 read-only 查询语义
  跟"业务编排"不同, ADR-0007 §影响清单已标注 "services/proxy_query 暂保留",
  本模块沿用同一姿态. 后续单独评估是否搬到 db/queries/.

输出契约: test/mcp_tools/spec.md §6.3 (vps) / §6.4 (ip)
"""

from __future__ import annotations

from db.models import (
    IPRecord,
    IPTask,
    ProxyRecord,
    TaskStatus,
    VPSRecord,
    VPSTask,
)
from db.session import session_scope
from log import get_logger


logger = get_logger("services.registration_query")


# ============================================================
# VPS 注册进度查询
# ============================================================

def query_vps_status(
    vps_id: int | None = None,
    task_id: int | None = None,
) -> dict:
    """查 VPS 装机进度. vps_id 或 task_id 二选一(vps_id 优先).

    返回形状 (spec §6.3):
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
        # 解析 vps_id (若只给 task_id)
        if vps_id is None:
            task = s.get(VPSTask, task_id)
            if task is None:
                return {"status": "not_found"}
            vps_id = task.vps_id

        vps = s.get(VPSRecord, vps_id)
        if vps is None:
            return {"status": "not_found"}

        # 最新一条 task (无论 status)
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

    返回形状 (spec §6.4, ⭐ 一条龙):
      {"status": "ok",
       "ip":   {"id", "egress_ip", "country_code", "status", "expire_date"},
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
        # 解析 ip_id (若只给 task_id)
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

            # task.status=done 时拿 proxy_record + vps (一条龙)
            if latest_task.status == TaskStatus.DONE:
                proxy_node_dict = _build_proxy_node(s, ip_id)

        return {
            "status": "ok",
            "ip": {
                "id": ip.id,
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
                "status": ip.status,
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
