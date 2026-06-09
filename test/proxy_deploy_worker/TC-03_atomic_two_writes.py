"""
========================================================================
TC-03 抢机两写同事务 (spec §4, §9 不变量 #1)

故事:
  _pick_vps_and_lock 抢到 VPS 后, 同一个 session_scope (= 同一 commit) 里:
    UPDATE vps_record SET stage='running' WHERE id=<vps.id>
    UPDATE ip_task   SET vps_id=<vps.id>  WHERE id=<task.id>
  两写必须在同一 commit, 不允许拆开(否则锁状态漂移).

测试:
  TC-03-a 抢机后 vps.stage='running' 落盘
  TC-03-b 抢机后 ip_task.vps_id=vps.id 落盘
  TC-03-c 两个字段都在 _pick_vps_and_lock 单次调用后可见 (同事务保证)
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from db.models import IPTask, VPSRecord, VPSStage
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_in_memory_engine,
)


class TestAtomicTwoWrites(unittest.TestCase):
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
            self.vps = insert_vps(s, ip="10.0.0.1")
            s.commit()
            self.task_id = self.task.id
            self.vps_id = self.vps.id

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc03a_vps_stage_running_after_pick(self):
        pick = ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        self.assertIsNotNone(pick)
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.stage, VPSStage.RUNNING)

    def test_tc03b_task_vps_id_filled(self):
        ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        with self.Session() as s:
            task = s.get(IPTask, self.task_id)
            self.assertEqual(task.vps_id, self.vps_id)

    def test_tc03c_both_visible_after_single_call(self):
        """单次 _pick_vps_and_lock 调用后, 两写同时可见 = 同事务."""
        ProxyDeployWorker._pick_vps_and_lock(self.task_id)
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            task = s.get(IPTask, self.task_id)
            self.assertEqual(vps.stage, VPSStage.RUNNING)
            self.assertEqual(task.vps_id, self.vps_id)


if __name__ == "__main__":
    unittest.main()
