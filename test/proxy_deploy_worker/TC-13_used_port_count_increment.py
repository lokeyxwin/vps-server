"""
========================================================================
TC-13 used_port_count +1 只在 done 时发生 (spec §9 不变量 #5)

故事:
  spec §9: "used_port_count += 1 只在 done 时发生; 失败一律不 +1
   (即使 xray 配置短暂存在过, rollback 后端口实际没占)."

测试:
  TC-13-a 成功 done → vps.used_port_count +1
  TC-13-b 内 ping 失败 → vps.used_port_count 不变 (rollback 后端口没占)
  TC-13-c 半成功 (外不通 pending_fw) → +1 (端口实际占了)
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import VPSRecord
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


def _setup_session_data(Session):
    with Session() as s:
        ip = insert_ip(s)
        task = insert_ip_task(s, ip.id)
        # 起始计数 0
        vps = insert_vps(s, ip="10.0.0.1", used_port_count=0)
        s.commit()
        return task.id, vps.id


def _run_once(Session, *, inner_ok: bool, outer_ok: bool):
    fake_xm = MagicMock()
    fake_xm.apply_proxy_binding.return_value = {"_baked": "cfg"}
    patches = [
        patch("workers.proxy_deploy_worker.session_scope",
              make_fake_session_scope(Session)),
        patch("workers.proxy_deploy_worker.VPSSession",
              make_fake_vps_session_cls()),
        patch("workers.proxy_deploy_worker.XrayManager", return_value=fake_xm),
        patch("workers.proxy_deploy_worker.get_used_ports", return_value=set()),
        patch("workers.proxy_deploy_worker.firewall.open_tcp_port_range",
              return_value="firewalld"),
        patch("workers.proxy_deploy_worker.ShadowsocksProbe",
              make_fake_ss_probe_cls(
                  inner_ok=inner_ok,
                  inner_egress="1.1.1.1" if inner_ok else "",
                  outer_ok=outer_ok,
              )),
    ]
    for p in patches:
        p.start()
    try:
        task_id, vps_id = _setup_session_data(Session)
        ProxyDeployWorker().process_task(task_id)
        return task_id, vps_id
    finally:
        for p in patches:
            p.stop()


class TestUsedPortCountIncrement(unittest.TestCase):

    def test_tc13a_done_increments(self):
        engine, Session = make_in_memory_engine()
        try:
            _, vps_id = _run_once(Session, inner_ok=True, outer_ok=True)
            with Session() as s:
                vps = s.get(VPSRecord, vps_id)
                self.assertEqual(vps.used_port_count, 1)
        finally:
            engine.dispose()

    def test_tc13b_failure_does_not_increment(self):
        engine, Session = make_in_memory_engine()
        try:
            _, vps_id = _run_once(Session, inner_ok=False, outer_ok=True)
            with Session() as s:
                vps = s.get(VPSRecord, vps_id)
                self.assertEqual(vps.used_port_count, 0,
                                 "rollback 后端口实际没占, 计数不 +1")
        finally:
            engine.dispose()

    def test_tc13c_pending_fw_still_increments(self):
        engine, Session = make_in_memory_engine()
        try:
            _, vps_id = _run_once(Session, inner_ok=True, outer_ok=False)
            with Session() as s:
                vps = s.get(VPSRecord, vps_id)
                self.assertEqual(vps.used_port_count, 1,
                                 "外不通仍算完工, 端口实际占了, +1")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
