"""
========================================================================
TC-06 + TC-07 get_ip_registration_status ⭐ 一条龙 (spec §6.4)

故事:
  task.status=done 时 proxy_node 必返 (一条龙). 其他 task 状态 / not_found 时
  proxy_node 为 null 或不存在.

子测:
  TC-06-a 一条龙: task.status=done + proxy_node 非空 → JSON 含完整 proxy_node 字段
  TC-06-b proxy_node 5 字段齐: vps_id/vps_ip/vps_port/inbound_user/inbound_pwd/status/protocol
  TC-07-a not_found 时 status='not_found'
  TC-07-b not_found 时无 ip/task/proxy_node 字段 (或都为 null)
  TC-07-c in_progress 时 proxy_node 为 null (没配好不返代理)
========================================================================
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from mcp.types import TextContent

from tools.get_ip_registration_status import TOOL, handler


_DONE_USING_RESULT = {
    "status": "ok",
    "ip": {
        "id": 42, "egress_ip": "1.2.3.4",
        "country_code": "SG", "status": "using",
        "expire_date": "2026-12-31",
    },
    "task": {
        "id": 87, "status": "done",
        "last_error_code": "", "last_error_msg": "",
        "completed_at": "2026-06-09T13:40:00",
    },
    "proxy_node": {
        "vps_id": 1, "vps_ip": "10.0.0.1", "vps_port": 8765,
        "protocol": "socks5",
        "inbound_user": "proxy_42", "inbound_pwd": "a" * 32,
        "status": "using",
    },
}

_IN_PROGRESS_RESULT = {
    "status": "ok",
    "ip": {"id": 42, "egress_ip": "1.2.3.4", "country_code": "SG",
           "status": "usable", "expire_date": None},
    "task": {"id": 87, "status": "in_progress",
             "last_error_code": "", "last_error_msg": "", "completed_at": None},
    "proxy_node": None,
}


class TestGetIpStatus(unittest.TestCase):

    def test_tc06a_one_stop_done_returns_proxy_node(self):
        with patch(
            "tools.get_ip_registration_status.query_ip_status",
            return_value=_DONE_USING_RESULT,
        ):
            out = asyncio.run(handler({"ip_id": 42}))
        self.assertIsInstance(out[0], TextContent)
        payload = json.loads(out[0].text)
        self.assertEqual(payload["task"]["status"], "done")
        self.assertIsNotNone(payload["proxy_node"])

    def test_tc06b_proxy_node_fields_complete(self):
        with patch(
            "tools.get_ip_registration_status.query_ip_status",
            return_value=_DONE_USING_RESULT,
        ):
            out = asyncio.run(handler({"ip_id": 42}))
        payload = json.loads(out[0].text)
        node = payload["proxy_node"]
        for k in ("vps_id", "vps_ip", "vps_port", "protocol",
                  "inbound_user", "inbound_pwd", "status"):
            self.assertIn(k, node, f"proxy_node 缺字段 {k}")
        # 一条龙真值校验
        self.assertEqual(node["vps_ip"], "10.0.0.1")
        self.assertEqual(node["vps_port"], 8765)
        self.assertEqual(node["inbound_user"], "proxy_42")
        self.assertEqual(len(node["inbound_pwd"]), 32)

    def test_tc07a_not_found_status(self):
        with patch(
            "tools.get_ip_registration_status.query_ip_status",
            return_value={"status": "not_found"},
        ):
            out = asyncio.run(handler({"ip_id": 99999}))
        payload = json.loads(out[0].text)
        self.assertEqual(payload["status"], "not_found")

    def test_tc07b_not_found_no_ip_task_proxy(self):
        with patch(
            "tools.get_ip_registration_status.query_ip_status",
            return_value={"status": "not_found"},
        ):
            out = asyncio.run(handler({"ip_id": 99999}))
        payload = json.loads(out[0].text)
        # not_found 时不应有 ip/task/proxy_node (或全部为 null)
        self.assertNotIn("ip", payload)
        self.assertNotIn("task", payload)
        self.assertNotIn("proxy_node", payload)

    def test_tc07c_in_progress_proxy_node_null(self):
        with patch(
            "tools.get_ip_registration_status.query_ip_status",
            return_value=_IN_PROGRESS_RESULT,
        ):
            out = asyncio.run(handler({"ip_id": 42}))
        payload = json.loads(out[0].text)
        self.assertEqual(payload["task"]["status"], "in_progress")
        self.assertIsNone(payload["proxy_node"])


class TestGetIpStatusMetadata(unittest.TestCase):

    def test_tool_metadata(self):
        self.assertEqual(TOOL.name, "get_ip_registration_status")
        self.assertTrue(TOOL.annotations.readOnlyHint)


if __name__ == "__main__":
    unittest.main()
