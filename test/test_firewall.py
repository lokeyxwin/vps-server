"""core/firewall.py 单测：探测 + 开放端口。"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.firewall import (
    detect_firewall,
    open_tcp_port_range,
    FirewallOpenError,
    FIREWALL_FIREWALLD,
    FIREWALL_UFW,
    FIREWALL_NONE,
)


def _exec_result(stdout="", stderr="", exit_code=0):
    return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}


class TestDetectFirewall(unittest.TestCase):
    @patch("core.firewall.execute_command")
    def test_detect_firewalld(self, mock_exec):
        mock_exec.return_value = _exec_result(stdout="active\n")
        self.assertEqual(detect_firewall(MagicMock()), FIREWALL_FIREWALLD)

    @patch("core.firewall.execute_command")
    def test_detect_ufw(self, mock_exec):
        # 第一次问 firewalld → 不 active；第二次问 ufw → active
        mock_exec.side_effect = [
            _exec_result(stdout="inactive\n"),
            _exec_result(stdout="Status: active\n"),
        ]
        self.assertEqual(detect_firewall(MagicMock()), FIREWALL_UFW)

    @patch("core.firewall.execute_command")
    def test_detect_none(self, mock_exec):
        mock_exec.side_effect = [
            _exec_result(stdout="inactive\n"),
            _exec_result(stdout=""),
        ]
        self.assertEqual(detect_firewall(MagicMock()), FIREWALL_NONE)


class TestOpenTcpPortRange(unittest.TestCase):
    @patch("core.firewall.execute_command")
    def test_open_firewalld_success(self, mock_exec):
        mock_exec.side_effect = [
            _exec_result(stdout="active\n"),     # detect
            _exec_result(exit_code=0),            # add-port
            _exec_result(exit_code=0),            # reload
        ]
        result = open_tcp_port_range(MagicMock(), 18440, 18450)
        self.assertEqual(result, FIREWALL_FIREWALLD)

    @patch("core.firewall.execute_command")
    def test_open_firewalld_add_fails(self, mock_exec):
        mock_exec.side_effect = [
            _exec_result(stdout="active\n"),
            _exec_result(exit_code=1, stderr="permission denied"),
            _exec_result(exit_code=0),
        ]
        with self.assertRaises(FirewallOpenError):
            open_tcp_port_range(MagicMock(), 18440, 18450)

    @patch("core.firewall.execute_command")
    def test_open_ufw_success(self, mock_exec):
        mock_exec.side_effect = [
            _exec_result(stdout="inactive\n"),       # detect firewalld
            _exec_result(stdout="Status: active\n"),  # detect ufw
            _exec_result(exit_code=0),                 # ufw allow
        ]
        result = open_tcp_port_range(MagicMock(), 18440, 18450)
        self.assertEqual(result, FIREWALL_UFW)

    @patch("core.firewall.execute_command")
    def test_open_none_does_nothing(self, mock_exec):
        mock_exec.side_effect = [
            _exec_result(stdout="inactive\n"),
            _exec_result(stdout=""),
        ]
        result = open_tcp_port_range(MagicMock(), 18440, 18450)
        self.assertEqual(result, FIREWALL_NONE)


if __name__ == "__main__":
    unittest.main()
