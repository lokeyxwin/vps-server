"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-05 _unified_tail 空配置: 没直进直出 + 没代理出口 (spec v5.1 §4)

故事:
  统一收尾扫配置, extract_existing_outbounds 返 [] (空配置):
    - 直进直出: 0 条 → 走 _find_default_port + _append_default_direct
    - 代理出口: 0 条 → 跳过纳管 / remove 循环
    - upload + validate + reload + is_running 走完
    - 返回 dict: xray_version + default_inbound_port=18440 + used_port_count=0

测试矩阵:
  TC-05-a 空配置 + 18440 没被占 → default_port=18440, used_count=0, 写默认 inbound
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from workers.xray_worker import XrayWorker


class TestTailEmptyConfig(unittest.TestCase):

    def test_tc05a_empty_config_appends_default_direct(self):
        xray = MagicMock()
        xray.extract_existing_outbounds.return_value = []
        xray.is_running.return_value = True
        xray.version.return_value = "Xray 26.3.27"
        # _unified_tail 因 outbounds==[] 走 else 分支: is_config_blank + (空 → build / 非空 → read)
        # 直接让 is_config_blank=True, 跳过 read_config
        client = MagicMock()

        with patch("workers.xray_worker.xc") as mock_xc, \
             patch("workers.xray_worker.test_internal") as mock_ping:
            mock_xc.is_config_blank.return_value = True
            mock_xc.build_vps_direct_config.return_value = {
                "log": {"loglevel": "warning"},
                "inbounds": [],
                "outbounds": [],
                "routing": {"rules": []},
            }
            mock_xc.remove_proxy_binding.side_effect = lambda c, p: c

            worker = XrayWorker()
            result = worker._unified_tail(client, xray, vps_id=1)

            # 没代理出口要 ping
            mock_ping.assert_not_called()
            # upload 被调一次, 含追加的默认 direct
            mock_xc.is_config_blank.assert_called()
            self.assertEqual(xray.upload_config.call_count, 1)
            uploaded_cfg = xray.upload_config.call_args.args[0]
            # 应至少有 1 个 socks5 inbound 在 18440
            ports = {inb.get("port") for inb in uploaded_cfg.get("inbounds", [])}
            self.assertIn(18440, ports)
            # 应有 freedom outbound
            protocols = {ob.get("protocol") for ob in uploaded_cfg.get("outbounds", [])}
            self.assertIn("freedom", protocols)

        self.assertEqual(result["default_inbound_port"], 18440)
        self.assertEqual(result["used_port_count"], 0)
        self.assertEqual(result["xray_version"], "Xray 26.3.27")


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
