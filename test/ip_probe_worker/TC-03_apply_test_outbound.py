"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-03 ③+④ 挂上游 _apply_test_outbound (spec v2 §3 ③+④)

故事:
  在测试 VPS 上调 xm.replace_proxy_binding(PROBE_TEST_PORT, outbound, user, pwd)
  把用户提交的上游凭据挂成 outbound。
  replace_proxy_binding 内部 = remove 旧三件套 + add 新三件套(幂等), 一行搞定 ③+④。

测试矩阵 (3 TC):
  TC-03-a 调用参数正确:
    - vps_port = PROBE_TEST_PORT (=19000)
    - outbound 来自 build_proxy_outbound(host, port, user, pwd, protocol)
    - inbound user/pwd 是随机串 (来自 generate_random_auth)
  TC-03-b 返回 (last_config, test_inbound_user, test_inbound_pwd)
  TC-03-c 上游协议不支持 → 透传 UnsupportedProtocolError
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from probe_vps import PROBE_TEST_PORT
from workers.ip_probe_worker import IPProbeWorker
from xray.config import UnsupportedProtocolError


class TestApplyTestOutbound(unittest.TestCase):
    def setUp(self):
        self.worker = IPProbeWorker()
        self.fake_xm = MagicMock()
        self.fake_xm.replace_proxy_binding.return_value = {"_baked_config": True}

    # ---------- TC-03-a ----------
    def test_tc03a_replace_proxy_binding_called_with_expected_args(self):
        self.worker._apply_test_outbound(
            self.fake_xm,
            entry_host="proxy.example.com",
            entry_port=1080,
            username="alice",
            password="secret",
            protocol="socks5",
        )
        self.fake_xm.replace_proxy_binding.assert_called_once()
        args, _ = self.fake_xm.replace_proxy_binding.call_args
        vps_port, outbound, inb_user, inb_pwd = args

        self.assertEqual(vps_port, PROBE_TEST_PORT)
        self.assertEqual(vps_port, 19000)

        # outbound 是 build_proxy_outbound 产物: tag/protocol/settings
        self.assertEqual(outbound["tag"], "probe-out")
        self.assertEqual(outbound["protocol"], "socks")  # socks5 → xray "socks"
        srv = outbound["settings"]["servers"][0]
        self.assertEqual(srv["address"], "proxy.example.com")
        self.assertEqual(srv["port"], 1080)
        self.assertEqual(srv["users"][0]["user"], "alice")
        self.assertEqual(srv["users"][0]["pass"], "secret")

        # 随机 inbound 账密: 非空 + 跟上游账密不同 (隔离生产)
        self.assertTrue(inb_user)
        self.assertTrue(inb_pwd)
        self.assertNotEqual(inb_user, "alice")
        self.assertNotEqual(inb_pwd, "secret")

    # ---------- TC-03-b ----------
    def test_tc03b_returns_last_config_and_random_auth(self):
        last_config, user, pwd = self.worker._apply_test_outbound(
            self.fake_xm,
            entry_host="proxy.example.com",
            entry_port=1080,
            username="u",
            password="p",
            protocol="socks5",
        )
        self.assertEqual(last_config, {"_baked_config": True})
        self.assertIsInstance(user, str)
        self.assertIsInstance(pwd, str)
        self.assertGreater(len(user), 0)
        self.assertGreater(len(pwd), 0)

    # ---------- TC-03-c ----------
    def test_tc03c_unsupported_protocol_propagates(self):
        with self.assertRaises(UnsupportedProtocolError):
            self.worker._apply_test_outbound(
                self.fake_xm,
                entry_host="proxy.example.com",
                entry_port=1080,
                username="u",
                password="p",
                protocol="vmess",  # 不在 SUPPORTED_PROTOCOLS
            )


if __name__ == "__main__":
    unittest.main()
