"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-06 ④ proxy_timeout 路径 + 重试 3 次 (spec v2 §7)

故事:
  test_internal_socks 返回 ok=False + exit_code=28 (CURLE_OPERATION_TIMEDOUT)。
  spec §7: timeout 是唯一一个要重试的失败类型, 重试 3 次仍超时 → 抛回。
  process() 应:
    - test_internal_socks 被调 3 次
    - status=proxy_timeout + 文案含 "已重试 3 次"
    - 清残留, 不入库

测试矩阵 (3 TC):
  TC-06-a status / message 含 "已重试 3 次"
  TC-06-b test_internal_socks 被调 3 次, rollback 被调 1 次
  TC-06-c 不入库

  实施时把 _TIMEOUT_RETRY_BACKOFF monkeypatch 为 0 避免 6s 等待。
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


class TestProxyTimeout(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self.fake_xm = MagicMock(name="XrayManager_instance")
        self.fake_xm.replace_proxy_binding.return_value = {"_baked": "cfg"}

        FakeSess = make_fake_vps_session_cls()
        self.mock_probe = MagicMock(
            return_value=make_internal_socks_result(
                ok=False, http_code=None, body="",
                exit_code=28, stderr="",
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
            patch("workers.ip_probe_worker._TIMEOUT_RETRY_BACKOFF", 0),  # 提速
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

    # ---------- TC-06-a ----------
    def test_tc06a_status_and_message(self):
        self.assertEqual(self.result["status"], "proxy_timeout")
        self.assertIn("已重试 3 次", self.result["message"])
        self.assertIn("proxy.example.com", self.result["message"])

    # ---------- TC-06-b ----------
    def test_tc06b_probe_called_three_times_rollback_once(self):
        self.assertEqual(
            self.mock_probe.call_count, 3,
            "spec §7: timeout 必须重试 3 次",
        )
        self.fake_xm.rollback_proxy_binding.assert_called_once()

    # ---------- TC-06-c ----------
    def test_tc06c_no_db_writes(self):
        with self.Session() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)
            self.assertEqual(s.query(IPTask).count(), 0)


if __name__ == "__main__":
    unittest.main()
