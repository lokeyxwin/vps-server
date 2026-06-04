"""ip.atom 原子层测试 —— 纯函数，无 SSH / DB / 网络依赖。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ip.atom import (
    PROTOCOL_HTTP,
    PROTOCOL_SOCKS5,
    SUPPORTED_PROTOCOLS,
    UnsupportedProtocolError,
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


if __name__ == "__main__":
    unittest.main()
