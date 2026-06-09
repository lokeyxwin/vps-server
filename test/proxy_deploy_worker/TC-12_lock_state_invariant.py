"""
========================================================================
TC-12 vps.stage 锁状态机不变量 (ADR-0005 §1/§3, spec §9 不变量 #2)

故事:
  - 抢机后 SSH 之前 → vps.stage='running'
  - 成功完工         → vps.stage='connectable' (释放回池子)
  - 任何失败路径     → vps.stage 保持 'running' (锁住等维修)

测试矩阵:
  TC-12-a 抢机后 (process_task 走完) 成功路径: stage='connectable'
  TC-12-b 内 ping 失败: stage 保持 'running'
  TC-12-c apply 失败: stage 保持 'running'
  TC-12-d firewall 失败: stage 保持 'running'
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import VPSRecord, VPSStage
from toolbox.firewall import FirewallOpenError
from workers.proxy_deploy_worker import ProxyDeployWorker
from xray.config import ConfigValidationError

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


def _base_patches(Session, fake_xm):
    """所有 case 共用的 patch 起手式."""
    return [
        patch(
            "workers.proxy_deploy_worker.session_scope",
            make_fake_session_scope(Session),
        ),
        patch(
            "workers.proxy_deploy_worker.VPSSession",
            make_fake_vps_session_cls(),
        ),
        patch(
            "workers.proxy_deploy_worker.XrayManager",
            return_value=fake_xm,
        ),
        patch(
            "workers.proxy_deploy_worker.get_used_ports",
            return_value=set(),
        ),
    ]


class TestLockStateInvariant(unittest.TestCase):

    def _setup_data(self, Session):
        with Session() as s:
            ip = insert_ip(s)
            task = insert_ip_task(s, ip.id)
            vps = insert_vps(s, ip="10.0.0.1")
            s.commit()
            return task.id, vps.id

    def _run_with_patches(self, patches, task_id):
        for p in patches:
            p.start()
        try:
            return ProxyDeployWorker().process_task(task_id)
        finally:
            for p in patches:
                p.stop()

    # ---------- TC-12-a ----------
    def test_tc12a_success_releases_lock(self):
        engine, Session = make_in_memory_engine()
        try:
            task_id, vps_id = self._setup_data(Session)
            fake_xm = MagicMock()
            fake_xm.apply_proxy_binding.return_value = {"_baked": "cfg"}
            patches = _base_patches(Session, fake_xm) + [
                patch("workers.proxy_deploy_worker.firewall.open_tcp_port_range",
                      return_value="firewalld"),
                patch("workers.proxy_deploy_worker.test_internal",
                      return_value=(True, "1.1.1.1")),
                patch("workers.proxy_deploy_worker.test_external",
                      return_value=True),
            ]
            self._run_with_patches(patches, task_id)
            with Session() as s:
                vps = s.get(VPSRecord, vps_id)
                self.assertEqual(vps.stage, VPSStage.CONNECTABLE)
        finally:
            engine.dispose()

    # ---------- TC-12-b ----------
    def test_tc12b_inner_ping_failed_keeps_lock(self):
        engine, Session = make_in_memory_engine()
        try:
            task_id, vps_id = self._setup_data(Session)
            fake_xm = MagicMock()
            fake_xm.apply_proxy_binding.return_value = {"_baked": "cfg"}
            patches = _base_patches(Session, fake_xm) + [
                patch("workers.proxy_deploy_worker.firewall.open_tcp_port_range",
                      return_value="firewalld"),
                patch("workers.proxy_deploy_worker.test_internal",
                      return_value=(False, "")),
                patch("workers.proxy_deploy_worker.test_external",
                      return_value=True),
            ]
            self._run_with_patches(patches, task_id)
            with Session() as s:
                vps = s.get(VPSRecord, vps_id)
                self.assertEqual(vps.stage, VPSStage.RUNNING)
        finally:
            engine.dispose()

    # ---------- TC-12-c ----------
    def test_tc12c_apply_failed_keeps_lock(self):
        engine, Session = make_in_memory_engine()
        try:
            task_id, vps_id = self._setup_data(Session)
            fake_xm = MagicMock()
            fake_xm.apply_proxy_binding.side_effect = ConfigValidationError("x")
            patches = _base_patches(Session, fake_xm) + [
                patch("workers.proxy_deploy_worker.firewall.open_tcp_port_range",
                      return_value="firewalld"),
                patch("workers.proxy_deploy_worker.test_internal",
                      return_value=(True, "")),
                patch("workers.proxy_deploy_worker.test_external",
                      return_value=True),
            ]
            self._run_with_patches(patches, task_id)
            with Session() as s:
                vps = s.get(VPSRecord, vps_id)
                self.assertEqual(vps.stage, VPSStage.RUNNING)
        finally:
            engine.dispose()

    # ---------- TC-12-d ----------
    def test_tc12d_firewall_failed_keeps_lock(self):
        engine, Session = make_in_memory_engine()
        try:
            task_id, vps_id = self._setup_data(Session)
            fake_xm = MagicMock()
            fake_xm.apply_proxy_binding.return_value = {"_baked": "cfg"}
            patches = _base_patches(Session, fake_xm) + [
                patch("workers.proxy_deploy_worker.firewall.open_tcp_port_range",
                      side_effect=FirewallOpenError("x")),
                patch("workers.proxy_deploy_worker.test_internal",
                      return_value=(True, "")),
                patch("workers.proxy_deploy_worker.test_external",
                      return_value=True),
            ]
            self._run_with_patches(patches, task_id)
            with Session() as s:
                vps = s.get(VPSRecord, vps_id)
                self.assertEqual(vps.stage, VPSStage.RUNNING)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
