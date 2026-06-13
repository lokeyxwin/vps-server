"""
========================================================================
TC-01 + TC-02: ALL_TOOLS 工具集注册 + 段落顺序 (spec §7/§8 + ADR-0009 §6.2/6.3 + ADR-0008 §3.3)

故事:
  ALL_TOOLS 必须含且仅含 _EXPECTED_NAMES_SET 列出的工具, 顺序按段:
    写入 → 状态查询 → 数据查询 → 写入修改(admin) → 运维(admin).
  工具集增量维护(CLAUDE.local §14.5): 加工具就在白名单 +1 行,
  **不 assert 工具总数**(数量由白名单 set 隐含, 不写死 len==N)。

子测:
  TC-01 ALL_TOOLS 的 Tool.name 集合 == _EXPECTED_NAMES_SET
  TC-02 顺序 == _EXPECTED_NAMES_ORDER(分段: 写入 / 状态查询 / 数据查询 /
    写入修改(admin) / 运维(admin))
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
    "update_ip_expire_date",
    "init_db",
    "init_probe_vps",
}

_EXPECTED_NAMES_ORDER = [
    "register_vps",
    "register_ip",
    "get_vps_registration_status",
    "get_ip_registration_status",
    "get_available_proxy_nodes",
    "update_ip_expire_date",
    "init_db",
    "init_probe_vps",
]


class TestRegistrationAndOrdering(unittest.TestCase):

    def test_tc01_exact_tool_set_registered(self):
        names = {t.name for t, _ in ALL_TOOLS}
        self.assertEqual(names, _EXPECTED_NAMES_SET)

    def test_tc02_five_section_order(self):
        names = [t.name for t, _ in ALL_TOOLS]
        self.assertEqual(
            names, _EXPECTED_NAMES_ORDER,
            "ALL_TOOLS 顺序必须是: 写入(2) → 状态查询(2) → 数据查询(1) → "
            "写入修改(1) → 运维(2)",
        )

    def test_tc02_each_position_is_tuple_of_tool_and_callable(self):
        """每条目必须是 (Tool, handler) 二元组."""
        for tool, handler in ALL_TOOLS:
            self.assertTrue(hasattr(tool, "name"))
            self.assertTrue(callable(handler))


if __name__ == "__main__":
    unittest.main()
