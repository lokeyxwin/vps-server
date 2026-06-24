"""
========================================================================
TC-11 防火墙放行失败 → rollback xray 三件套 + failed(firewall_open_failed)
(spec §7)

故事:
  apply 成功 (last_config 已拿到), 防火墙 open_tcp_port_range 抛 FirewallOpenError
  → 工人调 rollback_proxy_binding 撤回 xray 配置
  → task.status=failed + last_error_code='firewall_open_failed'
  → vps.stage 保持 running
  → proxy_record 不入库, used_port_count 不 +1
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPTask, ProxyRecord, TaskStatus, VPSRecord, VPSStage
from toolbox.firewall import FirewallOpenError
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_ss_probe_cls,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


class TestFirewallFailed(unittest.TestCase):
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
                side_effect=FirewallOpenError("fake firewall fail"),
            ),
            # firewall 先抛错, probes 走不到; 留默认即可
            patch(
                "workers.proxy_deploy_worker.ShadowsocksProbe",
                make_fake_ss_probe_cls(inner_ok=True, outer_ok=True),
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

        self.worker = ProxyDeployWorker()
        self.result = self.worker.process_task(self.task_id)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    def test_tc11a_returns_failed_firewall_open(self):
        self.assertEqual(self.result["status"], "failed")
        self.assertEqual(self.result["last_error_code"], "firewall_open_failed")

    def test_tc11b_rollback_called_with_last_config(self):
        self.fake_xm.rollback_proxy_binding.assert_called_once()
        args, _ = self.fake_xm.rollback_proxy_binding.call_args
        self.assertEqual(args[1], {"_baked": "cfg"})

    def test_tc11c_no_proxy_record_inserted(self):
        with self.Session() as s:
            self.assertEqual(s.query(ProxyRecord).count(), 0)

    def test_tc11d_vps_stage_remains_running_count_zero(self):
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.stage, VPSStage.RUNNING)
            self.assertEqual(vps.used_port_count, 0)

    def test_tc11e_task_failed_terminal(self):
        with self.Session() as s:
            t = s.get(IPTask, self.task_id)
            self.assertEqual(t.status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
