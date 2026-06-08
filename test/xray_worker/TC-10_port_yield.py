"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-10 默认入口让步算法 (ADR-0004 §2, spec v5.1 §4 步骤 3)

故事:
  让步规则: 首选 18440 → 被占试 18439 → 18438 → ... 下限 1024.
  "被占" = 端口已被任何 inbound 监听 (不管 freedom 还是其他).

测试矩阵:
  TC-10-a 空 inbound 列表 → _find_default_port 返 18440
  TC-10-b 18440 被占 → 返 18439
  TC-10-c 18440 + 18439 + 18438 都被占 → 返 18437
  TC-10-d _unified_tail 配置里 18440 被非 freedom inbound 占着 → 写入的 default direct 用 18439
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from workers.xray_worker import XrayWorker, _find_default_port


class TestPortYield(unittest.TestCase):

    def test_tc10a_empty_inbounds_returns_18440(self):
        self.assertEqual(_find_default_port({}), 18440)
        self.assertEqual(_find_default_port({"inbounds": []}), 18440)

    def test_tc10b_18440_taken_returns_18439(self):
        cfg = {"inbounds": [{"port": 18440}]}
        self.assertEqual(_find_default_port(cfg), 18439)

    def test_tc10c_three_consecutive_taken(self):
        cfg = {"inbounds": [{"port": 18440}, {"port": 18439}, {"port": 18438}]}
        self.assertEqual(_find_default_port(cfg), 18437)

    def test_tc10d_unified_tail_yields_to_18439(self):
        # 配置里 18440 被一条非 freedom inbound 占, 没 freedom outbound
        cfg_18440_taken = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "client-18440",
                    "port": 18440,
                    "protocol": "socks",
                    "settings": {
                        "accounts": [{"user": "x", "pass": "y"}],
                    },
                },
            ],
            "outbounds": [
                {
                    "tag": "p1",
                    "protocol": "socks",
                    "settings": {
                        "servers": [{"address": "h", "port": 8080, "users": [{"user": "u", "pass": "p"}]}],
                    },
                },
            ],
            "routing": {"rules": [{"type": "field", "inboundTag": ["client-18440"], "outboundTag": "p1"}]},
        }

        xray = MagicMock()
        # extract_existing_outbounds: 1 条代理出口在 18440, 但 ping 不通 → 走 remove
        # 这样既验证让步算法又确保 used_count=0
        xray.extract_existing_outbounds.return_value = [
            {
                "vps_port": 18440, "inbound_protocol": "socks",
                "inbound_user": "x", "inbound_pwd": "y",
                "outbound_protocol": "socks",
                "upstream_host": "h", "upstream_port": 8080,
                "upstream_user": "u", "upstream_pwd": "p",
                "egress_ip": "", "egress_country": "",
            },
        ]
        xray.is_running.return_value = True
        xray.version.return_value = "Xray 26.3.27"

        with patch("workers.xray_worker.xc") as mock_xc, \
             patch("workers.xray_worker.test_internal", return_value=(False, "")):
            mock_xc.is_config_blank.return_value = False
            # 第一次返回 read 出的原 cfg, 让 _find_default_port 看到 18440 被占
            mock_xc.read_config.return_value = cfg_18440_taken
            # remove_proxy_binding 把 18440 那条 inbound 删掉
            def fake_remove(c, p):
                new = {k: v for k, v in c.items()}
                new["inbounds"] = [i for i in c.get("inbounds", []) if i.get("port") != p]
                return new
            mock_xc.remove_proxy_binding.side_effect = fake_remove

            worker = XrayWorker()
            result = worker._unified_tail(MagicMock(), xray, vps_id=1)

        # 让步: 配置里 18440 被占 → default_inbound_port = 18439
        self.assertEqual(result["default_inbound_port"], 18439)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
