"""
========================================================================
TC-04 没机可挑 → failed(no_vps_capacity) 终态 (spec §7, ADR-0006 §5)

故事:
  挑机 SQL 0 行 → 任务直接 failed, last_error_code='no_vps_capacity'.
  关键约束:
    - 不进 pending_retry 循环, 不退避重试
    - 不动 vps.stage (没抢到机, 没机可动)
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from db.models import IPTask, TaskStatus
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    make_fake_session_scope,
    make_in_memory_engine,
)


class TestNoVpsCapacity(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self._patcher = patch(
            "workers.proxy_deploy_worker.session_scope",
            make_fake_session_scope(self.Session),
        )
        self._patcher.start()

        # 故意不插任何 VPS — 池子空
        with self.Session() as s:
            self.ip = insert_ip(s)
            self.task = insert_ip_task(s, self.ip.id)
            s.commit()
            self.task_id = self.task.id

        self.worker = ProxyDeployWorker()
        self.result = self.worker.process_task(self.task_id)

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc04a_returns_failed_no_vps_capacity(self):
        self.assertEqual(self.result["status"], "failed")
        self.assertEqual(self.result["last_error_code"], "no_vps_capacity")

    def test_tc04b_task_status_failed_terminal(self):
        with self.Session() as s:
            task = s.get(IPTask, self.task_id)
            self.assertEqual(task.status, TaskStatus.FAILED)
            self.assertEqual(task.last_error_code, "no_vps_capacity")
            # 没退避: 没回 PENDING, retry_count 没增
            self.assertEqual(task.retry_count, 0)
            self.assertIsNone(task.locked_until)

    def test_tc04c_task_vps_id_remains_null(self):
        """没抢机, task.vps_id 仍是 NULL."""
        with self.Session() as s:
            task = s.get(IPTask, self.task_id)
            self.assertIsNone(task.vps_id)


if __name__ == "__main__":
    unittest.main()
