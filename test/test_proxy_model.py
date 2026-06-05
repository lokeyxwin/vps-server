"""ProxyRecord ORM 模型测试。

覆盖：
- 字段默认值
- 密码加密 + get_inbound_pwd 反查
- 密文不含明文
- from_extracted_binding 工厂方法（含字段缺省时的兜底）
- __repr__ 不泄露密码
- (vps_id, vps_port) 唯一约束
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db import (
    Base,
    IPProtocol,
    IPRecord,
    ProxyRecord,
    ProxyStatus,
    VPSRecord,
    get_engine,
    session_scope,
)


def _seed_vps(ip: str = "1.2.3.4") -> int:
    """造一个 VPSRecord 给 proxy.vps_id 当外键，返回 id。

    依赖 setUp 已经把三张表清空，所以这里只管 add。
    """
    with session_scope() as s:
        rec = VPSRecord.from_form(ip=ip, username="root", password="pwd", port=22)
        s.add(rec)
        s.flush()
        return rec.id


def _seed_ip(egress_ip: str = "8.8.8.8") -> int:
    """造一条 IPRecord 给 proxy.ip_id 当外键，返回 id。"""
    with session_scope() as s:
        rec = IPRecord.from_form(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="up",
            password="p",
            protocol=IPProtocol.SOCKS5,
            egress_ip=egress_ip,
        )
        s.add(rec)
        s.flush()
        return rec.id


class TestProxyRecordModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = get_engine("sqlite")
        Base.metadata.create_all(
            cls.engine,
            tables=[
                VPSRecord.__table__,
                IPRecord.__table__,
                ProxyRecord.__table__,
            ],
        )

    def setUp(self):
        # 顺序：先删有 FK 的 proxy_record，再删被指向的 ip_record / vps_record
        with session_scope() as s:
            s.query(ProxyRecord).delete()
            s.query(IPRecord).delete()
            s.query(VPSRecord).delete()

    # ---------- 工厂方法 + 加密 ----------

    def test_from_extracted_binding_encrypts_password(self):
        vps_id = _seed_vps()
        plain = "ClientP@ss123"
        rec = ProxyRecord.from_extracted_binding(
            vps_id=vps_id,
            binding={
                "port": 18443,
                "protocol": "socks5",
                "inbound_user": "client_alice",
                "inbound_pwd": plain,
                "upstream_host": "proxy.brightdata.io",
                "egress_ip": "1.2.3.4",
                "egress_country": "US",
            },
        )
        self.assertIsInstance(rec.inbound_pwd_encrypted, bytes)
        self.assertNotIn(plain.encode(), rec.inbound_pwd_encrypted)

    def test_from_extracted_binding_decrypt_roundtrip(self):
        vps_id = _seed_vps()
        plain = "RoundTripPwd!"
        rec = ProxyRecord.from_extracted_binding(
            vps_id=vps_id,
            binding={
                "port": 18443, "protocol": "socks5",
                "inbound_user": "u", "inbound_pwd": plain,
                "upstream_host": "x", "egress_ip": "1.1.1.1",
                "egress_country": "JP",
            },
        )
        self.assertEqual(rec.get_inbound_pwd(), plain)

    def test_from_extracted_binding_handles_missing_optional_fields(self):
        """egress_ip / egress_country 没传 → 落空串而不是 None。"""
        vps_id = _seed_vps()
        rec = ProxyRecord.from_extracted_binding(
            vps_id=vps_id,
            binding={
                "port": 18443, "protocol": "socks5",
                "inbound_user": "u", "inbound_pwd": "p",
                "upstream_host": "x",
                # 没传 egress_ip / egress_country
            },
        )
        self.assertEqual(rec.egress_ip, "")
        self.assertEqual(rec.egress_country, "")

    def test_default_protocol_is_socks5(self):
        vps_id = _seed_vps()
        rec = ProxyRecord.from_extracted_binding(
            vps_id=vps_id,
            binding={
                "port": 18443,
                "inbound_user": "u", "inbound_pwd": "p",
                "upstream_host": "x", "egress_ip": "", "egress_country": "",
                # 没传 protocol → 走默认
            },
        )
        self.assertEqual(rec.protocol, "socks5")

    # ---------- 数据库往返 ----------

    def test_full_lifecycle_insert_query_decrypt(self):
        plain = "LifecycleClientPwd"
        vps_id = _seed_vps()

        with session_scope() as s:
            s.add(ProxyRecord.from_extracted_binding(
                vps_id=vps_id,
                binding={
                    "port": 18443, "protocol": "socks5",
                    "inbound_user": "cu", "inbound_pwd": plain,
                    "upstream_host": "proxy.brightdata.io",
                    "egress_ip": "5.6.7.8", "egress_country": "US",
                },
            ))

        with session_scope() as s:
            rec = s.query(ProxyRecord).filter_by(vps_id=vps_id, vps_port=18443).one()
            self.assertEqual(rec.protocol, "socks5")
            self.assertEqual(rec.inbound_user, "cu")
            self.assertEqual(rec.upstream_host, "proxy.brightdata.io")
            self.assertEqual(rec.egress_ip, "5.6.7.8")
            self.assertEqual(rec.egress_country, "US")
            self.assertEqual(rec.status, ProxyStatus.USING)  # 默认状态
            self.assertEqual(rec.get_inbound_pwd(), plain)

    def test_password_actually_encrypted_on_disk(self):
        """原生 SQL 直接读，确认数据库里存的不是明文。"""
        plain = "RawDiskClientCheck"
        vps_id = _seed_vps()

        with session_scope() as s:
            s.add(ProxyRecord.from_extracted_binding(
                vps_id=vps_id,
                binding={
                    "port": 18443, "protocol": "socks5",
                    "inbound_user": "u", "inbound_pwd": plain,
                    "upstream_host": "x", "egress_ip": "1.1.1.1",
                    "egress_country": "JP",
                },
            ))

        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT inbound_pwd_encrypted FROM proxy_record WHERE vps_port=18443"
            )).first()
            raw = row[0]
            self.assertNotIn(plain.encode(), raw)
            self.assertGreater(len(raw), len(plain))

    # ---------- 状态字段 ----------

    def test_status_can_transition_using_to_expired(self):
        """IP 过期场景：把 status 改成 expired，期待 update 成功。"""
        vps_id = _seed_vps()
        with session_scope() as s:
            s.add(ProxyRecord.from_extracted_binding(
                vps_id=vps_id,
                binding={
                    "port": 18443, "protocol": "socks5",
                    "inbound_user": "u", "inbound_pwd": "p",
                    "upstream_host": "x", "egress_ip": "", "egress_country": "",
                },
            ))

        with session_scope() as s:
            rec = s.query(ProxyRecord).filter_by(vps_port=18443).one()
            rec.status = ProxyStatus.EXPIRED

        with session_scope() as s:
            self.assertEqual(
                s.query(ProxyRecord).filter_by(vps_port=18443).one().status,
                ProxyStatus.EXPIRED,
            )

    def test_status_constants_values(self):
        """常量值约定不变（业务代码靠这个判断）。"""
        self.assertEqual(ProxyStatus.USING, "using")
        self.assertEqual(ProxyStatus.EXPIRED, "expired")

    # ---------- 唯一约束 ----------

    def test_same_vps_same_port_raises_integrity_error(self):
        vps_id = _seed_vps()
        with session_scope() as s:
            s.add(ProxyRecord.from_extracted_binding(
                vps_id=vps_id,
                binding={
                    "port": 18443, "protocol": "socks5",
                    "inbound_user": "u1", "inbound_pwd": "p1",
                    "upstream_host": "x", "egress_ip": "", "egress_country": "",
                },
            ))

        with self.assertRaises(IntegrityError):
            with session_scope() as s:
                s.add(ProxyRecord.from_extracted_binding(
                    vps_id=vps_id,
                    binding={
                        "port": 18443,  # 同 vps_id 同 port，撞唯一约束
                        "protocol": "socks5",
                        "inbound_user": "u2", "inbound_pwd": "p2",
                        "upstream_host": "y", "egress_ip": "", "egress_country": "",
                    },
                ))

    def test_different_vps_same_port_is_ok(self):
        """同一个端口数字在不同 VPS 上可以共存（端口范围是 per-VPS 的）。"""
        vps_a = _seed_vps("1.1.1.1")
        vps_b = _seed_vps("2.2.2.2")
        with session_scope() as s:
            s.add(ProxyRecord.from_extracted_binding(
                vps_id=vps_a,
                binding={
                    "port": 18443, "protocol": "socks5",
                    "inbound_user": "u", "inbound_pwd": "p",
                    "upstream_host": "x", "egress_ip": "", "egress_country": "",
                },
            ))
            s.add(ProxyRecord.from_extracted_binding(
                vps_id=vps_b,
                binding={
                    "port": 18443, "protocol": "socks5",
                    "inbound_user": "u", "inbound_pwd": "p",
                    "upstream_host": "x", "egress_ip": "", "egress_country": "",
                },
            ))
        # 不抛即过
        with session_scope() as s:
            self.assertEqual(s.query(ProxyRecord).count(), 2)

    # ---------- __repr__ ----------

    def test_repr_does_not_leak_password(self):
        vps_id = _seed_vps()
        rec = ProxyRecord.from_extracted_binding(
            vps_id=vps_id,
            binding={
                "port": 18443, "protocol": "socks5",
                "inbound_user": "u", "inbound_pwd": "SuperSecret",
                "upstream_host": "x", "egress_ip": "1.1.1.1",
                "egress_country": "JP",
            },
        )
        r = repr(rec)
        self.assertNotIn("SuperSecret", r)
        # 但 egress / status 等公开字段应该可见
        self.assertIn("1.1.1.1", r)
        self.assertIn("18443", r)

    # ---------- ip_id FK + from_new_deployment ----------

    def test_from_extracted_binding_leaves_ip_id_none(self):
        """rgvps 端口审计抠出来的 binding 不知道对应哪条 IP，ip_id 落 None。"""
        vps_id = _seed_vps()
        rec = ProxyRecord.from_extracted_binding(
            vps_id=vps_id,
            binding={
                "port": 18443, "protocol": "socks5",
                "inbound_user": "u", "inbound_pwd": "p",
                "upstream_host": "x", "egress_ip": "", "egress_country": "",
            },
        )
        self.assertIsNone(rec.ip_id)

    def test_from_new_deployment_sets_ip_id(self):
        """rgIP 新部署的 binding 必填 ip_id。"""
        vps_id = _seed_vps()
        ip_id = _seed_ip("9.9.9.9")
        rec = ProxyRecord.from_new_deployment(
            vps_id=vps_id, vps_port=18443, ip_id=ip_id,
            inbound_user="cu", inbound_pwd="cp",
            upstream_host="proxy.example.com",
            egress_ip="9.9.9.9", egress_country="US",
        )
        self.assertEqual(rec.ip_id, ip_id)
        self.assertEqual(rec.vps_id, vps_id)
        self.assertEqual(rec.vps_port, 18443)
        self.assertEqual(rec.protocol, "socks5")  # 默认
        self.assertEqual(rec.status, ProxyStatus.USING)  # 默认

    def test_from_new_deployment_encrypts_inbound_pwd(self):
        vps_id = _seed_vps()
        ip_id = _seed_ip("9.9.9.10")
        plain = "ClientP@ss"
        rec = ProxyRecord.from_new_deployment(
            vps_id=vps_id, vps_port=18444, ip_id=ip_id,
            inbound_user="u", inbound_pwd=plain,
            upstream_host="x", egress_ip="9.9.9.10",
        )
        self.assertIsInstance(rec.inbound_pwd_encrypted, bytes)
        self.assertNotIn(plain.encode(), rec.inbound_pwd_encrypted)
        self.assertEqual(rec.get_inbound_pwd(), plain)

    def test_proxy_record_with_ip_id_full_lifecycle(self):
        """rgIP 部署后的 proxy_record 能正确 JOIN 回 ip_record。"""
        vps_id = _seed_vps("1.2.3.4")
        ip_id = _seed_ip("9.9.9.11")

        with session_scope() as s:
            s.add(ProxyRecord.from_new_deployment(
                vps_id=vps_id, vps_port=18445, ip_id=ip_id,
                inbound_user="u", inbound_pwd="p",
                upstream_host="proxy.example.com",
                egress_ip="9.9.9.11", egress_country="SG",
            ))

        with session_scope() as s:
            rec = s.query(ProxyRecord).filter_by(vps_port=18445).one()
            self.assertEqual(rec.ip_id, ip_id)
            # 能反查到 IPRecord
            ip = s.query(IPRecord).filter_by(id=rec.ip_id).one()
            self.assertEqual(ip.egress_ip, "9.9.9.11")


if __name__ == "__main__":
    unittest.main()
