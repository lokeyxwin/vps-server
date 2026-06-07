"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-05 SSHWorker.process 主入口编排 (spec v4)

故事:
  process() 是 SSHWorker 的主入口, 编排 4 个私有方法跑 spec v4 §3 三条主路线:
    路线 A: _lookup_existing 命中 → already_registered (零写)
    路线 B: _lookup_existing 空 + _probe_ssh ok → _persist_and_dispatch → queued
    路线 C: _lookup_existing 空 + _probe_ssh 失败 → _handle_failure → 4 种 status 之一, **不入库**

  spec v4 关键变化 (相对 v3):
    - process() 不传 xray_version 给 _persist_and_dispatch (v4 删此参数)
    - 路线 B 返回的 vps.xray_version 永远 ""
    - 路线 C 返回 4 种 status (auth_failed / ssh_timeout / ssh_refused / ssh_failed),
      全部不入库, 返回 dict 无 vps_id 字段
    - 删 status='unreachable' 这条路径 (v4 没有此 stage 值)

测试矩阵 (9 TC):
  TC-05-a 已登记路径 → status='already_registered', _敲门/_入库/_失败 都不被调
  TC-05-b 新登记成功 → status='queued', task_id/vps_id 填好, vps.xray_version=""
  TC-05-c auth_failed → _handle_failure 收到 error_type='auth_failed', 返回不含 vps_id
  TC-05-d timeout → 返回 status='ssh_timeout', message 含 port
  TC-05-e refused → 返回 status='ssh_refused'
  TC-05-f failed → 返回 status='ssh_failed'
  TC-05-g ⭐ 防回退: 路线 C 真 SQLite 跑一遍, DB row count 不变
  TC-05-h ⭐ 防回退: 路线 B 真 SQLite 跑一遍, DB 中 vps_record.xray_version=""
  TC-05-i ⭐ 防回退: 路线 A 不写库, 已有 record 的情况下 row count 不变
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import TaskStatus, VPSRecord, VPSStage, VPSTask
from workers.ssh_worker import SSHWorker


def _make_in_memory_engine():
    """复用 TC-01/03/04 套路: 独立 in-memory SQLite + 外键 ON + 只建本测要的两表."""
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


# ===========================================================================
# 一组 (TC-05-a ~ TC-05-f): 纯 mock 编排, 验证 process() 调用顺序 + 返回形状
# ===========================================================================


class TestSSHWorkerProcessOrchestration(unittest.TestCase):
    """mock 4 个私有方法, 验 process() 编排正确性."""

    def setUp(self):
        self.worker = SSHWorker()

    # ---------- TC-05-a 已登记路径 ----------
    def test_tc05a_already_registered_short_circuit(self):
        existing = {
            "vps_id": 1,
            "ip": "1.2.3.4",
            "stage": "connectable",
            "xray_version": "26.3.27",
            "os_name": "ubuntu",
            "os_version": "22.04",
            "is_active": 1,
            "active_task": {
                "task_id": 10,
                "status": TaskStatus.PENDING,
                "retry_count": 0,
                "next_run_at": "",
                "last_error_code": "",
                "last_error_msg": "",
            },
            "last_error_code": "",
            "last_error_msg": "",
        }
        with patch.object(self.worker, "_lookup_existing", return_value=existing) as m_q, \
             patch.object(self.worker, "_probe_ssh") as m_probe, \
             patch.object(self.worker, "_persist_and_dispatch") as m_persist, \
             patch.object(self.worker, "_handle_failure") as m_fail:
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="p", port=22,
            )
            m_q.assert_called_once_with("1.2.3.4")
            # 路线 A 短路: 后三个方法都不应被调
            m_probe.assert_not_called()
            m_persist.assert_not_called()
            m_fail.assert_not_called()

        self.assertEqual(result["status"], "already_registered")
        self.assertEqual(result["vps"], existing)
        self.assertIn("active_task", result["vps"])

    # ---------- TC-05-b 新登记成功 ----------
    def test_tc05b_new_registration_success_returns_queued(self):
        probe = {
            "ok": True,
            "os_name": "ubuntu",
            "os_version": "22.04",
            "error_type": None,
            "error_message": "",
        }
        persist = {
            "vps_id": 1,
            "task_id": 10,
            "stage": VPSStage.CONNECTABLE,
            "os_name": "ubuntu",
            "os_version": "22.04",
        }
        with patch.object(self.worker, "_lookup_existing", return_value=None), \
             patch.object(self.worker, "_probe_ssh", return_value=probe) as m_probe, \
             patch.object(self.worker, "_persist_and_dispatch", return_value=persist) as m_persist, \
             patch.object(self.worker, "_handle_failure") as m_fail:
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="secret",
                port=22, ed=None, provider="aliyun.com",
            )
            m_probe.assert_called_once()
            m_persist.assert_called_once()
            m_fail.assert_not_called()
            # spec v4 防回退: process 不传 xray_version 给 _persist_and_dispatch
            call_kwargs = m_persist.call_args.kwargs
            self.assertNotIn("xray_version", call_kwargs)
            self.assertEqual(call_kwargs["os_name"], "ubuntu")
            self.assertEqual(call_kwargs["os_version"], "22.04")

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["task_id"], 10)
        self.assertEqual(result["vps_id"], 1)
        self.assertEqual(result["vps"]["stage"], "connectable")
        # spec v4 §5 不变量: 返回 vps.xray_version 字面值为 ""
        self.assertEqual(result["vps"]["xray_version"], "")
        self.assertIn("账密 OK", result["message"])

    # ---------- TC-05-c auth_failed ----------
    def test_tc05c_auth_failed_path(self):
        probe = {
            "ok": False,
            "os_name": "",
            "os_version": "",
            "error_type": "auth_failed",
            "error_message": "认证失败",
        }
        with patch.object(self.worker, "_lookup_existing", return_value=None), \
             patch.object(self.worker, "_probe_ssh", return_value=probe), \
             patch.object(self.worker, "_persist_and_dispatch") as m_persist:
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="bad", port=22,
            )
            # 路线 C: 不入库
            m_persist.assert_not_called()

        self.assertEqual(result["status"], "auth_failed")
        # spec v4: 路线 C 返回 dict 不含 vps_id
        self.assertNotIn("vps_id", result)
        self.assertIn("请核对账号密码", result["message"])

    # ---------- TC-05-d timeout ----------
    def test_tc05d_timeout_path(self):
        probe = {
            "ok": False,
            "os_name": "",
            "os_version": "",
            "error_type": "timeout",
            "error_message": "超时",
        }
        with patch.object(self.worker, "_lookup_existing", return_value=None), \
             patch.object(self.worker, "_probe_ssh", return_value=probe), \
             patch.object(self.worker, "_persist_and_dispatch") as m_persist:
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="p", port=2222,
            )
            m_persist.assert_not_called()

        # spec v4: timeout → ssh_timeout (不再是 unreachable)
        self.assertEqual(result["status"], "ssh_timeout")
        self.assertNotIn("vps_id", result)
        # message 含 port 数字
        self.assertIn("2222", result["message"])

    # ---------- TC-05-e refused ----------
    def test_tc05e_refused_path(self):
        probe = {
            "ok": False,
            "os_name": "",
            "os_version": "",
            "error_type": "refused",
            "error_message": "拒接",
        }
        with patch.object(self.worker, "_lookup_existing", return_value=None), \
             patch.object(self.worker, "_probe_ssh", return_value=probe), \
             patch.object(self.worker, "_persist_and_dispatch") as m_persist:
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="p", port=22,
            )
            m_persist.assert_not_called()

        self.assertEqual(result["status"], "ssh_refused")
        self.assertNotIn("vps_id", result)

    # ---------- TC-05-f failed ----------
    def test_tc05f_failed_path(self):
        probe = {
            "ok": False,
            "os_name": "",
            "os_version": "",
            "error_type": "failed",
            "error_message": "其他错误",
        }
        with patch.object(self.worker, "_lookup_existing", return_value=None), \
             patch.object(self.worker, "_probe_ssh", return_value=probe), \
             patch.object(self.worker, "_persist_and_dispatch") as m_persist:
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="p", port=22,
            )
            m_persist.assert_not_called()

        self.assertEqual(result["status"], "ssh_failed")
        self.assertNotIn("vps_id", result)


# ===========================================================================
# 二组 (TC-05-g/h/i): ⭐ 防回退, 真 SQLite + patch session_scope, 验"是否写库"
# ===========================================================================


class TestSSHWorkerProcessRegression(unittest.TestCase):
    """⭐ 防回退: 用真 SQLite 跑全链路, 验 spec v4 §5 不变量."""

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

    def _row_counts(self) -> tuple[int, int]:
        with self.Session() as s:
            return (
                s.query(VPSRecord).count(),
                s.query(VPSTask).count(),
            )

    # ---------- TC-05-g ⭐ 防回退: 路线 C 不写库 ----------
    def test_tc05g_regression_route_c_never_writes_db(self):
        """spec v4 §5 不变量: 路线 C 永远不写库.

        真 SQLite + 真 _handle_failure (不 mock), 仅 mock _probe_ssh 返回 timeout.
        """
        probe = {
            "ok": False,
            "os_name": "",
            "os_version": "",
            "error_type": "timeout",
            "error_message": "超时",
        }
        before = self._row_counts()
        self.assertEqual(before, (0, 0))

        with patch.object(self.worker, "_probe_ssh", return_value=probe):
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="p", port=22,
            )

        after = self._row_counts()
        self.assertEqual(result["status"], "ssh_timeout")
        # ⭐ 关键: DB 两表 row count 不变
        self.assertEqual(after, (0, 0))

    # ---------- TC-05-h ⭐ 防回退: 路线 B 入库 xray_version="" ----------
    def test_tc05h_regression_route_b_persists_empty_xray_version(self):
        """spec v4 §5 不变量: SSHWorker 写入的 xray_version 永远是空字符串.

        真 SQLite + 真 _persist_and_dispatch (不 mock), 仅 mock _probe_ssh 返回 ok=True.
        """
        probe = {
            "ok": True,
            "os_name": "ubuntu",
            "os_version": "22.04",
            "error_type": None,
            "error_message": "",
        }
        before = self._row_counts()
        self.assertEqual(before, (0, 0))

        with patch.object(self.worker, "_probe_ssh", return_value=probe):
            result = self.worker.process(
                ip="9.9.9.9", user="root", pwd="secret",
                port=22, ed=None, provider="aliyun.com",
            )

        self.assertEqual(result["status"], "queued")
        # ⭐ DB 中 vps_record.xray_version 必为 "" (原生 SQL 验)
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT xray_version, stage FROM vps_record WHERE ip='9.9.9.9'"
                )
            ).fetchone()
            self.assertEqual(row[0], "")
            self.assertEqual(row[1], "connectable")
        # 入库后 task 也存在 (PENDING)
        with self.Session() as s:
            tasks = s.query(VPSTask).all()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].status, TaskStatus.PENDING)

    # ---------- TC-05-i ⭐ 防回退: 路线 A 不写库 ----------
    def test_tc05i_regression_route_a_zero_writes(self):
        """spec v4 §3 路线 A: DB 已有时不打 SSH, 零写操作.

        真 SQLite 预先插入一条 VPSRecord, 再调 process 同 ip.
        _probe_ssh / _persist_and_dispatch 被 patch 成抛错, 一旦被调测试就挂.
        """
        # 预先插入一条
        with self.Session() as s:
            rec = VPSRecord.from_form(
                ip="1.2.3.4", username="root", password="pass",
                port=22, os_name="ubuntu", os_version="22.04",
                expire_date=None, provider_domain="",
            )
            s.add(rec)
            s.commit()
        before = self._row_counts()
        self.assertEqual(before, (1, 0))

        def _boom(*a, **kw):
            raise AssertionError("路线 A 不应调到 _probe_ssh / _persist_and_dispatch")

        with patch.object(self.worker, "_probe_ssh", side_effect=_boom), \
             patch.object(self.worker, "_persist_and_dispatch", side_effect=_boom):
            result = self.worker.process(
                ip="1.2.3.4", user="root", pwd="p", port=22,
            )

        self.assertEqual(result["status"], "already_registered")
        # ⭐ DB row count 不变 (没新增 record, 也没新增 task)
        after = self._row_counts()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：2026-06-07 (unittest, 9 OK = 9/9)
# 偏差：无
#   - TC-05-a~f 用 patch.object 直接 mock 4 个私有方法, 验编排
#   - TC-05-g/h/i 用真 in-memory SQLite + patch session_scope, 验是否写库
#   - TC-05-i 用 side_effect=_boom 把不该被调到的私有方法包成"调到就挂", 强约束
# 待用户决策事项：
#   - workers/ssh_worker.py 顶部 docstring 仍提到 unreachable + XrayManager.version,
#     是 v3 残留, 跟 spec v4 矛盾. 任务单要求"保留顶部 docstring 不动", 故本次未改.
#     建议下一次小修订一并清掉(无代码影响, 仅文档).
# ========================================================================
