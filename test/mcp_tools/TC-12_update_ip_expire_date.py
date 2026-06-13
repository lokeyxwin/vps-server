"""TC-12 update_ip_expire_date —— 第一个 update_* 写入工具 (ADR-0008 §3.3 ABCD).

验收金标准: test/mcp_tools/spec.md §6.6 + §8 不变量
约束依据: docs/adr/0008-*.md §3.3 ABCD + CLAUDE.local.md §14.3

5 个用例:
  TC-12-a test_update_ok                    命中 ip_id 补到期日, 原生查 DB 验证
  TC-12-b test_whitelist_no_collateral_write ⭐ 白名单不越界 (规则 C 核心)
  TC-12-c test_not_found                    ip_id 不存在, DB 无改动
  TC-12-d test_invalid_date                 非法日期, expire_date 保持原值
  TC-12-e test_tool_registered              ALL_TOOLS 注册 + name == stem + 非只读
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

    yield 一个 Session 工厂, 测试用它直接 insert 种子数据 + 原生查 DB 验证,
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
    egress_ip: str = "198.51.100.10",
    country_code: str = "SG",
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
            is_active=is_active,
            expire_date=expire_date,
        )
        s.add(ip)
        s.commit()
        return ip.id
    finally:
        s.close()


def _fetch_ip(Session, ip_id: int) -> IPRecord | None:
    """原生开一个新 Session 查 DB, 验证写入落盘."""
    s = Session()
    try:
        return s.get(IPRecord, ip_id)
    finally:
        s.close()


# ============================================================
# TC-12-a 命中 ip_id 补到期日 → ok + 原生查 DB 验证
# ============================================================

def test_update_ok(db_session):
    """库里一条 expire_date=null 的纳管 IP, 补一个具体到期日 → 成功写入."""
    ip_id = _seed_ip(db_session, expire_date=None)

    result = queries.update_ip_expire_date(ip_id, "2026-06-18")

    assert result["status"] == "ok"
    assert result["ip"]["id"] == ip_id
    assert result["ip"]["expire_date"] == "2026-06-18"
    # 原生查 DB: 真落盘
    fresh = _fetch_ip(db_session, ip_id)
    assert fresh.expire_date == date(2026, 6, 18)


# ============================================================
# TC-12-b ⭐ 白名单不越界 (规则 C 核心): 只动 expire_date
# ============================================================

def test_whitelist_no_collateral_write(db_session):
    """改 expire_date 后, is_active / egress_ip / country_code 必须原封不动."""
    ip_id = _seed_ip(
        db_session,
        egress_ip="203.0.113.99",
        country_code="SG",
        is_active=1,
        expire_date=date(2026, 1, 1),
    )

    result = queries.update_ip_expire_date(ip_id, "2027-12-31")
    assert result["status"] == "ok"

    fresh = _fetch_ip(db_session, ip_id)
    # 唯一允许变的列
    assert fresh.expire_date == date(2027, 12, 31)
    # 白名单外的列必须完全一致, 没被顺手改 / 整对象覆盖写回默认值
    assert fresh.is_active == 1
    assert fresh.egress_ip == "203.0.113.99"
    assert fresh.country_code == "SG"


# ============================================================
# TC-12-c ip_id 不存在 → not_found + DB 无改动
# ============================================================

def test_not_found(db_session):
    """不存在的 ip_id → not_found, 不误建行."""
    # 先放一条存在的 IP, 确认工具不会误碰它 / 误建新行
    existing_id = _seed_ip(db_session, expire_date=date(2026, 1, 1))

    result = queries.update_ip_expire_date(999999, "2026-06-18")

    assert result == {"status": "not_found"}
    # 没误建行
    assert _fetch_ip(db_session, 999999) is None
    # 既有行不受影响
    assert _fetch_ip(db_session, existing_id).expire_date == date(2026, 1, 1)


# ============================================================
# TC-12-d 非法日期 → invalid_date + expire_date 保持原值
# ============================================================

@pytest.mark.parametrize("bad_date", ["2026/06/18", "foo"])
def test_invalid_date(db_session, bad_date):
    """非法日期 → invalid_date, 不写 DB, 原 expire_date 不变."""
    ip_id = _seed_ip(db_session, expire_date=date(2026, 1, 1))

    result = queries.update_ip_expire_date(ip_id, bad_date)

    assert result["status"] == "invalid_date"
    assert result["expire_date"] == bad_date
    # DB 里原值不变
    fresh = _fetch_ip(db_session, ip_id)
    assert fresh.expire_date == date(2026, 1, 1)


# ============================================================
# TC-12-e 工具注册 + 三处对齐 + 非只读标记
# ============================================================

def test_tool_registered():
    from tools import ALL_TOOLS
    from tools.update_ip_expire_date import TOOL

    names = {t.name for t, _ in ALL_TOOLS}
    assert "update_ip_expire_date" in names
    # name == 文件 stem (spec §8 不变量 #1)
    assert TOOL.name == "update_ip_expire_date"
    # 写入工具, 不是只读
    assert TOOL.annotations.readOnlyHint is False
