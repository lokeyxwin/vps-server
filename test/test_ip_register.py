"""services.ip_register.register_ip 业务测试。

策略：mock 整个 SSH 链路 + 第三方 atom。覆盖 8 种业务 status 路径 + 4 种 SSH 连接错 +
DB 副作用验证（哪些 status 落库、哪些不落库、3 次重试 + 回滚兜底）。

测试用例覆盖：
- already_exists（IP 已存在）
- no_available_vps（找不到 VPS）
- no_available_port（真闲端口集为空 / apply 报 PortAlreadyBound）
- ok（全链路通）
- ok_security_group_blocked（内通外 ping 两次都不通）
- egress_mismatch（内 ping body ≠ egress_ip）+ 回滚验证
- failed: 内 ping 不通 + 回滚验证
- failed: 协议不支持
- failed: 写库 3 次重试全失败 + 回滚验证
- SSH 连接错：auth_failed / timeout / refused / 未知 ConnectionError
- outbound tag 含 country_code + egress_ip
- geoip 调用上移到 SSH 之前
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import (
    Base,
    IPRecord,
    IPProtocol,
    ProxyRecord,
    VPSRecord,
    XrayStatus,
    get_engine,
    session_scope,
)
from services.ip_register import register_ip


# ============================================================
# 公共工具：装两台 VPS / 装一条 IP 等
# ============================================================

def _seed_vps(
    ip: str = "10.0.0.1",
    idle_port_count: int = 5,
    xray_status: str = XrayStatus.RUNNING,
    is_active: int = 1,
) -> int:
    with session_scope() as s:
        rec = VPSRecord.from_form(
            ip=ip, username="root", password="rootpwd", port=22,
        )
        rec.xray_status = xray_status
        rec.idle_port_count = idle_port_count
        rec.is_active = is_active
        s.add(rec)
        s.flush()
        return rec.id


def _seed_ip(egress_ip: str = "1.2.3.4") -> int:
    with session_scope() as s:
        rec = IPRecord.from_form(
            entry_host="proxy.x.com", entry_port=1080,
            username="u", password="p",
            protocol=IPProtocol.SOCKS5, egress_ip=egress_ip,
        )
        s.add(rec)
        s.flush()
        return rec.id


def _common_kwargs(**overrides) -> dict:
    base = dict(
        entry_host="proxy.example.com",
        entry_port=1080,
        username="upstream_user",
        password="upstream_pwd",
        protocol="socks5",
        egress_ip="9.9.9.9",
        provider_domain="example.com",
        expire_date=date(2027, 1, 1),
    )
    base.update(overrides)
    return base


class _RegisterIPBase(unittest.TestCase):
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
        with session_scope() as s:
            s.query(ProxyRecord).delete()
            s.query(IPRecord).delete()
            s.query(VPSRecord).delete()


# ============================================================
# 入参校验
# ============================================================

class TestInputValidation(_RegisterIPBase):
    def test_unsupported_protocol_returns_failed(self):
        result = register_ip(**_common_kwargs(protocol="vmess"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("vmess", result["message"])


# ============================================================
# already_exists（短路 - 不动 SSH）
# ============================================================

class TestAlreadyExists(_RegisterIPBase):
    @patch("services.ip_register.VPSSession")
    def test_already_exists_returns_short_circuit(self, mock_vps_cls):
        _seed_ip(egress_ip="9.9.9.9")
        result = register_ip(**_common_kwargs(egress_ip="9.9.9.9"))
        self.assertEqual(result["status"], "already_exists")
        self.assertIn("existing", result)
        self.assertEqual(result["existing"]["egress_ip"], "9.9.9.9")
        # 不动 SSH
        mock_vps_cls.from_record.assert_not_called()

    @patch("services.ip_register.VPSSession")
    def test_already_exists_does_not_write_proxy(self, mock_vps_cls):
        _seed_ip(egress_ip="9.9.9.9")
        register_ip(**_common_kwargs(egress_ip="9.9.9.9"))
        with session_scope() as s:
            self.assertEqual(s.query(ProxyRecord).count(), 0)


# ============================================================
# no_available_vps
# ============================================================

class TestNoAvailableVPS(_RegisterIPBase):
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_no_vps_at_all(self, mock_geo, mock_vps_cls):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "no_available_vps")
        mock_vps_cls.from_record.assert_not_called()

    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_vps_with_zero_idle_not_picked(self, mock_geo, mock_vps_cls):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        _seed_vps(ip="10.0.0.1", idle_port_count=0)
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "no_available_vps")

    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_inactive_vps_not_picked(self, mock_geo, mock_vps_cls):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        _seed_vps(ip="10.0.0.1", idle_port_count=5, is_active=0)
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "no_available_vps")

    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_not_running_vps_not_picked(self, mock_geo, mock_vps_cls):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        _seed_vps(ip="10.0.0.1", idle_port_count=5, xray_status=XrayStatus.STOPPED)
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "no_available_vps")


# ============================================================
# no_available_port（proxy_record 已占满 / apply 撞）
# ============================================================

class TestNoAvailablePort(_RegisterIPBase):
    def _fill_all_ports(self, vps_id: int) -> None:
        """填满 18441-18450 共 10 个端口的 proxy_record。"""
        for port in range(18441, 18451):
            with session_scope() as s:
                # 给每条 proxy 造一个独立 IP（egress_ip 必须不同）
                ip_rec = IPRecord.from_form(
                    entry_host="x", entry_port=1080,
                    username="u", password="p",
                    protocol=IPProtocol.SOCKS5,
                    egress_ip=f"1.1.1.{port}",  # 不冲突
                )
                s.add(ip_rec); s.flush()
                pr = ProxyRecord.from_new_deployment(
                    vps_id=vps_id, vps_port=port, ip_id=ip_rec.id,
                    inbound_user="u", inbound_pwd="p",
                    upstream_host="x", egress_ip=f"1.1.1.{port}",
                )
                s.add(pr)

    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_all_10_ports_used_returns_no_port(
        self, mock_geo, mock_vps_cls, mock_xm_cls,
    ):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        vps_id = _seed_vps(idle_port_count=10)
        self._fill_all_ports(vps_id)

        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps

        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "no_available_port")

    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_apply_raises_port_already_bound(
        self, mock_geo, mock_vps_cls, mock_xm_cls,
    ):
        from xray import PortAlreadyBoundError
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        _seed_vps(idle_port_count=5)

        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps

        mock_xm = MagicMock()
        mock_xm.apply_proxy_binding.side_effect = PortAlreadyBoundError("port used")
        mock_xm_cls.return_value = mock_xm

        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "no_available_port")


# ============================================================
# 大场景：ok / ok_security_group_blocked / egress_mismatch / failed
# 用一个 fixture base 节省样板
# ============================================================

class TestDeploymentScenarios(_RegisterIPBase):
    """每个 case 都装好 VPS + mock 整个 SSH/xray/test_socks_proxy 链路。"""

    def setUp(self):
        super().setUp()
        self.vps_id = _seed_vps(ip="10.0.0.1", idle_port_count=5)

    def _make_xm(
        self,
        internal_body: str = "9.9.9.9",
        internal_ok: bool = True,
    ) -> MagicMock:
        xm = MagicMock()
        xm.apply_proxy_binding.return_value = {"inbounds": [{"tag": "client-18441"}]}
        xm.test_internal_socks.return_value = {
            "ok": internal_ok, "http_code": 200 if internal_ok else 0,
            "body": internal_body, "error": None,
        }
        return xm

    @patch("services.ip_register.test_socks_proxy")
    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_ok_full_happy_path(
        self, mock_geo, mock_vps_cls, mock_xm_cls, mock_ext,
    ):
        mock_geo.return_value = {
            "country_code": "US", "country_name": "United States",
            "city": "Los Angeles", "region_name": "California", "raw": {},
        }
        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps

        xm = self._make_xm(internal_body="9.9.9.9", internal_ok=True)
        mock_xm_cls.return_value = xm

        mock_ext.return_value = {
            "ok": True, "status_code": 200, "body": "9.9.9.9", "error": None,
        }

        result = register_ip(**_common_kwargs(egress_ip="9.9.9.9"))

        self.assertEqual(result["status"], "ok")
        # 节点信息
        self.assertEqual(result["node"]["host"], "10.0.0.1")
        self.assertEqual(result["node"]["port"], 18441)
        self.assertEqual(result["node"]["protocol"], "socks5")
        self.assertEqual(result["node"]["country_code"], "US")
        self.assertEqual(result["node"]["city"], "Los Angeles")
        # 入库副作用
        with session_scope() as s:
            self.assertEqual(s.query(IPRecord).count(), 1)
            self.assertEqual(s.query(ProxyRecord).count(), 1)
            vps = s.query(VPSRecord).filter_by(id=self.vps_id).one()
            self.assertEqual(vps.idle_port_count, 4)  # 原 5 - 1
        # outbound tag 含 country + egress_ip
        applied_call = xm.apply_proxy_binding.call_args
        proxy_outbound = applied_call.kwargs["proxy_outbound"]
        # tag 含 country + egress + port，跨部署唯一
        self.assertEqual(proxy_outbound["tag"], "proxy-US-9.9.9.9-18441")

    @patch("services.ip_register.open_tcp_port_range")
    @patch("services.ip_register.test_socks_proxy")
    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_ok_security_group_blocked_when_external_fails_twice(
        self, mock_geo, mock_vps_cls, mock_xm_cls, mock_ext, mock_fw,
    ):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps
        mock_xm_cls.return_value = self._make_xm(internal_body="9.9.9.9", internal_ok=True)
        # 两次都失败
        mock_ext.return_value = {
            "ok": False, "status_code": None, "body": "", "error": "timeout",
        }

        result = register_ip(**_common_kwargs(egress_ip="9.9.9.9"))

        self.assertEqual(result["status"], "ok_security_group_blocked")
        # 仍然入库
        with session_scope() as s:
            self.assertEqual(s.query(IPRecord).count(), 1)
            self.assertEqual(s.query(ProxyRecord).count(), 1)
        # 防火墙开过
        mock_fw.assert_called_once()
        # 外 ping 调用了 2 次（开防火墙前后各一次）
        self.assertEqual(mock_ext.call_count, 2)

    @patch("services.ip_register.test_socks_proxy")
    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_egress_mismatch_rolls_back_and_no_db_write(
        self, mock_geo, mock_vps_cls, mock_xm_cls, mock_ext,
    ):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps
        # 内 ping 通但 body ≠ egress_ip
        xm = self._make_xm(internal_body="DIFFERENT_IP", internal_ok=True)
        mock_xm_cls.return_value = xm

        result = register_ip(**_common_kwargs(egress_ip="9.9.9.9"))

        self.assertEqual(result["status"], "egress_mismatch")
        # 回滚被调
        xm.rollback_proxy_binding.assert_called_once()
        # 不入库
        with session_scope() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)
            self.assertEqual(s.query(ProxyRecord).count(), 0)
        # 外 ping 不会跑
        mock_ext.assert_not_called()

    @patch("services.ip_register.test_socks_proxy")
    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_internal_ping_fail_rolls_back_and_returns_failed(
        self, mock_geo, mock_vps_cls, mock_xm_cls, mock_ext,
    ):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps
        xm = self._make_xm(internal_body="", internal_ok=False)
        mock_xm_cls.return_value = xm

        result = register_ip(**_common_kwargs())

        self.assertEqual(result["status"], "failed")
        xm.rollback_proxy_binding.assert_called_once()
        with session_scope() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)
            self.assertEqual(s.query(ProxyRecord).count(), 0)


# ============================================================
# SSH 连接错
# ============================================================

class TestSSHConnectionErrors(_RegisterIPBase):
    def setUp(self):
        super().setUp()
        _seed_vps(idle_port_count=5)

    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_auth_failed(self, mock_geo, mock_vps_cls):
        from ssh.ops import AuthFailedError
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps_cls.from_record.side_effect = AuthFailedError("auth")
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "auth_failed")

    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_timeout(self, mock_geo, mock_vps_cls):
        from ssh.ops import ConnectTimeoutError
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps_cls.from_record.side_effect = ConnectTimeoutError("timeout")
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "timeout")

    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_refused(self, mock_geo, mock_vps_cls):
        from ssh.ops import ConnectRefusedError
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps_cls.from_record.side_effect = ConnectRefusedError("refused")
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "refused")

    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_unknown_connection_error(self, mock_geo, mock_vps_cls):
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps_cls.from_record.side_effect = ConnectionError("unknown")
        result = register_ip(**_common_kwargs())
        self.assertEqual(result["status"], "failed")


# ============================================================
# 写库 3 次重试 + 失败回滚
# ============================================================

class TestPersistRetryAndRollback(_RegisterIPBase):
    def setUp(self):
        super().setUp()
        self.vps_id = _seed_vps(idle_port_count=5)

    @patch("services.ip_register._persist_deployment")
    @patch("services.ip_register.test_socks_proxy")
    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_persist_retries_3_times_then_rolls_back(
        self, mock_geo, mock_vps_cls, mock_xm_cls, mock_ext, mock_persist,
    ):
        """写库 3 次都失败 → 回滚 xray + 返回 failed。"""
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        # SSH session mock
        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps

        # XrayManager mock：apply 成功 + 内 ping 通且 egress 匹配
        xm = MagicMock()
        xm.apply_proxy_binding.return_value = {"inbounds": [{"tag": "client-18441"}]}
        xm.test_internal_socks.return_value = {
            "ok": True, "http_code": 200, "body": "9.9.9.9", "error": None,
        }
        mock_xm_cls.return_value = xm

        # 写库 3 次都抛
        mock_persist.side_effect = RuntimeError("DB locked")

        result = register_ip(**_common_kwargs(egress_ip="9.9.9.9"))

        # 重试了 3 次
        self.assertEqual(mock_persist.call_count, 3)
        # 返回 failed
        self.assertEqual(result["status"], "failed")
        self.assertIn("DB", result["message"])
        # 回滚被调：mock_xm_cls.return_value 是所有 XrayManager(...) 实例的共用 mock，
        # 回滚发生在 _rollback_via_new_session 内开新 SSH 后又 new 一次 XrayManager
        xm.rollback_proxy_binding.assert_called_once()
        # mock_xm_cls 被实例化至少 2 次（apply 用一次 + 回滚兜底用一次）
        self.assertGreaterEqual(mock_xm_cls.call_count, 2)
        # 不入库
        with session_scope() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)

    @patch("services.ip_register._persist_deployment")
    @patch("services.ip_register.test_socks_proxy")
    @patch("services.ip_register.XrayManager")
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_persist_second_attempt_succeeds(
        self, mock_geo, mock_vps_cls, mock_xm_cls, mock_ext, mock_persist,
    ):
        """第一次失败、第二次成功 → 业务正常继续。"""
        mock_geo.return_value = {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}
        mock_vps = MagicMock()
        mock_vps_cls.from_record.return_value.__enter__.return_value = mock_vps

        xm = MagicMock()
        xm.apply_proxy_binding.return_value = {"inbounds": [{"tag": "client-18441"}]}
        xm.test_internal_socks.return_value = {
            "ok": True, "http_code": 200, "body": "9.9.9.9", "error": None,
        }
        mock_xm_cls.return_value = xm

        # 第一次抛、第二次成功（返回 (1, 1)）
        mock_persist.side_effect = [RuntimeError("transient"), (1, 1)]
        mock_ext.return_value = {
            "ok": True, "status_code": 200, "body": "9.9.9.9", "error": None,
        }

        result = register_ip(**_common_kwargs(egress_ip="9.9.9.9"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(mock_persist.call_count, 2)
        # 不回滚
        xm.rollback_proxy_binding.assert_not_called()


# ============================================================
# geoip 上移验证
# ============================================================

class TestGeoIPCallOrder(_RegisterIPBase):
    @patch("services.ip_register.VPSSession")
    @patch("services.ip_register.lookup_egress")
    def test_geoip_called_before_ssh(self, mock_geo, mock_vps_cls):
        """geoip 在 SSH 之前（even if no VPS available，geoip 仍要先调）。

        因为 outbound tag 要 country_code，所以 geoip 必须早于 apply_proxy_binding。
        这里测的是顺序：no_available_vps 时也走过了 geoip。
        """
        call_order = []

        def geo_side_effect(ip):
            call_order.append("geoip")
            return {"country_code": "US", "country_name": "", "city": "", "region_name": "", "raw": None}

        mock_geo.side_effect = geo_side_effect

        def ssh_side_effect(rec):
            call_order.append("ssh")
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=MagicMock())
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        mock_vps_cls.from_record.side_effect = ssh_side_effect

        result = register_ip(**_common_kwargs())
        # 没有 VPS → no_available_vps，但 geoip 已经调过了
        self.assertEqual(result["status"], "no_available_vps")
        self.assertIn("geoip", call_order)
        self.assertNotIn("ssh", call_order)


if __name__ == "__main__":
    unittest.main()
