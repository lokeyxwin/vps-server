"""TC-16 MCP 节点返回加 method + share_link (db/queries 层).

验收金标准: test/mcp_tools/spec.md §6.4 / §6.5 + ADR-0011 §决策 §8
任务单: task/doing_29_mcp_node_return_method_and_share_link.md

覆盖 _build_proxy_node(经 query_ip_status) + list_available_proxies 两个返回路径:
  shadowsocks 节点 → method 非空 + share_link 是标准 ss:// (能 base64 解出 method:pwd)
  socks5 存量节点 → method 空 + share_link 空串(不伪造 ss://)

子测:
  TC-16-a query_ip_status(SS): proxy_node 含 method + share_link, ss:// 解码往返正确
  TC-16-b query_ip_status(socks5): proxy_node.method 空 + share_link 空串
  TC-16-c list_available_proxies(SS): 节点含 method + share_link(ss://)
  TC-16-d list_available_proxies(socks5): method 空 + share_link 空串
  TC-16-e share_link host:port 跟 VPS 入口对齐
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import queries
from db.base import Base
from db.models import (
    IPRecord,
    IPTask,
    ProxyProtocol,
    ProxyRecord,
    ProxyStatus,
    TaskStatus,
    VPSRecord,
    VPSTask,
)


@pytest.fixture
def db_session(monkeypatch):
    """in-memory SQLite + create_all 5 张表, monkeypatch queries.session_scope."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            VPSRecord.__table__,
            VPSTask.__table__,
            IPRecord.__table__,
            IPTask.__table__,
            ProxyRecord.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def fake_scope():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(queries, "session_scope", fake_scope)
    yield Session


def _seed_vps(Session, *, ip: str = "203.0.113.10") -> int:
    s = Session()
    try:
        vps = VPSRecord(
            ip=ip,
            port=22,
            username="root",
            password_encrypted=b"dummy-ciphertext",
            is_active=1,
        )
        s.add(vps)
        s.commit()
        return vps.id
    finally:
        s.close()


def _seed_ip(Session, *, egress_ip: str, country_code: str = "SG") -> int:
    s = Session()
    try:
        ip = IPRecord(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="upstream_user",
            password_encrypted=b"dummy-ciphertext",
            protocol="socks5",
            egress_ip=egress_ip,
            country_code=country_code,
            country_name="Singapore",
            city="Singapore",
            is_active=1,
            expire_date=date(2026, 12, 31),
        )
        s.add(ip)
        s.commit()
        return ip.id
    finally:
        s.close()


def _seed_proxy(
    Session,
    *,
    vps_id: int,
    ip_id: int,
    vps_port: int,
    protocol: str,
    method: str,
    inbound_pwd: str,
) -> int:
    """用 from_new_deployment 造记录(内部加密, get_inbound_pwd 能往返)."""
    s = Session()
    try:
        proxy = ProxyRecord.from_new_deployment(
            vps_id=vps_id,
            vps_port=vps_port,
            ip_id=ip_id,
            inbound_user="proxy_user",
            inbound_pwd=inbound_pwd,
            upstream_host="proxy.example.com",
            egress_ip="198.51.100.10",
            egress_country="SG",
            protocol=protocol,
            method=method,
        )
        proxy.status = ProxyStatus.USING
        s.add(proxy)
        s.commit()
        return proxy.id
    finally:
        s.close()


def _seed_ip_task_done(Session, *, ip_id: int, vps_id: int) -> int:
    s = Session()
    try:
        task = IPTask(
            ip_id=ip_id,
            vps_id=vps_id,
            status=TaskStatus.DONE,
            completed_at=datetime(2026, 6, 24, 12, 0, 0),
        )
        s.add(task)
        s.commit()
        return task.id
    finally:
        s.close()


def _decode_ss_userinfo(share_link: str) -> str:
    """从 ss:// 链接抠出 userinfo 段并 base64url 解码."""
    userinfo = share_link.removeprefix("ss://").split("@", 1)[0]
    pad = "=" * (-len(userinfo) % 4)
    return base64.urlsafe_b64decode(userinfo + pad).decode()


# ============================================================
# TC-16-a query_ip_status(SS) → proxy_node 含 method + share_link
# ============================================================

def test_query_ip_status_ss_has_method_and_share_link(db_session):
    vps_id = _seed_vps(db_session, ip="203.0.113.10")
    ip_id = _seed_ip(db_session, egress_ip="198.51.100.10")
    _seed_proxy(
        db_session, vps_id=vps_id, ip_id=ip_id, vps_port=18441,
        protocol=ProxyProtocol.SHADOWSOCKS, method="aes-256-gcm",
        inbound_pwd="s3cr3t-pwd",
    )
    _seed_ip_task_done(db_session, ip_id=ip_id, vps_id=vps_id)

    result = queries.query_ip_status(ip_id=ip_id)
    node = result["proxy_node"]

    assert node is not None
    assert node["method"] == "aes-256-gcm"
    assert node["protocol"] == "shadowsocks"
    # share_link 是标准 ss://
    assert node["share_link"].startswith("ss://")
    # 解码 userinfo 还原 method:password
    assert _decode_ss_userinfo(node["share_link"]) == "aes-256-gcm:s3cr3t-pwd"


# ============================================================
# TC-16-b query_ip_status(socks5 存量) → method 空 + share_link 空串
# ============================================================

def test_query_ip_status_socks5_share_link_empty(db_session):
    vps_id = _seed_vps(db_session, ip="203.0.113.20")
    ip_id = _seed_ip(db_session, egress_ip="198.51.100.20")
    _seed_proxy(
        db_session, vps_id=vps_id, ip_id=ip_id, vps_port=18442,
        protocol=ProxyProtocol.SOCKS5, method="",
        inbound_pwd="legacy-pwd",
    )
    _seed_ip_task_done(db_session, ip_id=ip_id, vps_id=vps_id)

    result = queries.query_ip_status(ip_id=ip_id)
    node = result["proxy_node"]

    assert node is not None
    assert node["protocol"] == "socks5"
    assert node["method"] == ""
    # socks5 不伪造 ss://, share_link 空串
    assert node["share_link"] == ""


# ============================================================
# TC-16-c list_available_proxies(SS) → 含 method + share_link
# ============================================================

def test_list_available_proxies_ss_has_share_link(db_session):
    vps_id = _seed_vps(db_session, ip="203.0.113.30")
    ip_id = _seed_ip(db_session, egress_ip="198.51.100.30")
    _seed_proxy(
        db_session, vps_id=vps_id, ip_id=ip_id, vps_port=18443,
        protocol=ProxyProtocol.SHADOWSOCKS, method="aes-256-gcm",
        inbound_pwd="node-pwd",
    )

    rows = queries.list_available_proxies()
    assert len(rows) == 1
    row = rows[0]

    assert row["method"] == "aes-256-gcm"
    assert row["protocol"] == "shadowsocks"
    assert row["share_link"].startswith("ss://")
    assert _decode_ss_userinfo(row["share_link"]) == "aes-256-gcm:node-pwd"


# ============================================================
# TC-16-d list_available_proxies(socks5) → method 空 + share_link 空串
# ============================================================

def test_list_available_proxies_socks5_share_link_empty(db_session):
    vps_id = _seed_vps(db_session, ip="203.0.113.40")
    ip_id = _seed_ip(db_session, egress_ip="198.51.100.40")
    _seed_proxy(
        db_session, vps_id=vps_id, ip_id=ip_id, vps_port=18444,
        protocol=ProxyProtocol.SOCKS5, method="",
        inbound_pwd="legacy-pwd",
    )

    rows = queries.list_available_proxies()
    assert len(rows) == 1
    row = rows[0]

    assert row["protocol"] == "socks5"
    assert row["method"] == ""
    assert row["share_link"] == ""


# ============================================================
# TC-16-e share_link host:port 跟 VPS 入口对齐
# ============================================================

def test_share_link_host_port_matches_vps_entry(db_session):
    vps_id = _seed_vps(db_session, ip="203.0.113.50")
    ip_id = _seed_ip(db_session, egress_ip="198.51.100.50")
    _seed_proxy(
        db_session, vps_id=vps_id, ip_id=ip_id, vps_port=28888,
        protocol=ProxyProtocol.SHADOWSOCKS, method="aes-256-gcm",
        inbound_pwd="pwd",
    )

    rows = queries.list_available_proxies()
    share_link = rows[0]["share_link"]
    # host:port 段明文, 跟 VPS 入口 IP + 业务端口对齐(非出口 IP)
    assert "@203.0.113.50:28888" in share_link
