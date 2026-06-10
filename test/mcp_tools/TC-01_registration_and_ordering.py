"""
========================================================================
TC-01 + TC-02: 7 工具全部注册 + 四段顺序 (spec §7 + ADR-0009 §6.2/6.3)

故事:
  ALL_TOOLS 必须含且仅含 7 个工具, 顺序:
    写入(2) → 状态查询(2) → 数据查询(1) → 运维(2, admin).

子测:
  TC-01 ALL_TOOLS 含 7 个 Tool.name = {register_vps, register_ip,
    get_vps_registration_status, get_ip_registration_status,
    get_available_proxy_nodes, init_db, init_probe_vps}
  TC-02 四段顺序: 前 2 写入, 中 2 状态查询, 4 数据查询, 末 2 运维 (admin)
========================================================================
"""

from __future__ import annotations

import unittest

from tools import ALL_TOOLS


_EXPECTED_NAMES_SET = {
    "register_vps",
    "register_ip",
    "get_vps_registration_status",
    "get_ip_registration_status",
    "get_available_proxy_nodes",
    "init_db",
    "init_probe_vps",
}

_EXPECTED_NAMES_ORDER = [
    "register_vps",
    "register_ip",
    "get_vps_registration_status",
    "get_ip_registration_status",
    "get_available_proxy_nodes",
    "init_db",
    "init_probe_vps",
]


class TestRegistrationAndOrdering(unittest.TestCase):

    def test_tc01_seven_tools_registered(self):
        names = {t.name for t, _ in ALL_TOOLS}
        self.assertEqual(names, _EXPECTED_NAMES_SET)
        self.assertEqual(len(ALL_TOOLS), 7)

    def test_tc02_four_section_order(self):
        names = [t.name for t, _ in ALL_TOOLS]
        self.assertEqual(
            names, _EXPECTED_NAMES_ORDER,
            "ALL_TOOLS 顺序必须是: 写入(2) → 状态查询(2) → 数据查询(1) → 运维(2)",
        )

    def test_tc02_each_position_is_tuple_of_tool_and_callable(self):
        """每条目必须是 (Tool, handler) 二元组."""
        for tool, handler in ALL_TOOLS:
            self.assertTrue(hasattr(tool, "name"))
            self.assertTrue(callable(handler))


if __name__ == "__main__":
    unittest.main()
