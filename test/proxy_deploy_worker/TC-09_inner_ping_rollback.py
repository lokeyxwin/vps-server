"""
========================================================================
TC-09 内 ping 不通 → rollback 三件套 + failed(inner_ping_failed)
(spec §3 步骤 5a, §7, §9 不变量 #3)

故事:
  apply_proxy_binding 成功 + firewall 放行成功 → 但内 ping 不通
  → 立刻调 rollback_proxy_binding(vps_port, last_config) 拆三件套
  → task.status=failed + last_error_code='inner_ping_failed'
  → vps.stage 保持 running (失败不释放)
  → proxy_record 不入库, ip 不改 status, used_port_count 不 +1
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import (
    IPRecord,
    IPStatus,
    IPTask,
    ProxyRecord,
    TaskStatus,
    VPSRecord,
    VPSStage,
)
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


class TestInnerPingRollback(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()

        self.fake_xm = MagicMock()
        self.fake_xm.apply_proxy_binding.return_value = {"_baked": "cfg"}

        self._patches = [
            patch(
                "workers.proxy_deploy_worker.session_scope",
                make_fake_session_scope(self.Session),
            ),
            patch(
                "workers.proxy_deploy_worker.VPSSession",
                make_fake_vps_session_cls(),
            ),
            patch(
                "workers.proxy_deploy_worker.XrayManager",
                return_value=self.fake_xm,
            ),
            patch(
                "workers.proxy_deploy_worker.get_used_ports",
                return_value=set(),
            ),
            patch(
                "workers.proxy_deploy_worker.firewall.open_tcp_port_range",
                return_value="firewalld",
            ),
            # 内 ping 不通
            patch(
                "workers.proxy_deploy_worker.test_internal",
                return_value=(False, ""),
            ),
            patch(
                "workers.proxy_deploy_worker.test_external",
                return_value=True,
            ),
        ]
        for p in self._patches:
            p.start()

        with self.Session() as s:
            self.ip = insert_ip(s)
            self.task = insert_ip_task(s, self.ip.id)
            self.vps = insert_vps(s)
            s.commit()
            self.task_id = self.task.id
            self.vps_id = self.vps.id
            self.ip_id = self.ip.id

        self.worker = ProxyDeployWorker()
        self.result = self.worker.process_task(self.task_id)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    def test_tc09a_returns_failed_inner_ping(self):
        self.assertEqual(self.result["status"], "failed")
        self.assertEqual(self.result["last_error_code"], "inner_ping_failed")

    def test_tc09b_rollback_called_with_last_config(self):
        self.fake_xm.rollback_proxy_binding.assert_called_once()
        args, _ = self.fake_xm.rollback_proxy_binding.call_args
        self.assertEqual(args[1], {"_baked": "cfg"})

    def test_tc09c_no_proxy_record_inserted(self):
        with self.Session() as s:
            self.assertEqual(s.query(ProxyRecord).count(), 0)

    def test_tc09d_ip_status_unchanged_usable(self):
        with self.Session() as s:
            ip = s.get(IPRecord, self.ip_id)
            self.assertEqual(ip.status, IPStatus.USABLE)

    def test_tc09e_vps_count_not_incremented(self):
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.used_port_count, 0)

    def test_tc09f_vps_stage_remains_running(self):
        """失败不释放锁 (spec §9 不变量 #2, ADR-0005 §3)."""
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.stage, VPSStage.RUNNING)

    def test_tc09g_task_failed_terminal(self):
        with self.Session() as s:
            t = s.get(IPTask, self.task_id)
            self.assertEqual(t.status, TaskStatus.FAILED)
            self.assertEqual(t.last_error_code, "inner_ping_failed")


if __name__ == "__main__":
    unittest.main()
