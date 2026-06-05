"""core/proxy_check.py 单测：从本机走 socks5 测代理。"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.proxy_check import test_socks_proxy


class TestSocksProxyCheck(unittest.TestCase):
    @patch("core.proxy_check.requests.get")
    def test_proxy_success_returns_ok(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "1.2.3.4"
        mock_get.return_value = mock_resp

        result = test_socks_proxy("vps.example.com", 18440)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["body"], "1.2.3.4")
        self.assertIsNone(result["error"])
        # 验证 requests 是用 socks5h scheme 调的
        args, kwargs = mock_get.call_args
        self.assertIn("socks5h://", kwargs["proxies"]["http"])

    @patch("core.proxy_check.requests.get")
    def test_proxy_non_200_returns_not_ok(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "server error"
        mock_get.return_value = mock_resp

        result = test_socks_proxy("vps.example.com", 18440)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 500)

    @patch("core.proxy_check.requests.get")
    def test_proxy_timeout_returns_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.Timeout("read timed out")

        result = test_socks_proxy("vps.example.com", 18440)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["status_code"])
        self.assertIn("Timeout", result["error"])

    @patch("core.proxy_check.requests.get")
    def test_proxy_connection_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.ConnectionError("can't connect")

        result = test_socks_proxy("vps.example.com", 18440)

        self.assertFalse(result["ok"])
        self.assertIn("ConnectionError", result["error"])

    @patch("core.proxy_check.requests.get")
    def test_no_auth_uses_plain_scheme(self, mock_get):
        """user/pwd 都空 → URL 不带账密前缀（rgvps 阶段测 18440）。"""
        mock_resp = MagicMock(); mock_resp.status_code = 200; mock_resp.text = "1.1.1.1"
        mock_get.return_value = mock_resp

        test_socks_proxy("vps.example.com", 18440)

        args, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["proxies"]["http"],
            "socks5h://vps.example.com:18440",
        )

    @patch("core.proxy_check.requests.get")
    def test_with_auth_includes_credentials_in_url(self, mock_get):
        """user/pwd 非空 → URL 含 user:pwd@host:port（rgIP 阶段测客户端 inbound）。"""
        mock_resp = MagicMock(); mock_resp.status_code = 200; mock_resp.text = "1.1.1.1"
        mock_get.return_value = mock_resp

        test_socks_proxy("vps.example.com", 18443, user="cu", pwd="cp")

        args, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["proxies"]["http"],
            "socks5h://cu:cp@vps.example.com:18443",
        )
        self.assertEqual(
            kwargs["proxies"]["https"],
            "socks5h://cu:cp@vps.example.com:18443",
        )

    @patch("core.proxy_check.requests.get")
    def test_only_user_still_attaches_auth(self, mock_get):
        """只有 user 没有 pwd（罕见但合法）→ 也走带 auth 的 URL。"""
        mock_resp = MagicMock(); mock_resp.status_code = 200; mock_resp.text = "1.1.1.1"
        mock_get.return_value = mock_resp

        test_socks_proxy("vps.example.com", 18443, user="cu")

        args, kwargs = mock_get.call_args
        self.assertIn("cu:@vps.example.com:18443", kwargs["proxies"]["http"])


if __name__ == "__main__":
    unittest.main()
