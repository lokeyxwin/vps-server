"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-15 vps.stage 锁状态机 (ADR-0005 §决策 §1 §3, spec v5.2 §1 §9)

故事:
  ADR-0005 把 vps.stage 定为 VPS 资源占用锁 (跨工人/部门), 跟 vps_task 表的
  任务并发锁是两层不同维度. XrayWorker.process_task 的 stage 状态机:

    入参时       SSHWorker 入库默认 → stage='connectable'
    抢到 task    → _lock_vps_resource → stage='running' (SSH 之前占)
    完工         → _mark_done → stage='connectable' (释放回池子)
    失败任何路径  → stage 保持 'running' (锁住等维修)

测试矩阵:
  TC-15-a process_task 抢到 task 之后, SSH 之前, vps.stage 已经是 RUNNING
  TC-15-b process_task 完工成功 → vps.stage='connectable' + task.status='done'
  TC-15-c process_task 失败 (AuthFailedError) → vps.stage 保持 'running' + task.status='failed'
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import (
    IPRecord,
    ProxyRecord,
    TaskStatus,
    VPSRecord,
    VPSStage,
    VPSTask,
)
from ssh.ops import AuthFailedError
from workers.xray_worker import XrayWorker


def _make_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(
        engine,
        tables=[
            VPSRecord.__table__,
            VPSTask.__table__,
            IPRecord.__table__,
            ProxyRecord.__table__,
        ],
    )
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


class TestLockSemantics(unittest.TestCase):

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
            vps = VPSRecord.from_form(
                ip="10.0.0.99", username="root", password="pwd", port=22,
            )
            s.add(vps)
            s.commit()
            self.vps_id = vps.id
            task = VPSTask(vps_id=self.vps_id, status=TaskStatus.IN_PROGRESS)
            s.add(task)
            s.commit()
            self.task_id = task.id

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc15a_lock_acquired_before_ssh(self):
        """抢到 task 后, SSH 之前 vps.stage 已经是 RUNNING."""
        observed = {}

        def _spy_session(*args, **kwargs):
            # VPSSession 被构造的瞬间, stage 应该已经是 RUNNING
            with self.Session() as s:
                v = s.get(VPSRecord, self.vps_id)
                observed["stage_at_ssh_entry"] = v.stage
            # 不让真的进 SSH, 抛个异常退出 (不影响断言)
            raise RuntimeError("SSH 阶段不让进, 只测抢锁之后的 stage")

        with patch("workers.xray_worker.VPSSession", side_effect=_spy_session):
            worker = XrayWorker()
            worker.process_task(self.task_id)

        self.assertEqual(
            observed["stage_at_ssh_entry"], VPSStage.RUNNING,
            "抢到 task 后, SSH 之前必须先占资源锁 (stage=running)",
        )

    def test_tc15b_success_releases_lock_to_connectable(self):
        """完工成功 → vps.stage='connectable', task.status='done'."""
        with patch("workers.xray_worker.VPSSession") as MockSession, \
             patch("workers.xray_worker.XrayManager") as MockXm, \
             patch("workers.xray_worker.xc") as mock_xc:

            sess_inst = MockSession.return_value.__enter__.return_value
            sess_inst.client = MagicMock()

            xray = MockXm.return_value
            xray.is_installed.return_value = True
            xray.is_running.return_value = True
            xray.is_enabled.return_value = True
            xray.extract_existing_outbounds.return_value = []
            xray.version.return_value = "Xray 26.3.27"

            mock_xc.is_config_blank.return_value = False
            mock_xc.read_config.return_value = {
                "log": {"loglevel": "warning"},
                "inbounds": [],
                "outbounds": [],
                "routing": {"rules": []},
            }
            mock_xc.remove_proxy_binding.side_effect = lambda c, p: c

            # 让 vps 进 process_task 时 xray_version 非空 → 分支 C
            with self.Session() as s:
                v = s.get(VPSRecord, self.vps_id)
                v.xray_version = "Xray 26.3.27"
                s.commit()

            worker = XrayWorker()
            worker.process_task(self.task_id)

        with self.Session() as s:
            t = s.get(VPSTask, self.task_id)
            v = s.get(VPSRecord, self.vps_id)

        self.assertEqual(t.status, TaskStatus.DONE)
        self.assertEqual(
            v.stage, VPSStage.CONNECTABLE,
            "完工后 vps.stage 必须释放回 connectable",
        )

    def test_tc15c_failure_keeps_lock_running(self):
        """失败 (AuthFailedError) → vps.stage 保持 running, task.status='failed'."""
        with patch(
            "workers.xray_worker.VPSSession",
            side_effect=AuthFailedError("auth denied"),
        ):
            worker = XrayWorker()
            worker.process_task(self.task_id)

        with self.Session() as s:
            t = s.get(VPSTask, self.task_id)
            v = s.get(VPSRecord, self.vps_id)

        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.last_error_code, "auth_denied")
        self.assertEqual(
            v.stage, VPSStage.RUNNING,
            "失败后 vps.stage 必须保持 running (锁住等维修, ADR-0005 §3)",
        )


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
