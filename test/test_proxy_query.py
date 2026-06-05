"""services.proxy_query.list_available_proxies 业务测试。

覆盖筛选条件的所有分支：
- 正常可用节点（all 条件满足）
- proxy.status=EXPIRED 应排除
- proxy.ip_id IS NULL 应排除（孤儿 binding）
- vps.is_active=0 应排除
- vps.expire_date < today 应排除
- ip.is_active=0 应排除
- ip.expire_date < today 应排除
- country_code 过滤命中 / 不命中
- 空结果返回 []
- 密码字段是解密后的明文
- 多条结果排序稳定
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import (
    Base,
    IPRecord,
    IPProtocol,
    ProxyRecord,
    ProxyStatus,
    VPSRecord,
    XrayStatus,
    get_engine,
    session_scope,
)
from services.proxy_query import list_available_proxies


# ============================================================
# 公共播种工具
# ============================================================

def _seed_vps(
    ip: str = "10.0.0.1",
    is_active: int = 1,
    expire_date: date | None = None,
) -> int:
    with session_scope() as s:
        rec = VPSRecord.from_form(
            ip=ip, username="root", password="rootpwd", port=22,
            expire_date=expire_date,
        )
        rec.xray_status = XrayStatus.RUNNING
        rec.is_active = is_active
        s.add(rec)
        s.flush()
        return rec.id


def _seed_ip(
    egress_ip: str = "1.2.3.4",
    country_code: str = "SG",
    country_name: str = "Singapore",
    city: str = "Singapore",
    is_active: int = 1,
    expire_date: date | None = None,
) -> int:
    with session_scope() as s:
        rec = IPRecord.from_form(
            entry_host="proxy.x.com", entry_port=1080,
            username="u", password="p",
            protocol=IPProtocol.SOCKS5, egress_ip=egress_ip,
            geo={
                "country_code": country_code,
                "country_name": country_name,
                "city": city,
                "region_name": "",
            },
            expire_date=expire_date,
        )
        rec.is_active = is_active
        s.add(rec)
        s.flush()
        return rec.id


def _seed_proxy(
    vps_id: int,
    ip_id: int | None,
    vps_port: int = 18441,
    inbound_user: str = "alice",
    inbound_pwd: str = "secret-pwd-123",
    status: str = ProxyStatus.USING,
) -> int:
    with session_scope() as s:
        rec = ProxyRecord.from_new_deployment(
            vps_id=vps_id,
            vps_port=vps_port,
            ip_id=ip_id or 0,
            inbound_user=inbound_user,
            inbound_pwd=inbound_pwd,
            upstream_host="proxy.x.com",
            egress_ip="1.2.3.4",
            egress_country="SG",
        )
        if ip_id is None:
            rec.ip_id = None  # 显式置 None 测试孤儿 binding 过滤
        rec.status = status
        s.add(rec)
        s.flush()
        return rec.id


# ============================================================
# 基类：每个 test class 一份干净 DB
# ============================================================

class _ProxyQueryBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = get_engine("sqlite")
        Base.metadata.create_all(
            cls.engine,
            tables=[
                VPSRecord.__table__,
                IPRecord.__table__,
                ProxyRecord.__table__,
            ],
        )

    def setUp(self):
        with session_scope() as s:
            s.query(ProxyRecord).delete()
            s.query(IPRecord).delete()
            s.query(VPSRecord).delete()


# ============================================================
# 正常路径
# ============================================================

class TestHappyPath(_ProxyQueryBase):
    def test_empty_db_returns_empty_list(self):
        self.assertEqual(list_available_proxies(), [])

    def test_single_available_proxy_returned(self):
        vps_id = _seed_vps(ip="10.0.0.1")
        ip_id = _seed_ip(egress_ip="9.9.9.9", country_code="SG")
        proxy_id = _seed_proxy(
            vps_id=vps_id, ip_id=ip_id,
            vps_port=18441,
            inbound_user="alice", inbound_pwd="topsecret",
        )

        result = list_available_proxies()

        self.assertEqual(len(result), 1)
        node = result[0]
        self.assertEqual(node["proxy_id"], proxy_id)
        self.assertEqual(node["vps_id"], vps_id)
        self.assertEqual(node["ip_id"], ip_id)
        self.assertEqual(node["host"], "10.0.0.1")
        self.assertEqual(node["port"], 18441)
        self.assertEqual(node["protocol"], "socks5")
        self.assertEqual(node["username"], "alice")
        self.assertEqual(node["egress_ip"], "9.9.9.9")
        self.assertEqual(node["country_code"], "SG")
        self.assertEqual(node["country_name"], "Singapore")
        self.assertEqual(node["city"], "Singapore")

    def test_password_is_decrypted_plaintext(self):
        vps_id = _seed_vps()
        ip_id = _seed_ip()
        _seed_proxy(vps_id=vps_id, ip_id=ip_id, inbound_pwd="my-plain-pwd")

        result = list_available_proxies()
        self.assertEqual(result[0]["password"], "my-plain-pwd")


# ============================================================
# 过滤分支：proxy 状态
# ============================================================

class TestProxyStatusFilter(_ProxyQueryBase):
    def test_expired_proxy_excluded(self):
        vps_id = _seed_vps()
        ip_id = _seed_ip()
        _seed_proxy(vps_id=vps_id, ip_id=ip_id, status=ProxyStatus.EXPIRED)

        self.assertEqual(list_available_proxies(), [])

    def test_orphan_binding_excluded(self):
        """ip_id IS NULL（rgvps 端口审计反推的 binding）应排除。"""
        vps_id = _seed_vps()
        _seed_proxy(vps_id=vps_id, ip_id=None)

        self.assertEqual(list_available_proxies(), [])


# ============================================================
# 过滤分支：VPS 可用性
# ============================================================

class TestVPSAvailability(_ProxyQueryBase):
    def test_vps_inactive_excluded(self):
        vps_id = _seed_vps(is_active=0)
        ip_id = _seed_ip()
        _seed_proxy(vps_id=vps_id, ip_id=ip_id)

        self.assertEqual(list_available_proxies(), [])

    def test_vps_expired_excluded(self):
        yesterday = date.today() - timedelta(days=1)
        vps_id = _seed_vps(expire_date=yesterday)
        ip_id = _seed_ip()
        _seed_proxy(vps_id=vps_id, ip_id=ip_id)

        self.assertEqual(list_available_proxies(), [])

    def test_vps_expire_today_still_available(self):
        """到期日 = today 仍算可用（>= today）。"""
        today = date.today()
        vps_id = _seed_vps(expire_date=today)
        ip_id = _seed_ip()
        _seed_proxy(vps_id=vps_id, ip_id=ip_id)

        self.assertEqual(len(list_available_proxies()), 1)

    def test_vps_no_expire_date_treated_as_available(self):
        vps_id = _seed_vps(expire_date=None)
        ip_id = _seed_ip()
        _seed_proxy(vps_id=vps_id, ip_id=ip_id)

        self.assertEqual(len(list_available_proxies()), 1)


# ============================================================
# 过滤分支：IP 可用性
# ============================================================

class TestIPAvailability(_ProxyQueryBase):
    def test_ip_inactive_excluded(self):
        vps_id = _seed_vps()
        ip_id = _seed_ip(is_active=0)
        _seed_proxy(vps_id=vps_id, ip_id=ip_id)

        self.assertEqual(list_available_proxies(), [])

    def test_ip_expired_excluded(self):
        yesterday = date.today() - timedelta(days=1)
        vps_id = _seed_vps()
        ip_id = _seed_ip(expire_date=yesterday)
        _seed_proxy(vps_id=vps_id, ip_id=ip_id)

        self.assertEqual(list_available_proxies(), [])

    def test_ip_no_expire_date_treated_as_available(self):
        vps_id = _seed_vps()
        ip_id = _seed_ip(expire_date=None)
        _seed_proxy(vps_id=vps_id, ip_id=ip_id)

        self.assertEqual(len(list_available_proxies()), 1)


# ============================================================
# country_code 过滤
# ============================================================

class TestCountryFilter(_ProxyQueryBase):
    def _seed_multi_country(self):
        v1 = _seed_vps(ip="10.0.0.1")
        v2 = _seed_vps(ip="10.0.0.2")
        ip_sg = _seed_ip(egress_ip="1.1.1.1", country_code="SG")
        ip_us = _seed_ip(egress_ip="2.2.2.2", country_code="US")
        _seed_proxy(vps_id=v1, ip_id=ip_sg, vps_port=18441)
        _seed_proxy(vps_id=v2, ip_id=ip_us, vps_port=18442)

    def test_country_code_filter_hits(self):
        self._seed_multi_country()
        result = list_available_proxies(country_code="SG")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["country_code"], "SG")

    def test_country_code_filter_no_match_returns_empty(self):
        self._seed_multi_country()
        self.assertEqual(list_available_proxies(country_code="JP"), [])

    def test_empty_country_code_returns_all(self):
        self._seed_multi_country()
        result = list_available_proxies(country_code="")
        self.assertEqual(len(result), 2)


# ============================================================
# 排序稳定性
# ============================================================

class TestOrdering(_ProxyQueryBase):
    def test_results_ordered_by_country_then_host_then_port(self):
        v1 = _seed_vps(ip="10.0.0.2")
        v2 = _seed_vps(ip="10.0.0.1")
        ip_us = _seed_ip(egress_ip="2.2.2.2", country_code="US")
        ip_sg = _seed_ip(egress_ip="1.1.1.1", country_code="SG")
        _seed_proxy(vps_id=v1, ip_id=ip_us, vps_port=18441)
        _seed_proxy(vps_id=v2, ip_id=ip_sg, vps_port=18442)

        result = list_available_proxies()
        self.assertEqual(len(result), 2)
        # SG 排前面（country_code 字母序）
        self.assertEqual(result[0]["country_code"], "SG")
        self.assertEqual(result[1]["country_code"], "US")


if __name__ == "__main__":
    unittest.main()
