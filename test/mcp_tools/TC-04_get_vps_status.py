"""
========================================================================
TC-04 + TC-05 get_vps_registration_status handler + JSON 形状 (spec §6.3)

故事:
  handler 调 services.registration_query.query_vps_status, JSON 包 TextContent 返.
  query 返 {status, vps, task} 形状 (§6.3).

子测:
  TC-04-a handler 透传 vps_id / task_id 入参
  TC-04-b handler 不写 SQL (mock query 验只调一次)
  TC-05-a 返回 JSON 顶层有 'status' 字段
  TC-05-b ok 时 'vps' 'task' 字段都齐
  TC-05-c JSON 内嵌字段对齐 spec §6.3 (id/ip/stage/xray_version/is_active + task 子段)
========================================================================
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from mcp.types import TextContent

from tools.get_vps_registration_status import TOOL, handler


_FAKE_OK_RESULT = {
    "status": "ok",
    "vps": {
        "id": 1, "ip": "10.0.0.1", "stage": "running",
        "xray_version": "Xray 1.8.0", "is_active": 1,
    },
    "task": {
        "id": 87, "status": "done",
        "last_error_code": "", "last_error_msg": "",
        "completed_at": "2026-06-09T13:40:00",
    },
}


class TestGetVpsStatusHandler(unittest.TestCase):

    def test_tc04a_handler_passes_vps_id(self):
        with patch(
            "tools.get_vps_registration_status.query_vps_status",
            return_value=_FAKE_OK_RESULT,
        ) as mock_q:
            asyncio.run(handler({"vps_id": 42}))
        mock_q.assert_called_once_with(vps_id=42, task_id=None)

    def test_tc04b_handler_passes_task_id(self):
        with patch(
            "tools.get_vps_registration_status.query_vps_status",
            return_value=_FAKE_OK_RESULT,
        ) as mock_q:
            asyncio.run(handler({"task_id": 99}))
        mock_q.assert_called_once_with(vps_id=None, task_id=99)

    def test_tc04c_handler_no_args_returns_not_found(self):
        with patch(
            "tools.get_vps_registration_status.query_vps_status",
            return_value={"status": "not_found"},
        ) as mock_q:
            out = asyncio.run(handler(None))
        mock_q.assert_called_once_with(vps_id=None, task_id=None)
        payload = json.loads(out[0].text)
        self.assertEqual(payload["status"], "not_found")

    def test_tc05a_json_top_level_status_field(self):
        with patch(
            "tools.get_vps_registration_status.query_vps_status",
            return_value=_FAKE_OK_RESULT,
        ):
            out = asyncio.run(handler({"vps_id": 1}))
        self.assertIsInstance(out[0], TextContent)
        payload = json.loads(out[0].text)
        self.assertIn("status", payload)
        self.assertEqual(payload["status"], "ok")

    def test_tc05b_ok_has_vps_and_task(self):
        with patch(
            "tools.get_vps_registration_status.query_vps_status",
            return_value=_FAKE_OK_RESULT,
        ):
            out = asyncio.run(handler({"vps_id": 1}))
        payload = json.loads(out[0].text)
        self.assertIn("vps", payload)
        self.assertIn("task", payload)

    def test_tc05c_vps_and_task_subfields_match_spec(self):
        with patch(
            "tools.get_vps_registration_status.query_vps_status",
            return_value=_FAKE_OK_RESULT,
        ):
            out = asyncio.run(handler({"vps_id": 1}))
        payload = json.loads(out[0].text)
        for k in ("id", "ip", "stage", "xray_version", "is_active"):
            self.assertIn(k, payload["vps"], f"vps 缺字段 {k}")
        for k in ("id", "status", "last_error_code", "completed_at"):
            self.assertIn(k, payload["task"], f"task 缺字段 {k}")


class TestGetVpsStatusMetadata(unittest.TestCase):

    def test_tool_metadata(self):
        self.assertEqual(TOOL.name, "get_vps_registration_status")
        self.assertTrue(TOOL.annotations.readOnlyHint)
        self.assertEqual(TOOL.inputSchema["required"], [])


if __name__ == "__main__":
    unittest.main()
