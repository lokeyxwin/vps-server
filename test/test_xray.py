import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import xray
from xray import (
    install,
    uninstall,
    is_installed,
    is_service_active,
    get_version,
    INSTALL_COMMAND,
    UNINSTALL_COMMAND,
    XRAY_INSTALL_FAILED_MESSAGE,
    InstallFailedError,
)


class TestXrayMocked(unittest.TestCase):
    """xray 原子的单元测试，全部 mock execute_command。"""

    @patch("xray.atom.execute_command")
    def test_install_success(self, mock_exec):
        from xray import INSTALL_TIMEOUT
        mock_exec.return_value = {"stdout": "ok", "stderr": "", "exit_code": 0}
        install(MagicMock())
        mock_exec.assert_called_once_with(
            unittest.mock.ANY, INSTALL_COMMAND, timeout=INSTALL_TIMEOUT
        )

    @patch("xray.atom.execute_command")
    def test_install_failure_raises(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "", "stderr": "curl: command not found", "exit_code": 127
        }
        with self.assertRaises(InstallFailedError) as ctx:
            install(MagicMock())
        self.assertIn(XRAY_INSTALL_FAILED_MESSAGE, str(ctx.exception))
        self.assertIn("127", str(ctx.exception))

    @patch("xray.atom.execute_command")
    def test_uninstall_success(self, mock_exec):
        from xray import INSTALL_TIMEOUT
        mock_exec.return_value = {"stdout": "removed", "stderr": "", "exit_code": 0}
        uninstall(MagicMock())
        mock_exec.assert_called_once_with(
            unittest.mock.ANY, UNINSTALL_COMMAND, timeout=INSTALL_TIMEOUT
        )

    @patch("xray.atom.execute_command")
    def test_is_installed_true(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "/usr/local/bin/xray\n", "stderr": "", "exit_code": 0
        }
        self.assertTrue(is_installed(MagicMock()))

    @patch("xray.atom.execute_command")
    def test_is_installed_false_when_command_not_found(self, mock_exec):
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 1}
        self.assertFalse(is_installed(MagicMock()))

    @patch("xray.atom.execute_command")
    def test_is_installed_false_when_empty_stdout(self, mock_exec):
        mock_exec.return_value = {"stdout": "\n", "stderr": "", "exit_code": 0}
        self.assertFalse(is_installed(MagicMock()))

    @patch("xray.atom.execute_command")
    def test_is_service_active_true(self, mock_exec):
        mock_exec.return_value = {"stdout": "active\n", "stderr": "", "exit_code": 0}
        self.assertTrue(is_service_active(MagicMock()))

    @patch("xray.atom.execute_command")
    def test_is_service_active_false_inactive(self, mock_exec):
        mock_exec.return_value = {"stdout": "inactive\n", "stderr": "", "exit_code": 3}
        self.assertFalse(is_service_active(MagicMock()))

    @patch("xray.atom.execute_command")
    def test_is_service_active_false_failed(self, mock_exec):
        mock_exec.return_value = {"stdout": "failed\n", "stderr": "", "exit_code": 3}
        self.assertFalse(is_service_active(MagicMock()))

    @patch("xray.atom.execute_command")
    def test_get_version_returns_string(self, mock_exec):
        # `head -n1` 在服务器上已截到第一行，这里直接 mock 截后的输出
        mock_exec.return_value = {
            "stdout": "Xray 1.8.4 (Xray, Penetrates Everything.)\n",
            "stderr": "", "exit_code": 0,
        }
        version = get_version(MagicMock())
        self.assertEqual(version, "Xray 1.8.4 (Xray, Penetrates Everything.)")

    @patch("xray.atom.execute_command")
    def test_get_version_returns_empty_when_not_installed(self, mock_exec):
        mock_exec.return_value = {"stdout": "", "stderr": "not found", "exit_code": 127}
        self.assertEqual(get_version(MagicMock()), "")


if __name__ == "__main__":
    unittest.main()
