"""install_xray_on_vps 业务的 mock 测试，覆盖全部 status 路径。"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import Base, VPSRecord, XrayStatus, get_engine, session_scope
from services.vps_install_xray import install_xray_on_vps
from core import (
    AuthFailedError,
    ConnectTimeoutError,
    ConnectRefusedError,
    AUTH_FAILED_MESSAGE,
    CONNECT_TIMEOUT_MESSAGE,
    CONNECT_REFUSED_MESSAGE,
)
from xray import (
    InstallFailedError,
    VerifyFailedError,
    ServiceNotActiveError,
    EnableFailedError,
)


TEST_IP = "10.20.30.40"


def _seed_vps(ip: str, xray_status: str = XrayStatus.NOT_INSTALLED) -> None:
    with session_scope() as s:
        s.query(VPSRecord).filter_by(ip=ip).delete()
        record = VPSRecord.from_form(ip=ip, username="root", password="pwd", port=22)
        record.xray_status = xray_status
        s.add(record)


def _get_vps(ip: str) -> VPSRecord:
    with session_scope() as s:
        rec = s.query(VPSRecord).filter_by(ip=ip).one()
        s.expunge(rec)
        return rec


def _stub_vps_manager(mock_vps_cls, raise_exc: Exception | None = None) -> MagicMock:
    """造一个 VPSSession.from_record(...) → with 上下文。

    raise_exc 不为 None 时，进入 with 块时会抛出 raise_exc。
    """
    vps_ctx = MagicMock()
    vps_ctx.client = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(side_effect=raise_exc) if raise_exc else MagicMock(return_value=vps_ctx)
    cm.__exit__ = MagicMock(return_value=False)
    mock_vps_cls.from_record.return_value = cm
    return vps_ctx


def _stub_xray_manager(
    mock_xray_cls, ensure_return=None, ensure_raise=None,
    partial_version: str = "Xray 1.8.4 (mock)",
    partial_is_installed: bool = True,
    internal_ok: bool = True,
) -> MagicMock:
    """造一个 XrayManager(client) → 实例 → ensure_installed_and_running 的 mock。

    partial_version / partial_is_installed 用于失败路径。
    internal_ok 控制内部 ping 的 mock 返回。
    """
    xray_instance = MagicMock()
    if ensure_raise is not None:
        xray_instance.ensure_installed_and_running.side_effect = ensure_raise
    else:
        xray_instance.ensure_installed_and_running.return_value = ensure_return or {
            "version": "Xray 1.8.4",
            "was_already_installed": False,
            "actions_taken": ["installed"],
        }
    xray_instance.version.return_value = partial_version
    xray_instance.is_installed.return_value = partial_is_installed
    # 内部 ping 默认通
    xray_instance.test_internal_socks.return_value = {
        "ok": internal_ok,
        "http_code": 200 if internal_ok else None,
        "body": "1.2.3.4" if internal_ok else "",
        "error": None if internal_ok else "mock internal fail",
    }
    mock_xray_cls.return_value = xray_instance
    return xray_instance


class TestInstallXrayMocked(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = get_engine("sqlite")
        Base.metadata.create_all(cls.engine, tables=[VPSRecord.__table__])

    def setUp(self):
        with session_scope() as s:
            s.query(VPSRecord).filter_by(ip=TEST_IP).delete()

    # ---------- 前置短路（DB 视角）----------

    def test_not_registered_when_ip_missing(self):
        result = install_xray_on_vps(ip="99.99.99.99")
        self.assertEqual(result["status"], "not_registered")
        self.assertIn("rgvps", result["message"])

    def test_already_running_when_db_says_so(self):
        _seed_vps(TEST_IP, xray_status=XrayStatus.RUNNING)
        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "already_running")

    def test_in_progress_when_another_install_running(self):
        _seed_vps(TEST_IP, xray_status=XrayStatus.INSTALLING)
        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "in_progress")

    # ---------- 连接错误（沿用 register_vps 风格的四种细分）----------

    @patch("services.vps_install_xray.VPSSession")
    def test_auth_failed(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=AuthFailedError(AUTH_FAILED_MESSAGE))

        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "auth_failed")
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_install_xray.VPSSession")
    def test_timeout(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=ConnectTimeoutError(CONNECT_TIMEOUT_MESSAGE))
        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "timeout")

    @patch("services.vps_install_xray.VPSSession")
    def test_refused(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=ConnectRefusedError(CONNECT_REFUSED_MESSAGE))
        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "refused")

    @patch("services.vps_install_xray.VPSSession")
    def test_unknown_connection_error(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=ConnectionError("misc"))
        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "failed")

    # ---------- 全新安装路径 ----------

    @patch("services.vps_install_xray.test_socks_proxy")
    @patch("services.vps_install_xray.open_tcp_port_range")
    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_ok_fresh_install(self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_return={
            "version": "Xray 1.8.4",
            "was_already_installed": False,
            "actions_taken": ["installed", "enabled_autostart"],
        })
        mock_fw.return_value = "firewalld"
        mock_ext.return_value = {"ok": True, "status_code": 200, "body": "1.2.3.4", "error": None}

        result = install_xray_on_vps(ip=TEST_IP)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], "Xray 1.8.4")
        self.assertIn("installed", result["actions"])
        self.assertTrue(result["internal_ping"]["ok"])
        self.assertTrue(result["external_ping"]["ok"])

        rec = _get_vps(TEST_IP)
        self.assertEqual(rec.xray_status, XrayStatus.RUNNING)
        self.assertEqual(rec.xray_version, "Xray 1.8.4")
        self.assertIsNotNone(rec.xray_installed_at)

    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_install_failed(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=InstallFailedError("xray 安装失败: exit=1"))

        result = install_xray_on_vps(ip=TEST_IP)

        self.assertEqual(result["status"], "install_failed")
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_verify_failed(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=VerifyFailedError("无版本号"))

        result = install_xray_on_vps(ip=TEST_IP)

        self.assertEqual(result["status"], "verify_failed")
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_service_not_active(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=ServiceNotActiveError("start 后仍未 active"))

        result = install_xray_on_vps(ip=TEST_IP)

        self.assertEqual(result["status"], "service_not_active")

    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_enable_failed(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=EnableFailedError("enable 失败"))

        result = install_xray_on_vps(ip=TEST_IP)

        self.assertEqual(result["status"], "enable_failed")

    # ---------- 已装路径（imported）----------

    @patch("services.vps_install_xray.test_socks_proxy")
    @patch("services.vps_install_xray.open_tcp_port_range")
    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_imported_when_server_already_has_xray(
        self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext,
    ):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_return={
            "version": "Xray 1.8.4",
            "was_already_installed": True,
            "actions_taken": [],
        })
        mock_fw.return_value = "firewalld"
        mock_ext.return_value = {"ok": True, "status_code": 200, "body": "1.2.3.4", "error": None}

        result = install_xray_on_vps(ip=TEST_IP)

        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["version"], "Xray 1.8.4")
        self.assertTrue(result["external_ping"]["ok"])

        rec = _get_vps(TEST_IP)
        self.assertEqual(rec.xray_status, XrayStatus.RUNNING)
        self.assertIn("纳管", rec.xray_status_message)

    # ---------- 连通性检查的新分支 ----------

    @patch("services.vps_install_xray.test_socks_proxy")
    @patch("services.vps_install_xray.open_tcp_port_range")
    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_internal_check_failed(self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext):
        """xray 服务在跑但 socks5 不响应（内部 ping 失败）。"""
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, internal_ok=False)
        mock_fw.return_value = "firewalld"

        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "internal_check_failed")
        # 内部失败后不应该再调外部
        mock_ext.assert_not_called()
        # DB 标记 install_failed
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_install_xray.test_socks_proxy")
    @patch("services.vps_install_xray.open_tcp_port_range")
    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_external_unreachable_when_security_group_closed(
        self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext,
    ):
        """内部通但外部不通（云安全组未放行）。"""
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls)
        mock_fw.return_value = "firewalld"
        mock_ext.return_value = {
            "ok": False, "status_code": None, "body": "",
            "error": "ConnectionError: connection timeout",
        }

        result = install_xray_on_vps(ip=TEST_IP)
        self.assertEqual(result["status"], "external_unreachable")
        self.assertIn("安全策略组", result["message"])
        self.assertTrue(result["internal_ping"]["ok"])
        self.assertFalse(result["external_ping"]["ok"])
        # DB 仍标 running（VPS 内部 OK）
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.RUNNING)

    @patch("services.vps_install_xray.test_socks_proxy")
    @patch("services.vps_install_xray.open_tcp_port_range")
    @patch("services.vps_install_xray.XrayManager")
    @patch("services.vps_install_xray.VPSSession")
    def test_firewall_failure_does_not_block(
        self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext,
    ):
        """防火墙开放失败应该只警告，不阻塞后续流程。"""
        from core import FirewallOpenError
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls)
        mock_fw.side_effect = FirewallOpenError("没权限")
        mock_ext.return_value = {"ok": True, "status_code": 200, "body": "1.2.3.4", "error": None}

        result = install_xray_on_vps(ip=TEST_IP)
        # 防火墙失败 ≠ 业务失败
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
