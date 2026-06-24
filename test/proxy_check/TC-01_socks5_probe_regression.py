"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-01 Socks5Probe 封装回归 (ADR-0011 §决策 §4: 封类行为零变化)

故事:
  现有 socks5 内/外 ping 逻辑封装成 Socks5Probe 类。类方法只是委托给
  模块级 test_internal / test_external, 行为必须与原函数完全一致。

测试矩阵:
  TC-01-a test_internal 通  → 委托 test_internal_socks, 返回 (True, egress)
  TC-01-b test_internal 不通 → 返回 (False, "")
  TC-01-c test_internal 带账密时透传 user/pwd/port/timeout 给底层
  TC-01-d test_external 通  → 委托 test_socks_proxy, 返回 True
  TC-01-e test_external 不通 → 返回 False
  TC-01-f test_external 透传 host/port/user/pwd 给底层 requests 代理
  TC-01-g 类方法与模块级函数结果逐一相等(回归断言)
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

# 用别名 import, 避开 pytest python_functions=["test_*"] 误收模块级函数。
from toolbox.proxy_check import Socks5Probe
from toolbox.proxy_check import test_external as module_test_external
from toolbox.proxy_check import test_internal as module_test_internal


class TestSocks5ProbeInternal(unittest.TestCase):
    def setUp(self):
        self.probe = Socks5Probe()
        self.client = MagicMock(name="ssh_client")

    # ---------- TC-01-a ----------
    @patch("xray.service.test_internal_socks")
    def test_tc01a_internal_ok_returns_egress(self, mock_tis):
        mock_tis.return_value = {"ok": True, "body": "203.0.113.7", "exit_code": 0}
        ok, egress = self.probe.test_internal(self.client, 18441, user="u", pwd="p")
        self.assertTrue(ok)
        self.assertEqual(egress, "203.0.113.7")

    # ---------- TC-01-b ----------
    @patch("xray.service.test_internal_socks")
    def test_tc01b_internal_fail_returns_empty(self, mock_tis):
        mock_tis.return_value = {"ok": False, "body": "", "exit_code": 28}
        ok, egress = self.probe.test_internal(self.client, 18441)
        self.assertFalse(ok)
        self.assertEqual(egress, "")

    # ---------- TC-01-c ----------
    @patch("xray.service.test_internal_socks")
    def test_tc01c_internal_forwards_args(self, mock_tis):
        mock_tis.return_value = {"ok": True, "body": "1.1.1.1"}
        self.probe.test_internal(self.client, 18450, user="alice", pwd="secret", timeout=33)
        mock_tis.assert_called_once_with(
            client=self.client, port=18450, user="alice", pwd="secret", timeout=33,
        )

    # ---------- TC-01-g (内 ping 回归: 类 == 模块函数) ----------
    @patch("xray.service.test_internal_socks")
    def test_tc01g_internal_matches_module_func(self, mock_tis):
        mock_tis.return_value = {"ok": True, "body": "9.9.9.9"}
        func_result = module_test_internal(self.client, 18442, user="x", pwd="y")
        cls_result = self.probe.test_internal(self.client, 18442, user="x", pwd="y")
        self.assertEqual(func_result, cls_result)


class TestSocks5ProbeExternal(unittest.TestCase):
    def setUp(self):
        self.probe = Socks5Probe()

    # ---------- TC-01-d ----------
    @patch("toolbox.proxy_check.requests")
    def test_tc01d_external_ok_returns_true(self, mock_requests):
        resp = MagicMock(status_code=200, text="203.0.113.7")
        mock_requests.get.return_value = resp
        self.assertTrue(self.probe.test_external("host", 18441, user="u", pwd="p"))

    # ---------- TC-01-e ----------
    @patch("toolbox.proxy_check.requests")
    def test_tc01e_external_fail_returns_false(self, mock_requests):
        mock_requests.get.side_effect = OSError("connection refused")
        self.assertFalse(self.probe.test_external("host", 18441))

    # ---------- TC-01-f ----------
    @patch("toolbox.proxy_check.requests")
    def test_tc01f_external_forwards_auth_into_proxy_url(self, mock_requests):
        resp = MagicMock(status_code=200, text="ok")
        mock_requests.get.return_value = resp
        self.probe.test_external("1.2.3.4", 18441, user="bob", pwd="pw")
        _, kwargs = mock_requests.get.call_args
        proxy_url = kwargs["proxies"]["https"]
        self.assertIn("bob:pw@1.2.3.4:18441", proxy_url)

    # ---------- TC-01-g (外 ping 回归: 类 == 模块函数) ----------
    @patch("toolbox.proxy_check.requests")
    def test_tc01g_external_matches_module_func(self, mock_requests):
        resp = MagicMock(status_code=200, text="ok")
        mock_requests.get.return_value = resp
        func_result = module_test_external("h", 18443, user="a", pwd="b")
        cls_result = self.probe.test_external("h", 18443, user="a", pwd="b")
        self.assertEqual(func_result, cls_result)


if __name__ == "__main__":
    unittest.main()
