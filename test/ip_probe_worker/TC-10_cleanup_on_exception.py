"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-10 try/finally 兜底 (spec v2 §7 + §9 不变量)

故事:
  process() 跑到内 ping 步骤时, test_internal_socks 抛了非业务异常
  (paramiko 通道断 / RuntimeError 之类)。
  process() 应:
    - finally 兜底拆 19000 残留 (rollback_proxy_binding 被调)
    - status=proxy_failed (兜底分类)
    - 不入库

  这条 TC 保证 spec §9 不变量 "同步段返回前 19000 端口必须干净" 在
  任何异常路径都成立。

测试矩阵 (3 TC):
  TC-10-a status=proxy_failed + message 含异常类型
  TC-10-b rollback_proxy_binding 仍被调 (finally 兜底)
  TC-10-c 不入库
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
)


class TestCleanupOnException(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self.fake_xm = MagicMock()
        self.fake_xm.replace_proxy_binding.return_value = {"_baked": "cfg"}
        FakeSess = make_fake_vps_session_cls()

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
            patch(
                "workers.ip_probe_worker.test_internal_socks",
                side_effect=RuntimeError("test: paramiko 通道断了"),
            ),
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

    # ---------- TC-10-a ----------
    def test_tc10a_status_failed_with_exception_detail(self):
        self.assertEqual(self.result["status"], "proxy_failed")
        self.assertIn("RuntimeError", self.result["message"])
        self.assertIn("paramiko 通道断", self.result["message"])

    # ---------- TC-10-b ----------
    def test_tc10b_rollback_called_in_finally(self):
        """spec §9 不变量: 异常路径也必须拆 19000 残留。"""
        self.fake_xm.rollback_proxy_binding.assert_called_once()

    # ---------- TC-10-c ----------
    def test_tc10c_no_db_writes(self):
        with self.Session() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)
            self.assertEqual(s.query(IPTask).count(), 0)


if __name__ == "__main__":
    unittest.main()
