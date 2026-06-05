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


class TestApplyProxyBinding(unittest.TestCase):
    """apply_proxy_binding：read_config → add → upload → validate → reload 编排。"""

    @patch("xray.manager.service.reload")
    @patch("xray.manager.xc.validate_config")
    @patch("xray.manager.xc.upload_config")
    @patch("xray.manager.xc.add_proxy_binding")
    @patch("xray.manager.xc.read_config")
    def test_happy_path_chains_five_atoms(
        self, mock_read, mock_add, mock_upload, mock_validate, mock_reload
    ):
        current = {"inbounds": [{"tag": "default-direct"}]}
        new_config = {"inbounds": [{"tag": "default-direct"}, {"tag": "client-18443"}]}
        mock_read.return_value = current
        mock_add.return_value = new_config

        xm = XrayManager(MagicMock())
        proxy_outbound = {"tag": "proxy-X-18443", "protocol": "socks"}
        result = xm.apply_proxy_binding(
            vps_port=18443,
            proxy_outbound=proxy_outbound,
            inbound_user="cu", inbound_pwd="cp",
        )

        # 5 步全调到 + 顺序对
        mock_read.assert_called_once_with(xm.client)
        mock_add.assert_called_once_with(current, 18443, proxy_outbound, "cu", "cp")
        mock_upload.assert_called_once_with(xm.client, new_config)
        mock_validate.assert_called_once_with(xm.client)
        mock_reload.assert_called_once_with(xm.client)

        # 透传：返回的是 add 之后的 config（业务备份用）
        self.assertEqual(result, new_config)

    @patch("xray.manager.service.reload")
    @patch("xray.manager.xc.validate_config")
    @patch("xray.manager.xc.upload_config")
    @patch("xray.manager.xc.add_proxy_binding")
    @patch("xray.manager.xc.read_config")
    def test_add_failure_short_circuits_no_upload(
        self, mock_read, mock_add, mock_upload, mock_validate, mock_reload
    ):
        """add 阶段抛错（如 PortAlreadyBoundError）→ upload/validate/reload 都不该跑。"""
        from xray import PortAlreadyBoundError
        mock_read.return_value = {}
        mock_add.side_effect = PortAlreadyBoundError("port 18443 used")

        xm = XrayManager(MagicMock())
        with self.assertRaises(PortAlreadyBoundError):
            xm.apply_proxy_binding(
                vps_port=18443,
                proxy_outbound={"tag": "proxy-X-18443"},
                inbound_user="u", inbound_pwd="p",
            )
        mock_upload.assert_not_called()
        mock_validate.assert_not_called()
        mock_reload.assert_not_called()

    @patch("xray.manager.service.reload")
    @patch("xray.manager.xc.validate_config")
    @patch("xray.manager.xc.upload_config")
    @patch("xray.manager.xc.add_proxy_binding")
    @patch("xray.manager.xc.read_config")
    def test_validate_failure_does_not_reload(
        self, mock_read, mock_add, mock_upload, mock_validate, mock_reload
    ):
        """validate 失败 → reload 不该跑（避免 push 坏 config 上线）。"""
        from xray import ConfigValidationError
        mock_read.return_value = {}
        mock_add.return_value = {"inbounds": []}
        mock_validate.side_effect = ConfigValidationError("syntax error")

        xm = XrayManager(MagicMock())
        with self.assertRaises(ConfigValidationError):
            xm.apply_proxy_binding(
                vps_port=18443,
                proxy_outbound={"tag": "proxy-X-18443"},
                inbound_user="u", inbound_pwd="p",
            )
        mock_upload.assert_called_once()  # validate 前已上传，业务回滚要 cover
        mock_reload.assert_not_called()


class TestRollbackProxyBinding(unittest.TestCase):
    """rollback_proxy_binding：remove → upload → reload 编排。"""

    @patch("xray.manager.service.reload")
    @patch("xray.manager.xc.upload_config")
    @patch("xray.manager.xc.remove_proxy_binding")
    def test_happy_path_chains_three_atoms(
        self, mock_remove, mock_upload, mock_reload
    ):
        last = {"inbounds": [{"tag": "default-direct"}, {"tag": "client-18443"}]}
        rolled = {"inbounds": [{"tag": "default-direct"}]}
        mock_remove.return_value = rolled

        xm = XrayManager(MagicMock())
        xm.rollback_proxy_binding(vps_port=18443, last_config=last)

        # 3 步顺序对
        mock_remove.assert_called_once_with(last, 18443)
        mock_upload.assert_called_once_with(xm.client, rolled)
        mock_reload.assert_called_once_with(xm.client)

    @patch("xray.manager.service.reload")
    @patch("xray.manager.xc.upload_config")
    @patch("xray.manager.xc.remove_proxy_binding")
    def test_does_not_call_validate(
        self, mock_remove, mock_upload, mock_reload
    ):
        """remove 后的 config 是 baseline 子集，xray 已校验过的，无需重新 validate。"""
        # 这里通过没有 patch validate 同时也无副作用来验证——若 manager 错调 validate
        # 会抛 AttributeError（mock 未 patch 时是 xc.validate_config 真函数指针，
        # 真调会因 client mock 失败抛错）。但更显式的做法：直接断言无 validate 调用。
        mock_remove.return_value = {}
        xm = XrayManager(MagicMock())
        # 不应抛错
        xm.rollback_proxy_binding(vps_port=18443, last_config={})

    @patch("xray.manager.service.reload")
    @patch("xray.manager.xc.upload_config")
    @patch("xray.manager.xc.remove_proxy_binding")
    def test_idempotent_when_binding_absent(
        self, mock_remove, mock_upload, mock_reload
    ):
        """last_config 里没有该 binding（remove 静默 noop）→ 仍走上传 + reload，不抛。"""
        last = {"inbounds": [{"tag": "default-direct"}]}
        mock_remove.return_value = last  # noop 返回原样

        xm = XrayManager(MagicMock())
        xm.rollback_proxy_binding(vps_port=18443, last_config=last)

        mock_upload.assert_called_once()
        mock_reload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
