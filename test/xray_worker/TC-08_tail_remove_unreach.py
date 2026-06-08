"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-08 _unified_tail 内 ping 不通 → remove 三件套, 不记表 (spec v5.1 §4 + ADR-0004 §4)

故事:
  extract_existing_outbounds 返一条 socks 代理出口:
    - test_internal 返 (False, "") 不通
    - 走 remove_proxy_binding(vps_port) 删 inbound + 路由 + 不再被引用的 outbound
    - ip_record / proxy_record 完全不写
    - used_port_count=0

测试矩阵:
  TC-08-a ping 不通 → remove_proxy_binding 被调一次, 表无新行
========================================================================
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import IPRecord, ProxyRecord, VPSRecord
from workers.xray_worker import XrayWorker


def _make_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(
        engine, tables=[VPSRecord.__table__, IPRecord.__table__, ProxyRecord.__table__],
    )
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


class TestTailRemoveUnreach(unittest.TestCase):

    def setUp(self):
        self.engine, self.Session = _make_engine()

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

        with self.Session() as s:
            vps = VPSRecord.from_form(ip="10.0.0.2", username="root", password="pwd", port=22)
            s.add(vps); s.commit()
            self.vps_id = vps.id

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc08a_ping_fail_calls_remove_not_persist(self):
        xray = MagicMock()
        xray.extract_existing_outbounds.return_value = [
            {
                "vps_port": 1080,
                "inbound_protocol": "socks",
                "inbound_user": "u",
                "inbound_pwd": "p",
                "outbound_protocol": "socks",
                "upstream_host": "dead.example.com",
                "upstream_port": 8080,
                "upstream_user": "",
                "upstream_pwd": "",
                "egress_ip": "",
                "egress_country": "",
            },
        ]
        xray.is_running.return_value = True
        xray.version.return_value = "Xray 26.3.27"
        client = MagicMock()

        cfg = {
            "log": {"loglevel": "warning"},
            "inbounds": [{"tag": "client-1080", "port": 1080, "protocol": "socks"}],
            "outbounds": [{"tag": "p1", "protocol": "socks"}],
            "routing": {"rules": [{"type": "field", "inboundTag": ["client-1080"], "outboundTag": "p1"}]},
        }
        sentinel_after_remove = {"removed": True, **cfg}

        with patch("workers.xray_worker.xc") as mock_xc, \
             patch("workers.xray_worker.test_internal", return_value=(False, "")):
            mock_xc.is_config_blank.return_value = False
            mock_xc.read_config.return_value = cfg
            mock_xc.remove_proxy_binding.return_value = sentinel_after_remove

            worker = XrayWorker()
            result = worker._unified_tail(client, xray, vps_id=self.vps_id)

            mock_xc.remove_proxy_binding.assert_called_once()
            # 第一个位置参数是 cfg, 第二个是 vps_port
            call_args = mock_xc.remove_proxy_binding.call_args.args
            self.assertEqual(call_args[1], 1080)

        with self.Session() as s:
            self.assertEqual(s.query(IPRecord).count(), 0)
            self.assertEqual(s.query(ProxyRecord).count(), 0)

        self.assertEqual(result["used_port_count"], 0)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
