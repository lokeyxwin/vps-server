"""register_vps 业务（包含 xray 全流程）的测试。"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import Base, VPSRecord, XrayStatus, get_engine, session_scope
from services.vps_register import register_vps
from ssh.ops import (
    AuthFailedError,
    ConnectTimeoutError,
    ConnectRefusedError,
    AUTH_FAILED_MESSAGE,
    CONNECT_TIMEOUT_MESSAGE,
    CONNECT_REFUSED_MESSAGE,
)


class TestRegisterVpsMocked(unittest.TestCase):
    """Mock SSH + xray，验证业务编排所有路径。"""

    @classmethod
    def setUpClass(cls):
        cls.engine = get_engine("sqlite")
        Base.metadata.create_all(cls.engine, tables=[VPSRecord.__table__])

    def setUp(self):
        with session_scope() as s:
            s.query(VPSRecord).delete()

    # ---------- 辅助 ----------

    def _stub_vps_with_info(self, mock_vps_cls, info=None):
        """register_vps 业务里的 VPSSession 用法：with 上下文 + get_system_info。"""
        info = info or {"username": "root", "os_name": "Ubuntu", "os_version": "22.04"}
        vps_ctx = MagicMock()
        vps_ctx.get_system_info.return_value = info
        cm = MagicMock()
        cm.__enter__.return_value = vps_ctx
        cm.__exit__.return_value = False
        mock_vps_cls.return_value = cm
        return vps_ctx

    def _stub_install_xray_result(self, mock_install, status="ok", **kwargs):
        result = {"status": status, "ip": kwargs.get("ip", "1.1.1.1"), **kwargs}
        mock_install.return_value = result
        return result

    # ---------- 三个失败路径（SSH 连接错）----------

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_auth_failure_returns_auth_failed(self, mock_vps_cls, mock_install):
        mock_vps_cls.return_value.__enter__.side_effect = AuthFailedError(AUTH_FAILED_MESSAGE)

        result = register_vps(ip="3.3.3.3", username="root", password="wrong", port=22)
        self.assertEqual(result["status"], "auth_failed")
        # 关键：连接失败不应该写 DB、不应该调 install_xray
        with session_scope() as s:
            self.assertEqual(s.query(VPSRecord).filter_by(ip="3.3.3.3").count(), 0)
        mock_install.assert_not_called()

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_timeout(self, mock_vps_cls, mock_install):
        mock_vps_cls.return_value.__enter__.side_effect = ConnectTimeoutError(CONNECT_TIMEOUT_MESSAGE)
        result = register_vps(ip="3.3.3.4", username="root", password="x", port=9999)
        self.assertEqual(result["status"], "timeout")
        mock_install.assert_not_called()

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_refused(self, mock_vps_cls, mock_install):
        mock_vps_cls.return_value.__enter__.side_effect = ConnectRefusedError(CONNECT_REFUSED_MESSAGE)
        result = register_vps(ip="3.3.3.5", username="root", password="x", port=22)
        self.assertEqual(result["status"], "refused")

    # ---------- 重复入库 ----------

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_duplicate_returns_duplicate_and_skips_ssh(self, mock_vps_cls, mock_install):
        self._stub_vps_with_info(mock_vps_cls)
        self._stub_install_xray_result(mock_install)
        register_vps(ip="2.2.2.2", username="root", password="x", port=22)

        # 重置 mock，确认第二次完全不调
        mock_vps_cls.reset_mock()
        mock_install.reset_mock()

        result = register_vps(ip="2.2.2.2", username="root", password="x", port=22)
        self.assertEqual(result["status"], "duplicate")
        mock_vps_cls.assert_not_called()
        mock_install.assert_not_called()

    # ---------- 成功路径 ----------

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_success_full_flow(self, mock_vps_cls, mock_install):
        self._stub_vps_with_info(mock_vps_cls)
        self._stub_install_xray_result(
            mock_install, status="ok", ip="1.1.1.1", version="Xray 1.8.4",
        )

        result = register_vps(
            ip="1.1.1.1",
            username="root",
            password="MyPwd@123",
            port=22,
            expire_date=date(2026, 12, 31),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ip"], "1.1.1.1")
        self.assertIn("Ubuntu", result["os"])
        self.assertEqual(result["xray"]["status"], "ok")

        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="1.1.1.1").one()
            self.assertEqual(rec.username, "root")
            self.assertEqual(rec.os_name, "Ubuntu")
            self.assertEqual(rec.expire_date, date(2026, 12, 31))
            self.assertEqual(rec.get_password(), "MyPwd@123")

        mock_install.assert_called_once_with("1.1.1.1")

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_provider_domain_persisted_through_register(self, mock_vps_cls, mock_install):
        """provider_domain 参数应一路透传到 DB，验证不被业务层丢弃。"""
        self._stub_vps_with_info(mock_vps_cls)
        self._stub_install_xray_result(mock_install, status="ok", ip="1.1.1.5")

        result = register_vps(
            ip="1.1.1.5",
            username="root",
            password="x",
            port=22,
            provider_domain="linode.com",
        )

        self.assertEqual(result["status"], "ok")
        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="1.1.1.5").one()
            self.assertEqual(rec.provider_domain, "linode.com")

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_provider_domain_omitted_falls_back_to_empty(self, mock_vps_cls, mock_install):
        """未传 provider_domain 时 DB 里应为空串，不污染既有用法。"""
        self._stub_vps_with_info(mock_vps_cls)
        self._stub_install_xray_result(mock_install, status="ok", ip="1.1.1.6")

        register_vps(ip="1.1.1.6", username="root", password="x", port=22)

        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip="1.1.1.6").one()
            self.assertEqual(rec.provider_domain, "")

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_imported_xray_path_also_returns_ok(self, mock_vps_cls, mock_install):
        """xray 在服务器上已装的场景，业务整体仍是 ok。"""
        self._stub_vps_with_info(mock_vps_cls)
        self._stub_install_xray_result(mock_install, status="imported", version="Xray 1.8.4")

        result = register_vps(ip="1.1.1.2", username="root", password="x", port=22)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["xray"]["status"], "imported")

    @patch("services.vps_register.init_vps_xray")
    @patch("services.vps_register.VPSSession")
    def test_xray_partial_failure(self, mock_vps_cls, mock_install):
        """VPS 注册成功但 xray 流程失败 → status=ok_xray_partial。"""
        self._stub_vps_with_info(mock_vps_cls)
        self._stub_install_xray_result(mock_install, status="install_failed", message="xray 装挂了")

        result = register_vps(ip="1.1.1.3", username="root", password="x", port=22)

        self.assertEqual(result["status"], "ok_xray_partial")
        self.assertEqual(result["xray"]["status"], "install_failed")
        # VPS 已入库
        with session_scope() as s:
            self.assertEqual(s.query(VPSRecord).filter_by(ip="1.1.1.3").count(), 1)


if __name__ == "__main__":
    unittest.main()
