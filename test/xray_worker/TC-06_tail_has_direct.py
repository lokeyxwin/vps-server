"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-06 _unified_tail 配置里已有直进直出 → 借用不补 (spec v5.1 §4 步骤 3)

故事:
  extract_existing_outbounds 返一条 freedom 出口 + 没代理出口:
    - direct_entries 非空 → 借用现有的当 default_inbound_port
    - 不调 _append_default_direct
    - 代理出口 0 条 → 没 ping
    - upload + validate + reload + 验证

测试矩阵:
  TC-06-a 已有 socks5→freedom 端口=8080 → default_port=8080, 不追加 inbound
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from workers.xray_worker import XrayWorker


_EXISTING_CONFIG_WITH_DIRECT = {
    "log": {"loglevel": "warning"},
    "inbounds": [
        {
            "tag": "default-direct",
            "port": 8080,
            "listen": "0.0.0.0",
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True},
        },
    ],
    "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    "routing": {
        "rules": [
            {
                "type": "field",
                "inboundTag": ["default-direct"],
                "outboundTag": "direct",
            },
        ],
    },
}


class TestTailHasDirect(unittest.TestCase):

    def test_tc06a_reuse_existing_direct_inbound(self):
        xray = MagicMock()
        xray.extract_existing_outbounds.return_value = [
            {
                "vps_port": 8080,
                "inbound_protocol": "socks",
                "inbound_user": "",
                "inbound_pwd": "",
                "outbound_protocol": "freedom",
                "upstream_host": "",
                "upstream_port": 0,
                "upstream_user": "",
                "upstream_pwd": "",
                "egress_ip": "",
                "egress_country": "",
            },
        ]
        xray.is_running.return_value = True
        xray.version.return_value = "Xray 26.3.27"
        client = MagicMock()

        with patch("workers.xray_worker.xc") as mock_xc, \
             patch("workers.xray_worker.test_internal") as mock_ping:
            mock_xc.is_config_blank.return_value = False
            mock_xc.read_config.return_value = _EXISTING_CONFIG_WITH_DIRECT

            worker = XrayWorker()
            result = worker._unified_tail(client, xray, vps_id=1)

            mock_ping.assert_not_called()
            # 上传的配置应该跟读到的几乎一致 (没追加新 inbound)
            uploaded = xray.upload_config.call_args.args[0]
            self.assertEqual(len(uploaded.get("inbounds", [])), 1)
            self.assertEqual(uploaded["inbounds"][0]["port"], 8080)

        self.assertEqual(result["default_inbound_port"], 8080)
        self.assertEqual(result["used_port_count"], 0)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
