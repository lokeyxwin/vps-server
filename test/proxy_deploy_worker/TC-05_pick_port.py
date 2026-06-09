"""
========================================================================
TC-05 挑端口算法: 排除清单 + 高位随机 (spec §5)

故事:
  _pick_port 在 VPS 上挑端口:
    候选池 = range(1024, 65536)
          - get_used_ports 返回的真实占用
          - COMMON_RESERVED_PORTS (即 EXCLUDED_PORTS)
          - {XRAY_DEFAULT_PORT=18440}
          - {该 VPS 已用 proxy_record.vps_port WHERE status='using'}
  从候选池随机挑一个.

测试矩阵:
  TC-05-a 排除 COMMON_RESERVED_PORTS: 不会挑到 22/443/3306 等
  TC-05-b 排除 XRAY_DEFAULT_PORT (18440)
  TC-05-c 排除该 VPS 上 proxy_record.vps_port (status='using')
  TC-05-d 排除 get_used_ports 报的真实占用
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from db.models import ProxyRecord, ProxyStatus
from workers.proxy_deploy_worker import ProxyDeployWorker

from ._helpers import (
    insert_ip,
    insert_vps,
    make_fake_session_scope,
    make_in_memory_engine,
)


class TestPickPort(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self._patcher = patch(
            "workers.proxy_deploy_worker.session_scope",
            make_fake_session_scope(self.Session),
        )
        self._patcher.start()

        with self.Session() as s:
            self.ip = insert_ip(s)
            self.vps = insert_vps(s, ip="10.0.0.1")
            s.commit()
            self.vps_id = self.vps.id
            self.ip_id = self.ip.id

        self.fake_client = MagicMock(name="ssh_client")

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc05a_excludes_common_reserved(self):
        from toolbox.ports import COMMON_RESERVED_PORTS
        with patch("workers.proxy_deploy_worker.get_used_ports", return_value=set()):
            picks = {
                ProxyDeployWorker._pick_port(self.fake_client, self.vps_id)
                for _ in range(50)
            }
        for p in picks:
            self.assertNotIn(p, COMMON_RESERVED_PORTS,
                             f"挑到了排除清单端口 {p}")

    def test_tc05b_excludes_xray_default_port(self):
        from config import XRAY_DEFAULT_PORT
        with patch("workers.proxy_deploy_worker.get_used_ports", return_value=set()):
            picks = {
                ProxyDeployWorker._pick_port(self.fake_client, self.vps_id)
                for _ in range(50)
            }
        self.assertNotIn(XRAY_DEFAULT_PORT, picks)

    def test_tc05c_excludes_already_bound_using(self):
        from toolbox.security import encrypt_password
        with self.Session() as s:
            # 该 VPS 上挂了一条 proxy_record 在端口 23456 (using)
            s.add(ProxyRecord(
                vps_id=self.vps_id, vps_port=23456, ip_id=self.ip_id,
                inbound_user="x", inbound_pwd_encrypted=encrypt_password("pwd"),
                upstream_host="up.example.com",
                status=ProxyStatus.USING,
            ))
            s.commit()
        with patch("workers.proxy_deploy_worker.get_used_ports", return_value=set()):
            picks = {
                ProxyDeployWorker._pick_port(self.fake_client, self.vps_id)
                for _ in range(50)
            }
        self.assertNotIn(23456, picks, "已挂的端口不应再挑")

    def test_tc05d_excludes_real_os_used_ports(self):
        """模拟 ss -tln 报 8081 在监听, 不应挑到它."""
        fake_used = {8081, 9999}
        with patch("workers.proxy_deploy_worker.get_used_ports", return_value=fake_used):
            picks = {
                ProxyDeployWorker._pick_port(self.fake_client, self.vps_id)
                for _ in range(50)
            }
        self.assertNotIn(8081, picks)
        self.assertNotIn(9999, picks)


if __name__ == "__main__":
    unittest.main()
