"""IPRecord ORM 模型测试。

覆盖：
- 字段默认值（地区字段空、is_active=1、user_label="")
- 密码加密 + get_password 反查
- 密文不含明文（原生 SQL 读盘验证）
- from_form 工厂方法（带 geo / 不带 geo）
- __repr__ 不泄露 username / password
- egress_ip 唯一约束（业务身份键）
- entry_host 不唯一（同一入口可对应多条 egress）
- is_active 可被业务层切换
- IPProtocol 常量
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db import Base, IPProtocol, IPRecord, get_engine, session_scope


class TestIPRecordModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = get_engine("sqlite")
        Base.metadata.create_all(cls.engine, tables=[IPRecord.__table__])

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(cls.engine, tables=[IPRecord.__table__])

    def setUp(self):
        with session_scope() as s:
            s.query(IPRecord).delete()

    # ---------- 工厂方法 + 加密 ----------

    def _make(self, **overrides) -> IPRecord:
        """默认参数构造一条 IPRecord，便于各 case 覆盖。"""
        base = dict(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="upstream_user",
            password="UpstreamPwd123",
            protocol=IPProtocol.SOCKS5,
            egress_ip="1.2.3.4",
        )
        base.update(overrides)
        return IPRecord.from_form(**base)

    def test_from_form_encrypts_password(self):
        rec = self._make(password="MyUpstreamSecret")
        self.assertIsInstance(rec.password_encrypted, bytes)
        self.assertNotIn(b"MyUpstreamSecret", rec.password_encrypted)
        self.assertGreater(len(rec.password_encrypted), len("MyUpstreamSecret"))

    def test_get_password_returns_plaintext(self):
        rec = self._make(password="RoundTripPwd!")
        self.assertEqual(rec.get_password(), "RoundTripPwd!")

    def test_from_form_geo_none_leaves_region_fields_empty(self):
        rec = self._make(geo=None)
        self.assertEqual(rec.country_code, "")
        self.assertEqual(rec.country_name, "")
        self.assertEqual(rec.city, "")
        self.assertEqual(rec.region_name, "")

    def test_from_form_geo_populates_region_fields(self):
        rec = self._make(geo={
            "country_code": "US",
            "country_name": "United States",
            "city": "Los Angeles",
            "region_name": "California",
        })
        self.assertEqual(rec.country_code, "US")
        self.assertEqual(rec.country_name, "United States")
        self.assertEqual(rec.city, "Los Angeles")
        self.assertEqual(rec.region_name, "California")

    def test_from_form_partial_geo_fills_missing_with_empty(self):
        """geoip 有时只给 country 没给 city —— 缺的字段落空串。"""
        rec = self._make(geo={"country_code": "SG", "country_name": "Singapore"})
        self.assertEqual(rec.country_code, "SG")
        self.assertEqual(rec.country_name, "Singapore")
        self.assertEqual(rec.city, "")
        self.assertEqual(rec.region_name, "")

    def test_default_is_active_is_1(self):
        """新登记的 IP 默认可用。"""
        with session_scope() as s:
            s.add(self._make(egress_ip="9.9.9.9"))

        with session_scope() as s:
            rec = s.query(IPRecord).filter_by(egress_ip="9.9.9.9").one()
            self.assertEqual(rec.is_active, 1)

    def test_default_user_label_is_empty(self):
        with session_scope() as s:
            s.add(self._make(egress_ip="9.9.9.10"))

        with session_scope() as s:
            rec = s.query(IPRecord).filter_by(egress_ip="9.9.9.10").one()
            self.assertEqual(rec.user_label, "")

    def test_default_provider_domain_is_empty(self):
        with session_scope() as s:
            s.add(self._make(egress_ip="9.9.9.11"))

        with session_scope() as s:
            rec = s.query(IPRecord).filter_by(egress_ip="9.9.9.11").one()
            self.assertEqual(rec.provider_domain, "")

    def test_expire_date_can_be_none(self):
        with session_scope() as s:
            s.add(self._make(egress_ip="9.9.9.12", expire_date=None))

        with session_scope() as s:
            rec = s.query(IPRecord).filter_by(egress_ip="9.9.9.12").one()
            self.assertIsNone(rec.expire_date)

    # ---------- 数据库往返 ----------

    def test_full_lifecycle_insert_query_decrypt(self):
        plain = "LifecycleUpstreamPwd"
        with session_scope() as s:
            s.add(IPRecord.from_form(
                entry_host="proxy.brightdata.io",
                entry_port=22225,
                username="brd-cust-XYZ",
                password=plain,
                protocol=IPProtocol.SOCKS5,
                egress_ip="5.6.7.8",
                provider_domain="brightdata.com",
                expire_date=date(2027, 1, 1),
                user_label="梯子A",
                geo={
                    "country_code": "US",
                    "country_name": "United States",
                    "city": "Los Angeles",
                    "region_name": "California",
                },
            ))

        with session_scope() as s:
            rec = s.query(IPRecord).filter_by(egress_ip="5.6.7.8").one()
            self.assertEqual(rec.entry_host, "proxy.brightdata.io")
            self.assertEqual(rec.entry_port, 22225)
            self.assertEqual(rec.username, "brd-cust-XYZ")
            self.assertEqual(rec.protocol, IPProtocol.SOCKS5)
            self.assertEqual(rec.provider_domain, "brightdata.com")
            self.assertEqual(rec.expire_date, date(2027, 1, 1))
            self.assertEqual(rec.user_label, "梯子A")
            self.assertEqual(rec.country_code, "US")
            self.assertEqual(rec.city, "Los Angeles")
            self.assertEqual(rec.is_active, 1)
            self.assertEqual(rec.get_password(), plain)
            self.assertIsNotNone(rec.created_at)
            self.assertIsNotNone(rec.id)

    def test_password_actually_encrypted_on_disk(self):
        """原生 SQL 直接读，确认数据库里存的不是明文。"""
        plain = "RawDiskUpstreamCheck"
        with session_scope() as s:
            s.add(self._make(egress_ip="20.20.20.20", password=plain))

        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT password_encrypted FROM ip_record WHERE egress_ip='20.20.20.20'"
            )).first()
            raw = row[0]
            self.assertNotIn(plain.encode(), raw)
            self.assertGreater(len(raw), len(plain))

    # ---------- 唯一约束 ----------

    def test_duplicate_egress_ip_raises_integrity_error(self):
        """egress_ip 是业务身份键，不允许重复。"""
        with session_scope() as s:
            s.add(self._make(egress_ip="1.1.1.1"))

        with self.assertRaises(IntegrityError):
            with session_scope() as s:
                s.add(self._make(egress_ip="1.1.1.1"))  # 同 egress

    def test_same_entry_host_different_egress_is_ok(self):
        """同一入口域名可以挂多条 egress（云服务商常见做法）。"""
        with session_scope() as s:
            s.add(self._make(
                entry_host="proxy.shared.io", egress_ip="2.2.2.1"
            ))
            s.add(self._make(
                entry_host="proxy.shared.io", egress_ip="2.2.2.2"
            ))

        with session_scope() as s:
            self.assertEqual(
                s.query(IPRecord).filter_by(entry_host="proxy.shared.io").count(),
                2,
            )

    # ---------- 状态切换 ----------

    def test_is_active_can_transition_to_0(self):
        """IP 过期场景：业务层把 is_active 改成 0。"""
        with session_scope() as s:
            s.add(self._make(egress_ip="30.30.30.30"))

        with session_scope() as s:
            rec = s.query(IPRecord).filter_by(egress_ip="30.30.30.30").one()
            rec.is_active = 0

        with session_scope() as s:
            self.assertEqual(
                s.query(IPRecord).filter_by(egress_ip="30.30.30.30").one().is_active,
                0,
            )

    # ---------- 常量 ----------

    def test_ip_protocol_constants(self):
        self.assertEqual(IPProtocol.SOCKS5, "socks5")
        self.assertEqual(IPProtocol.HTTP, "http")

    # ---------- __repr__ ----------

    def test_repr_does_not_leak_credentials(self):
        rec = self._make(
            egress_ip="42.42.42.42",
            username="UpstreamSecretUser",
            password="UpstreamSecretPwd",
            geo={"country_code": "JP", "country_name": "Japan",
                 "city": "Tokyo", "region_name": "Tokyo"},
        )
        r = repr(rec)
        # 敏感字段不能出现
        self.assertNotIn("UpstreamSecretPwd", r)
        self.assertNotIn("UpstreamSecretUser", r)
        # 公开字段可见
        self.assertIn("42.42.42.42", r)
        self.assertIn("JP", r)


if __name__ == "__main__":
    unittest.main()
