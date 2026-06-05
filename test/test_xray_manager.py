"""XrayManager.ensure_installed_and_running 内部分支测试。

业务测试 mock 掉了整个 ensure 方法；这里测它的内部状态机。
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from xray import (
    XrayManager,
    InstallFailedError,
    VerifyFailedError,
    ServiceNotActiveError,
    EnableFailedError,
)


class TestEnsureInstalledAndRunning(unittest.TestCase):
    """ensure_installed_and_running 内部 if/else 分支测试。

    所有 atom 函数都被 patch 掉。这里只测「分支编排」。
    """

    def setUp(self):
        # 把 service 模块（运行时操作）和 config 模块（配置文件）的函数都 patch 掉。
        # atoms 这个变量名保留只是历史称呼——按 CLAUDE.md "原子函数"统称，
        # 在 manager 视角下，service / config 里的都属于"被薄包装的原子"。
        self.patches = []
        self.atoms = {}
        # 服务运行时操作：xray.service
        for name in [
            "is_installed", "is_running", "is_enabled",
            "version", "install", "uninstall", "start", "enable",
        ]:
            p = patch(f"xray.manager.service.{name}")
            self.atoms[name] = p.start()
            self.patches.append(p)
        # 配置操作：xray.config（manager 里 import 为 xc）
        for name in ["is_config_blank", "write_default_config"]:
            p = patch(f"xray.manager.xc.{name}")
            self.atoms[name] = p.start()
            self.patches.append(p)
        # 默认全 happy path
        self.atoms["is_installed"].return_value = True
        self.atoms["is_running"].return_value = True
        self.atoms["is_enabled"].return_value = True
        self.atoms["version"].return_value = "Xray 1.8.4"
        self.atoms["is_config_blank"].return_value = False

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _make_manager(self):
        return XrayManager(MagicMock())

    # ---------- version 作为「是否已装」的入口判断 ----------

    def test_no_version_triggers_install(self):
        """version 返回空 → 走全新安装路径。"""
        self.atoms["version"].side_effect = [
            "",                # 第一次：装前查不到
            "Xray 1.8.4",      # 第二次：装完验证 OK
        ]
        result = self._make_manager().ensure_installed_and_running()
        self.atoms["install"].assert_called_once()
        self.assertFalse(result["was_already_installed"])
        self.assertIn("installed", result["actions_taken"])

    def test_has_version_skips_install(self):
        """version 有返回 → 跳过 install。"""
        result = self._make_manager().ensure_installed_and_running()
        self.atoms["install"].assert_not_called()
        self.assertTrue(result["was_already_installed"])

    # ---------- 验证失败 ----------

    def test_verify_failed_when_install_succeeds_but_no_version(self):
        """装完仍拿不到 version → VerifyFailedError。"""
        self.atoms["version"].side_effect = ["", ""]  # 装前空、装后还空
        with self.assertRaises(VerifyFailedError):
            self._make_manager().ensure_installed_and_running()

    # ---------- config 处理 ----------

    def test_blank_config_triggers_write_default(self):
        self.atoms["is_config_blank"].return_value = True
        result = self._make_manager().ensure_installed_and_running()
        self.atoms["write_default_config"].assert_called_once()
        self.assertIn("wrote_default_config", result["actions_taken"])

    def test_existing_config_not_overwritten(self):
        self.atoms["is_config_blank"].return_value = False
        result = self._make_manager().ensure_installed_and_running()
        self.atoms["write_default_config"].assert_not_called()
        self.assertNotIn("wrote_default_config", result["actions_taken"])

    # ---------- 服务启动 ----------

    def test_inactive_service_gets_started(self):
        # 第一次 is_running False，start 后第二次 True
        self.atoms["is_running"].side_effect = [False, True]
        result = self._make_manager().ensure_installed_and_running()
        self.atoms["start"].assert_called_once()
        self.assertIn("started", result["actions_taken"])

    def test_service_still_inactive_after_start_raises(self):
        self.atoms["is_running"].side_effect = [False, False]
        with self.assertRaises(ServiceNotActiveError):
            self._make_manager().ensure_installed_and_running()

    # ---------- 开机自启 ----------

    def test_not_enabled_triggers_enable(self):
        self.atoms["is_enabled"].return_value = False
        result = self._make_manager().ensure_installed_and_running()
        self.atoms["enable"].assert_called_once()
        self.assertIn("enabled_autostart", result["actions_taken"])

    def test_already_enabled_skips(self):
        self.atoms["is_enabled"].return_value = True
        result = self._make_manager().ensure_installed_and_running()
        self.atoms["enable"].assert_not_called()

    def test_enable_failure_propagates(self):
        self.atoms["is_enabled"].return_value = False
        self.atoms["enable"].side_effect = EnableFailedError("无权限")
        with self.assertRaises(EnableFailedError):
            self._make_manager().ensure_installed_and_running()

    # ---------- install 失败 ----------

    def test_install_failure_propagates(self):
        self.atoms["version"].side_effect = ["", "Xray 1.8.4"]
        self.atoms["install"].side_effect = InstallFailedError("网络挂了")
        with self.assertRaises(InstallFailedError):
            self._make_manager().ensure_installed_and_running()

    # ---------- 完整路径组合 ----------

    def test_full_fresh_install_actions(self):
        """场景：完全新机器，全部步骤都要做。"""
        self.atoms["version"].side_effect = ["", "Xray 1.8.4"]
        self.atoms["is_config_blank"].return_value = True
        self.atoms["is_running"].side_effect = [False, True]
        self.atoms["is_enabled"].return_value = False

        result = self._make_manager().ensure_installed_and_running()

        self.assertEqual(
            result["actions_taken"],
            ["installed", "wrote_default_config", "started", "enabled_autostart"],
        )
        self.assertFalse(result["was_already_installed"])

    def test_full_imported_perfect_state(self):
        """场景：已装、config 已配、跑着、自启已开——啥也不做。"""
        # 默认 setUp 就是 perfect state
        result = self._make_manager().ensure_installed_and_running()
        self.assertEqual(result["actions_taken"], [])
        self.assertTrue(result["was_already_installed"])


class TestImportExistingBindings(unittest.TestCase):
    """XrayManager.import_existing_bindings：is_config_blank 短路 + read+extract 组合。"""

    @patch("xray.manager.xc.is_config_blank")
    def test_blank_config_returns_empty_list_short_circuit(self, mock_blank):
        """config 空时短路：根本不读文件。"""
        mock_blank.return_value = True
        xm = XrayManager(MagicMock())
        # 同时 patch 一下 read_config 看它有没有被叫
        with patch("xray.manager.xc.read_config") as mock_read:
            result = xm.import_existing_bindings()
            self.assertEqual(result, [])
            mock_read.assert_not_called()

    @patch("xray.manager.xc.extract_port_bindings")
    @patch("xray.manager.xc.read_config")
    @patch("xray.manager.xc.is_config_blank")
    def test_config_present_delegates_read_then_extract(
        self, mock_blank, mock_read, mock_extract
    ):
        """有 config 时：read_config → extract_port_bindings 串联。"""
        mock_blank.return_value = False
        mock_read.return_value = {"inbounds": ["fake"]}
        mock_extract.return_value = [{"port": 18443, "egress_ip": "1.2.3.4"}]

        xm = XrayManager(MagicMock())
        result = xm.import_existing_bindings()

        # 编排正确性
        mock_read.assert_called_once_with(xm.client)
        mock_extract.assert_called_once_with({"inbounds": ["fake"]})
        # 透传
        self.assertEqual(result, [{"port": 18443, "egress_ip": "1.2.3.4"}])


if __name__ == "__main__":
    unittest.main()
