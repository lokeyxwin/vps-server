"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-02 SSHWorker._probe_ssh 行为单测 (spec v4)

故事:
  SSHWorker 路线 B 步骤 ①②③: SSH 探测 + 顺手采集 OS, 不查 xray.
  _probe_ssh 用 VPSSession with 包起来用完即关, 返回 ok/os_*/error_*.

  spec v4 关键变化:
    - 删 xray_version 字段 (SSHWorker 不查 xray)
    - 绝不调用 XrayManager (防回退测试 TC-02-f)
    - SSH 通但 OS 读不到 → os 留空, ok 仍 True

测试矩阵 (7 TC):
  TC-02-a SSH 通 → ok=True, os 拿到, 返回 dict 不含 xray_version
  TC-02-b AuthFailedError → ok=False, error_type='auth_failed'
  TC-02-c ConnectTimeoutError → error_type='timeout'
  TC-02-d ConnectRefusedError → error_type='refused'
  TC-02-e 通用 Exception → error_type='failed'
  TC-02-f ⭐ 防回退: 内部不应调用/import XrayManager
  TC-02-g SSH 通但 get_system_info 抛 → os 留空, ok 仍 True
========================================================================
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from ssh.ops import (
    AuthFailedError,
    ConnectRefusedError,
    ConnectTimeoutError,
)
from workers.ssh_worker import SSHWorker


class TestSSHWorkerProbeSSH(unittest.TestCase):
    def setUp(self):
        self.worker = SSHWorker()

    def _patch_session(self, *, enter_return=None, enter_raise=None):
        """构造 mock VPSSession context manager."""
        fake_session = MagicMock()
        ctx = MagicMock()
        if enter_raise is not None:
            ctx.__enter__.side_effect = enter_raise
        else:
            ctx.__enter__.return_value = enter_return or fake_session
        ctx.__exit__.return_value = False

        # patch workers.ssh_worker.VPSSession 类
        return patch(
            "workers.ssh_worker.VPSSession",
            return_value=ctx,
        ), fake_session

    # ---------- TC-02-a ----------
    def test_tc02a_ssh_ok_returns_os_no_xray_version_field(self):
        fake_session = MagicMock()
        fake_session.get_system_info.return_value = {
            "username": "root",
            "os_name": "CentOS Linux",
            "os_version": "7",
        }
        ctx = MagicMock()
        ctx.__enter__.return_value = fake_session
        ctx.__exit__.return_value = False

        with patch("workers.ssh_worker.VPSSession", return_value=ctx):
            result = self.worker._probe_ssh("1.2.3.4", "root", "p", 22)

        self.assertTrue(result["ok"])
        self.assertEqual(result["os_name"], "CentOS Linux")
        self.assertEqual(result["os_version"], "7")
        self.assertIsNone(result["error_type"])
        # ⭐ spec v4: 返回 dict 不应含 xray_version
        self.assertNotIn("xray_version", result)

    # ---------- TC-02-b ----------
    def test_tc02b_auth_failed(self):
        ctx = MagicMock()
        ctx.__enter__.side_effect = AuthFailedError("认证失败")
        ctx.__exit__.return_value = False
        with patch("workers.ssh_worker.VPSSession", return_value=ctx):
            result = self.worker._probe_ssh("1.2.3.4", "root", "p", 22)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "auth_failed")
        self.assertNotEqual(result["error_message"], "")
        self.assertEqual(result["os_name"], "")
        self.assertEqual(result["os_version"], "")

    # ---------- TC-02-c ----------
    def test_tc02c_timeout(self):
        ctx = MagicMock()
        ctx.__enter__.side_effect = ConnectTimeoutError("超时")
        ctx.__exit__.return_value = False
        with patch("workers.ssh_worker.VPSSession", return_value=ctx):
            result = self.worker._probe_ssh("1.2.3.4", "root", "p", 22)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "timeout")
        self.assertNotEqual(result["error_message"], "")

    # ---------- TC-02-d ----------
    def test_tc02d_refused(self):
        ctx = MagicMock()
        ctx.__enter__.side_effect = ConnectRefusedError("拒接")
        ctx.__exit__.return_value = False
        with patch("workers.ssh_worker.VPSSession", return_value=ctx):
            result = self.worker._probe_ssh("1.2.3.4", "root", "p", 22)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "refused")
        self.assertNotEqual(result["error_message"], "")

    # ---------- TC-02-e ----------
    def test_tc02e_generic_connection_error(self):
        ctx = MagicMock()
        ctx.__enter__.side_effect = ConnectionError("未知")
        ctx.__exit__.return_value = False
        with patch("workers.ssh_worker.VPSSession", return_value=ctx):
            result = self.worker._probe_ssh("1.2.3.4", "root", "p", 22)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "failed")

    # ---------- TC-02-f 防回退 (核心!) ----------
    def test_tc02f_regression_does_not_invoke_xray_manager(self):
        """spec v4 §4 不做的事: SSHWorker 绝不查 xray, 绝不调用 XrayManager.

        2 条断言:
          ① workers.ssh_worker 模块不应 import XrayManager (顶层 namespace 没这名字)
          ② _probe_ssh 期间不实例化 XrayManager (即便偷偷 import 也要触发不到)
        """
        import workers.ssh_worker as ssh_worker_mod

        # ① 顶层 namespace 不应有 XrayManager
        self.assertFalse(
            hasattr(ssh_worker_mod, "XrayManager"),
            "workers.ssh_worker 不应 import XrayManager (spec v4 §4)",
        )

        # ② 即便我们 patch 一个假 XrayManager 进去, _probe_ssh 也不应碰它
        fake_xm_class = MagicMock()
        with patch.object(
            ssh_worker_mod, "XrayManager", fake_xm_class, create=True
        ):
            fake_session = MagicMock()
            fake_session.get_system_info.return_value = {
                "os_name": "x", "os_version": "y", "username": "root",
            }
            ctx = MagicMock()
            ctx.__enter__.return_value = fake_session
            ctx.__exit__.return_value = False
            with patch("workers.ssh_worker.VPSSession", return_value=ctx):
                self.worker._probe_ssh("1.2.3.4", "root", "p", 22)
            fake_xm_class.assert_not_called()

    # ---------- TC-02-g ----------
    def test_tc02g_os_read_failure_still_ok(self):
        """spec v4 §6: SSH 通但 get_system_info 抛 → os 留空, ok 仍 True."""
        fake_session = MagicMock()
        fake_session.get_system_info.side_effect = RuntimeError("读 OS 失败")
        ctx = MagicMock()
        ctx.__enter__.return_value = fake_session
        ctx.__exit__.return_value = False
        with patch("workers.ssh_worker.VPSSession", return_value=ctx):
            result = self.worker._probe_ssh("1.2.3.4", "root", "p", 22)
        self.assertTrue(result["ok"])
        self.assertEqual(result["os_name"], "")
        self.assertEqual(result["os_version"], "")
        self.assertIsNone(result["error_type"])


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：2026-06-07 (mock VPSSession context manager, 7 OK = 7/7)
# 偏差：无 (TC-02-f 防回退双断言: 顶层 namespace 不应 import XrayManager +
#       即便 patch 进去也不该实例化, 两条都过)
# ========================================================================
