"""TC-13 get_registered_ips —— 列全量已登记 IP(过期 + 未过期)只读查询工具.

验收金标准: test/mcp_tools/spec.md §6.7
任务单: task/done_24_get_registered_ips.md
关联 ADR: ADR-0010(ip 无 status 字段, 过期看 is_active + expire_date)

4 个用例:
  TC-13-a test_returns_all_active_and_expired   过期 + 未过期 + 纳管(expire=null)都返
  TC-13-b test_shape_has_ip_id_no_password ⭐    含 ip_id, 绝不含密码/上游凭据
  TC-13-c test_empty_db_returns_empty_list       空库返 []
  TC-13-d test_tool_registered                   ALL_TOOLS 注册 + name == stem + 只读
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import queries
from db.base import Base
from db.models import (
    IPRecord,
    IPTask,
    ProxyRecord,
    VPSRecord,
    VPSTask,
)


@pytest.fixture
def db_session(monkeypatch):
    """in-memory SQLite + create_all 5 张表, monkeypatch queries.session_scope.

    yield 一个 Session 工厂, 测试用它直接 insert 种子数据,
    跟被测函数走同一个 engine.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            VPSRecord.__table__,
            VPSTask.__table__,
            IPRecord.__table__,
            IPTask.__table__,
            ProxyRecord.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def fake_scope():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(queries, "session_scope", fake_scope)
    yield Session


def _seed_ip(
    Session,
    *,
    egress_ip: str,
    country_code: str = "SG",
    country_name: str = "Singapore",
    city: str = "Singapore",
    is_active: int = 1,
    expire_date: date | None = None,
) -> int:
    """插一条 IP 记录, 返回 ip_id. 直接造对象避开加密依赖 (不调 from_form)."""
    s = Session()
    try:
        ip = IPRecord(
            entry_host="proxy.example.com",
            entry_port=1080,
            username="upstream_user",
            password_encrypted=b"dummy-ciphertext",
            protocol="socks5",
            egress_ip=egress_ip,
            country_code=country_code,
            country_name=country_name,
            city=city,
            is_active=is_active,
            expire_date=expire_date,
        )
        s.add(ip)
        s.commit()
        return ip.id
    finally:
        s.close()


# ============================================================
# TC-13-a 过期 + 未过期 + 纳管(expire=null) 三种都返
# ============================================================

def test_returns_all_active_and_expired(db_session):
    """库里有可用 / 过期 / 纳管(expire=null)三种 IP, 工具应全部返回."""
    active_id = _seed_ip(
        db_session, egress_ip="198.51.100.10",
        is_active=1, expire_date=date(2026, 12, 31),
    )
    expired_id = _seed_ip(
        db_session, egress_ip="198.51.100.20",
        is_active=0, expire_date=date(2026, 6, 1),
    )
    managed_id = _seed_ip(
        db_session, egress_ip="198.51.100.30",
        is_active=1, expire_date=None,
    )

    result = queries.get_registered_ips()

    assert len(result) == 3
    by_id = {row["ip_id"]: row for row in result}
    assert set(by_id) == {active_id, expired_id, managed_id}

    # 过期那条形状正确(is_active=0, 有到期日)
    assert by_id[expired_id]["is_active"] == 0
    assert by_id[expired_id]["expire_date"] == "2026-06-01"
    # 可用那条
    assert by_id[active_id]["is_active"] == 1
    assert by_id[active_id]["expire_date"] == "2026-12-31"
    # 纳管那条 expire_date=null
    assert by_id[managed_id]["is_active"] == 1
    assert by_id[managed_id]["expire_date"] is None


# ============================================================
# TC-13-b ⭐(安全) 含 ip_id, 绝不含密码/上游凭据
# ============================================================

def test_shape_has_ip_id_no_password(db_session):
    """每条含核心字段, 且绝不泄露 password / username / entry_host."""
    _seed_ip(db_session, egress_ip="203.0.113.50")

    result = queries.get_registered_ips()
    assert len(result) == 1
    row = result[0]

    # 必含核心字段
    for key in ("ip_id", "egress_ip", "expire_date", "is_active"):
        assert key in row

    # 绝不含任何上游凭据 / 入口信息字段
    forbidden = {
        "password", "password_encrypted", "pwd",
        "username", "user",
        "entry_host", "entry_port",
    }
    leaked = forbidden & set(row)
    assert not leaked, f"返回泄露了禁止字段: {leaked}"

    # 字段集合就是约定的 7 个, 不多不少
    assert set(row) == {
        "ip_id", "egress_ip", "country_code", "country_name",
        "city", "expire_date", "is_active",
    }


# ============================================================
# TC-13-c 空库返 []
# ============================================================

def test_empty_db_returns_empty_list(db_session):
    """空 ip_record 表 → 返 [](不抛异常)."""
    result = queries.get_registered_ips()
    assert result == []


# ============================================================
# TC-13-d 工具注册 + 三处对齐 + 只读标记
# ============================================================

def test_tool_registered():
    from tools import ALL_TOOLS
    from tools.get_registered_ips import TOOL

    names = {t.name for t, _ in ALL_TOOLS}
    assert "get_registered_ips" in names
    # name == 文件 stem (spec §8 不变量 #1)
    assert TOOL.name == "get_registered_ips"
    # 只读查询工具
    assert TOOL.annotations.readOnlyHint is True
    assert TOOL.annotations.destructiveHint is False
