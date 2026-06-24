"""
========================================================================
TC-15 非法 inbound protocol → failed(apply_binding_failed) 不重试
(ADR-0011 方案 A; review LOW#2)

故事:
  add_proxy_binding 收到未识别的对外 inbound protocol → 抛 InboundProtocolError。
  这是配置错(不是网络抖动) → 应归 _APPLY_BINDING_ERRORS → failed(apply_binding_failed) 终态,
  绝不能泡到外层 except Exception → retriable 被当网络抖动重试。

测试:
  TC-15-a process_task 返回 status=failed + last_error_code='apply_binding_failed'
          (而不是 retriable / ssh_disconnected)
  TC-15-b ip_task 落 FAILED 终态 + last_error_code='apply_binding_failed'
          + last_error_msg 含 'InboundProtocolError'
  TC-15-c vps.stage 保持 running (失败不释放)
  TC-15-d 无 proxy_record 写入
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import IPTask, ProxyRecord, TaskStatus, VPSRecord, VPSStage
from workers.proxy_deploy_worker import ProxyDeployWorker
from xray.config import InboundProtocolError

from ._helpers import (
    insert_ip,
    insert_ip_task,
    insert_vps,
    make_fake_session_scope,
    make_fake_ss_probe_cls,
    make_fake_vps_session_cls,
    make_in_memory_engine,
)


class TestInboundProtocolError(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()

        self.fake_xm = MagicMock()
        # apply 阶段抛 InboundProtocolError (模拟非法 protocol 透传到 add_proxy_binding)
        self.fake_xm.apply_proxy_binding.side_effect = InboundProtocolError(
            "fake unsupported inbound protocol 'vmess'"
        )

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
            patch(
                "workers.proxy_deploy_worker.firewall.open_tcp_port_range",
                return_value="firewalld",
            ),
            # apply 先抛错, probes 走不到; 留默认即可
            patch(
                "workers.proxy_deploy_worker.ShadowsocksProbe",
                make_fake_ss_probe_cls(inner_ok=True, outer_ok=True),
            ),
        ]
        for p in self._patches:
            p.start()

        with self.Session() as s:
            self.ip = insert_ip(s)
            self.task = insert_ip_task(s, self.ip.id)
            self.vps = insert_vps(s)
            s.commit()
            self.task_id = self.task.id
            self.vps_id = self.vps.id

        self.worker = ProxyDeployWorker()
        self.result = self.worker.process_task(self.task_id)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    def test_tc15a_returns_failed_apply_binding_not_retriable(self):
        self.assertEqual(self.result["status"], "failed")
        self.assertEqual(self.result["last_error_code"], "apply_binding_failed")

    def test_tc15b_task_failed_terminal_with_detail(self):
        with self.Session() as s:
            t = s.get(IPTask, self.task_id)
            self.assertEqual(t.status, TaskStatus.FAILED)
            self.assertEqual(t.last_error_code, "apply_binding_failed")
            self.assertIn("InboundProtocolError", t.last_error_msg)

    def test_tc15c_vps_stage_remains_running(self):
        with self.Session() as s:
            vps = s.get(VPSRecord, self.vps_id)
            self.assertEqual(vps.stage, VPSStage.RUNNING)

    def test_tc15d_no_proxy_record_inserted(self):
        with self.Session() as s:
            self.assertEqual(s.query(ProxyRecord).count(), 0)


if __name__ == "__main__":
    unittest.main()
