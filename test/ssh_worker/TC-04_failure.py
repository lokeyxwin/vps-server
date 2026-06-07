"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-04 SSHWorker._handle_failure 行为单测 (spec v4)

故事:
  spec v4 路线 C 大改: SSH 失败全部抛回, **永远不入库**.
  _handle_failure 把 error_type 转成 status code + 给用户提示文案.
  - 不熔断 (重试已在 _probe_ssh 内部 connect_with_retry 走完)
  - 4 种 status: auth_failed / ssh_timeout / ssh_refused / ssh_failed

测试矩阵 (6 TC):
  TC-04-a auth_failed → status='auth_failed', message 含"请核对账号密码"
  TC-04-b timeout → status='ssh_timeout', message 含 port + 端口/安全策略组提示
  TC-04-c refused → status='ssh_refused'
  TC-04-d failed → status='ssh_failed'
  TC-04-e ⭐ 防回退: 所有 case DB row count 不变 (VPSRecord 和 VPSTask 都无新增)
  TC-04-f 提示文案合规: 不含"防火墙"作首要排查, 含"端口"或"安全策略组"
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import VPSRecord, VPSTask
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


class TestSSHWorkerFailurePath(unittest.TestCase):
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

    # ---------- TC-04-a ----------
    def test_tc04a_auth_failed_returns_status_no_db(self):
        before = self._row_counts()
        result = self.worker._handle_failure(
            error_type="auth_failed",
            error_message="认证失败",
            port=22,
        )
        after = self._row_counts()
        self.assertEqual(result["status"], "auth_failed")
        self.assertIn("请核对账号密码", result["message"])
        self.assertEqual(before, after)

    # ---------- TC-04-b ----------
    def test_tc04b_timeout_returns_status_no_db(self):
        before = self._row_counts()
        result = self.worker._handle_failure(
            error_type="timeout",
            error_message="超时",
            port=2222,
        )
        after = self._row_counts()
        self.assertEqual(result["status"], "ssh_timeout")
        self.assertIn("2222", result["message"])  # 含 port
        # 含 port / 安全策略组 提示
        self.assertTrue(
            "端口" in result["message"] or "安全策略组" in result["message"]
        )
        self.assertEqual(before, after)

    # ---------- TC-04-c ----------
    def test_tc04c_refused_returns_status_no_db(self):
        before = self._row_counts()
        result = self.worker._handle_failure(
            error_type="refused",
            error_message="拒接",
            port=22,
        )
        after = self._row_counts()
        self.assertEqual(result["status"], "ssh_refused")
        self.assertEqual(before, after)

    # ---------- TC-04-d ----------
    def test_tc04d_failed_returns_status_no_db(self):
        before = self._row_counts()
        result = self.worker._handle_failure(
            error_type="failed",
            error_message="网络中断",
            port=22,
        )
        after = self._row_counts()
        self.assertEqual(result["status"], "ssh_failed")
        self.assertIn("网络中断", result["message"])
        self.assertEqual(before, after)

    # ---------- TC-04-e ⭐ 防回退 (核心!) ----------
    def test_tc04e_regression_no_db_writes_under_any_error_type(self):
        """spec v4 §5 不变量: 路线 C 永远不写库. 4 个 error_type 都验."""
        before = self._row_counts()
        for et in ("auth_failed", "timeout", "refused", "failed"):
            self.worker._handle_failure(
                error_type=et, error_message="x",
                port=22,
            )
        after = self._row_counts()
        self.assertEqual(before, after)
        self.assertEqual(before, (0, 0))

    # ---------- TC-04-f ----------
    def test_tc04f_messages_compliant_with_spec_v4_wording(self):
        """spec v4 用户拍板: 不引导用户去防火墙作首要排查, 主推端口/安全策略组."""
        # timeout
        t = self.worker._handle_failure(
            error_type="timeout", error_message="x",
            port=22,
        )
        # spec v4: timeout 文案以"端口/安全策略组"为主, 不引导防火墙
        self.assertNotIn("防火墙", t["message"])
        self.assertTrue(
            "端口" in t["message"] or "安全策略组" in t["message"]
        )
        # refused
        r = self.worker._handle_failure(
            error_type="refused", error_message="x",
            port=22,
        )
        self.assertNotIn("防火墙", r["message"])
        self.assertTrue(
            "端口" in r["message"] or "安全策略组" in r["message"]
        )


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：2026-06-07 (独立 in-memory SQLite + patch session_scope, 6 OK = 6/6)
# 偏差：无 (TC-04-e 防回退验 4 个 error_type 跑完 DB row count 仍是 (0,0))
# ========================================================================
