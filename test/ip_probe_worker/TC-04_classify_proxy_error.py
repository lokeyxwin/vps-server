"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-04 _classify_proxy_error 映射 (spec v2 §E + T-12 偏差降级)

故事:
  curl exit code → status 字符串映射。
  T-12 实际产出: cmd 保留 2>&1, paramiko 通道 stderr 多为空,
  实施时主分类全靠 exit_code, stderr 关键字作软兜底。

测试矩阵 (6 TC):
  TC-04-a exit_code=7  → proxy_refused
  TC-04-b exit_code=28 → proxy_timeout
  TC-04-c exit_code=97 → proxy_auth_failed (主路径)
  TC-04-d 其他 exit_code(默认空 stderr) → proxy_failed
  TC-04-e 其他 exit_code + stderr 含 'auth' 关键字 → 软兜底升 auth_failed
  TC-04-f 其他 exit_code + stderr 含 'SOCKS5' 关键字 → 软兜底升 auth_failed
========================================================================
"""

from __future__ import annotations

import unittest

from workers.ip_probe_worker import IPProbeWorker


class TestClassifyProxyError(unittest.TestCase):
    def setUp(self):
        self.worker = IPProbeWorker()

    # ---------- TC-04-a ----------
    def test_tc04a_exit_7_is_refused(self):
        self.assertEqual(self.worker._classify_proxy_error(7, ""), "proxy_refused")

    # ---------- TC-04-b ----------
    def test_tc04b_exit_28_is_timeout(self):
        self.assertEqual(self.worker._classify_proxy_error(28, ""), "proxy_timeout")

    # ---------- TC-04-c ----------
    def test_tc04c_exit_97_is_auth_failed(self):
        self.assertEqual(
            self.worker._classify_proxy_error(97, ""), "proxy_auth_failed"
        )

    # ---------- TC-04-d ----------
    def test_tc04d_other_exit_no_stderr_is_failed(self):
        self.assertEqual(self.worker._classify_proxy_error(1, ""), "proxy_failed")
        self.assertEqual(self.worker._classify_proxy_error(56, ""), "proxy_failed")
        self.assertEqual(self.worker._classify_proxy_error(0, ""), "proxy_failed")

    # ---------- TC-04-e ----------
    def test_tc04e_other_exit_with_auth_keyword_is_auth_failed(self):
        """软兜底: stderr 含 'auth' 关键字时升级到 auth_failed (T-12 stderr 多为空, 此路径罕见)。"""
        self.assertEqual(
            self.worker._classify_proxy_error(
                52, "* SOCKS5: Authentication failed"
            ),
            "proxy_auth_failed",
        )

    # ---------- TC-04-f ----------
    def test_tc04f_other_exit_with_socks5_keyword_is_auth_failed(self):
        """SOCKS5 大小写不敏感命中。"""
        self.assertEqual(
            self.worker._classify_proxy_error(
                52, "curl: SOCKS5 negotiation failed",
            ),
            "proxy_auth_failed",
        )


if __name__ == "__main__":
    unittest.main()
