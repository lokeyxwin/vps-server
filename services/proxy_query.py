"""代理节点查询业务：列出当前可用的 proxy 节点。

未来的 MCP 查询工具入口（admin / user 两套 MCP 都会复用这个函数）。
返回纯结构化 dict 列表，不做任何展示层格式化——格式化交给 agent。

"可用"的精确定义：
- proxy.status = USING
- proxy.ip_id IS NOT NULL（rgvps 端口审计反推的孤儿 binding 不算）
- vps.is_active = 1 且 (vps.expire_date 为空 或 vps.expire_date >= today)
- ip.is_active = 1 且 (ip.expire_date 为空 或 ip.expire_date >= today)
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_

from db import (
    IPRecord,
    ProxyRecord,
    ProxyStatus,
    VPSRecord,
    session_scope,
)
from log import get_logger


logger = get_logger("services.proxy_query")


def list_available_proxies(country_code: str = "") -> list[dict]:
    """列出所有可用的代理节点。

    参数：
        country_code: 可选，按国家代码过滤（如 "SG" / "US"），空串=不过滤

    返回 list[dict]，每个 dict 形如：
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

    无匹配返回 []。
    """
    today = date.today()
    cc_filter_msg = f" country_code={country_code!r}" if country_code else ""
    logger.info("查询可用代理节点：today=%s%s", today, cc_filter_msg)

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

    logger.info("查询完成：命中 %d 条可用节点", len(results))
    return results
