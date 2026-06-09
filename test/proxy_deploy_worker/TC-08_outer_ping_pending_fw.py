"""
========================================================================
TC-08 半成功: 内通 + 外不通 → done + pending_fw (spec §3 步骤 5b, §6, §8)

故事:
  内 ping 通(代理本身配好了) 但外 ping 不通(云厂商安全策略组没放行)
  → 不算工人失败, 走 done 路径
  → proxy_record.status = pending_fw (等用户去面板放行)
  → vps.used_port_count 仍 +1 (端口实际占了)
  → vps.stage 仍释放回 connectable
  → ip_task.status = done

边界(spec §8): 外部安全组不归本工人管.
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPTask, ProxyRecord, ProxyStatus, TaskStatus, VPSRecord, VPSStage
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


class TestPendingFw(unittest.TestCase):
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
            # 内 ping 通
            patch(
                "workers.proxy_deploy_worker.test_internal",
                return_value=(True, "203.0.113.42"),
            ),
            # 外 ping 不通
            patch(
                "workers.proxy_deploy_worker.test_external",
                return_value=False,
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

    def test_tc08a_returns_done_outer_false(self):
        self.assertEqual(self.result["status"], "done")
        self.assertFalse(self.result["outer_ping_ok"])

    def test_tc08b_proxy_status_pending_fw(self):
        with self.Session() as s:
            p = s.query(ProxyRecord).first()
            self.assertEqual(p.status, ProxyStatus.PENDING_FW)

    def test_tc08c_used_port_count_still_plus_one(self):
        """半成功端口实际占了, used_port_count 也要 +1."""
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.used_port_count, 1)

    def test_tc08d_vps_stage_released(self):
        """半成功仍算工人完工, vps.stage 释放回 connectable."""
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.stage, VPSStage.CONNECTABLE)

    def test_tc08e_task_status_done_not_failed(self):
        with self.Session() as s:
            t = s.get(IPTask, self.task_id)
            self.assertEqual(t.status, TaskStatus.DONE)


if __name__ == "__main__":
    unittest.main()
