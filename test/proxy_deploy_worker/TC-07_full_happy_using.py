"""
========================================================================
TC-07 全链路 happy path: 内通 + 外通 → done + using (spec §3 §6)

故事:
  挑机 → 挑端口 → apply_proxy_binding 成功 → firewall 放行成功
  → 内 ping 通 → 外 ping 通
  → 收尾: 同事务一次写 proxy_record / vps / task 3 表 (ADR-0010 删 ip.status 后)

测试矩阵 (对外协议 = Shadowsocks, ADR-0011):
  TC-07-a 返回 {"status":"done", task_id, vps_id, vps_port, outer_ping_ok=True}
  TC-07-b proxy_record INSERT: status=USING, vps_id/vps_port/ip_id 正确, protocol=shadowsocks
  TC-07-c proxy_record.method = aes-256-gcm (config.SS_METHOD) + inbound_user 留空 (SS 无 user)
  TC-07-d proxy_record.inbound_pwd_encrypted: 解密后长度 == 32 (uuid4().hex SS password)
  TC-07-e proxy_record 存在且 ip_id 指向这条 IP (替代旧 ip.status 断言, ADR-0010)
  TC-07-f vps.used_port_count +1, vps.stage 从 running → connectable (释放)
  TC-07-g ip_task: in_progress → done, last_error_code 清空
  TC-07-h apply_proxy_binding 被调用一次(收 method/password), rollback 没被调
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import config as app_config
from db.models import (
    IPTask,
    ProxyProtocol,
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
    make_fake_ss_probe_cls,
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
            # SS 内 ping 通 + 外 ping 通
            patch(
                "workers.proxy_deploy_worker.ShadowsocksProbe",
                make_fake_ss_probe_cls(inner_ok=True, outer_ok=True),
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
            self.assertEqual(p.protocol, ProxyProtocol.SHADOWSOCKS)

    # ---------- TC-07-c ⭐ SS method + 无 inbound_user (ADR-0011) ----------
    def test_tc07c_ss_method_and_no_inbound_user(self):
        """SS 节点: method=config.SS_METHOD, inbound_user 留空 (SS 无 user)."""
        with self.Session() as s:
            p = s.query(ProxyRecord).first()
            self.assertEqual(p.method, app_config.SS_METHOD)
            self.assertEqual(p.method, "aes-256-gcm")
            self.assertEqual(p.inbound_user, "")

    # ---------- TC-07-d ⭐ SS password 长度 32 (uuid4.hex) ----------
    def test_tc07d_inbound_pwd_uuid4_hex_length(self):
        """SS password = uuid4().hex → 解密后必须 32 字符 (落 inbound_pwd 槽)."""
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

    # ---------- TC-07-i ⭐ apply 收到 SS method/password (ADR-0011) ----------
    def test_tc07i_apply_called_with_method_password(self):
        """apply_proxy_binding(vps_port, outbound, method, password): 第 3 参=method, 第 4=password."""
        args, kwargs = self.fake_xm.apply_proxy_binding.call_args
        # 兼容位置/关键字两种传法
        method = kwargs.get("method", args[2] if len(args) > 2 else None)
        password = kwargs.get("password", args[3] if len(args) > 3 else None)
        self.assertEqual(method, app_config.SS_METHOD)
        self.assertIsNotNone(password)
        self.assertEqual(len(password), 32)


if __name__ == "__main__":
    unittest.main()
