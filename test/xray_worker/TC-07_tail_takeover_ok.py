"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-07 _unified_tail 纳管一条 ping 通的代理出口 (spec v5.1 §4 步骤 4)

故事:
  extract_existing_outbounds 返一条 socks 代理出口:
    - test_internal 返 (True, "1.2.3.4") 即"通了 + 出口 IP=1.2.3.4"
    - _upsert_managed 写 ip_record (egress_ip=1.2.3.4, expire_date=NULL) + proxy_record (using)
    - used_port_count=1
  另外要补一条直进直出 (配置里没 freedom outbound)

测试矩阵:
  TC-07-a 1 条 socks 代理出口 + ping 通 → 写 1 行 ip_record + 1 行 proxy_record
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import IPRecord, ProxyRecord, ProxyStatus, VPSRecord
from workers.xray_worker import XrayWorker


def _make_in_memory_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(
        engine,
        tables=[VPSRecord.__table__, IPRecord.__table__, ProxyRecord.__table__],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


class TestTailTakeoverOk(unittest.TestCase):

    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

        @contextmanager
        def _fake_scope():
            s = self.Session()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        self._patcher = patch("workers.xray_worker.session_scope", _fake_scope)
        self._patcher.start()
        # 预先建一行 VPS
        with self.Session() as s:
            vps = VPSRecord.from_form(
                ip="10.0.0.1", username="root", password="pwd", port=22,
            )
            s.add(vps)
            s.commit()
            self.vps_id = vps.id

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc07a_managed_inbound_written(self):
        xray = MagicMock()
        xray.extract_existing_outbounds.return_value = [
            {
                "vps_port": 1080,
                "inbound_protocol": "socks",
                "inbound_user": "alice",
                "inbound_pwd": "alicepwd",
                "outbound_protocol": "socks",
                "upstream_host": "us-proxy.example.com",
                "upstream_port": 8080,
                "upstream_user": "u1",
                "upstream_pwd": "p1",
                "egress_ip": "",
                "egress_country": "",
            },
        ]
        xray.is_running.return_value = True
        xray.version.return_value = "Xray 26.3.27"
        client = MagicMock()

        cfg_with_proxy = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "client-1080",
                    "port": 1080,
                    "protocol": "socks",
                    "settings": {
                        "auth": "password",
                        "accounts": [{"user": "alice", "pass": "alicepwd"}],
                    },
                },
            ],
            "outbounds": [
                {
                    "tag": "proxy-out",
                    "protocol": "socks",
                    "settings": {
                        "servers": [
                            {
                                "address": "us-proxy.example.com",
                                "port": 8080,
                                "users": [{"user": "u1", "pass": "p1"}],
                            },
                        ],
                    },
                },
            ],
            "routing": {
                "rules": [
                    {
                        "type": "field",
                        "inboundTag": ["client-1080"],
                        "outboundTag": "proxy-out",
                    },
                ],
            },
        }

        with patch("workers.xray_worker.xc") as mock_xc, \
             patch("workers.xray_worker.test_internal", return_value=(True, "1.2.3.4")), \
             patch("workers.xray_worker.lookup_egress", return_value={
                 "country_code": "US",
                 "country_name": "United States",
                 "city": "Los Angeles",
                 "region_name": "California",
             }):
            mock_xc.is_config_blank.return_value = False
            mock_xc.read_config.return_value = cfg_with_proxy
            mock_xc.remove_proxy_binding.side_effect = lambda c, p: c

            worker = XrayWorker()
            result = worker._unified_tail(client, xray, vps_id=self.vps_id)

        with self.Session() as s:
            ip_rows = s.query(IPRecord).all()
            proxy_rows = s.query(ProxyRecord).all()
        self.assertEqual(len(ip_rows), 1)
        self.assertEqual(ip_rows[0].egress_ip, "1.2.3.4")
        self.assertEqual(ip_rows[0].country_code, "US")
        self.assertIsNone(ip_rows[0].expire_date)
        self.assertEqual(ip_rows[0].is_active, 1)
        self.assertEqual(ip_rows[0].entry_host, "us-proxy.example.com")
        self.assertEqual(ip_rows[0].entry_port, 8080)

        self.assertEqual(len(proxy_rows), 1)
        self.assertEqual(proxy_rows[0].vps_port, 1080)
        self.assertEqual(proxy_rows[0].status, ProxyStatus.USING)
        self.assertEqual(proxy_rows[0].ip_id, ip_rows[0].id)
        self.assertEqual(proxy_rows[0].egress_ip, "1.2.3.4")

        self.assertEqual(result["used_port_count"], 1)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
