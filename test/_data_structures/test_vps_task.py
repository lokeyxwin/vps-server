"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-SCHEMA-VPSTASK VPSTask 异步任务表 + TaskStatus 4 值（v4）

故事:
  task/02 新增 `vps_task` 表 + 通用 `TaskStatus` 状态常量类。
  SSHWorker 入库 VPS 时建一条 pending 任务,XrayWorker 扫表领活儿。

  v4 拍板: TaskStatus 只 4 值 (PENDING / IN_PROGRESS / DONE / FAILED)。
  重试 / 退避 / 熔断细节藏在 XrayWorker 内部,不出 task 状态机:
    - 没有 PENDING_RETRY (工人在 in_progress 期间内部循环重试)
    - 没有 CIRCUIT_BROKEN (retry_count >= N 直接标 failed)

  字段保留 retry_count + next_run_at + worker_id + locked_until,
  但语义改为 XrayWorker 内部用 (自管退避 + 软锁),不驱动 status 状态机。

测试矩阵 (12 TC):
  TC-01 status 默认 "pending"
  TC-02 retry_count 默认 0
  TC-03 next_run_at server_default = now()
  TC-04 worker_id 默认空串 / locked_until / completed_at 默认 NULL
  TC-05 TaskStatus 只 4 个常量,旧 PENDING_RETRY / CIRCUIT_BROKEN 不存在
  TC-06 4 个 status 值都能 round-trip
  TC-07 vps_id FK 引用不存在的 vps_record 报错 (SQLite 需开 FK)
  TC-08 update task 后 updated_at 自动变 (onupdate=now)
  TC-09 happy path: pending → in_progress → done + completed_at 非空
  TC-10 两个索引建好 (ix_vps_task_status_next_run + ix_vps_task_vps_status)
  TC-11 ⚠️ 抢锁原子性 (@skip,等真机 PostgreSQL/MySQL 多连接环境再测)
  TC-12 __repr__ 含 id/vps_id/status/retry,不含 last_error_msg

不应发生:
  - 任何写库动作落到 db/vps_server.db (用独立 in-memory engine)
  - import services/*
========================================================================
"""

from __future__ import annotations

import time
import unittest

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import TaskStatus, VPSRecord, VPSTask


def _make_in_memory_engine(enable_fk: bool = True):
    """每个 TestCase 自带一个全新 in-memory SQLite engine + sessionmaker。

    enable_fk=True 时开启 SQLite 的 FK 约束(默认是关闭的,
    TC-07 需要开启才能验 FK 报错)。
    """
    engine = create_engine("sqlite:///:memory:")
    if enable_fk:
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    # 同时建 vps_record + vps_task (FK 依赖 vps_record 存在)
    Base.metadata.create_all(
        engine, tables=[VPSRecord.__table__, VPSTask.__table__]
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _new_vps(session, ip="10.0.0.1") -> VPSRecord:
    """测试夹具：建一台 VPS 给 task FK 用。"""
    rec = VPSRecord.from_form(ip=ip, username="root", password="x", port=22)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return rec


class TestVPSTaskSchema(unittest.TestCase):
    """vps_task / TaskStatus schema 12 条契约。"""

    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

    def tearDown(self):
        self.engine.dispose()

    # ---------- TC-01 ----------
    def test_tc01_status_default_is_pending(self):
        with self.Session() as s:
            vps = _new_vps(s)
            task = VPSTask(vps_id=vps.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertEqual(task.status, TaskStatus.PENDING)
            self.assertEqual(task.status, "pending")

    # ---------- TC-02 ----------
    def test_tc02_retry_count_default_zero(self):
        with self.Session() as s:
            vps = _new_vps(s)
            task = VPSTask(vps_id=vps.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertEqual(task.retry_count, 0)

    # ---------- TC-03 ----------
    def test_tc03_next_run_at_server_default_now(self):
        """next_run_at 走 server_default=func.now(),建表后非空。"""
        with self.Session() as s:
            vps = _new_vps(s)
            task = VPSTask(vps_id=vps.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertIsNotNone(task.next_run_at)

    # ---------- TC-04 ----------
    def test_tc04_worker_id_empty_and_lock_completed_nullable(self):
        with self.Session() as s:
            vps = _new_vps(s)
            task = VPSTask(vps_id=vps.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertEqual(task.worker_id, "")
            self.assertIsNone(task.locked_until)
            self.assertIsNone(task.completed_at)
            self.assertEqual(task.last_error_code, "")
            self.assertEqual(task.last_error_msg, "")

    # ---------- TC-05 ----------
    def test_tc05_task_status_has_only_four_constants(self):
        self.assertEqual(TaskStatus.PENDING, "pending")
        self.assertEqual(TaskStatus.IN_PROGRESS, "in_progress")
        self.assertEqual(TaskStatus.DONE, "done")
        self.assertEqual(TaskStatus.FAILED, "failed")

        # v4 已删除的旧值
        for ghost in ("PENDING_RETRY", "CIRCUIT_BROKEN"):
            self.assertFalse(
                hasattr(TaskStatus, ghost),
                f"TaskStatus 不应再有 {ghost} (v4 已删除)",
            )

    # ---------- TC-06 ----------
    def test_tc06_all_four_statuses_round_trip(self):
        with self.Session() as s:
            vps = _new_vps(s)
            tasks = [
                VPSTask(vps_id=vps.id, status=TaskStatus.PENDING),
                VPSTask(vps_id=vps.id, status=TaskStatus.IN_PROGRESS),
                VPSTask(vps_id=vps.id, status=TaskStatus.DONE),
                VPSTask(vps_id=vps.id, status=TaskStatus.FAILED),
            ]
            s.add_all(tasks)
            s.commit()

        with self.Session() as s:
            stored = sorted(t.status for t in s.query(VPSTask).all())
            self.assertEqual(
                stored,
                ["done", "failed", "in_progress", "pending"],
            )

    # ---------- TC-07 ----------
    def test_tc07_fk_violation_when_vps_id_missing(self):
        """vps_id 指向不存在的 vps_record 时,FK 约束应报错。"""
        with self.Session() as s:
            task = VPSTask(vps_id=99999, status=TaskStatus.PENDING)
            s.add(task)
            with self.assertRaises(IntegrityError):
                s.commit()

    # ---------- TC-08 ----------
    def test_tc08_updated_at_changes_on_update(self):
        with self.Session() as s:
            vps = _new_vps(s)
            task = VPSTask(vps_id=vps.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            initial_updated = task.updated_at

        # SQLite CURRENT_TIMESTAMP 精度到秒,睡 1.1s 保证差值看得见
        time.sleep(1.1)

        with self.Session() as s:
            t = s.query(VPSTask).first()
            t.status = TaskStatus.IN_PROGRESS
            s.commit()
            s.refresh(t)
            self.assertGreater(
                t.updated_at,
                initial_updated,
                "onupdate=now() 没生效",
            )

    # ---------- TC-09 ----------
    def test_tc09_happy_path_pending_to_in_progress_to_done(self):
        """pending → in_progress → done + completed_at 非空。"""
        from datetime import datetime, timezone

        with self.Session() as s:
            vps = _new_vps(s)
            task = VPSTask(vps_id=vps.id)
            s.add(task)
            s.commit()
            task_id = task.id

        # 抢锁
        with self.Session() as s:
            t = s.get(VPSTask, task_id)
            self.assertEqual(t.status, TaskStatus.PENDING)
            t.status = TaskStatus.IN_PROGRESS
            t.worker_id = "xray_worker_1"
            s.commit()

        # 干完
        with self.Session() as s:
            t = s.get(VPSTask, task_id)
            self.assertEqual(t.status, TaskStatus.IN_PROGRESS)
            t.status = TaskStatus.DONE
            t.completed_at = datetime.now(timezone.utc)
            s.commit()

        with self.Session() as s:
            t = s.get(VPSTask, task_id)
            self.assertEqual(t.status, TaskStatus.DONE)
            self.assertIsNotNone(t.completed_at)

    # ---------- TC-10 ----------
    def test_tc10_two_indexes_created(self):
        """两个复合索引建好,名字对得上。"""
        index_names = {
            idx["name"] for idx in inspect(self.engine).get_indexes("vps_task")
        }
        self.assertIn("ix_vps_task_status_next_run", index_names)
        self.assertIn("ix_vps_task_vps_status", index_names)

        # 验列组成
        all_idx = {
            idx["name"]: idx["column_names"]
            for idx in inspect(self.engine).get_indexes("vps_task")
        }
        self.assertEqual(
            all_idx["ix_vps_task_status_next_run"],
            ["status", "next_run_at"],
        )
        self.assertEqual(
            all_idx["ix_vps_task_vps_status"],
            ["vps_id", "status"],
        )

    # ---------- TC-11 ----------
    @unittest.skip("等真机 PostgreSQL/MySQL 多连接环境验证抢锁原子性")
    def test_tc11_lock_acquire_atomicity(self):
        """两个 worker 同时跑:
            UPDATE vps_task SET status='in_progress', worker_id=?
            WHERE id=? AND status='pending'
        只能 1 个 affected_rows=1,另一个 affected_rows=0。
        SQLite 单连接难测,留给真机环境。
        """
        pass  # noqa: PIE790  — skip 占位

    # ---------- TC-12 ----------
    def test_tc12_repr_exposes_progress_without_error_msg(self):
        with self.Session() as s:
            vps = _new_vps(s)
            task = VPSTask(
                vps_id=vps.id,
                status=TaskStatus.IN_PROGRESS,
                retry_count=2,
                last_error_msg="敏感长错误信息,不应进 repr",
            )
            s.add(task)
            s.commit()
            s.refresh(task)
            # 在 session 内取出值,避免 session 关闭后 ORM 懒加载报错
            task_id = task.id
            vps_id = vps.id
            r = repr(task)

        self.assertIn(f"id={task_id}", r)
        self.assertIn(f"vps={vps_id}", r)
        self.assertIn("status=in_progress", r)
        self.assertIn("retry=2", r)
        self.assertNotIn("敏感长错误信息", r)
        self.assertNotIn("last_error_msg", r)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：2026-06-07（独立 in-memory SQLite，1.134s，11 OK + 1 skip = 12/12）
# 偏差：
#   - TC-11 (抢锁原子性) 用 @unittest.skip 标记跳过。
#     原因: SQLite 单连接难模拟两个 worker 并发抢锁,留给真机 PG/MySQL
#     多连接环境验证。任务单 §测试用例 已注明该 TC 用 skip 计入"全过"。
#   - TC-07 (FK 违反) 在 SQLite 上需要显式开 PRAGMA foreign_keys=ON,
#     测试 fixture _make_in_memory_engine 已通过 connect 事件钩住开启。
#     生产 MySQL/PG 不需要这步。
# 待用户决策事项：无
# ========================================================================
