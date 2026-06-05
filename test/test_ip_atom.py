"""ip.atom 原子层测试 —— 纯函数，无 SSH / DB / 网络依赖。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from ip.atom import (
    PROTOCOL_HTTP,
    PROTOCOL_SOCKS5,
    SUPPORTED_PROTOCOLS,
    PortConflictError,
    UnsupportedProtocolError,
    build_deploy_config,
    build_proxy_outbound,
)


class TestBuildProxyOutbound(unittest.TestCase):
    """build_proxy_outbound：代理凭据 → xray outbound dict 的翻译。"""

    # ---------- 协议映射 ----------

    def test_socks5_protocol_maps_to_xray_socks(self):
        """对外讲 socks5，xray 里写 socks（这俩名字不一样，必须验证映射）。"""
        out = build_proxy_outbound(
            host="1.2.3.4", port=1080, user="alice", pwd="secret",
            protocol=PROTOCOL_SOCKS5,
        )
        self.assertEqual(out["protocol"], "socks")

    def test_http_protocol_maps_to_xray_http(self):
        out = build_proxy_outbound(
            host="proxy.example.com", port=8080, user="u", pwd="p",
            protocol=PROTOCOL_HTTP,
        )
        self.assertEqual(out["protocol"], "http")

    # ---------- 服务器字段 ----------

    def test_host_and_port_passthrough(self):
        out = build_proxy_outbound(
            host="proxy.brightdata.io", port=22225,
            user="user-1", pwd="pwd-1", protocol=PROTOCOL_SOCKS5,
        )
        server = out["settings"]["servers"][0]
        self.assertEqual(server["address"], "proxy.brightdata.io")
        self.assertEqual(server["port"], 22225)

    def test_single_server_in_servers_array(self):
        """xray outbound 的 servers 是数组，但本工具一条凭据=一台 server。"""
        out = build_proxy_outbound(
            host="x", port=1, user="", pwd="", protocol=PROTOCOL_SOCKS5,
        )
        self.assertEqual(len(out["settings"]["servers"]), 1)

    # ---------- 认证分支 ----------

    def test_auth_creds_attached_when_both_set(self):
        out = build_proxy_outbound(
            host="x", port=1, user="alice", pwd="secret",
            protocol=PROTOCOL_SOCKS5,
        )
        self.assertEqual(
            out["settings"]["servers"][0]["users"],
            [{"user": "alice", "pass": "secret"}],
        )

    def test_no_auth_omits_users_field(self):
        """user 和 pwd 都为空 = 免认证代理，不写 users 字段（xray 才不会拒）。"""
        out = build_proxy_outbound(
            host="x", port=1, user="", pwd="", protocol=PROTOCOL_SOCKS5,
        )
        self.assertNotIn("users", out["settings"]["servers"][0])

    def test_only_user_still_includes_users(self):
        """有些代理只验证 user，密码可空。任一非空都带 users 字段。"""
        out = build_proxy_outbound(
            host="x", port=1, user="alice", pwd="",
            protocol=PROTOCOL_SOCKS5,
        )
        self.assertEqual(
            out["settings"]["servers"][0]["users"],
            [{"user": "alice", "pass": ""}],
        )

    def test_only_pwd_still_includes_users(self):
        out = build_proxy_outbound(
            host="x", port=1, user="", pwd="secret",
            protocol=PROTOCOL_SOCKS5,
        )
        self.assertEqual(
            out["settings"]["servers"][0]["users"],
            [{"user": "", "pass": "secret"}],
        )

    # ---------- tag ----------

    def test_default_tag_is_proxy_out(self):
        out = build_proxy_outbound(
            host="x", port=1, user="", pwd="", protocol=PROTOCOL_SOCKS5,
        )
        self.assertEqual(out["tag"], "proxy-out")

    def test_custom_tag_overrides_default(self):
        out = build_proxy_outbound(
            host="x", port=1, user="", pwd="", protocol=PROTOCOL_SOCKS5,
            tag="upstream-ip-42",
        )
        self.assertEqual(out["tag"], "upstream-ip-42")

    # ---------- 失败路径 ----------

    def test_unsupported_protocol_raises(self):
        with self.assertRaises(UnsupportedProtocolError):
            build_proxy_outbound(
                host="x", port=1, user="", pwd="",
                protocol="vmess",
            )

    def test_unsupported_protocol_message_includes_input(self):
        """异常文案里要带传入的协议名，方便排查（CLAUDE.md 要求错误可定位）。"""
        try:
            build_proxy_outbound(
                host="x", port=1, user="", pwd="",
                protocol="trojan",
            )
        except UnsupportedProtocolError as exc:
            self.assertIn("trojan", str(exc))
        else:
            self.fail("expected UnsupportedProtocolError")

    def test_empty_protocol_string_raises(self):
        with self.assertRaises(UnsupportedProtocolError):
            build_proxy_outbound(
                host="x", port=1, user="", pwd="",
                protocol="",
            )

    # ---------- 整体结构 ----------

    def test_top_level_keys(self):
        """xray outbound 三件套：tag / protocol / settings。"""
        out = build_proxy_outbound(
            host="x", port=1, user="", pwd="", protocol=PROTOCOL_SOCKS5,
        )
        self.assertEqual(set(out.keys()), {"tag", "protocol", "settings"})

    def test_supported_protocols_constant_exposed(self):
        """业务层会用 SUPPORTED_PROTOCOLS 做入参校验，确保常量稳定可用。"""
        self.assertIn(PROTOCOL_SOCKS5, SUPPORTED_PROTOCOLS)
        self.assertIn(PROTOCOL_HTTP, SUPPORTED_PROTOCOLS)


class TestBuildDeployConfig(unittest.TestCase):
    """build_deploy_config：单个 outbound + vps_port + 客户端账密 → 完整 xray config dict。"""

    def _make_proxy(self, tag: str = "proxy-out") -> dict:
        """方便测试：用 build_proxy_outbound 造个真实的 proxy outbound 喂进去。"""
        return build_proxy_outbound(
            host="1.2.3.4", port=1080, user="alice", pwd="secret",
            protocol=PROTOCOL_SOCKS5, tag=tag,
        )

    # ---------- 顶层形状 ----------

    def test_top_level_keys(self):
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(set(out.keys()), {"log", "inbounds", "outbounds", "routing"})

    def test_log_level_warning(self):
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["log"]["loglevel"], "warning")

    # ---------- inbounds ----------

    def test_inbounds_has_exactly_two(self):
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(len(out["inbounds"]), 2)

    def test_first_inbound_is_default_direct(self):
        """第一个 inbound 永远是 default-direct（VPS 自用直出，monitor 默认端口）。"""
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        default_in = out["inbounds"][0]
        self.assertEqual(default_in["tag"], "default-direct")
        self.assertEqual(default_in["port"], config.XRAY_DEFAULT_PORT)
        self.assertEqual(default_in["settings"]["auth"], "noauth")

    def test_client_inbound_tag_includes_port(self):
        out = build_deploy_config(
            vps_port=18443, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["inbounds"][1]["tag"], "client-18443")

    def test_client_inbound_port_matches_param(self):
        out = build_deploy_config(
            vps_port=18445, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["inbounds"][1]["port"], 18445)

    def test_client_inbound_uses_password_auth(self):
        """客户端 inbound 必须是 password auth，账密原样塞进 accounts。"""
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="client_alice", inbound_pwd="strong_p@ss",
        )
        client_in = out["inbounds"][1]
        self.assertEqual(client_in["settings"]["auth"], "password")
        self.assertEqual(
            client_in["settings"]["accounts"],
            [{"user": "client_alice", "pass": "strong_p@ss"}],
        )

    def test_client_inbound_udp_enabled(self):
        """SOCKS5 一般要 UDP 支持，跟 default-direct 保持一致。"""
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertTrue(out["inbounds"][1]["settings"]["udp"])

    # ---------- outbounds ----------

    def test_outbounds_has_exactly_two(self):
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(len(out["outbounds"]), 2)

    def test_first_outbound_is_freedom_direct(self):
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["outbounds"][0]["tag"], "direct")
        self.assertEqual(out["outbounds"][0]["protocol"], "freedom")

    def test_proxy_outbound_passed_through_as_is(self):
        """传入的 proxy_outbound dict 应该原样出现在 outbounds[1]，不被改写。"""
        proxy = self._make_proxy(tag="my-special-tag")
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=proxy,
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertIs(out["outbounds"][1], proxy)

    # ---------- routing ----------

    def test_routing_has_two_rules(self):
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(len(out["routing"]["rules"]), 2)

    def test_first_routing_wires_default_direct_to_freedom(self):
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        rule = out["routing"]["rules"][0]
        self.assertEqual(rule["inboundTag"], ["default-direct"])
        self.assertEqual(rule["outboundTag"], "direct")

    def test_second_routing_wires_client_to_proxy_tag(self):
        """关键：路由把 client-{port} 接到 proxy_outbound 的 tag，否则流量不走代理。"""
        proxy = self._make_proxy(tag="upstream-ip-42")
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=proxy,
            inbound_user="cu", inbound_pwd="cp",
        )
        rule = out["routing"]["rules"][1]
        self.assertEqual(rule["inboundTag"], ["client-18441"])
        self.assertEqual(rule["outboundTag"], "upstream-ip-42")

    # ---------- 失败路径 ----------

    def test_vps_port_conflict_with_default_port_raises(self):
        with self.assertRaises(PortConflictError):
            build_deploy_config(
                vps_port=config.XRAY_DEFAULT_PORT,
                proxy_outbound=self._make_proxy(),
                inbound_user="cu", inbound_pwd="cp",
            )

    def test_port_conflict_error_message_includes_value(self):
        try:
            build_deploy_config(
                vps_port=config.XRAY_DEFAULT_PORT,
                proxy_outbound=self._make_proxy(),
                inbound_user="cu", inbound_pwd="cp",
            )
        except PortConflictError as exc:
            self.assertIn(str(config.XRAY_DEFAULT_PORT), str(exc))
        else:
            self.fail("expected PortConflictError")

    # ---------- 端到端：可序列化 JSON ----------

    def test_output_is_json_serializable(self):
        """业务层会 json.dumps 后写入文件，确保输出不含不可序列化对象。"""
        import json
        out = build_deploy_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        # 不抛异常就算过
        json.dumps(out)


if __name__ == "__main__":
    unittest.main()
