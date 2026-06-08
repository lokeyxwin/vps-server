"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-11 让步降到 1024 全占 → no_default_port 失败 (ADR-0004 §2, spec v5.2 §7 + ADR-0005)

故事:
  让步算法降到下限 1024 仍找不到空位 → 抛 NoDefaultPortError.
  process_task 接住 → _mark_failed(task_id, "no_default_port", ...) →
    task.status = 'failed'
    task.last_error_code = 'no_default_port'
    task.locked_until = NULL
  vps.stage 保持 'running' (失败锁住等"维修工人"或人工介入, ADR-0005 §3).
  (注: 抢到 task 时 _lock_vps_resource 把 stage 升 running, 失败时不释放)

测试矩阵:
  TC-11-a 18440~1024 全占的 cfg → _find_default_port 抛 NoDefaultPortError
  TC-11-b process_task 命中 NoDefaultPortError → task=failed, vps.stage='running' 保留
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import TaskStatus, VPSRecord, VPSStage, VPSTask
from workers.xray_worker import (
    DEFAULT_PORT_CEILING,
    DEFAULT_PORT_FLOOR,
    NoDefaultPortError,
    XrayWorker,
    _find_default_port,
)


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


class TestNoDefaultPort(unittest.TestCase):

    def test_tc11a_all_taken_raises(self):
        cfg = {
            "inbounds": [
                {"port": p}
                for p in range(DEFAULT_PORT_FLOOR, DEFAULT_PORT_CEILING + 1)
            ],
        }
        with self.assertRaises(NoDefaultPortError):
            _find_default_port(cfg)

    def test_tc11b_process_task_marks_failed_on_no_default_port(self):
        engine, Session = _make_engine()
        try:
            @contextmanager
            def _fake_scope():
                s = Session()
                try:
                    yield s
                    s.commit()
                except Exception:
                    s.rollback()
                    raise
                finally:
                    s.close()

            with patch("workers.xray_worker.session_scope", _fake_scope):
                with Session() as s:
                    vps = VPSRecord.from_form(ip="10.0.0.5", username="root", password="p", port=22)
                    s.add(vps); s.commit()
                    vps_id = vps.id
                    task = VPSTask(vps_id=vps_id, status=TaskStatus.IN_PROGRESS)
                    s.add(task); s.commit()
                    task_id = task.id

                # 走 process_task 流程, 让让步算法抛 NoDefaultPortError
                with patch("workers.xray_worker.VPSSession") as MockSession, \
                     patch("workers.xray_worker.XrayManager") as MockXm:
                    # SSH 进去 → XrayManager 模拟现状 = C 分支 (装着跑着)
                    sess_inst = MockSession.return_value.__enter__.return_value
                    sess_inst.client = MagicMock()
                    xray = MockXm.return_value
                    xray.is_installed.return_value = True
                    xray.is_running.return_value = True
                    xray.is_enabled.return_value = True
                    # 配置里有 1 条 socks 出口但 ping 不通 → 走 remove
                    # 同时 18440-1024 全占 → 加默认入口时抛
                    xray.extract_existing_outbounds.return_value = []
                    # is_config_blank=False, read_config 返一份全占的配置
                    full_cfg = {
                        "inbounds": [
                            {"port": p}
                            for p in range(DEFAULT_PORT_FLOOR, DEFAULT_PORT_CEILING + 1)
                        ],
                        "outbounds": [],
                        "routing": {"rules": []},
                    }
                    with patch("workers.xray_worker.xc") as mock_xc:
                        mock_xc.is_config_blank.return_value = False
                        mock_xc.read_config.return_value = full_cfg

                        # 让 vps 进 process_task 时 xray_version 不空, 走 C 分支
                        with Session() as s:
                            v = s.get(VPSRecord, vps_id)
                            v.xray_version = "Xray 26.3.27"
                            s.commit()

                        worker = XrayWorker()
                        worker.process_task(task_id)

                with Session() as s:
                    t = s.get(VPSTask, task_id)
                    v = s.get(VPSRecord, vps_id)
                self.assertEqual(t.status, TaskStatus.FAILED)
                self.assertEqual(t.last_error_code, "no_default_port")
                self.assertIsNone(t.locked_until)
                # vps.stage 失败时保持 RUNNING (锁住等维修, ADR-0005 §3)
                self.assertEqual(v.stage, VPSStage.RUNNING)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
