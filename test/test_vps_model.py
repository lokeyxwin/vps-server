import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db import Base, VPSRecord, XrayStatus, get_engine, session_scope
from db.session import SessionLocal


class TestVPSRecordModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = get_engine("sqlite")
        Base.metadata.create_all(cls.engine, tables=[VPSRecord.__table__])

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(cls.engine, tables=[VPSRecord.__table__])

    def setUp(self):
        # 每个测试前清空表，避免互相干扰
        with session_scope() as s:
            s.query(VPSRecord).delete()

    def test_from_form_creates_record_with_encrypted_password(self):
        record = VPSRecord.from_form(
            ip="1.2.3.4",
            username="root",
            password="MySecret123",
            port=22,
        )
        self.assertNotEqual(record.password_encrypted, b"")
        self.assertNotIn(b"MySecret123", record.password_encrypted)
        self.assertIsInstance(record.password_encrypted, bytes)

    def test_get_password_returns_plaintext(self):
        record = VPSRecord.from_form(
            ip="1.2.3.4", username="root", password="MySecret123", port=22
        )
        self.assertEqual(record.get_password(), "MySecret123")

    def test_password_actually_encrypted_on_disk(self):
        """直接读 SQLite 原始字节，确认数据库里存的不是明文。"""
        plaintext = "RawDiskCheck@2024"
        record = VPSRecord.from_form(
            ip="10.0.0.1", username="admin", password=plaintext, port=22
        )
        with session_scope() as s:
            s.add(record)

        # 用原生 SQL 读出来
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT password_encrypted FROM vps_record WHERE ip='10.0.0.1'")).first()
            raw_bytes = row[0]
            self.assertNotIn(plaintext.encode(), raw_bytes)
            self.assertTrue(len(raw_bytes) > len(plaintext))  # 密文比明文长

    def test_full_lifecycle_insert_query_decrypt(self):
        plain = "LifecycleP@ssw0rd"
        with session_scope() as s:
            s.add(VPSRecord.from_form(
                ip="172.16.0.1",
                username="root",
                password=plain,
                port=22,
                os_name="Ubuntu",
                os_version="22.04",
                expire_date=date(2026, 12, 31),
            ))

        with session_scope() as s:
            record = s.query(VPSRecord).filter_by(ip="172.16.0.1").one()
            self.assertEqual(record.os_name, "Ubuntu")
            self.assertEqual(record.os_version, "22.04")
            self.assertEqual(record.expire_date, date(2026, 12, 31))
            self.assertEqual(record.get_password(), plain)
            self.assertIsNotNone(record.created_at)
            self.assertIsNotNone(record.id)

    def test_duplicate_ip_raises_integrity_error(self):
        with session_scope() as s:
            s.add(VPSRecord.from_form(ip="9.9.9.9", username="root", password="x", port=22))

        with self.assertRaises(IntegrityError):
            with session_scope() as s:
                s.add(VPSRecord.from_form(ip="9.9.9.9", username="root", password="y", port=22))

    def test_provider_domain_defaults_to_empty_string(self):
        """未传 provider_domain 时落库为空串，不影响其它字段。"""
        with session_scope() as s:
            s.add(VPSRecord.from_form(
                ip="100.100.100.200", username="root", password="x", port=22
            ))

        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="100.100.100.200").one()
            self.assertEqual(rec.provider_domain, "")

    def test_provider_domain_persisted_when_provided(self):
        """传入 provider_domain 时按原样落库，业务可按域名分组查询。"""
        with session_scope() as s:
            s.add(VPSRecord.from_form(
                ip="100.100.100.201",
                username="root",
                password="x",
                port=22,
                provider_domain="linode.com",
            ))

        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="100.100.100.201").one()
            self.assertEqual(rec.provider_domain, "linode.com")

    def test_new_record_defaults_xray_fields(self):
        """新建 VPSRecord 记录默认 xray_status='not_installed'，其他 xray 字段为空/None。"""
        with session_scope() as s:
            s.add(VPSRecord.from_form(
                ip="100.100.100.100", username="root", password="x", port=22
            ))

        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="100.100.100.100").one()
            self.assertEqual(rec.xray_status, XrayStatus.NOT_INSTALLED)
            self.assertEqual(rec.xray_version, "")
            self.assertIsNone(rec.xray_installed_at)
            self.assertIsNone(rec.xray_last_checked_at)
            self.assertEqual(rec.xray_status_message, "")

    def test_xray_status_can_be_updated(self):
        """xray 字段可以被业务层修改并提交。"""
        from datetime import datetime
        with session_scope() as s:
            s.add(VPSRecord.from_form(ip="100.100.100.101", username="root", password="x", port=22))

        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="100.100.100.101").one()
            rec.xray_status = XrayStatus.RUNNING
            rec.xray_version = "Xray 1.8.4"
            rec.xray_installed_at = datetime(2026, 1, 1, 12, 0, 0)
            rec.xray_status_message = "installed via official script"

        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="100.100.100.101").one()
            self.assertEqual(rec.xray_status, "running")
            self.assertEqual(rec.xray_version, "Xray 1.8.4")
            self.assertIsNotNone(rec.xray_installed_at)
            self.assertEqual(rec.xray_status_message, "installed via official script")

    def test_xray_status_constants(self):
        """常量类提供 6 个状态值，不允许 typo。"""
        self.assertEqual(XrayStatus.NOT_INSTALLED, "not_installed")
        self.assertEqual(XrayStatus.INSTALLING, "installing")
        self.assertEqual(XrayStatus.INSTALL_FAILED, "install_failed")
        self.assertEqual(XrayStatus.RUNNING, "running")
        self.assertEqual(XrayStatus.STOPPED, "stopped")
        self.assertEqual(XrayStatus.UNINSTALLED, "uninstalled")

    def test_repr_does_not_leak_password(self):
        record = VPSRecord.from_form(
            ip="1.2.3.4", username="root", password="SuperSecret", port=22
        )
        repr_str = repr(record)
        self.assertNotIn("SuperSecret", repr_str)
        self.assertNotIn("password", repr_str.lower())


if __name__ == "__main__":
    unittest.main()
