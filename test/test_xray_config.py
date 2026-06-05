"""xray.config 新增能力的测试。

涵盖：
- generate_random_auth：随机账密生成
- build_vps_direct_config：VPS 直出场景的完整 config
- upload_config / validate_config / write_default_config：SSH 操作类
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
    ConfigValidationError,
    ConfigWriteError,
    build_vps_direct_config,
    generate_random_auth,
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


if __name__ == "__main__":
    unittest.main()
