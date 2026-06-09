"""
========================================================================
TC-06 register_vps MCP 入口 (T-06, ADR-0007, spec/mcp_tools §6.1)

故事:
  tools/register_vps.py 是 SSHWorker.process 的 MCP 协议适配层.
  handler 接 MCP arguments → 转 SSHWorker.process kwargs → JSON 包 TextContent 返.

子测:
  a TOOL 元数据: name='register_vps', required 4 字段, port 无 default, idempotentHint=True
  b handler 透传参数 (port 转 int, ed 解析)
  c handler 返 [TextContent], 6 种 status 全跑一遍, JSON parse status 对得上
  d ed 字段解析: '2026-12-31'→date / ''→None / 'bad'→ValueError
  e port 必填: 不传 → KeyError; string → int 转换
  f 注册到 ALL_TOOLS
  g 防回退: TOOL.name 不含 'rgvps'; description 含全部 6 种 status 名
  h 防回退: description 不含 '查 xray' / '看 xray 版本' (SSHWorker v4 §5 不变量)
========================================================================
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from mcp.types import TextContent

from tools import ALL_TOOLS
from tools.register_vps import TOOL, handler


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class TestRegisterVpsToolMetadata(unittest.TestCase):
    # ---------- a ----------
    def test_06a_tool_metadata(self):
        self.assertEqual(TOOL.name, "register_vps")
        self.assertNotIn("rgvps", TOOL.name)
        self.assertEqual(
            set(TOOL.inputSchema["required"]),
            {"ip", "user", "pwd", "port"},
        )
        port_schema = TOOL.inputSchema["properties"]["port"]
        self.assertNotIn("default", port_schema,
                         "port 必填, 不应有 default")
        self.assertEqual(port_schema["type"], "integer")
        self.assertTrue(TOOL.annotations.idempotentHint)
        self.assertFalse(TOOL.annotations.readOnlyHint)


class TestRegisterVpsHandler(unittest.TestCase):
    def setUp(self):
        self.mock_process = MagicMock(return_value={
            "status": "queued",
            "task_id": 1, "vps_id": 1,
            "vps": {"ip": "1.2.3.4"},
            "message": "ok",
        })
        self._patcher = patch(
            "tools.register_vps.SSHWorker",
            return_value=MagicMock(process=self.mock_process),
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    # ---------- b ----------
    def test_06b_handler_passes_args(self):
        _run(handler({
            "ip": "1.2.3.4", "user": "root", "pwd": "secret",
            "port": "2222",      # 故意传 string, 验 int 转换
            "ed": "2026-12-31",
            "provider": "vultr.com",
        }))
        self.mock_process.assert_called_once()
        kw = self.mock_process.call_args.kwargs
        self.assertEqual(kw["ip"], "1.2.3.4")
        self.assertEqual(kw["user"], "root")
        self.assertEqual(kw["pwd"], "secret")
        self.assertEqual(kw["port"], 2222)
        self.assertIsInstance(kw["port"], int)
        self.assertEqual(kw["ed"], date(2026, 12, 31))
        self.assertEqual(kw["provider"], "vultr.com")

    # ---------- c ----------
    def test_06c_handler_returns_textcontent_json_status_6kinds(self):
        statuses = [
            "queued", "already_registered",
            "auth_failed", "ssh_timeout", "ssh_refused", "ssh_failed",
        ]
        for st in statuses:
            self.mock_process.return_value = {"status": st, "extra": "x"}
            out = _run(handler({
                "ip": "1.2.3.4", "user": "root", "pwd": "x", "port": 22,
            }))
            self.assertEqual(len(out), 1)
            self.assertIsInstance(out[0], TextContent)
            payload = json.loads(out[0].text)
            self.assertEqual(payload["status"], st,
                             f"status {st} 透传失败")

    # ---------- d ----------
    def test_06d_ed_parse(self):
        # 空串 → None
        _run(handler({
            "ip": "1.2.3.4", "user": "r", "pwd": "x", "port": 22, "ed": "",
        }))
        self.assertIsNone(self.mock_process.call_args.kwargs["ed"])

        # 缺省 → None
        self.mock_process.reset_mock()
        _run(handler({
            "ip": "1.2.3.4", "user": "r", "pwd": "x", "port": 22,
        }))
        self.assertIsNone(self.mock_process.call_args.kwargs["ed"])

        # 错误格式 → ValueError (MCP 框架级会捕获)
        with self.assertRaises(ValueError):
            _run(handler({
                "ip": "1.2.3.4", "user": "r", "pwd": "x", "port": 22,
                "ed": "not-a-date",
            }))

    # ---------- e ----------
    def test_06e_port_required(self):
        # 缺 port → KeyError (inputSchema required 兜底, 但代码 args["port"] 也会抛)
        with self.assertRaises(KeyError):
            _run(handler({
                "ip": "1.2.3.4", "user": "r", "pwd": "x",
            }))


class TestRegisterVpsRegistration(unittest.TestCase):
    # ---------- f ----------
    def test_06f_registered_in_all_tools(self):
        names = [t.name for t, _ in ALL_TOOLS]
        self.assertIn("register_vps", names)


class TestRegisterVpsAntiRegression(unittest.TestCase):
    # ---------- g ----------
    def test_06g_no_rgvps_in_name_description_has_all_status(self):
        self.assertNotIn("rgvps", TOOL.name)
        # description 含全部 6 种 status 名
        for st in [
            "queued", "already_registered",
            "auth_failed", "ssh_timeout", "ssh_refused", "ssh_failed",
        ]:
            self.assertIn(st, TOOL.description,
                          f"description 漏了 status {st}")

    # ---------- h ----------
    def test_06h_no_xray_query_in_description(self):
        """SSHWorker v4 §5 不变量: SSHWorker 不查 xray 也不写 xray_version.
        description 不应误导 agent 说本工具会装/查 xray."""
        bad_phrases = ["查 xray", "看 xray 版本", "装好 xray"]
        for phrase in bad_phrases:
            self.assertNotIn(phrase, TOOL.description,
                             f"description 不应含 '{phrase}'")


if __name__ == "__main__":
    unittest.main()
