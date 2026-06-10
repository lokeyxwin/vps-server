"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-09 ⑥ 二次查重 duplicate_by_actual (spec v2 §3 ⑥)

故事:
  declared_egress_ip 跟库里都没命中 (① 早期查重不短路)。
  挂 outbound + 内 ping 通, 拿到实测 actual_egress_ip。
  但库里**实际已存在** actual_egress_ip (用户报错了声明值, 实测撞库)。
  process() 应:
    - 走完 ⑥ 二次查重命中, status=duplicate
    - 清残留, 不入库 (不新增 IPRecord / IPTask)
    - message 含"实测"二字

测试矩阵 (3 TC):
  TC-09-a 预先 seed 一条 actual 的 IPRecord → process duplicate 命中
  TC-09-b 不新增 ip_record / ip_task (count 不变)
  TC-09-c rollback_proxy_binding 仍被调
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPRecord, IPTask
from workers.ip_probe_worker import IPProbeWorker

from ._helpers import (
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_geo,
    make_in_memory_engine,
    make_internal_socks_result,
)


_ACTUAL = "203.0.113.42"


class TestDuplicateByActual(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self.fake_xm = MagicMock()
        self.fake_xm.replace_proxy_binding.return_value = {"_baked": "cfg"}
        FakeSess = make_fake_vps_session_cls()

        self._patches = [
            patch(
                "workers.ip_probe_worker.session_scope",
                make_fake_session_scope(self.Session),
            ),
            patch(
                "workers.ip_probe_worker.get_probe_vps_pool",
                return_value=(
                    {"ip": "10.0.0.1", "port": 22, "username": "root", "password": "x"},
                ),
            ),
            patch("workers.ip_probe_worker.VPSSession", FakeSess),
            patch("workers.ip_probe_worker.XrayManager", return_value=self.fake_xm),
            # ADR-0009: 跳过测试机自举 (单独测见 test/probe_vps/TC-*).
            patch("workers.ip_probe_worker.bootstrap.ensure_ready", return_value=None),
            patch(
                "workers.ip_probe_worker.test_internal_socks",
                return_value=make_internal_socks_result(
                    ok=True, http_code=200, body=_ACTUAL,
                    exit_code=0, stderr="",
                ),
            ),
            patch(
                "workers.ip_probe_worker.lookup_egress",
                return_value=make_geo("US"),
            ),
        ]
        for p in self._patches:
            p.start()

        # 预 seed: 库已有 actual 这条
        with self.Session() as s:
            existing = IPRecord.from_form(
                entry_host="old-proxy.example.com",
                entry_port=2080,
                username="old",
                password="old-pwd",
                protocol="socks5",
                egress_ip=_ACTUAL,
            )
            s.add(existing)
            s.commit()

        self.worker = IPProbeWorker()
        # declared 故意不同, 让 ① 不命中, 走到 ⑥
        self.result = self.worker.process(
            entry_host="new-proxy.example.com",
            entry_port=1080,
            username="alice",
            password="real",
            protocol="socks5",
            declared_egress_ip="198.51.100.99",  # 跟 actual 不一致, 故意
        )

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    # ---------- TC-09-a ----------
    def test_tc09a_returns_duplicate_with_actual_egress(self):
        self.assertEqual(self.result["status"], "duplicate")
        self.assertEqual(self.result["egress_ip"], _ACTUAL)
        self.assertIn("实测", self.result["message"])
        self.assertIn(_ACTUAL, self.result["message"])

    # ---------- TC-09-b ----------
    def test_tc09b_no_new_ip_record_or_task(self):
        with self.Session() as s:
            # 预 seed 那 1 条还在, 但没新增
            self.assertEqual(s.query(IPRecord).count(), 1)
            self.assertEqual(s.query(IPTask).count(), 0)

    # ---------- TC-09-c ----------
    def test_tc09c_rollback_called(self):
        self.fake_xm.rollback_proxy_binding.assert_called_once()


if __name__ == "__main__":
    unittest.main()
