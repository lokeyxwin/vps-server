"""
========================================================================
TC-03 register_ip 改名后行为不变 (ADR-0007 §决策 §2)

故事:
  tools/register_ip.py (原 tools/rgip.py 改名) TOOL.name='register_ip',
  handler 透传 IPProbeWorker.process 返回的 7 种 status, JSON 包 TextContent 返.

子测:
  TC-03-a TOOL.name == 'register_ip', title 不变, 7 种 status 全在 description
  TC-03-b handler 透传参数 (entry_host/entry_port/username/password/protocol)
  TC-03-c handler 透传 7 种 status JSON 字段对得上
========================================================================
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from mcp.types import TextContent

from tools.register_ip import TOOL, handler


_RGIP_STATUSES = [
    "queued",
    "duplicate",
    "proxy_auth_failed",
    "proxy_timeout",
    "proxy_refused",
    "proxy_failed",
    "probe_vps_unreachable",
]


class TestRegisterIpRenamed(unittest.TestCase):

    def test_tc03a_name_is_register_ip_and_status_in_description(self):
        self.assertEqual(TOOL.name, "register_ip")
        # 全部 7 种 status 在 description
        for st in _RGIP_STATUSES:
            self.assertIn(st, TOOL.description,
                          f"description 漏了 status {st}")

    def test_tc03b_handler_passes_args(self):
        mock_process = MagicMock(return_value={"status": "queued", "ip_id": 1, "task_id": 1})
        with patch(
            "tools.register_ip.IPProbeWorker",
            return_value=MagicMock(process=mock_process),
        ):
            asyncio.run(handler({
                "entry_host": "up.example.com",
                "entry_port": 1080,
                "username": "alice",
                "password": "s3cret",
                "protocol": "socks5",
            }))
        kw = mock_process.call_args.kwargs
        self.assertEqual(kw["entry_host"], "up.example.com")
        self.assertEqual(kw["entry_port"], 1080)
        self.assertEqual(kw["username"], "alice")
        self.assertEqual(kw["protocol"], "socks5")

    def test_tc03c_handler_passes_through_7_statuses(self):
        for st in _RGIP_STATUSES:
            mock_process = MagicMock(return_value={"status": st})
            with patch(
                "tools.register_ip.IPProbeWorker",
                return_value=MagicMock(process=mock_process),
            ):
                out = asyncio.run(handler({
                    "entry_host": "x", "entry_port": 1,
                    "username": "u", "password": "p", "protocol": "socks5",
                }))
            self.assertEqual(len(out), 1)
            self.assertIsInstance(out[0], TextContent)
            payload = json.loads(out[0].text)
            self.assertEqual(payload["status"], st)


if __name__ == "__main__":
    unittest.main()
