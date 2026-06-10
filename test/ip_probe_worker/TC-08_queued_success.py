"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-08 ⑦ 入库成功 + 派任务 (spec v2 §3 ⑦+⑧, §9 不变量)

故事:
  test_internal_socks 通 + body=实测出口 IP, lookup_egress 返回 country 信息。
  process() 应:
    - 入 ip_record: status=usable, is_active=1, egress_ip=actual, country_* 来自 geo
    - 派 ip_task: status=pending, ip_id=新 id, vps_id=NULL
    - 拆 19000 残留 (rollback 被调)
    - 返回 status=queued + ip_id + task_id
    - 不入库声明值 (declared_egress_ip 跟 actual 不一致时, 库里只见 actual)

测试矩阵 (6 TC):
  TC-08-a 返回 status=queued + ip_id + task_id
  TC-08-b ip_record 入库: status=usable, is_active=1, egress_ip=actual, country=US
  TC-08-c ip_task 入库: status=pending, ip_id=新 id, vps_id=NULL
  TC-08-d 拆残留: rollback_proxy_binding(PROBE_TEST_PORT, last_config) 被调
  TC-08-e 不变量: 入库的 egress_ip 是 actual 不是 declared (即便 declared 给了别的值)
  TC-08-f password 落盘加密 (原生 SQL 查 password_encrypted 不含明文)
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import text

from db.models import IPRecord, IPTask, TaskStatus
from workers.ip_probe_worker import IPProbeWorker

from ._helpers import (
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_geo,
    make_in_memory_engine,
    make_internal_socks_result,
)


_ACTUAL_EGRESS = "203.0.113.42"


class TestQueuedSuccess(unittest.TestCase):
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
                    ok=True, http_code=200, body=_ACTUAL_EGRESS,
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
        # 故意 declared 跟 actual 不一致, 验证不变量
        self.result = self.worker.process(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="alice",
            password="real-secret",
            protocol="socks5",
            declared_egress_ip="198.51.100.99",  # 故意错的, 应被忽略 (库无此条)
            provider_domain="iproyal.com",
        )

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    # ---------- TC-08-a ----------
    def test_tc08a_returns_queued_with_ids(self):
        self.assertEqual(self.result["status"], "queued")
        self.assertIn("ip_id", self.result)
        self.assertIn("task_id", self.result)
        self.assertEqual(self.result["egress_ip"], _ACTUAL_EGRESS)

    # ---------- TC-08-b ----------
    def test_tc08b_ip_record_fields(self):
        with self.Session() as s:
            recs = s.query(IPRecord).all()
            self.assertEqual(len(recs), 1)
            rec = recs[0]
            self.assertEqual(rec.is_active, 1)
            self.assertEqual(rec.egress_ip, _ACTUAL_EGRESS)
            self.assertEqual(rec.country_code, "US")
            self.assertEqual(rec.country_name, "United States")
            self.assertEqual(rec.provider_domain, "iproyal.com")

    # ---------- TC-08-c ----------
    def test_tc08c_ip_task_fields(self):
        with self.Session() as s:
            tasks = s.query(IPTask).all()
            self.assertEqual(len(tasks), 1)
            t = tasks[0]
            self.assertEqual(t.status, TaskStatus.PENDING)
            self.assertEqual(t.status, "pending")
            self.assertIsNone(t.vps_id, "spec §9: vps_id 必须 NULL")
            # ip_id 指向刚入的 ip_record
            rec_id = s.query(IPRecord).first().id
            self.assertEqual(t.ip_id, rec_id)

    # ---------- TC-08-d ----------
    def test_tc08d_rollback_called_to_clean_residue(self):
        from probe_vps import PROBE_TEST_PORT
        self.fake_xm.rollback_proxy_binding.assert_called_once()
        args, _ = self.fake_xm.rollback_proxy_binding.call_args
        self.assertEqual(args[0], PROBE_TEST_PORT)

    # ---------- TC-08-e ----------
    def test_tc08e_declared_value_not_persisted(self):
        """declared='198.51.100.99' 但入库 egress_ip=actual='203.0.113.42'。"""
        with self.Session() as s:
            self.assertEqual(s.query(IPRecord).count(), 1)
            rec = s.query(IPRecord).first()
            self.assertEqual(rec.egress_ip, _ACTUAL_EGRESS)
            self.assertNotEqual(rec.egress_ip, "198.51.100.99")

    # ---------- TC-08-f ----------
    def test_tc08f_password_encrypted_in_storage(self):
        """原生 SQL 查 password_encrypted, 不应含明文 'real-secret'。"""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT password_encrypted FROM ip_record")
            ).first()
            self.assertIsNotNone(row)
            self.assertNotIn(b"real-secret", row[0])


if __name__ == "__main__":
    unittest.main()
