"""
========================================================================
TC-07 全链路 happy path: 内通 + 外通 → done + using (spec §3 §6)

故事:
  挑机 → 挑端口 → apply_proxy_binding 成功 → firewall 放行成功
  → 内 ping 通 → 外 ping 通
  → 收尾: 同事务一次写 proxy_record / vps / task 3 表 (ADR-0010 删 ip.status 后)

测试矩阵 (含 inbound 账密 2 个断言, 需求窗口 2026-06-09 拍板):
  TC-07-a 返回 {"status":"done", task_id, vps_id, vps_port, outer_ping_ok=True}
  TC-07-b proxy_record INSERT: status=USING, vps_id/vps_port/ip_id 正确
  TC-07-c proxy_record.inbound_user = "proxy_{ip.id}" ⭐ 账密规则验证
  TC-07-d proxy_record.inbound_pwd_encrypted: 解密后长度 == 32 (uuid4().hex)
  TC-07-e proxy_record 存在且 ip_id 指向这条 IP (替代旧 ip.status 断言, ADR-0010)
  TC-07-f vps.used_port_count +1, vps.stage 从 running → connectable (释放)
  TC-07-g ip_task: in_progress → done, last_error_code 清空
  TC-07-h apply_proxy_binding 被调用一次, rollback 没被调
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import (
    IPTask,
    ProxyRecord,
    ProxyStatus,
    TaskStatus,
    VPSRecord,
    VPSStage,
)
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


class TestFullHappyUsing(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()

        self.fake_xm = MagicMock(name="XrayManager")
        # apply 成功返回一个伪 last_config
        self.fake_xm.apply_proxy_binding.return_value = {"_baked": "cfg"}

        self._patches = [
            patch(
                "workers.proxy_deploy_worker.session_scope",
                make_fake_session_scope(self.Session),
            ),
            patch(
                "workers.proxy_deploy_worker.VPSSession",
                make_fake_vps_session_cls(),
            ),
            patch(
                "workers.proxy_deploy_worker.XrayManager",
                return_value=self.fake_xm,
            ),
            patch(
                "workers.proxy_deploy_worker.get_used_ports",
                return_value=set(),
            ),
            # firewall 静默成功
            patch(
                "workers.proxy_deploy_worker.firewall.open_tcp_port_range",
                return_value="firewalld",
            ),
            # 内 ping 通
            patch(
                "workers.proxy_deploy_worker.test_internal",
                return_value=(True, "203.0.113.42"),
            ),
            # 外 ping 通
            patch(
                "workers.proxy_deploy_worker.test_external",
                return_value=True,
            ),
        ]
        for p in self._patches:
            p.start()

        with self.Session() as s:
            self.ip = insert_ip(s, egress_ip="2.2.2.2", country_code="SG")
            self.task = insert_ip_task(s, self.ip.id)
            self.vps = insert_vps(s, ip="10.0.0.1")
            s.commit()
            self.task_id = self.task.id
            self.vps_id = self.vps.id
            self.ip_id = self.ip.id

        self.worker = ProxyDeployWorker()
        self.result = self.worker.process_task(self.task_id)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    # ---------- TC-07-a ----------
    def test_tc07a_returns_done(self):
        self.assertEqual(self.result["status"], "done")
        self.assertEqual(self.result["task_id"], self.task_id)
        self.assertEqual(self.result["vps_id"], self.vps_id)
        self.assertIn("vps_port", self.result)
        self.assertTrue(self.result["outer_ping_ok"])

    # ---------- TC-07-b ----------
    def test_tc07b_proxy_record_inserted_with_using(self):
        with self.Session() as s:
            recs = s.query(ProxyRecord).all()
            self.assertEqual(len(recs), 1)
            p = recs[0]
            self.assertEqual(p.vps_id, self.vps_id)
            self.assertEqual(p.ip_id, self.ip_id)
            self.assertEqual(p.status, ProxyStatus.USING)
            self.assertEqual(p.egress_ip, "2.2.2.2")
            self.assertEqual(p.egress_country, "SG")
            self.assertEqual(p.protocol, "socks5")

    # ---------- TC-07-c ⭐ inbound_user 业务命名 ----------
    def test_tc07c_inbound_user_business_naming(self):
        """inbound_user = f'proxy_{ip.id}' (2026-06-09 需求拍板)."""
        with self.Session() as s:
            p = s.query(ProxyRecord).first()
            self.assertEqual(p.inbound_user, f"proxy_{self.ip_id}")

    # ---------- TC-07-d ⭐ inbound_pwd 长度 32 (uuid4.hex) ----------
    def test_tc07d_inbound_pwd_uuid4_hex_length(self):
        """inbound_pwd = uuid4().hex → 解密后必须 32 字符."""
        with self.Session() as s:
            p = s.query(ProxyRecord).first()
            pwd = p.get_inbound_pwd()
            self.assertEqual(len(pwd), 32, f"uuid4().hex 长度必须 32, 实际 {len(pwd)}")
            # 全部是 hex 字符 (0-9a-f)
            self.assertTrue(all(c in "0123456789abcdef" for c in pwd))

    # ---------- TC-07-e ----------
    def test_tc07e_proxy_record_linked_to_ip(self):
        """ADR-0010: ip.status 字段删除后, '这条 IP 在用' 的真相源是 proxy_record."""
        with self.Session() as s:
            proxy = (
                s.query(ProxyRecord)
                .filter(ProxyRecord.ip_id == self.ip_id)
                .first()
            )
            self.assertIsNotNone(proxy,
                                 "完工后 proxy_record 必有一条 ip_id 关联本 IP")

    # ---------- TC-07-f ----------
    def test_tc07f_vps_count_plus_one_and_lock_released(self):
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.used_port_count, 1)
            self.assertEqual(vps.stage, VPSStage.CONNECTABLE,
                             "成功完工必须释放资源锁回 connectable")

    # ---------- TC-07-g ----------
    def test_tc07g_task_done_no_error(self):
        with self.Session() as s:
            t = s.get(IPTask, self.task_id)
            self.assertEqual(t.status, TaskStatus.DONE)
            self.assertEqual(t.last_error_code, "")
            self.assertEqual(t.last_error_msg, "")
            self.assertIsNotNone(t.completed_at)

    # ---------- TC-07-h ----------
    def test_tc07h_apply_called_rollback_not(self):
        self.fake_xm.apply_proxy_binding.assert_called_once()
        self.fake_xm.rollback_proxy_binding.assert_not_called()


if __name__ == "__main__":
    unittest.main()
