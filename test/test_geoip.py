"""core/geoip.py 单元测试。

策略：mock requests.get 的返回 / 异常，不实际打 ipinfo.io。
覆盖：
- 成功返回完整字段
- 带 token 时 URL params 含 token
- 无 token 时走匿名模式（仍返回成功）
- 429 限流兜底
- 5xx / 其他 HTTP 错兜底
- 网络异常兜底
- JSON 解析失败兜底
- bogon（私有 IP）兜底
- 空 IP 输入兜底
- 部分字段缺失（只有 country，没 city / region）落空串
- ISO → country_name 本地映射命中 / 未命中
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from core.geoip import lookup_egress


def _mock_response(status_code=200, json_data=None, text="", raises=None):
    m = MagicMock()
    m.status_code = status_code
    m.text = text
    if raises:
        m.json.side_effect = raises
    else:
        m.json.return_value = json_data or {}
    return m


class TestLookupEgress(unittest.TestCase):
    def setUp(self):
        # 每个测试默认有 token，单独 case 自行清掉
        self._saved_token = config.IPINFO_TOKEN
        config.IPINFO_TOKEN = "test-token-fake"

    def tearDown(self):
        config.IPINFO_TOKEN = self._saved_token

    # ---------- 成功路径 ----------

    @patch("core.geoip.requests.get")
    def test_success_full_fields(self, mock_get):
        mock_get.return_value = _mock_response(json_data={
            "ip": "8.8.8.8",
            "country": "US",
            "city": "Mountain View",
            "region": "California",
            "loc": "37.4056,-122.0775",
            "org": "AS15169 Google LLC",
            "timezone": "America/Los_Angeles",
        })
        result = lookup_egress("8.8.8.8")
        self.assertEqual(result["country_code"], "US")
        self.assertEqual(result["country_name"], "United States")
        self.assertEqual(result["city"], "Mountain View")
        self.assertEqual(result["region_name"], "California")
        self.assertEqual(result["raw"]["org"], "AS15169 Google LLC")

    @patch("core.geoip.requests.get")
    def test_success_only_country(self, mock_get):
        """部分 IP 在 ipinfo 数据库里只有 country，city/region 为空。"""
        mock_get.return_value = _mock_response(json_data={
            "ip": "1.2.3.4",
            "country": "SG",
        })
        result = lookup_egress("1.2.3.4")
        self.assertEqual(result["country_code"], "SG")
        self.assertEqual(result["country_name"], "Singapore")
        self.assertEqual(result["city"], "")
        self.assertEqual(result["region_name"], "")

    @patch("core.geoip.requests.get")
    def test_unknown_iso_country_name_empty(self, mock_get):
        """ISO 码不在本地映射表 → country_code 落库，country_name 留空。"""
        mock_get.return_value = _mock_response(json_data={
            "ip": "1.2.3.4",
            "country": "ZZ",  # 不存在的国家码
            "city": "Nowhere",
        })
        result = lookup_egress("1.2.3.4")
        self.assertEqual(result["country_code"], "ZZ")
        self.assertEqual(result["country_name"], "")
        self.assertEqual(result["city"], "Nowhere")

    @patch("core.geoip.requests.get")
    def test_country_code_uppercased(self, mock_get):
        """ISO 码标准化为大写（防御 API 返回小写）。"""
        mock_get.return_value = _mock_response(json_data={
            "ip": "1.2.3.4", "country": "jp",
        })
        result = lookup_egress("1.2.3.4")
        self.assertEqual(result["country_code"], "JP")
        self.assertEqual(result["country_name"], "Japan")

    # ---------- token 行为 ----------

    @patch("core.geoip.requests.get")
    def test_token_attached_when_present(self, mock_get):
        mock_get.return_value = _mock_response(json_data={"ip": "1.1.1.1", "country": "US"})
        lookup_egress("1.1.1.1")
        _, kwargs = mock_get.call_args
        self.assertIn("token", kwargs["params"])
        self.assertEqual(kwargs["params"]["token"], "test-token-fake")

    @patch("core.geoip.requests.get")
    def test_no_token_anonymous_mode(self, mock_get):
        config.IPINFO_TOKEN = ""
        mock_get.return_value = _mock_response(json_data={"ip": "1.1.1.1", "country": "US"})
        result = lookup_egress("1.1.1.1")
        # 仍然成功
        self.assertEqual(result["country_code"], "US")
        # 未带 token 参数
        _, kwargs = mock_get.call_args
        self.assertNotIn("token", kwargs["params"])

    # ---------- 失败兜底 ----------

    @patch("core.geoip.requests.get")
    def test_429_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response(status_code=429, text="rate limited")
        result = lookup_egress("1.1.1.1")
        self.assertEqual(result["country_code"], "")
        self.assertEqual(result["city"], "")
        self.assertIsNone(result["raw"])

    @patch("core.geoip.requests.get")
    def test_5xx_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response(status_code=503, text="service unavailable")
        result = lookup_egress("1.1.1.1")
        self.assertEqual(result["country_code"], "")
        self.assertIsNone(result["raw"])

    @patch("core.geoip.requests.get")
    def test_network_error_returns_empty(self, mock_get):
        mock_get.side_effect = ConnectionError("dns failed")
        result = lookup_egress("1.1.1.1")
        self.assertEqual(result["country_code"], "")
        self.assertEqual(result["city"], "")
        self.assertEqual(result["region_name"], "")
        self.assertIsNone(result["raw"])

    @patch("core.geoip.requests.get")
    def test_json_decode_error_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response(
            status_code=200, text="<html>nginx 502</html>", raises=ValueError("not json"),
        )
        result = lookup_egress("1.1.1.1")
        self.assertEqual(result["country_code"], "")
        self.assertIsNone(result["raw"])

    @patch("core.geoip.requests.get")
    def test_bogon_returns_empty(self, mock_get):
        """ipinfo 对私有 IP / bogon 返回 {bogon: true}，不能当合法 geo。"""
        mock_get.return_value = _mock_response(json_data={
            "ip": "192.168.1.1", "bogon": True,
        })
        result = lookup_egress("192.168.1.1")
        self.assertEqual(result["country_code"], "")
        self.assertIsNone(result["raw"])

    def test_empty_ip_input_returns_empty_without_http(self):
        """空 IP 不发请求，直接兜底返回。"""
        with patch("core.geoip.requests.get") as mock_get:
            result = lookup_egress("")
            mock_get.assert_not_called()
        self.assertEqual(result["country_code"], "")
        self.assertIsNone(result["raw"])


if __name__ == "__main__":
    unittest.main()
