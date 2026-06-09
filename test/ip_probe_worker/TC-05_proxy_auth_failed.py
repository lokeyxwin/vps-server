"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-05 ④ proxy_auth_failed 路径 (spec v2 §7)

故事:
  test_internal_socks 返回 ok=False + exit_code=97 (CURLE_PROXY, socks auth 失败)。
  process() 应:
    - 不重试(spec §7: auth_failed 不重试)
    - status=proxy_auth_failed + 温馨文案(含 OCR 提示)
    - 清残留 (rollback_proxy_binding 被调)
    - 不入库

测试矩阵 (3 TC):
  TC-05-a status / message / OCR 关键字
  TC-05-b rollback_proxy_binding 被调 1 次, PROBE_TEST_PORT 参数
  TC-05-c ip_record / ip_task 表都没新增行 (不入库)
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
    make_in_memory_engine,
    make_internal_socks_result,
)


class TestProxyAuthFailed(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self.fake_xm = MagicMock(name="XrayManager_instance")
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
                    ok=False, http_code=None, body="",
                    exit_code=97, stderr="",
                ),
            ),
        ]
        for p in self._patches:
            p.start()

        self.worker = IPProbeWorker()
        self.result = self.worker.process(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="alice",
            password="wrong-pwd",
            protocol="socks5",
        )

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    # ---------- TC-05-a ----------
    def test_tc05a_status_and_message_with_ocr_hint(self):
        self.assertEqual(self.result["status"], "proxy_auth_failed")
        msg = self.result["message"]
        self.assertIn("proxy.example.com", msg)
        self.assertIn("1080", msg)
        # OCR 关键字
        self.assertIn("0/O", msg)
        self.assertIn("1/l/I", msg)
        # 区分服务商面板密码 vs 代理认证密码
        self.assertIn("服务商面板", msg)

    # ---------- TC-05-b ----------
    def test_tc05b_rollback_proxy_binding_called(self):
        from probe_vps import PROBE_TEST_PORT
        self.fake_xm.rollback_proxy_binding.assert_called_once()
        args, _ = self.fake_xm.rollback_proxy_binding.call_args
        self.assertEqual(args[0], PROBE_TEST_PORT)
        self.assertEqual(args[1], {"_baked": "cfg"})

    # ---------- TC-05-c ----------
    def test_tc05c_no_db_writes(self):
        with self.Session() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)
            self.assertEqual(s.query(IPTask).count(), 0)


if __name__ == "__main__":
    unittest.main()
