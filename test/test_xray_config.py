"""xray.config 测试。

涵盖：
- 纯函数：generate_random_auth / build_proxy_outbound /
          build_vps_direct_config / build_proxy_relay_config
- SSH 操作：upload_config / validate_config / write_default_config
"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config as project_config
from xray.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PORT,
    DEFAULT_CONFIG_JSON,
    PROTOCOL_HTTP,
    PROTOCOL_SOCKS5,
    SUPPORTED_PROTOCOLS,
    ConfigReadError,
    ConfigValidationError,
    ConfigWriteError,
    PortConflictError,
    UnsupportedProtocolError,
    build_proxy_outbound,
    build_proxy_relay_config,
    build_vps_direct_config,
    extract_port_bindings,
    generate_random_auth,
    read_config,
    upload_config,
    validate_config,
    write_default_config,
)


# ============================================================
# generate_random_auth
# ============================================================

class TestGenerateRandomAuth(unittest.TestCase):
    def test_returns_two_strings(self):
        u, p = generate_random_auth()
        self.assertIsInstance(u, str)
        self.assertIsInstance(p, str)

    def test_default_length_16(self):
        u, p = generate_random_auth()
        self.assertEqual(len(u), 16)
        self.assertEqual(len(p), 16)

    def test_custom_length(self):
        u, p = generate_random_auth(length=32)
        self.assertEqual(len(u), 32)
        self.assertEqual(len(p), 32)

    def test_user_and_pwd_are_different(self):
        """生成 user 和 pwd 是两条独立随机串，几乎不可能相同。"""
        u, p = generate_random_auth()
        self.assertNotEqual(u, p)

    def test_two_calls_produce_different_results(self):
        """两次调用不能撞——验证用的是 secrets 而不是固定种子。"""
        u1, _ = generate_random_auth()
        u2, _ = generate_random_auth()
        self.assertNotEqual(u1, u2)

    def test_only_alphanum_chars(self):
        """字符集是大小写字母 + 数字，避免 shell / URL 转义。"""
        import string
        allowed = set(string.ascii_letters + string.digits)
        u, p = generate_random_auth(length=64)
        self.assertTrue(set(u).issubset(allowed))
        self.assertTrue(set(p).issubset(allowed))


# ============================================================
# build_vps_direct_config
# ============================================================

class TestBuildVpsDirectConfig(unittest.TestCase):
    def test_top_level_keys(self):
        out = build_vps_direct_config()
        self.assertEqual(set(out.keys()), {"log", "inbounds", "outbounds", "routing"})

    def test_single_inbound_default_direct(self):
        out = build_vps_direct_config()
        self.assertEqual(len(out["inbounds"]), 1)
        self.assertEqual(out["inbounds"][0]["tag"], "default-direct")
        self.assertEqual(out["inbounds"][0]["port"], DEFAULT_PORT)
        self.assertEqual(out["inbounds"][0]["settings"]["auth"], "noauth")

    def test_single_outbound_freedom_direct(self):
        out = build_vps_direct_config()
        self.assertEqual(len(out["outbounds"]), 1)
        self.assertEqual(out["outbounds"][0]["tag"], "direct")
        self.assertEqual(out["outbounds"][0]["protocol"], "freedom")

    def test_routing_wires_default_to_direct(self):
        out = build_vps_direct_config()
        rules = out["routing"]["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["inboundTag"], ["default-direct"])
        self.assertEqual(rules[0]["outboundTag"], "direct")

    def test_is_json_serializable(self):
        json.dumps(build_vps_direct_config())

    def test_default_config_json_constant_matches_dict(self):
        """DEFAULT_CONFIG_JSON 字符串常量应该等于 build_vps_direct_config() 的 JSON 化。"""
        parsed = json.loads(DEFAULT_CONFIG_JSON)
        self.assertEqual(parsed, build_vps_direct_config())


# ============================================================
# upload_config
# ============================================================

class TestUploadConfig(unittest.TestCase):
    @patch("xray.config.execute_command")
    def test_success_uses_heredoc_to_path(self, mock_exec):
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}
        upload_config(MagicMock(), {"hello": "world"})

        args, _ = mock_exec.call_args
        cmd = args[1]
        # 用 heredoc 写到 DEFAULT_CONFIG_PATH
        self.assertIn(f"cat > {DEFAULT_CONFIG_PATH}", cmd)
        self.assertIn("XRAY_CONFIG_EOF", cmd)
        # JSON 内容被序列化进去
        self.assertIn("hello", cmd)
        self.assertIn("world", cmd)

    @patch("xray.config.execute_command")
    def test_failure_raises_config_write_error(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "", "stderr": "Permission denied", "exit_code": 1
        }
        with self.assertRaises(ConfigWriteError) as ctx:
            upload_config(MagicMock(), {"x": 1})
        self.assertIn("Permission denied", str(ctx.exception))


# ============================================================
# validate_config
# ============================================================

class TestValidateConfig(unittest.TestCase):
    @patch("xray.config.execute_command")
    def test_success(self, mock_exec):
        mock_exec.return_value = {"stdout": "Configuration OK", "stderr": "", "exit_code": 0}
        validate_config(MagicMock())  # 不抛即过

    @patch("xray.config.execute_command")
    def test_failure_raises(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "", "stderr": "Failed to load: line 42 syntax error", "exit_code": 1
        }
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_config(MagicMock())
        # 错误文案带 xray 的 stderr 便于定位
        self.assertIn("line 42", str(ctx.exception))

    @patch("xray.config.execute_command")
    def test_uses_confdir_command(self, mock_exec):
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}
        validate_config(MagicMock())
        args, _ = mock_exec.call_args
        self.assertIn("xray test", args[1])
        self.assertIn("-confdir", args[1])


# ============================================================
# write_default_config（端到端：跟 upload_config 联动）
# ============================================================

class TestWriteDefaultConfig(unittest.TestCase):
    @patch("xray.config.execute_command")
    def test_writes_vps_direct_config(self, mock_exec):
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}
        write_default_config(MagicMock())

        args, _ = mock_exec.call_args
        cmd = args[1]
        # 写入的内容应含 VPS 直出标志
        self.assertIn("default-direct", cmd)
        self.assertIn("freedom", cmd)
        self.assertIn(str(DEFAULT_PORT), cmd)


# ============================================================
# build_proxy_outbound：代理凭据 → outbound dict 翻译
# ============================================================

class TestBuildProxyOutbound(unittest.TestCase):
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


# ============================================================
# build_proxy_relay_config：场景二完整 config（客户端走代理跳转）
# ============================================================

class TestBuildProxyRelayConfig(unittest.TestCase):
    """完整 config：单个 outbound + vps_port + 客户端账密 → 完整 xray config dict。"""

    def _make_proxy(self, tag: str = "proxy-out") -> dict:
        """方便测试：用 build_proxy_outbound 造个真实的 proxy outbound 喂进去。"""
        return build_proxy_outbound(
            host="1.2.3.4", port=1080, user="alice", pwd="secret",
            protocol=PROTOCOL_SOCKS5, tag=tag,
        )

    # ---------- 顶层形状 ----------

    def test_top_level_keys(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(set(out.keys()), {"log", "inbounds", "outbounds", "routing"})

    def test_log_level_warning(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["log"]["loglevel"], "warning")

    # ---------- inbounds ----------

    def test_inbounds_has_exactly_two(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(len(out["inbounds"]), 2)

    def test_first_inbound_is_default_direct(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        default_in = out["inbounds"][0]
        self.assertEqual(default_in["tag"], "default-direct")
        self.assertEqual(default_in["port"], DEFAULT_PORT)
        self.assertEqual(default_in["settings"]["auth"], "noauth")

    def test_client_inbound_tag_includes_port(self):
        out = build_proxy_relay_config(
            vps_port=18443, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["inbounds"][1]["tag"], "client-18443")

    def test_client_inbound_port_matches_param(self):
        out = build_proxy_relay_config(
            vps_port=18445, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["inbounds"][1]["port"], 18445)

    def test_client_inbound_uses_password_auth(self):
        out = build_proxy_relay_config(
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
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertTrue(out["inbounds"][1]["settings"]["udp"])

    # ---------- outbounds ----------

    def test_outbounds_has_exactly_two(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(len(out["outbounds"]), 2)

    def test_first_outbound_is_freedom_direct(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(out["outbounds"][0]["tag"], "direct")
        self.assertEqual(out["outbounds"][0]["protocol"], "freedom")

    def test_proxy_outbound_passed_through_as_is(self):
        proxy = self._make_proxy(tag="my-special-tag")
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=proxy,
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertIs(out["outbounds"][1], proxy)

    # ---------- routing ----------

    def test_routing_has_two_rules(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        self.assertEqual(len(out["routing"]["rules"]), 2)

    def test_first_routing_wires_default_direct_to_freedom(self):
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        rule = out["routing"]["rules"][0]
        self.assertEqual(rule["inboundTag"], ["default-direct"])
        self.assertEqual(rule["outboundTag"], "direct")

    def test_second_routing_wires_client_to_proxy_tag(self):
        proxy = self._make_proxy(tag="upstream-ip-42")
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=proxy,
            inbound_user="cu", inbound_pwd="cp",
        )
        rule = out["routing"]["rules"][1]
        self.assertEqual(rule["inboundTag"], ["client-18441"])
        self.assertEqual(rule["outboundTag"], "upstream-ip-42")

    # ---------- 失败路径 ----------

    def test_vps_port_conflict_with_default_port_raises(self):
        with self.assertRaises(PortConflictError):
            build_proxy_relay_config(
                vps_port=DEFAULT_PORT,
                proxy_outbound=self._make_proxy(),
                inbound_user="cu", inbound_pwd="cp",
            )

    def test_port_conflict_error_message_includes_value(self):
        try:
            build_proxy_relay_config(
                vps_port=DEFAULT_PORT,
                proxy_outbound=self._make_proxy(),
                inbound_user="cu", inbound_pwd="cp",
            )
        except PortConflictError as exc:
            self.assertIn(str(DEFAULT_PORT), str(exc))
        else:
            self.fail("expected PortConflictError")

    # ---------- 端到端：可序列化 JSON ----------

    def test_output_is_json_serializable(self):
        """业务层会 json.dumps 后写入文件，确保输出不含不可序列化对象。"""
        out = build_proxy_relay_config(
            vps_port=18441, proxy_outbound=self._make_proxy(),
            inbound_user="cu", inbound_pwd="cp",
        )
        json.dumps(out)  # 不抛即过


# ============================================================
# extract_port_bindings：从 config dict 反向抠出绑定信息
# ============================================================

class TestExtractPortBindings(unittest.TestCase):
    def _make_full_config_with_one_proxy(self) -> dict:
        """造一份典型 IP 业务部署后的 config：
        - default-direct (18440) + freedom
        - client-18443 (账密 socks5) → proxy-out-1（带 _meta）
        """
        proxy_outbound = build_proxy_outbound(
            host="1.2.3.4", port=1080, user="up", pwd="upwd",
            protocol=PROTOCOL_SOCKS5, tag="proxy-out-1",
        )
        proxy_outbound["_meta"] = {"egress_ip": "5.6.7.8", "egress_country": "US"}
        cfg = build_proxy_relay_config(
            vps_port=18443, proxy_outbound=proxy_outbound,
            inbound_user="client_u", inbound_pwd="client_p",
        )
        # build_proxy_relay_config 内部把 client inbound tag 设为 "client-18443"
        # routing 已正确接上 client-18443 → proxy-out-1
        return cfg

    # ---------- 跳过 default-direct ----------

    def test_skips_default_direct_inbound(self):
        cfg = build_vps_direct_config()  # 只有 default-direct
        self.assertEqual(extract_port_bindings(cfg), [])

    def test_emits_only_non_default_inbounds(self):
        cfg = self._make_full_config_with_one_proxy()
        bindings = extract_port_bindings(cfg)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["port"], 18443)

    # ---------- inbound 字段映射 ----------

    def test_protocol_reverse_mapped_to_user_name(self):
        """xray 里写 socks，反查后是 socks5。"""
        cfg = self._make_full_config_with_one_proxy()
        bindings = extract_port_bindings(cfg)
        self.assertEqual(bindings[0]["protocol"], PROTOCOL_SOCKS5)

    def test_inbound_creds_extracted(self):
        cfg = self._make_full_config_with_one_proxy()
        bindings = extract_port_bindings(cfg)
        self.assertEqual(bindings[0]["inbound_user"], "client_u")
        self.assertEqual(bindings[0]["inbound_pwd"], "client_p")

    def test_listen_address_extracted(self):
        cfg = self._make_full_config_with_one_proxy()
        bindings = extract_port_bindings(cfg)
        self.assertEqual(bindings[0]["listen_address"], "0.0.0.0")

    # ---------- outbound 通过 routing 链接 ----------

    def test_upstream_host_from_outbound(self):
        cfg = self._make_full_config_with_one_proxy()
        bindings = extract_port_bindings(cfg)
        self.assertEqual(bindings[0]["upstream_host"], "1.2.3.4")

    def test_meta_egress_ip_extracted(self):
        cfg = self._make_full_config_with_one_proxy()
        bindings = extract_port_bindings(cfg)
        self.assertEqual(bindings[0]["egress_ip"], "5.6.7.8")

    def test_meta_egress_country_extracted(self):
        cfg = self._make_full_config_with_one_proxy()
        bindings = extract_port_bindings(cfg)
        self.assertEqual(bindings[0]["egress_country"], "US")

    def test_missing_meta_returns_empty_strings(self):
        """outbound 没有 _meta 字段（旧 config / 业务还没塞）时，egress 字段为空串。"""
        proxy = build_proxy_outbound(
            host="x", port=1, user="u", pwd="p",
            protocol=PROTOCOL_SOCKS5, tag="proxy-out-1",
        )
        # 故意不加 _meta
        cfg = build_proxy_relay_config(
            vps_port=18444, proxy_outbound=proxy,
            inbound_user="cu", inbound_pwd="cp",
        )
        bindings = extract_port_bindings(cfg)
        self.assertEqual(bindings[0]["egress_ip"], "")
        self.assertEqual(bindings[0]["egress_country"], "")

    # ---------- 边界 ----------

    def test_empty_config_returns_empty_list(self):
        self.assertEqual(extract_port_bindings({}), [])

    def test_config_with_only_outbounds_no_inbounds(self):
        cfg = {"outbounds": [{"tag": "direct", "protocol": "freedom"}]}
        self.assertEqual(extract_port_bindings(cfg), [])

    def test_inbound_without_routing_rule_still_emitted(self):
        """没有 routing 接上 outbound 的 inbound 仍出现，但 upstream 字段空。"""
        cfg = {
            "inbounds": [{
                "tag": "orphan",
                "port": 18443,
                "listen": "0.0.0.0",
                "protocol": "socks",
                "settings": {"accounts": [{"user": "u", "pass": "p"}]},
            }],
            "outbounds": [],
            "routing": {"rules": []},
        }
        bindings = extract_port_bindings(cfg)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["upstream_host"], "")
        self.assertEqual(bindings[0]["egress_ip"], "")

    def test_multiple_bindings_in_one_config(self):
        """一台 VPS 上同时挂两个代理出口，extract 应返回两条记录。"""
        # 手工拼一个有两条 client inbound 的 config
        cfg = {
            "inbounds": [
                {
                    "tag": "default-direct", "port": 18440, "listen": "0.0.0.0",
                    "protocol": "socks", "settings": {"auth": "noauth"},
                },
                {
                    "tag": "client-18443", "port": 18443, "listen": "0.0.0.0",
                    "protocol": "socks",
                    "settings": {"accounts": [{"user": "u1", "pass": "p1"}]},
                },
                {
                    "tag": "client-18445", "port": 18445, "listen": "0.0.0.0",
                    "protocol": "http",
                    "settings": {"accounts": [{"user": "u2", "pass": "p2"}]},
                },
            ],
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {
                    "tag": "proxy-A", "protocol": "socks",
                    "settings": {"servers": [{"address": "a.example.com", "port": 1080}]},
                    "_meta": {"egress_ip": "1.1.1.1", "egress_country": "JP"},
                },
                {
                    "tag": "proxy-B", "protocol": "http",
                    "settings": {"servers": [{"address": "b.example.com", "port": 8080}]},
                    "_meta": {"egress_ip": "2.2.2.2", "egress_country": "DE"},
                },
            ],
            "routing": {
                "rules": [
                    {"inboundTag": ["default-direct"], "outboundTag": "direct"},
                    {"inboundTag": ["client-18443"], "outboundTag": "proxy-A"},
                    {"inboundTag": ["client-18445"], "outboundTag": "proxy-B"},
                ],
            },
        }
        bindings = extract_port_bindings(cfg)
        self.assertEqual(len(bindings), 2)

        by_port = {b["port"]: b for b in bindings}
        self.assertEqual(by_port[18443]["protocol"], "socks5")
        self.assertEqual(by_port[18443]["upstream_host"], "a.example.com")
        self.assertEqual(by_port[18443]["egress_country"], "JP")
        self.assertEqual(by_port[18445]["protocol"], "http")
        self.assertEqual(by_port[18445]["upstream_host"], "b.example.com")
        self.assertEqual(by_port[18445]["egress_country"], "DE")


# ============================================================
# read_config：SFTP 拉 + JSON parse
# ============================================================

class TestReadConfig(unittest.TestCase):
    @patch("xray.config.execute_command")
    def test_empty_file_returns_empty_dict(self, mock_exec):
        """空 config（刚装好 / x-ui 未配）→ {} 而非抛错。"""
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}
        self.assertEqual(read_config(MagicMock()), {})

    @patch("xray.config.execute_command")
    def test_whitespace_only_returns_empty(self, mock_exec):
        mock_exec.return_value = {"stdout": "  \n  \n", "stderr": "", "exit_code": 0}
        self.assertEqual(read_config(MagicMock()), {})

    @patch("xray.config.execute_command")
    def test_valid_json_parsed_to_dict(self, mock_exec):
        sample = build_vps_direct_config()
        mock_exec.return_value = {
            "stdout": json.dumps(sample), "stderr": "", "exit_code": 0,
        }
        self.assertEqual(read_config(MagicMock()), sample)

    @patch("xray.config.execute_command")
    def test_invalid_json_raises_config_read_error(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "{this is not json",
            "stderr": "", "exit_code": 0,
        }
        with self.assertRaises(ConfigReadError):
            read_config(MagicMock())

    @patch("xray.config.execute_command")
    def test_cats_default_config_path(self, mock_exec):
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}
        read_config(MagicMock())
        args, _ = mock_exec.call_args
        self.assertIn(DEFAULT_CONFIG_PATH, args[1])


if __name__ == "__main__":
    unittest.main()
