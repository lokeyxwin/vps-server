"""
========================================================================
TC-01 + TC-02: 5 工具全部注册 + 三段顺序 (spec §7)

故事:
  ALL_TOOLS 必须含且仅含 5 个工具, 顺序: 写入(2) → 状态查询(2) → 数据查询(1).

子测:
  TC-01 ALL_TOOLS 含 5 个 Tool.name = {register_vps, register_ip,
    get_vps_registration_status, get_ip_registration_status, get_available_proxy_nodes}
  TC-02 三段顺序: 前 2 写入, 中 2 状态查询, 末 1 数据查询
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
}

_EXPECTED_NAMES_ORDER = [
    "register_vps",
    "register_ip",
    "get_vps_registration_status",
    "get_ip_registration_status",
    "get_available_proxy_nodes",
]


class TestRegistrationAndOrdering(unittest.TestCase):

    def test_tc01_five_tools_registered(self):
        names = {t.name for t, _ in ALL_TOOLS}
        self.assertEqual(names, _EXPECTED_NAMES_SET)
        self.assertEqual(len(ALL_TOOLS), 5)

    def test_tc02_three_section_order(self):
        names = [t.name for t, _ in ALL_TOOLS]
        self.assertEqual(names, _EXPECTED_NAMES_ORDER,
                         "ALL_TOOLS 顺序必须是: 写入(2) → 状态查询(2) → 数据查询(1)")

    def test_tc02_each_position_is_tuple_of_tool_and_callable(self):
        """每条目必须是 (Tool, handler) 二元组."""
        for tool, handler in ALL_TOOLS:
            self.assertTrue(hasattr(tool, "name"))
            self.assertTrue(callable(handler))


if __name__ == "__main__":
    unittest.main()
