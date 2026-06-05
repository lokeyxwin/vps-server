"""init_vps_xray 业务的 mock 测试，覆盖全部 status 路径。"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import Base, ProxyRecord, ProxyStatus, VPSRecord, XrayStatus, get_engine, session_scope
from services.vps_init import init_vps_xray
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
    默认 vps.get_available_ports 返回完整业务范围（10 个端口），无被占用。
    """
    vps_ctx = MagicMock()
    vps_ctx.client = MagicMock()
    vps_ctx.get_available_ports.return_value = set(range(18441, 18451))
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
    # 默认场景：没有现存 binding（首次安装 / 默认 config）
    xray_instance.import_existing_bindings.return_value = []
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
        Base.metadata.create_all(
            cls.engine, tables=[VPSRecord.__table__, ProxyRecord.__table__]
        )

    def setUp(self):
        with session_scope() as s:
            # ProxyRecord 有 FK 指向 VPSRecord，先删 proxy 再删 vps
            s.query(ProxyRecord).delete()
            s.query(VPSRecord).filter_by(ip=TEST_IP).delete()

    # ---------- 前置短路（DB 视角）----------

    def test_not_registered_when_ip_missing(self):
        result = init_vps_xray(ip="99.99.99.99")
        self.assertEqual(result["status"], "not_registered")
        self.assertIn("rgvps", result["message"])

    def test_already_running_when_db_says_so(self):
        _seed_vps(TEST_IP, xray_status=XrayStatus.RUNNING)
        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "already_running")

    def test_in_progress_when_another_install_running(self):
        _seed_vps(TEST_IP, xray_status=XrayStatus.INSTALLING)
        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "in_progress")

    # ---------- 连接错误（沿用 register_vps 风格的四种细分）----------

    @patch("services.vps_init.VPSSession")
    def test_auth_failed(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=AuthFailedError(AUTH_FAILED_MESSAGE))

        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "auth_failed")
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_init.VPSSession")
    def test_timeout(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=ConnectTimeoutError(CONNECT_TIMEOUT_MESSAGE))
        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "timeout")

    @patch("services.vps_init.VPSSession")
    def test_refused(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=ConnectRefusedError(CONNECT_REFUSED_MESSAGE))
        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "refused")

    @patch("services.vps_init.VPSSession")
    def test_unknown_connection_error(self, mock_vps_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls, raise_exc=ConnectionError("misc"))
        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "failed")

    # ---------- 全新安装路径 ----------

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
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

        result = init_vps_xray(ip=TEST_IP)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], "Xray 1.8.4")
        self.assertIn("installed", result["actions"])
        self.assertTrue(result["internal_ping"]["ok"])
        self.assertTrue(result["external_ping"]["ok"])

        rec = _get_vps(TEST_IP)
        self.assertEqual(rec.xray_status, XrayStatus.RUNNING)
        self.assertEqual(rec.xray_version, "Xray 1.8.4")
        self.assertIsNotNone(rec.xray_installed_at)

    # ---------- 端口审计（新增）----------

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_port_audit_clean_vps_full_range_available(
        self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext,
    ):
        """没有任何已部署 binding 的情况：可用端口数 = 区间长度（10 个）。"""
        _seed_vps(TEST_IP)
        vps_ctx = _stub_vps_manager(mock_vps_cls)
        vps_ctx.get_available_ports.return_value = set(range(18441, 18451))
        xm = _stub_xray_manager(mock_xray_cls)
        xm.import_existing_bindings.return_value = []
        mock_fw.return_value = "firewalld"
        mock_ext.return_value = {"ok": True, "status_code": 200, "body": "1.2.3.4", "error": None}

        result = init_vps_xray(ip=TEST_IP)

        self.assertEqual(result["status"], "ok")
        audit = result["port_audit"]
        self.assertEqual(audit["available_count"], 10)
        self.assertEqual(audit["available_ports"], list(range(18441, 18451)))
        self.assertEqual(audit["existing_bindings"], [])

        # 验证编排：审计两步真的调了
        xm.import_existing_bindings.assert_called_once()
        vps_ctx.get_available_ports.assert_called_once_with(18441, 18450)

        # 验证 idle_port_count 真落库（核心断言）
        self.assertEqual(_get_vps(TEST_IP).idle_port_count, 10)

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_port_audit_with_existing_bindings_subtracts_them(
        self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext,
    ):
        """已部署 2 条 binding 占用 18443/18445，可用集合应该排除掉它们。"""
        _seed_vps(TEST_IP)
        vps_ctx = _stub_vps_manager(mock_vps_cls)
        # 假设 ss -tln 已经把 18443/18445 算占了（mock 这种最常见的情况），
        # vps.get_available_ports 默认已扣 OS 占用，返回不含 18443/18445 的集合
        vps_ctx.get_available_ports.return_value = (
            set(range(18441, 18451)) - {18443, 18445}
        )
        xm = _stub_xray_manager(mock_xray_cls)
        xm.import_existing_bindings.return_value = [
            {"port": 18443, "egress_ip": "1.1.1.1", "egress_country": "JP",
             "protocol": "socks5", "listen_address": "0.0.0.0",
             "inbound_user": "u1", "inbound_pwd": "p1", "upstream_host": "a.com"},
            {"port": 18445, "egress_ip": "2.2.2.2", "egress_country": "DE",
             "protocol": "http", "listen_address": "0.0.0.0",
             "inbound_user": "u2", "inbound_pwd": "p2", "upstream_host": "b.com"},
        ]
        mock_fw.return_value = "ufw"
        mock_ext.return_value = {"ok": True, "status_code": 200, "body": "1.2.3.4", "error": None}

        result = init_vps_xray(ip=TEST_IP)

        audit = result["port_audit"]
        self.assertEqual(audit["available_count"], 8)
        self.assertNotIn(18443, audit["available_ports"])
        self.assertNotIn(18445, audit["available_ports"])
        self.assertEqual(len(audit["existing_bindings"]), 2)
        # binding 内容透传完整
        self.assertEqual(audit["existing_bindings"][0]["egress_country"], "JP")

        # idle_port_count 真落库
        self.assertEqual(_get_vps(TEST_IP).idle_port_count, 8)

        # 两条 binding 应该真落进 proxy_record 表
        with session_scope() as s:
            rows = s.query(ProxyRecord).order_by(ProxyRecord.vps_port).all()
            self.assertEqual(len(rows), 2)
            # 18443 → JP
            self.assertEqual(rows[0].vps_port, 18443)
            self.assertEqual(rows[0].egress_ip, "1.1.1.1")
            self.assertEqual(rows[0].egress_country, "JP")
            self.assertEqual(rows[0].protocol, "socks5")
            self.assertEqual(rows[0].inbound_user, "u1")
            self.assertEqual(rows[0].get_inbound_pwd(), "p1")  # 解密回明文
            self.assertEqual(rows[0].status, ProxyStatus.USING)
            # 18445 → DE
            self.assertEqual(rows[1].vps_port, 18445)
            self.assertEqual(rows[1].egress_country, "DE")
            self.assertEqual(rows[1].protocol, "http")

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_proxy_upsert_idempotent_on_reinit(
        self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext,
    ):
        """重装场景：第一次抄录后再跑一次，应该是 UPDATE 而不是 INSERT 撞唯一键。"""
        _seed_vps(TEST_IP)
        vps_ctx = _stub_vps_manager(mock_vps_cls)
        vps_ctx.get_available_ports.return_value = (
            set(range(18441, 18451)) - {18443}
        )
        xm = _stub_xray_manager(mock_xray_cls)
        xm.import_existing_bindings.return_value = [
            {"port": 18443, "egress_ip": "9.9.9.9", "egress_country": "FR",
             "protocol": "socks5", "listen_address": "0.0.0.0",
             "inbound_user": "round1_user", "inbound_pwd": "round1_pwd",
             "upstream_host": "round1.example.com"},
        ]
        mock_fw.return_value = "none"
        mock_ext.return_value = {"ok": True, "status_code": 200, "body": "X", "error": None}

        # 第一次跑：建表里没有
        init_vps_xray(ip=TEST_IP)
        with session_scope() as s:
            self.assertEqual(s.query(ProxyRecord).count(), 1)

        # 把 DB 里的 VPS 改回 NOT_INSTALLED 让流程能再跑（业务有 RUNNING 短路）
        with session_scope() as s:
            s.query(VPSRecord).filter_by(ip=TEST_IP).update({
                VPSRecord.xray_status: XrayStatus.NOT_INSTALLED,
            })

        # 第二次跑：相同的 (vps_id, vps_port)，应该 UPDATE 而不是抛唯一键冲突
        # 改一下 binding 内容确认 UPDATE 真生效
        xm.import_existing_bindings.return_value = [
            {"port": 18443, "egress_ip": "8.8.8.8", "egress_country": "US",
             "protocol": "http", "listen_address": "0.0.0.0",
             "inbound_user": "round2_user", "inbound_pwd": "round2_pwd",
             "upstream_host": "round2.example.com"},
        ]
        init_vps_xray(ip=TEST_IP)

        with session_scope() as s:
            rows = s.query(ProxyRecord).all()
            self.assertEqual(len(rows), 1)  # 仍然只有 1 行，没新插
            rec = rows[0]
            self.assertEqual(rec.egress_ip, "8.8.8.8")  # 字段被刷新
            self.assertEqual(rec.egress_country, "US")
            self.assertEqual(rec.protocol, "http")
            self.assertEqual(rec.inbound_user, "round2_user")
            self.assertEqual(rec.get_inbound_pwd(), "round2_pwd")
            self.assertEqual(rec.upstream_host, "round2.example.com")

    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_install_failed(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=InstallFailedError("xray 安装失败: exit=1"))

        result = init_vps_xray(ip=TEST_IP)

        self.assertEqual(result["status"], "install_failed")
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_verify_failed(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=VerifyFailedError("无版本号"))

        result = init_vps_xray(ip=TEST_IP)

        self.assertEqual(result["status"], "verify_failed")
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_service_not_active(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=ServiceNotActiveError("start 后仍未 active"))

        result = init_vps_xray(ip=TEST_IP)

        self.assertEqual(result["status"], "service_not_active")

    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_enable_failed(self, mock_vps_cls, mock_xray_cls):
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, ensure_raise=EnableFailedError("enable 失败"))

        result = init_vps_xray(ip=TEST_IP)

        self.assertEqual(result["status"], "enable_failed")

    # ---------- 已装路径（imported）----------

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
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

        result = init_vps_xray(ip=TEST_IP)

        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["version"], "Xray 1.8.4")
        self.assertTrue(result["external_ping"]["ok"])

        rec = _get_vps(TEST_IP)
        self.assertEqual(rec.xray_status, XrayStatus.RUNNING)
        self.assertIn("纳管", rec.xray_status_message)

    # ---------- 连通性检查的新分支 ----------

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
    def test_internal_check_failed(self, mock_vps_cls, mock_xray_cls, mock_fw, mock_ext):
        """xray 服务在跑但 socks5 不响应（内部 ping 失败）。"""
        _seed_vps(TEST_IP)
        _stub_vps_manager(mock_vps_cls)
        _stub_xray_manager(mock_xray_cls, internal_ok=False)
        mock_fw.return_value = "firewalld"

        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "internal_check_failed")
        # 内部失败后不应该再调外部
        mock_ext.assert_not_called()
        # DB 标记 install_failed
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.INSTALL_FAILED)

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
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

        result = init_vps_xray(ip=TEST_IP)
        self.assertEqual(result["status"], "external_unreachable")
        self.assertIn("安全策略组", result["message"])
        self.assertTrue(result["internal_ping"]["ok"])
        self.assertFalse(result["external_ping"]["ok"])
        # DB 仍标 running（VPS 内部 OK）
        self.assertEqual(_get_vps(TEST_IP).xray_status, XrayStatus.RUNNING)

    @patch("services.vps_init.test_socks_proxy")
    @patch("services.vps_init.open_tcp_port_range")
    @patch("services.vps_init.XrayManager")
    @patch("services.vps_init.VPSSession")
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

        result = init_vps_xray(ip=TEST_IP)
        # 防火墙失败 ≠ 业务失败
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
