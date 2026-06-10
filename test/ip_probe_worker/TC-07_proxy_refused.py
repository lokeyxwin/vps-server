"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-07 ④ proxy_refused 路径 (spec v2 §7)

故事:
  test_internal_socks 返回 ok=False + exit_code=7 (CURLE_COULDNT_CONNECT)。
  spec §7: refused 不重试。process() 应:
    - test_internal_socks 只调 1 次
    - status=proxy_refused + 文案含 "被拒绝"
    - 清残留, 不入库

测试矩阵 (3 TC):
  TC-07-a status / message
  TC-07-b 不重试 + rollback 调用
  TC-07-c 不入库
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPRecord, IPTask
from workers.ip_probe_worker import IPProbeWorker

from ._helpers import (
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_in_memory_engine,
    make_internal_socks_result,
)


class TestProxyRefused(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self.fake_xm = MagicMock()
        self.fake_xm.replace_proxy_binding.return_value = {"_baked": "cfg"}
        FakeSess = make_fake_vps_session_cls()

        self.mock_probe = MagicMock(
            return_value=make_internal_socks_result(
                ok=False, http_code=None, body="",
                exit_code=7, stderr="",
            ),
        )

        self._patches = [
            patch(
                "workers.ip_probe_worker.session_scope",
                make_fake_session_scope(self.Session),
            ),
            patch(
                "workers.ip_probe_worker.get_probe_vps_pool",
                return_value=(
                    {"ip": "10.0.0.1", "port": 22, "username": "root", "password": "x"},
                ),
            ),
            patch("workers.ip_probe_worker.VPSSession", FakeSess),
            patch("workers.ip_probe_worker.XrayManager", return_value=self.fake_xm),
            patch("workers.ip_probe_worker.test_internal_socks", self.mock_probe),
            # ADR-0009: 跳过测试机自举 (单独测见 test/probe_vps/TC-*).
            patch("workers.ip_probe_worker.bootstrap.ensure_ready", return_value=None),
        ]
        for p in self._patches:
            p.start()

        self.worker = IPProbeWorker()
        self.result = self.worker.process(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="u",
            password="p",
            protocol="socks5",
        )

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    # ---------- TC-07-a ----------
    def test_tc07a_status_and_message(self):
        self.assertEqual(self.result["status"], "proxy_refused")
        self.assertIn("被拒绝", self.result["message"])
        self.assertIn("proxy.example.com", self.result["message"])

    # ---------- TC-07-b ----------
    def test_tc07b_no_retry_rollback_called(self):
        self.assertEqual(self.mock_probe.call_count, 1, "refused 不该重试")
        self.fake_xm.rollback_proxy_binding.assert_called_once()

    # ---------- TC-07-c ----------
    def test_tc07c_no_db_writes(self):
        with self.Session() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)
            self.assertEqual(s.query(IPTask).count(), 0)


if __name__ == "__main__":
    unittest.main()
