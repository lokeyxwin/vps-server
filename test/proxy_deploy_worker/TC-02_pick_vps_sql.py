"""
========================================================================
TC-02 挑机 SQL: 4 条件硬过滤 + 最闲优先 (spec §4)

故事:
  ProxyDeployWorker._pick_vps_and_lock 走 SQL 挑 VPS, 4 条件必须全满足:
    stage='connectable' AND xray_version!='' AND is_active=1
    AND used_port_count < MAX_PORTS_PER_VPS (=3)
  排序 ORDER BY used_port_count ASC (最闲优先), 同档 RANDOM().

测试矩阵:
  TC-02-a stage='running' 的不挑 (锁占用)
  TC-02-b xray_version='' 的不挑 (没装机)
  TC-02-c is_active=0 的不挑 (过期)
  TC-02-d used_port_count >= MAX_PORTS_PER_VPS 的不挑 (满)
  TC-02-e 最闲优先: 多台合格时挑 used_port_count 最小的
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from db.models import VPSRecord, VPSStage
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_in_memory_engine,
)


class TestPickVpsSql(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self._patcher = patch(
            "workers.proxy_deploy_worker.session_scope",
            make_fake_session_scope(self.Session),
        )
        self._patcher.start()

        with self.Session() as s:
            self.ip = insert_ip(s)
            self.task = insert_ip_task(s, self.ip.id)
            s.commit()
            self.task_id = self.task.id

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc02a_running_excluded(self):
        with self.Session() as s:
            insert_vps(s, ip="10.0.0.1", stage=VPSStage.RUNNING)
            s.commit()
        pick = ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        self.assertIsNone(pick, "stage=running 应被排除")

    def test_tc02b_no_xray_excluded(self):
        with self.Session() as s:
            insert_vps(s, ip="10.0.0.2", xray_version="")
            s.commit()
        pick = ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        self.assertIsNone(pick, "xray_version='' 应被排除")

    def test_tc02c_inactive_excluded(self):
        with self.Session() as s:
            insert_vps(s, ip="10.0.0.3", is_active=0)
            s.commit()
        pick = ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        self.assertIsNone(pick, "is_active=0 应被排除")

    def test_tc02d_full_excluded(self):
        from config import MAX_PORTS_PER_VPS
        with self.Session() as s:
            insert_vps(s, ip="10.0.0.4", used_port_count=MAX_PORTS_PER_VPS)
            s.commit()
        pick = ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        self.assertIsNone(pick, "used_port_count==MAX 应被排除")

    def test_tc02e_most_idle_first(self):
        """3 台合格机, used_port_count 分别 2/0/1; 应挑 used_port_count=0 那台."""
        with self.Session() as s:
            insert_vps(s, ip="10.0.0.A", used_port_count=2)
            insert_vps(s, ip="10.0.0.B", used_port_count=0)  # 最闲
            insert_vps(s, ip="10.0.0.C", used_port_count=1)
            s.commit()
        pick = ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        self.assertIsNotNone(pick)
        self.assertEqual(pick["ip"], "10.0.0.B")


if __name__ == "__main__":
    unittest.main()
