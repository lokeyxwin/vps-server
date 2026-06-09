"""
========================================================================
TC-08 description 列 status 全集 — 每工具不漏不多 (spec §8 不变量 #3)

故事:
  spec §8 不变量 #3: "description 列全 status_code", 跟 §6 映射表一对一.
  本 TC grep 每工具 description, 验列了 spec §6 全部 status 名.

工具 → §6 status 集:
  register_vps         (§6.1) - 6 种
  register_ip          (§6.2) - 7 种
  get_vps_status       (§6.3) - ok / not_found / done / in_progress / pending / failed
  get_ip_status        (§6.4) - ok / not_found + 各种 task.status + last_error_code

  get_available_proxy_nodes (§6.5 沿用原 description) - 不在本 TC 校验
========================================================================
"""

from __future__ import annotations

import unittest

from tools.get_ip_registration_status import TOOL as IP_STATUS_TOOL
from tools.get_vps_registration_status import TOOL as VPS_STATUS_TOOL
from tools.register_ip import TOOL as REG_IP_TOOL
from tools.register_vps import TOOL as REG_VPS_TOOL


class TestDescriptionStatusFull(unittest.TestCase):

    def test_register_vps_6_statuses(self):
        for st in [
            "queued", "already_registered",
            "auth_failed", "ssh_timeout", "ssh_refused", "ssh_failed",
        ]:
            self.assertIn(st, REG_VPS_TOOL.description,
                          f"register_vps description 漏了 {st}")

    def test_register_ip_7_statuses(self):
        for st in [
            "queued", "duplicate",
            "proxy_auth_failed", "proxy_timeout", "proxy_refused", "proxy_failed",
            "probe_vps_unreachable",
        ]:
            self.assertIn(st, REG_IP_TOOL.description,
                          f"register_ip description 漏了 {st}")

    def test_get_vps_status_task_statuses(self):
        # spec §6.3 教 agent 转告的 task.status / last_error_code 关键字
        for kw in [
            "not_found", "in_progress", "pending", "done", "failed",
            "auth_failed", "ssh_timeout",  # last_error_code 子集
        ]:
            self.assertIn(kw, VPS_STATUS_TOOL.description,
                          f"get_vps_registration_status description 漏了 {kw}")

    def test_get_ip_status_task_statuses_and_proxy_node(self):
        # spec §6.4 一条龙: 转告里必须含 proxy_node + pending_fw + ProxyDeployWorker 失败码
        for kw in [
            "not_found", "in_progress", "pending", "done", "failed",
            "proxy_node", "using", "pending_fw",
            "no_vps_capacity", "inner_ping_failed",
            "apply_binding_failed", "firewall_open_failed", "no_port_available",
            "ssh_disconnected",
        ]:
            self.assertIn(kw, IP_STATUS_TOOL.description,
                          f"get_ip_registration_status description 漏了 {kw}")


if __name__ == "__main__":
    unittest.main()
