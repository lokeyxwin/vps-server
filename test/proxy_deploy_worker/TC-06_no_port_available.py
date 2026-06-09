"""
========================================================================
TC-06 端口候选池空 → failed(no_port_available) (spec §3 步骤 3, §7)

故事:
  挑端口时 compute_available_ports 返回空集 → _pick_port 返 None
  → process_task 标 task.status=failed + last_error_code='no_port_available'
  → vps.stage 保持 running (已抢机, 等维修工人)
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPTask, TaskStatus, VPSRecord, VPSStage
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


class TestNoPortAvailable(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self._patches = [
            patch(
                "workers.proxy_deploy_worker.session_scope",
                make_fake_session_scope(self.Session),
            ),
            patch(
                "workers.proxy_deploy_worker.VPSSession",
                make_fake_vps_session_cls(),
            ),
            # 模拟端口全占满 (整个 1024-65535 都被监听)
            patch(
                "workers.proxy_deploy_worker.get_used_ports",
                return_value=set(range(1024, 65536)),
            ),
        ]
        for p in self._patches:
            p.start()

        with self.Session() as s:
            self.ip = insert_ip(s)
            self.task = insert_ip_task(s, self.ip.id)
            self.vps = insert_vps(s, ip="10.0.0.1")
            s.commit()
            self.task_id = self.task.id
            self.vps_id = self.vps.id

        self.worker = ProxyDeployWorker()
        self.result = self.worker.process_task(self.task_id)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    def test_tc06a_returns_failed_no_port_available(self):
        self.assertEqual(self.result["status"], "failed")
        self.assertEqual(self.result["last_error_code"], "no_port_available")

    def test_tc06b_task_failed_terminal(self):
        with self.Session() as s:
            task = s.get(IPTask, self.task_id)
            self.assertEqual(task.status, TaskStatus.FAILED)
            self.assertEqual(task.last_error_code, "no_port_available")
            self.assertEqual(task.retry_count, 0)

    def test_tc06c_vps_stage_remains_running(self):
        """已抢机, vps.stage 保持 running 等人介入 (ADR-0005 §3)."""
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.stage, VPSStage.RUNNING)


if __name__ == "__main__":
    unittest.main()
