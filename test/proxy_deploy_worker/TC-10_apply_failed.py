"""
========================================================================
TC-10 apply_proxy_binding 抛错 → failed(apply_binding_failed) (spec §7)

故事:
  XrayManager.apply_proxy_binding 在 add/upload/validate/reload 任一阶段抛 xray 异常
  → 工人捕获 → task.status=failed + last_error_code='apply_binding_failed'
  → vps.stage 保持 running
  → apply 抛错时 last_config 仍是 None, rollback 不会被调用
    (apply 内部失败位置不确定, 留给运维核查; spec §7 注: 已 rollback 含义是"尽力而为")
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPTask, ProxyRecord, TaskStatus, VPSRecord, VPSStage
from workers.proxy_deploy_worker import ProxyDeployWorker
from xray.config import ConfigValidationError

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_ss_probe_cls,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


class TestApplyFailed(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()

        self.fake_xm = MagicMock()
        self.fake_xm.apply_proxy_binding.side_effect = ConfigValidationError(
            "fake xray config validate fail"
        )

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
            # apply 先抛错, probes 走不到; 留默认即可
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

    def test_tc10a_returns_failed_apply_binding(self):
        self.assertEqual(self.result["status"], "failed")
        self.assertEqual(self.result["last_error_code"], "apply_binding_failed")

    def test_tc10b_task_failed_terminal(self):
        with self.Session() as s:
            t = s.get(IPTask, self.task_id)
            self.assertEqual(t.status, TaskStatus.FAILED)
            self.assertEqual(t.last_error_code, "apply_binding_failed")
            self.assertIn("ConfigValidationError", t.last_error_msg)

    def test_tc10c_vps_stage_remains_running(self):
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.stage, VPSStage.RUNNING)

    def test_tc10d_no_proxy_record_inserted(self):
        with self.Session() as s:
            self.assertEqual(s.query(ProxyRecord).count(), 0)

    def test_tc10e_rollback_not_called_when_apply_raises(self):
        """apply 抛错时 last_config 还是 None, rollback 不被调."""
        self.fake_xm.rollback_proxy_binding.assert_not_called()


if __name__ == "__main__":
    unittest.main()
