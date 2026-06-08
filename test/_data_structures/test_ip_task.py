"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-SCHEMA-IPTASK IPTask 异步任务表 (T-11)

故事:
  T-11 新增 ip_task 表 (1:1 对称 VPSTask)。IPProbeWorker 入库 IP 时建
  一条 pending (vps_id=NULL "谁配的谁写"); ProxyDeployWorker 扫表领,
  挑到 VPS 后回填 vps_id。

  spec test/ip_probe_worker/spec.md v2 §G 锚点:
    - ip_id NOT NULL + FK -> ip_record.id
    - vps_id nullable=True + FK -> vps_record.id
    - TaskStatus 复用 (PENDING / IN_PROGRESS / DONE / FAILED)
    - 索引 ix_ip_task_status_next_run (status, next_run_at)
    - 索引 ix_ip_task_ip_status (ip_id, status)

测试矩阵 (12 TC):
  TC-01 status 默认 "pending"
  TC-02 retry_count 默认 0
  TC-03 next_run_at server_default = now()
  TC-04 worker_id="" / locked_until / completed_at 默认 NULL
        last_error_code / last_error_msg 默认空串
  TC-05 vps_id 默认 NULL (谁配的谁写)
  TC-06 ip_id 必填 -> 不传时 IntegrityError
  TC-07 ip_id FK 违反 (引用不存在的 ip_record) 报错
  TC-08 vps_id 回填后能 update 成功 (ProxyDeployWorker 行为)
  TC-09 happy path: pending -> in_progress -> done + completed_at 非空
  TC-10 两个索引建好 (ix_ip_task_status_next_run + ix_ip_task_ip_status)
  TC-11 TaskStatus 4 值跟 VPSTask 共用 (复用, 非新加)
  TC-12 __repr__ 含 id/ip/vps/status/retry, 不含 last_error_msg

不应发生:
  - 任何写库动作落到 db/vps_server.db (用独立 in-memory engine)
========================================================================
"""

from __future__ import annotations

import time
import unittest

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import IPRecord, IPTask, TaskStatus, VPSRecord


def _make_in_memory_engine(enable_fk: bool = True):
    """每个 TestCase 自带 in-memory SQLite engine + sessionmaker。

    enable_fk=True 时开启 SQLite FK 约束(默认关闭),TC-07 需要它。
    建 ip_record + vps_record + ip_task 三表(FK 依赖)。
    """
    engine = create_engine("sqlite:///:memory:")
    if enable_fk:
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(
        engine,
        tables=[
            VPSRecord.__table__,
            IPRecord.__table__,
            IPTask.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _new_ip(session, egress_ip: str = "1.1.1.1") -> IPRecord:
    rec = IPRecord.from_form(
        entry_host="proxy.example.com",
        entry_port=1080,
        username="u",
        password="p",
        protocol="socks5",
        egress_ip=egress_ip,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return rec


def _new_vps(session, ip: str = "10.0.0.1") -> VPSRecord:
    rec = VPSRecord.from_form(ip=ip, username="root", password="x", port=22)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return rec


class TestIPTaskSchema(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

    def tearDown(self):
        self.engine.dispose()

    # ---------- TC-01 ----------
    def test_tc01_status_default_is_pending(self):
        with self.Session() as s:
            ip = _new_ip(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertEqual(task.status, TaskStatus.PENDING)
            self.assertEqual(task.status, "pending")

    # ---------- TC-02 ----------
    def test_tc02_retry_count_default_zero(self):
        with self.Session() as s:
            ip = _new_ip(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertEqual(task.retry_count, 0)

    # ---------- TC-03 ----------
    def test_tc03_next_run_at_server_default_now(self):
        with self.Session() as s:
            ip = _new_ip(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertIsNotNone(task.next_run_at)

    # ---------- TC-04 ----------
    def test_tc04_worker_id_empty_lock_completed_nullable(self):
        with self.Session() as s:
            ip = _new_ip(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertEqual(task.worker_id, "")
            self.assertIsNone(task.locked_until)
            self.assertIsNone(task.completed_at)
            self.assertEqual(task.last_error_code, "")
            self.assertEqual(task.last_error_msg, "")

    # ---------- TC-05 ----------
    def test_tc05_vps_id_default_null(self):
        """IPProbeWorker 建任务时不传 vps_id, DB 落 NULL (谁配的谁写)。"""
        with self.Session() as s:
            ip = _new_ip(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            self.assertIsNone(task.vps_id)

    # ---------- TC-06 ----------
    def test_tc06_ip_id_required(self):
        """ip_id 必填: 不传时 IntegrityError。"""
        with self.Session() as s:
            task = IPTask()
            s.add(task)
            with self.assertRaises(IntegrityError):
                s.commit()

    # ---------- TC-07 ----------
    def test_tc07_fk_violation_when_ip_id_missing(self):
        """ip_id 指向不存在的 ip_record 时 FK 报错。"""
        with self.Session() as s:
            task = IPTask(ip_id=99999, status=TaskStatus.PENDING)
            s.add(task)
            with self.assertRaises(IntegrityError):
                s.commit()

    # ---------- TC-08 ----------
    def test_tc08_vps_id_backfill_persists(self):
        """ProxyDeployWorker 挑到 VPS 后回填 vps_id, update 成功。"""
        with self.Session() as s:
            ip = _new_ip(s)
            vps = _new_vps(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            task_id = task.id
            vps_id = vps.id

        with self.Session() as s:
            t = s.get(IPTask, task_id)
            self.assertIsNone(t.vps_id)
            t.vps_id = vps_id
            s.commit()

        with self.Session() as s:
            t = s.get(IPTask, task_id)
            self.assertEqual(t.vps_id, vps_id)

    # ---------- TC-09 ----------
    def test_tc09_happy_path_pending_to_done(self):
        from datetime import datetime, timezone

        with self.Session() as s:
            ip = _new_ip(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            task_id = task.id

        with self.Session() as s:
            t = s.get(IPTask, task_id)
            self.assertEqual(t.status, TaskStatus.PENDING)
            t.status = TaskStatus.IN_PROGRESS
            t.worker_id = "proxy_deploy_worker_1"
            s.commit()

        with self.Session() as s:
            t = s.get(IPTask, task_id)
            self.assertEqual(t.status, TaskStatus.IN_PROGRESS)
            t.status = TaskStatus.DONE
            t.completed_at = datetime.now(timezone.utc)
            s.commit()

        with self.Session() as s:
            t = s.get(IPTask, task_id)
            self.assertEqual(t.status, TaskStatus.DONE)
            self.assertIsNotNone(t.completed_at)

    # ---------- TC-10 ----------
    def test_tc10_two_indexes_created(self):
        idx_map = {
            idx["name"]: idx["column_names"]
            for idx in inspect(self.engine).get_indexes("ip_task")
        }
        self.assertIn("ix_ip_task_status_next_run", idx_map)
        self.assertIn("ix_ip_task_ip_status", idx_map)
        self.assertEqual(
            idx_map["ix_ip_task_status_next_run"],
            ["status", "next_run_at"],
        )
        self.assertEqual(
            idx_map["ix_ip_task_ip_status"],
            ["ip_id", "status"],
        )

    # ---------- TC-11 ----------
    def test_tc11_task_status_shared_with_vps_task(self):
        """TaskStatus 4 值跟 VPSTask 共用 (复用, 非新加)。"""
        self.assertEqual(TaskStatus.PENDING, "pending")
        self.assertEqual(TaskStatus.IN_PROGRESS, "in_progress")
        self.assertEqual(TaskStatus.DONE, "done")
        self.assertEqual(TaskStatus.FAILED, "failed")
        # 4 值都能赋给 IPTask.status
        with self.Session() as s:
            ip = _new_ip(s)
            tasks = [
                IPTask(ip_id=ip.id, status=TaskStatus.PENDING),
                IPTask(ip_id=ip.id, status=TaskStatus.IN_PROGRESS),
                IPTask(ip_id=ip.id, status=TaskStatus.DONE),
                IPTask(ip_id=ip.id, status=TaskStatus.FAILED),
            ]
            s.add_all(tasks)
            s.commit()

        with self.Session() as s:
            stored = sorted(t.status for t in s.query(IPTask).all())
            self.assertEqual(
                stored, ["done", "failed", "in_progress", "pending"],
            )

    # ---------- TC-12 ----------
    def test_tc12_repr_exposes_progress_without_error_msg(self):
        with self.Session() as s:
            ip = _new_ip(s)
            vps = _new_vps(s)
            task = IPTask(
                ip_id=ip.id,
                vps_id=vps.id,
                status=TaskStatus.IN_PROGRESS,
                retry_count=2,
                last_error_msg="敏感长错误信息,不应进 repr",
            )
            s.add(task)
            s.commit()
            s.refresh(task)
            task_id = task.id
            ip_id = ip.id
            vps_id = vps.id
            r = repr(task)

        self.assertIn(f"id={task_id}", r)
        self.assertIn(f"ip={ip_id}", r)
        self.assertIn(f"vps={vps_id}", r)
        self.assertIn("status=in_progress", r)
        self.assertIn("retry=2", r)
        self.assertNotIn("敏感长错误信息", r)
        self.assertNotIn("last_error_msg", r)

    def test_tc12b_repr_when_vps_id_null_shows_qmark(self):
        """vps_id 未回填(NULL)时 repr 显示 '?' 而不是空。"""
        with self.Session() as s:
            ip = _new_ip(s)
            task = IPTask(ip_id=ip.id)
            s.add(task)
            s.commit()
            s.refresh(task)
            r = repr(task)
        self.assertIn("vps=?", r)


if __name__ == "__main__":
    unittest.main()
