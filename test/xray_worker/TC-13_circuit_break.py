"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-13 熔断: retry_count 累到 5 → status=FAILED (spec v5.1 §7)

故事:
  spec v5 原表写 'circuit_broken' 是笔误 (db TaskStatus 只 4 值, v4 拍板).
  v5.1 修订后, "熔断"语义靠 retry_count >= MAX_RETRY_COUNT (=5) 升 FAILED.
  本测验证: retry_count=4 状态进 _handle_retriable → retry_count++=5 →
  立即 FAILED, 沿用最后一次 last_error_code.

测试矩阵:
  TC-13-a retry_count=4 + _handle_retriable → status=FAILED, retry_count=5,
          locked_until=NULL, worker_id=""
  TC-13-b 不可重试场景 (_mark_failed) → status=FAILED 直接, 不动 retry_count
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import TaskStatus, VPSRecord, VPSTask
from workers.xray_worker import XrayWorker


def _make_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(
        engine, tables=[VPSRecord.__table__, VPSTask.__table__],
    )
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


class TestCircuitBreak(unittest.TestCase):

    def setUp(self):
        self.engine, self.Session = _make_engine()

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

        self._patcher = patch("workers.xray_worker.session_scope", _fake_scope)
        self._patcher.start()

        with self.Session() as s:
            vps = VPSRecord.from_form(ip="10.0.0.7", username="root", password="p", port=22)
            s.add(vps); s.commit()
            self.vps_id = vps.id

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc13a_retry_count_4_to_5_marks_failed(self):
        with self.Session() as s:
            t = VPSTask(
                vps_id=self.vps_id,
                status=TaskStatus.IN_PROGRESS,
                retry_count=4,
                worker_id="someone",
            )
            s.add(t); s.commit()
            task_id = t.id

        XrayWorker._handle_retriable(task_id, "install_failed", "last error")

        with self.Session() as s:
            t = s.get(VPSTask, task_id)
        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.retry_count, 5)
        self.assertEqual(t.last_error_code, "install_failed")
        self.assertIsNone(t.locked_until)
        self.assertEqual(t.worker_id, "")

    def test_tc13b_mark_failed_directly_unretriable(self):
        with self.Session() as s:
            t = VPSTask(
                vps_id=self.vps_id,
                status=TaskStatus.IN_PROGRESS,
                retry_count=0,
                worker_id="someone",
            )
            s.add(t); s.commit()
            task_id = t.id

        XrayWorker._mark_failed(task_id, "auth_denied", "wrong password")

        with self.Session() as s:
            t = s.get(VPSTask, task_id)
        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.retry_count, 0)
        self.assertEqual(t.last_error_code, "auth_denied")
        self.assertIsNone(t.locked_until)
        self.assertEqual(t.worker_id, "")


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
