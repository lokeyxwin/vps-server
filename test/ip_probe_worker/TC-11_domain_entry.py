"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-11 域名入口 entry_host (spec v2 §10 边界)

故事:
  entry_host 是域名(如 "proxy.miluproxy.com")而非 IP。
  process() 流程跟 IP 入口完全一样, xray 自己解析域名;
  实测出口 IP 跟入口主机不可能相等 (域名 ≠ IP), 不当问题。

测试矩阵 (2 TC):
  TC-11-a 域名入口 + 内 ping 通 → 入库成功, entry_host 落库是域名串
  TC-11-b build_proxy_outbound 透传域名给 xray (server.address 是域名)
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPRecord
from workers.ip_probe_worker import IPProbeWorker

from ._helpers import (
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_geo,
    make_in_memory_engine,
    make_internal_socks_result,
)


_ACTUAL = "203.0.113.42"
_DOMAIN = "proxy.miluproxy.com"


class TestDomainEntry(unittest.TestCase):
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

        self.worker = IPProbeWorker()
        self.result = self.worker.process(
            entry_host=_DOMAIN,
            entry_port=1080,
            username="alice",
            password="secret",
            protocol="socks5",
        )

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    # ---------- TC-11-a ----------
    def test_tc11a_domain_entry_persists_as_string(self):
        self.assertEqual(self.result["status"], "queued")
        with self.Session() as s:
            rec = s.query(IPRecord).first()
            self.assertIsNotNone(rec)
            self.assertEqual(rec.entry_host, _DOMAIN)
            # 实测出口 IP 跟入口主机自然不同
            self.assertEqual(rec.egress_ip, _ACTUAL)
            self.assertNotEqual(rec.egress_ip, _DOMAIN)

    # ---------- TC-11-b ----------
    def test_tc11b_build_proxy_outbound_received_domain(self):
        """replace_proxy_binding 收到的 outbound dict 里 server.address 应是域名串。"""
        self.fake_xm.replace_proxy_binding.assert_called_once()
        args, _ = self.fake_xm.replace_proxy_binding.call_args
        outbound = args[1]
        srv = outbound["settings"]["servers"][0]
        self.assertEqual(srv["address"], _DOMAIN)
        self.assertEqual(srv["port"], 1080)


if __name__ == "__main__":
    unittest.main()
