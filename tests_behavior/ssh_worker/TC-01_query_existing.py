"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-01 SSHWorker._查重 行为单测 (spec v4)

故事:
  SSHWorker 路线 A: DB 已有这台 VPS 时不打 SSH, 直接查表打包返回.
  _查重 负责 SQL SELECT + 组装返回 dict.

  spec v4 关键变化:
    - 删 stage_message 字段 (错误住任务表)
    - "活跃" task 集合: [PENDING, IN_PROGRESS] (v4 没 PENDING_RETRY)
    - 即便没活跃 task, 最近一条 task 的 last_error_* 也带回

测试矩阵 (7 TC):
  TC-01-a DB 空 → 返回 None
  TC-01-b 有 VPSRecord 无 task → 返回 dict 含完整 vps 字段
  TC-01-c VPSRecord + VPSTask(PENDING) → active_task 字段填充正确
  TC-01-d VPSRecord + VPSTask(DONE) → active_task = None
  TC-01-e VPSRecord + VPSTask(FAILED) → active_task = None, last_error_* 取自最近 failed task
  TC-01-f VPSRecord + VPSTask(PENDING + IN_PROGRESS) → 返回最近的 (created_at desc)
  TC-01-g ⭐ 防回退: 返回 dict 不含 stage_message 字段
========================================================================
"""

from __future__ import annotations

import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import TaskStatus, VPSRecord, VPSStage, VPSTask
from workers.ssh_worker import SSHWorker


def _make_in_memory_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(
        engine, tables=[VPSRecord.__table__, VPSTask.__table__]
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


class TestSSHWorkerQueryExisting(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

        # patch SSHWorker 用的 session_scope 指向本测试的 in-memory engine
        @contextmanager
        def _fake_scope():
            s = self.Session()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        self._patcher = patch(
            "workers.ssh_worker.session_scope", _fake_scope
        )
        self._patcher.start()
        self.worker = SSHWorker()

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def _insert_vps(self, ip="1.2.3.4") -> int:
        with self.Session() as s:
            rec = VPSRecord.from_form(
                ip=ip, username="root", password="pass", port=22,
                os_name="CentOS Linux", os_version="7",
            )
            rec.stage = VPSStage.CONNECTABLE
            rec.xray_version = ""
            s.add(rec)
            s.commit()
            return rec.id

    def _insert_task(
        self, vps_id: int, status: str,
        last_error_code: str = "", last_error_msg: str = "",
    ) -> int:
        with self.Session() as s:
            task = VPSTask(
                vps_id=vps_id, status=status,
                last_error_code=last_error_code, last_error_msg=last_error_msg,
            )
            s.add(task)
            s.commit()
            return task.id

    # ---------- TC-01-a ----------
    def test_tc01a_empty_db_returns_none(self):
        self.assertIsNone(self.worker._查重("1.2.3.4"))

    # ---------- TC-01-b ----------
    def test_tc01b_hit_with_no_task(self):
        self._insert_vps("1.2.3.4")
        result = self.worker._查重("1.2.3.4")
        self.assertIsNotNone(result)
        # 完整字段
        self.assertEqual(result["ip"], "1.2.3.4")
        self.assertEqual(result["stage"], VPSStage.CONNECTABLE)
        self.assertEqual(result["xray_version"], "")
        self.assertEqual(result["os_name"], "CentOS Linux")
        self.assertEqual(result["os_version"], "7")
        self.assertEqual(result["is_active"], 1)
        self.assertIsNone(result["active_task"])
        self.assertIn("vps_id", result)
        self.assertEqual(result["last_error_code"], "")
        self.assertEqual(result["last_error_msg"], "")

    # ---------- TC-01-c ----------
    def test_tc01c_active_task_pending(self):
        vps_id = self._insert_vps("1.2.3.4")
        task_id = self._insert_task(
            vps_id, TaskStatus.PENDING,
            last_error_code="", last_error_msg="",
        )
        result = self.worker._查重("1.2.3.4")
        self.assertIsNotNone(result["active_task"])
        at = result["active_task"]
        self.assertEqual(at["task_id"], task_id)
        self.assertEqual(at["status"], TaskStatus.PENDING)
        self.assertEqual(at["retry_count"], 0)
        self.assertIn("next_run_at", at)
        self.assertEqual(at["last_error_code"], "")
        self.assertEqual(at["last_error_msg"], "")

    # ---------- TC-01-d ----------
    def test_tc01d_done_task_not_active(self):
        vps_id = self._insert_vps("1.2.3.4")
        self._insert_task(vps_id, TaskStatus.DONE)
        result = self.worker._查重("1.2.3.4")
        self.assertIsNone(result["active_task"])

    # ---------- TC-01-e ----------
    def test_tc01e_failed_task_not_active_but_last_error_passed_through(self):
        vps_id = self._insert_vps("1.2.3.4")
        self._insert_task(
            vps_id, TaskStatus.FAILED,
            last_error_code="install_xray_failed",
            last_error_msg="github.com 拉取超时",
        )
        result = self.worker._查重("1.2.3.4")
        # failed 不算活跃
        self.assertIsNone(result["active_task"])
        # 但最近 task 的错误信息要带回
        self.assertEqual(result["last_error_code"], "install_xray_failed")
        self.assertEqual(result["last_error_msg"], "github.com 拉取超时")

    # ---------- TC-01-f ----------
    def test_tc01f_two_active_returns_latest_by_created_at(self):
        vps_id = self._insert_vps("1.2.3.4")
        first_id = self._insert_task(vps_id, TaskStatus.PENDING)
        # SQLite CURRENT_TIMESTAMP 精度秒,确保 created_at 区分得开
        time.sleep(1.1)
        second_id = self._insert_task(vps_id, TaskStatus.IN_PROGRESS)
        result = self.worker._查重("1.2.3.4")
        self.assertIsNotNone(result["active_task"])
        # 最近一条是 IN_PROGRESS
        self.assertEqual(result["active_task"]["task_id"], second_id)
        self.assertEqual(result["active_task"]["status"], TaskStatus.IN_PROGRESS)
        # 确保不是返回 first_id
        self.assertNotEqual(result["active_task"]["task_id"], first_id)

    # ---------- TC-01-g 防回退 ----------
    def test_tc01g_regression_no_stage_message_field(self):
        """spec v4 删 stage_message: 返回 dict 不应有这个字段."""
        self._insert_vps("1.2.3.4")
        result = self.worker._查重("1.2.3.4")
        self.assertNotIn("stage_message", result)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：2026-06-07 (独立 in-memory SQLite + patch session_scope, 1.143s, 7 OK = 7/7)
# 偏差：无
# ========================================================================
