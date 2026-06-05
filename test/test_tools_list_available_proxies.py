"""tools.list_available_proxies MCP 工具适配层测试。

策略：mock services 业务函数——本层只负责协议适配，不重复测业务筛选逻辑。
覆盖：
- Tool 元数据符合 MCP 标准（name / inputSchema 结构）
- handler 把业务返回 list[dict] 包成 [TextContent] 且 JSON 可解析
- handler 透传 country_code 参数到业务函数
- handler 容忍 arguments=None / 空 dict / 缺 country_code 键
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp.types import TextContent, Tool

from tools.list_available_proxies import TOOL, handler


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class TestToolMetadata(unittest.TestCase):
    def test_tool_is_mcp_tool_instance(self):
        self.assertIsInstance(TOOL, Tool)

    def test_tool_name(self):
        self.assertEqual(TOOL.name, "list_available_proxies")

    def test_input_schema_has_country_code(self):
        schema = TOOL.inputSchema
        self.assertEqual(schema["type"], "object")
        self.assertIn("country_code", schema["properties"])
        self.assertEqual(schema["properties"]["country_code"]["type"], "string")

    def test_input_schema_has_no_required_fields(self):
        # country_code 可选，required 列表应为空
        self.assertEqual(TOOL.inputSchema.get("required", []), [])


class TestHandler(unittest.TestCase):
    @patch("tools.list_available_proxies.list_available_proxies")
    def test_handler_returns_text_content_list(self, mock_impl):
        mock_impl.return_value = [
            {"host": "1.1.1.1", "port": 18441, "country_code": "SG"},
        ]
        result = _run(handler({"country_code": "SG"}))

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TextContent)
        self.assertEqual(result[0].type, "text")

    @patch("tools.list_available_proxies.list_available_proxies")
    def test_handler_text_is_valid_json(self, mock_impl):
        nodes = [{"host": "1.1.1.1", "port": 18441}]
        mock_impl.return_value = nodes

        result = _run(handler({}))
        parsed = json.loads(result[0].text)
        self.assertEqual(parsed, nodes)

    @patch("tools.list_available_proxies.list_available_proxies")
    def test_handler_passes_country_code(self, mock_impl):
        mock_impl.return_value = []
        _run(handler({"country_code": "SG"}))
        mock_impl.assert_called_once_with(country_code="SG")

    @patch("tools.list_available_proxies.list_available_proxies")
    def test_handler_defaults_country_code_to_empty(self, mock_impl):
        mock_impl.return_value = []
        _run(handler({}))
        mock_impl.assert_called_once_with(country_code="")

    @patch("tools.list_available_proxies.list_available_proxies")
    def test_handler_tolerates_none_arguments(self, mock_impl):
        mock_impl.return_value = []
        _run(handler(None))
        mock_impl.assert_called_once_with(country_code="")

    @patch("tools.list_available_proxies.list_available_proxies")
    def test_handler_tolerates_null_country_code(self, mock_impl):
        """arguments={'country_code': null} 也应 default 成空串。"""
        mock_impl.return_value = []
        _run(handler({"country_code": None}))
        mock_impl.assert_called_once_with(country_code="")


class TestToolsRegistry(unittest.TestCase):
    def test_all_tools_includes_list_available_proxies(self):
        from tools import ALL_TOOLS
        names = [tool.name for tool, _ in ALL_TOOLS]
        self.assertIn("list_available_proxies", names)

    def test_all_tools_entries_are_tool_handler_pairs(self):
        from tools import ALL_TOOLS
        for entry in ALL_TOOLS:
            self.assertEqual(len(entry), 2)
            tool, h = entry
            self.assertIsInstance(tool, Tool)
            self.assertTrue(callable(h))


if __name__ == "__main__":
    unittest.main()
