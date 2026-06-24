"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-SCHEMA-PROXY-T26 ProxyProtocol 常量类 + ProxyRecord.method 字段

故事:
  task/26 给 proxy_record 加 method 字段（SS 加密方式）+ ProxyProtocol
  常量类（socks5 / shadowsocks）。对外节点从 socks5 改 Shadowsocks 后,
  光记 protocol 不够, SS 还要存加密方式才能拼出标准 ss://（ADR-0011 §决策 §3）。

  本测试在内存 SQLite 上验证模型层契约, 不污染 dev DB, 不依赖任何
  worker / service 实现。

测试矩阵:
  TC-01 ProxyProtocol 常量值正确 (socks5 / shadowsocks)
  TC-02 ProxyRecord.method 字段存在于 ORM mapping
  TC-03 method 默认空串 (from_new_deployment 不传 method)
  TC-04 from_new_deployment(protocol=SHADOWSOCKS, method=...) round-trip 一致
  TC-05 protocol 默认值仍是 socks5 (旧节点兼容)
  TC-06 __repr__ 不泄露密码 (回归)

不应发生:
  - 任何写库动作落到 db/vps_server.db (用独立 in-memory engine)
  - import services/*
========================================================================
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import IPRecord, ProxyProtocol, ProxyRecord, VPSRecord


def _make_in_memory_engine():
    """每个 TestCase 自带一个全新的 in-memory SQLite engine + sessionmaker。

    建齐 ProxyRecord 的外键依赖表 (vps_record / ip_record), 不复用
    db.session.SessionLocal —— 那是绑在 dev DB 上的, 会污染。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[VPSRecord.__table__, IPRecord.__table__, ProxyRecord.__table__],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _seed_vps_and_ip(session) -> tuple[int, int]:
    """造一台 VPS + 一条 IP, 返回 (vps_id, ip_id) 供 ProxyRecord 外键引用。"""
    vps = VPSRecord.from_form(
        ip="10.0.0.1", username="root", password="x", port=22
    )
    ip = IPRecord.from_form(
        entry_host="up.example.com",
        entry_port=1080,
        username="u",
        password="p",
        protocol="socks5",
        egress_ip="203.0.113.7",
    )
    session.add_all([vps, ip])
    session.commit()
    session.refresh(vps)
    session.refresh(ip)
    return vps.id, ip.id


class TestProxyProtocolConstants(unittest.TestCase):
    """ProxyProtocol 常量类 (ADR-0011 §决策 §3)。"""

    # ---------- TC-01 ----------
    def test_tc01_protocol_constant_values(self) -> None:
        """ProxyProtocol 常量值正确。"""
        self.assertEqual(ProxyProtocol.SOCKS5, "socks5")
        self.assertEqual(ProxyProtocol.SHADOWSOCKS, "shadowsocks")


class TestProxyRecordMethodField(unittest.TestCase):
    """ProxyRecord.method 字段行为契约。"""

    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

    def tearDown(self):
        self.engine.dispose()

    # ---------- TC-02 ----------
    def test_tc02_method_field_in_mapping(self) -> None:
        """method 字段存在于 ProxyRecord ORM mapping。"""
        mapper_columns = {c.key for c in inspect(ProxyRecord).mapper.columns}
        self.assertIn("method", mapper_columns)

    # ---------- TC-03 ----------
    def test_tc03_method_defaults_to_empty(self) -> None:
        """from_new_deployment 不传 method → 落库读回空串。"""
        with self.Session() as s:
            vps_id, ip_id = _seed_vps_and_ip(s)
            rec = ProxyRecord.from_new_deployment(
                vps_id=vps_id,
                vps_port=40001,
                ip_id=ip_id,
                inbound_user="cu",
                inbound_pwd="cp",
                upstream_host="up.example.com",
                egress_ip="203.0.113.7",
            )
            s.add(rec)
            s.commit()

        with self.Session() as s:
            got = s.query(ProxyRecord).filter_by(vps_port=40001).one()
            self.assertEqual(got.method, "")
            # 不传 protocol 默认仍 socks5
            self.assertEqual(got.protocol, ProxyProtocol.SOCKS5)

    # ---------- TC-04 ----------
    def test_tc04_shadowsocks_deployment_round_trips(self) -> None:
        """from_new_deployment(protocol=SHADOWSOCKS, method=...) 落库读回一致。"""
        with self.Session() as s:
            vps_id, ip_id = _seed_vps_and_ip(s)
            rec = ProxyRecord.from_new_deployment(
                vps_id=vps_id,
                vps_port=40002,
                ip_id=ip_id,
                inbound_user="cu",
                inbound_pwd="cp",
                upstream_host="up.example.com",
                egress_ip="203.0.113.7",
                protocol=ProxyProtocol.SHADOWSOCKS,
                method="aes-256-gcm",
            )
            s.add(rec)
            s.commit()

        with self.Session() as s:
            got = s.query(ProxyRecord).filter_by(vps_port=40002).one()
            self.assertEqual(got.protocol, "shadowsocks")
            self.assertEqual(got.method, "aes-256-gcm")

    # ---------- TC-05 ----------
    def test_tc05_column_default_is_socks5(self) -> None:
        """protocol column 默认仍是 socks5 (旧节点兼容); method column 默认空串。"""
        proto_col = inspect(ProxyRecord).mapper.columns["protocol"]
        self.assertEqual(proto_col.default.arg, ProxyProtocol.SOCKS5)
        method_col = inspect(ProxyRecord).mapper.columns["method"]
        self.assertEqual(method_col.default.arg, "")

    # ---------- TC-06 ----------
    def test_tc06_repr_hides_password(self) -> None:
        """__repr__ 不泄露 inbound 密码明文 (回归)。"""
        with self.Session() as s:
            vps_id, ip_id = _seed_vps_and_ip(s)
            rec = ProxyRecord.from_new_deployment(
                vps_id=vps_id,
                vps_port=40003,
                ip_id=ip_id,
                inbound_user="cu",
                inbound_pwd="SuperSecretInboundPwd",
                upstream_host="up.example.com",
                egress_ip="203.0.113.7",
                protocol=ProxyProtocol.SHADOWSOCKS,
                method="aes-256-gcm",
            )
            s.add(rec)
            s.commit()
            s.refresh(rec)
            repr_str = repr(rec)
            self.assertNotIn("SuperSecretInboundPwd", repr_str)
            self.assertNotIn("password", repr_str.lower())


if __name__ == "__main__":
    unittest.main()
