"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-01 ① 早期查重 _lookup_by_declared (spec v2 §3 ①)

故事:
  用户提交一条上游 IP 凭据, declared_egress_ip 已在 ip_record 里 →
  process() 立刻短路返回 status=duplicate, 不 SSH 不调任何工具。
  declared 不在库 → 不短路, 继续走 ②(本 TC 只测短路触发, 后续步骤交集成 TC)。

测试矩阵 (3 TC):
  TC-01-a 库有该 egress_ip → _lookup_by_declared 返回 dict, process 短路 duplicate
  TC-01-b 库无该 egress_ip → _lookup_by_declared 返回 None
  TC-01-c declared 空字符串 → process 跳过 ① 直接进 ②
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from db.models import IPRecord
from workers import ip_probe_worker as ipw
from workers.ip_probe_worker import IPProbeWorker, _ProbeVPSAllDownError

from ._helpers import make_fake_session_scope, make_in_memory_engine


class TestLookupDeclared(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self._scope_patcher = patch(
            "workers.ip_probe_worker.session_scope",
            make_fake_session_scope(self.Session),
        )
        self._scope_patcher.start()
        self.worker = IPProbeWorker()

    def tearDown(self):
        self._scope_patcher.stop()
        self.engine.dispose()

    def _seed_ip(self, egress_ip: str = "1.2.3.4") -> int:
        with self.Session() as s:
            rec = IPRecord.from_form(
                entry_host="proxy.example.com",
                entry_port=1080,
                username="u",
                password="p",
                protocol="socks5",
                egress_ip=egress_ip,
            )
            s.add(rec)
            s.commit()
            return rec.id

    # ---------- TC-01-a ----------
    def test_tc01a_existing_declared_short_circuits_duplicate(self):
        self._seed_ip("1.2.3.4")
        # 短路前不应调任何远端工具, 让 _pick_probe_vps 故意抛错验证没被调
        with patch.object(
            self.worker, "_pick_probe_vps",
            side_effect=AssertionError("不应进入 ②"),
        ):
            result = self.worker.process(
                entry_host="proxy.example.com",
                entry_port=1080,
                username="u",
                password="p",
                protocol="socks5",
                declared_egress_ip="1.2.3.4",
            )
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["egress_ip"], "1.2.3.4")
        self.assertIn("1.2.3.4", result["message"])

    # ---------- TC-01-b ----------
    def test_tc01b_no_match_returns_none(self):
        self.assertIsNone(self.worker._lookup_by_declared("9.9.9.9"))

    # ---------- TC-01-c ----------
    def test_tc01c_empty_declared_skips_to_step_two(self):
        """declared 空 → ① 整段跳过, 进入 ②。用 _pick_probe_vps 抛错截停证明进入 ②。"""
        with patch.object(
            self.worker, "_pick_probe_vps",
            side_effect=_ProbeVPSAllDownError("flag: 已进入 ②"),
        ):
            result = self.worker.process(
                entry_host="proxy.example.com",
                entry_port=1080,
                username="u",
                password="p",
                protocol="socks5",
                declared_egress_ip="",
            )
        # 因为 ② 抛了 _ProbeVPSAllDownError, 应该到 probe_vps_unreachable 分支
        self.assertEqual(result["status"], "probe_vps_unreachable")
        self.assertIn("已进入 ②", result["message"])


if __name__ == "__main__":
    unittest.main()
