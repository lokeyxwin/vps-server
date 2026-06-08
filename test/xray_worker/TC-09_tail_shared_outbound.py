"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-09 _unified_tail 共享 outbound (1 通 1 不通) (spec v5.1 §4 + ADR-0004 §5)

故事:
  2 条 inbound 共享同一个上游 outbound:
    - inbound A (port=1080) test_internal 返 (True, "1.2.3.4") → 纳管入库
    - inbound B (port=1081) test_internal 返 (False, "")        → remove_proxy_binding(1081)
  remove_proxy_binding 已经实现"防御性 outbound 引用计数",
  共享 outbound 还被 A 引用 → outbound 保留, 只删 B 自己的 inbound + 路由。
  最终: used_count=1, ip_record 1 行, proxy_record 1 行(vps_port=1080)。

测试矩阵:
  TC-09-a 一通一不通 → 通的写库, 不通的 remove
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


_SHARED_OUTBOUND_CFG = {
    "log": {"loglevel": "warning"},
    "inbounds": [
        {"tag": "client-1080", "port": 1080, "protocol": "socks"},
        {"tag": "client-1081", "port": 1081, "protocol": "socks"},
    ],
    "outbounds": [
        {
            "tag": "shared-proxy",
            "protocol": "socks",
            "settings": {
                "servers": [{
                    "address": "shared.example.com",
                    "port": 8080,
                    "users": [{"user": "u", "pass": "p"}],
                }],
            },
        },
    ],
    "routing": {
        "rules": [
            {"type": "field", "inboundTag": ["client-1080"], "outboundTag": "shared-proxy"},
            {"type": "field", "inboundTag": ["client-1081"], "outboundTag": "shared-proxy"},
        ],
    },
}


class TestTailSharedOutbound(unittest.TestCase):

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
            vps = VPSRecord.from_form(ip="10.0.0.3", username="root", password="pwd", port=22)
            s.add(vps); s.commit()
            self.vps_id = vps.id

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()

    def test_tc09a_one_alive_one_dead(self):
        entries = [
            {
                "vps_port": 1080, "inbound_protocol": "socks",
                "inbound_user": "a", "inbound_pwd": "a",
                "outbound_protocol": "socks",
                "upstream_host": "shared.example.com", "upstream_port": 8080,
                "upstream_user": "u", "upstream_pwd": "p",
                "egress_ip": "", "egress_country": "",
            },
            {
                "vps_port": 1081, "inbound_protocol": "socks",
                "inbound_user": "b", "inbound_pwd": "b",
                "outbound_protocol": "socks",
                "upstream_host": "shared.example.com", "upstream_port": 8080,
                "upstream_user": "u", "upstream_pwd": "p",
                "egress_ip": "", "egress_country": "",
            },
        ]
        xray = MagicMock()
        xray.extract_existing_outbounds.return_value = entries
        xray.is_running.return_value = True
        xray.version.return_value = "Xray 26.3.27"
        client = MagicMock()

        # test_internal: 1080 通 (返 egress=1.2.3.4), 1081 不通
        ping_results = {1080: (True, "1.2.3.4"), 1081: (False, "")}
        def fake_ping(client, port, user, pwd):  # noqa: ARG001
            return ping_results[port]

        # 真函数 remove_proxy_binding 测它的智能保留行为
        from xray import config as real_xc

        with patch("workers.xray_worker.xc") as mock_xc, \
             patch("workers.xray_worker.test_internal", side_effect=fake_ping), \
             patch("workers.xray_worker.lookup_egress", return_value={
                 "country_code": "US", "country_name": "United States",
                 "city": "LA", "region_name": "CA",
             }):
            mock_xc.is_config_blank.return_value = False
            mock_xc.read_config.return_value = _SHARED_OUTBOUND_CFG
            # 用真 remove_proxy_binding 实现, 验证它的引用计数行为
            mock_xc.remove_proxy_binding.side_effect = real_xc.remove_proxy_binding

            worker = XrayWorker()
            result = worker._unified_tail(client, xray, vps_id=self.vps_id)

            uploaded = xray.upload_config.call_args.args[0]
            # 1081 应该被 remove (inbound + 路由)
            inbound_tags = {inb.get("tag") for inb in uploaded.get("inbounds", [])}
            self.assertNotIn("client-1081", inbound_tags)
            # 1080 inbound 仍在 (它通了, 被纳管而不是 remove)
            self.assertIn("client-1080", inbound_tags)
            # shared-proxy outbound 仍在 (被 1080 引用, 引用计数保留)
            outbound_tags = {ob.get("tag") for ob in uploaded.get("outbounds", [])}
            self.assertIn("shared-proxy", outbound_tags)

        with self.Session() as s:
            ip_rows = s.query(IPRecord).all()
            proxy_rows = s.query(ProxyRecord).all()
        self.assertEqual(len(ip_rows), 1)
        self.assertEqual(ip_rows[0].egress_ip, "1.2.3.4")
        self.assertEqual(len(proxy_rows), 1)
        self.assertEqual(proxy_rows[0].vps_port, 1080)

        self.assertEqual(result["used_port_count"], 1)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
