"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-03 SSHWorker._入库派任务 行为单测 (spec v4)

故事:
  SSHWorker 路线 B 步骤 ④: 写 vps_record + 建 vps_task 入库.

  spec v4 关键变化:
    - 删 xray_version 入参 (SSHWorker 不查 xray, 永远写空字符串)
    - 不写 stage_message (字段已删除)
    - stage 永远 connectable
    - 返回 dict 不含 xray_version 字段

测试矩阵 (7 TC):
  TC-03-a 调用 _入库派任务 → DB 多一条 VPSRecord + 一条 VPSTask(PENDING)
  TC-03-b 返回 dict 含 vps_id/task_id/stage/os_name/os_version
  TC-03-c password 落盘加密 (原生 SQL 查 password_encrypted 不含明文)
  TC-03-d __repr__ 不输出密码
  TC-03-e ⭐ 防回退: vps_record.xray_version 必为 "" + 表无 stage_message 列
  TC-03-f ed=None 入库正常, expire_date 落 NULL
  TC-03-g provider="" 入库正常, provider_domain 落空字符串
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, text
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


class TestSSHWorkerPersist(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

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

    # ---------- TC-03-a ----------
    def test_tc03a_creates_vps_and_task(self):
        result = self.worker._入库派任务(
            ip="1.2.3.4", user="root", pwd="secret", port=22,
            ed=None, provider="aliyun.com",
            os_name="CentOS Linux", os_version="7",
        )
        with self.Session() as s:
            recs = s.query(VPSRecord).all()
            tasks = s.query(VPSTask).all()
            self.assertEqual(len(recs), 1)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(recs[0].stage, VPSStage.CONNECTABLE)
            self.assertEqual(recs[0].xray_version, "")
            self.assertEqual(tasks[0].status, TaskStatus.PENDING)
            self.assertEqual(tasks[0].vps_id, recs[0].id)

    # ---------- TC-03-b ----------
    def test_tc03b_returns_expected_dict(self):
        result = self.worker._入库派任务(
            ip="1.2.3.4", user="root", pwd="secret", port=22,
            ed=None, provider="",
            os_name="CentOS Linux", os_version="7",
        )
        self.assertIn("vps_id", result)
        self.assertIn("task_id", result)
        self.assertEqual(result["stage"], VPSStage.CONNECTABLE)
        self.assertEqual(result["stage"], "connectable")
        self.assertEqual(result["os_name"], "CentOS Linux")
        self.assertEqual(result["os_version"], "7")
        # spec v4: SSHWorker 不持有 xray_version, 返回 dict 不应有此字段
        self.assertNotIn("xray_version", result)

    # ---------- TC-03-c ----------
    def test_tc03c_password_encrypted_in_storage(self):
        self.worker._入库派任务(
            ip="1.2.3.4", user="root", pwd="MySecret_PlainPwd_123", port=22,
            ed=None, provider="",
            os_name="x", os_version="y",
        )
        # 原生 SQL 查 raw bytes, 验证不含明文
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT password_encrypted FROM vps_record WHERE ip='1.2.3.4'")
            ).fetchone()
            raw = bytes(row[0])
        self.assertNotIn(b"MySecret_PlainPwd_123", raw)

    # ---------- TC-03-d ----------
    def test_tc03d_repr_no_password(self):
        self.worker._入库派任务(
            ip="1.2.3.4", user="root", pwd="MySecret_PlainPwd_123", port=22,
            ed=None, provider="",
            os_name="x", os_version="y",
        )
        with self.Session() as s:
            rec = s.query(VPSRecord).first()
            r = repr(rec)
        self.assertNotIn("MySecret_PlainPwd_123", r)
        self.assertNotIn("password", r.lower())

    # ---------- TC-03-e ⭐ 防回退 ----------
    def test_tc03e_regression_xray_version_empty_and_no_stage_message_column(self):
        self.worker._入库派任务(
            ip="1.2.3.4", user="root", pwd="p", port=22,
            ed=None, provider="",
            os_name="x", os_version="y",
        )
        # ① xray_version 必为 ""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT xray_version FROM vps_record WHERE ip='1.2.3.4'")
            ).fetchone()
            self.assertEqual(row[0], "")
        # ② vps_record 表无 stage_message 列
        cols = {c["name"] for c in inspect(self.engine).get_columns("vps_record")}
        self.assertNotIn("stage_message", cols)

    # ---------- TC-03-f ----------
    def test_tc03f_ed_none_is_ok(self):
        result = self.worker._入库派任务(
            ip="1.2.3.4", user="root", pwd="p", port=22,
            ed=None, provider="aliyun.com",
            os_name="x", os_version="y",
        )
        with self.Session() as s:
            rec = s.query(VPSRecord).first()
            self.assertIsNone(rec.expire_date)

    # ---------- TC-03-g ----------
    def test_tc03g_empty_provider_is_ok(self):
        result = self.worker._入库派任务(
            ip="1.2.3.4", user="root", pwd="p", port=22,
            ed=None, provider="",
            os_name="x", os_version="y",
        )
        with self.Session() as s:
            rec = s.query(VPSRecord).first()
            self.assertEqual(rec.provider_domain, "")


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：2026-06-07 (独立 in-memory SQLite + patch session_scope, 7 OK = 7/7)
# 偏差：无 (TC-03-e 防回退用原生 SQL 验 xray_version="" + inspect 验表无 stage_message 列)
# ========================================================================
