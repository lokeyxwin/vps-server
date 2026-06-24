"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-02 ShadowsocksProbe.test_external —— TCP 端口可达测 (ADR-0011 §决策 §5)

故事:
  SS 外 ping = worker 本机对 (host, port) 做 socket.create_connection。
  成功 = TCP 可达 = 安全组放行; 拒绝/超时/解析失败 = 不通。
  绝不拉任何核心(不 import xray / 不 requests 代理)。

测试矩阵:
  TC-02-a 连得通          → True
  TC-02-b 连接被拒(refused) → False
  TC-02-c 连接超时(timeout) → False
  TC-02-d 解析失败(gaierror)→ False
  TC-02-e 透传 (host, port) + timeout 给 socket.create_connection
========================================================================
"""

from __future__ import annotations

import socket
import unittest
from unittest.mock import MagicMock, patch


from toolbox.proxy_check import ShadowsocksProbe


class TestShadowsocksProbeExternal(unittest.TestCase):
    def setUp(self):
        self.probe = ShadowsocksProbe()

    # ---------- TC-02-a ----------
    @patch("toolbox.proxy_check.socket.create_connection")
    def test_tc02a_reachable_returns_true(self, mock_conn):
        mock_conn.return_value = MagicMock(name="sock")
        self.assertTrue(self.probe.test_external("1.2.3.4", 18441))

    # ---------- TC-02-b ----------
    @patch("toolbox.proxy_check.socket.create_connection")
    def test_tc02b_refused_returns_false(self, mock_conn):
        mock_conn.side_effect = ConnectionRefusedError("refused")
        self.assertFalse(self.probe.test_external("1.2.3.4", 18441))

    # ---------- TC-02-c ----------
    @patch("toolbox.proxy_check.socket.create_connection")
    def test_tc02c_timeout_returns_false(self, mock_conn):
        mock_conn.side_effect = socket.timeout("timed out")
        self.assertFalse(self.probe.test_external("1.2.3.4", 18441))

    # ---------- TC-02-d ----------
    @patch("toolbox.proxy_check.socket.create_connection")
    def test_tc02d_resolve_fail_returns_false(self, mock_conn):
        mock_conn.side_effect = socket.gaierror("name resolution failed")
        self.assertFalse(self.probe.test_external("bad-host", 18441))

    # ---------- TC-02-e ----------
    @patch("toolbox.proxy_check.socket.create_connection")
    def test_tc02e_forwards_host_port_timeout(self, mock_conn):
        mock_conn.return_value = MagicMock()
        self.probe.test_external("5.6.7.8", 22222, timeout=9)
        args, kwargs = mock_conn.call_args
        self.assertEqual(args[0], ("5.6.7.8", 22222))
        self.assertEqual(kwargs.get("timeout"), 9)

    # ---------- TC-02-f (不拉核心: 上下文管理器自动 close) ----------
    @patch("toolbox.proxy_check.socket.create_connection")
    def test_tc02f_socket_closed_via_context_manager(self, mock_conn):
        fake_sock = MagicMock(name="sock")
        mock_conn.return_value.__enter__ = MagicMock(return_value=fake_sock)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        self.assertTrue(self.probe.test_external("1.2.3.4", 18441))
        mock_conn.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
