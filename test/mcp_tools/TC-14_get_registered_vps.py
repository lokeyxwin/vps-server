"""TC-14 get_registered_vps —— 列全量已登记 VPS(装/未装、忙/闲、过期/未过期)只读查询.

验收金标准: test/mcp_tools/spec.md §6.8
任务单: task/done_25_get_registered_vps.md
关联 ADR: ADR-0005(stage 资源锁语义 connectable/running)

4 个用例:
  TC-14-a test_returns_all_states          装/未装、忙/闲、过期/未过期 全返
  TC-14-b test_shape_no_credentials ⭐      含 vps_id 等核心字段, 绝不含任何 SSH 凭据
  TC-14-c test_empty_db_returns_empty_list  空库返 []
  TC-14-d test_tool_registered              ALL_TOOLS 注册 + name == stem + 只读
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
    VPSStage,
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


def _seed_vps(
    Session,
    *,
    ip: str,
    os_name: str = "Ubuntu",
    os_version: str = "22.04",
    xray_version: str = "",
    stage: str = VPSStage.CONNECTABLE,
    used_port_count: int = 0,
    is_active: int = 1,
    expire_date: date | None = None,
    provider_domain: str = "aliyun.com",
) -> int:
    """插一条 VPS 记录, 返回 vps_id. 直接造对象避开加密依赖 (不调 from_form)."""
    s = Session()
    try:
        vps = VPSRecord(
            ip=ip,
            port=22,
            username="root",
            password_encrypted=b"dummy-ciphertext",
            os_name=os_name,
            os_version=os_version,
            xray_version=xray_version,
            stage=stage,
            used_port_count=used_port_count,
            is_active=is_active,
            expire_date=expire_date,
            provider_domain=provider_domain,
        )
        s.add(vps)
        s.commit()
        return vps.id
    finally:
        s.close()


# ============================================================
# TC-14-a 装/未装、忙/闲、过期/未过期 全返
# ============================================================

def test_returns_all_states(db_session):
    """库里覆盖「装了xray running」「没装 connectable」「过期」三种, 工具应全返."""
    installed_busy_id = _seed_vps(
        db_session, ip="203.0.113.10",
        xray_version="Xray 26.3.27", stage=VPSStage.RUNNING,
        used_port_count=2, is_active=1, expire_date=date(2026, 12, 31),
    )
    fresh_idle_id = _seed_vps(
        db_session, ip="203.0.113.20",
        xray_version="", stage=VPSStage.CONNECTABLE,
        used_port_count=0, is_active=1, expire_date=None,
    )
    expired_id = _seed_vps(
        db_session, ip="203.0.113.30",
        xray_version="Xray 26.3.27", stage=VPSStage.CONNECTABLE,
        used_port_count=0, is_active=0, expire_date=date(2026, 6, 1),
    )

    result = queries.get_registered_vps()

    assert len(result) == 3
    by_id = {row["vps_id"]: row for row in result}
    assert set(by_id) == {installed_busy_id, fresh_idle_id, expired_id}

    # 装了 xray + 忙
    assert by_id[installed_busy_id]["xray_version"] == "Xray 26.3.27"
    assert by_id[installed_busy_id]["stage"] == "running"
    assert by_id[installed_busy_id]["used_port_count"] == 2
    # 没装 xray + 空闲
    assert by_id[fresh_idle_id]["xray_version"] == ""
    assert by_id[fresh_idle_id]["stage"] == "connectable"
    assert by_id[fresh_idle_id]["expire_date"] is None
    # 过期
    assert by_id[expired_id]["is_active"] == 0
    assert by_id[expired_id]["expire_date"] == "2026-06-01"


# ============================================================
# TC-14-b ⭐(安全) 含核心字段, 绝不含任何 SSH 凭据
# ============================================================

def test_shape_no_credentials(db_session):
    """每条含核心运维字段, 且绝不泄露 password / port / username 任何凭据."""
    _seed_vps(db_session, ip="198.51.100.5")

    result = queries.get_registered_vps()
    assert len(result) == 1
    row = result[0]

    # 必含核心字段
    for key in (
        "vps_id", "ip", "xray_version", "stage",
        "used_port_count", "expire_date", "is_active",
    ):
        assert key in row

    # 绝不含任何 SSH 凭据字段
    forbidden = {
        "password", "password_encrypted", "pwd",
        "port", "username", "user",
    }
    leaked = forbidden & set(row)
    assert not leaked, f"返回泄露了禁止字段: {leaked}"

    # 字段集合就是约定的 10 个, 不多不少
    assert set(row) == {
        "vps_id", "ip", "os_name", "os_version", "xray_version",
        "stage", "used_port_count", "expire_date", "is_active",
        "provider_domain",
    }


# ============================================================
# TC-14-c 空库返 []
# ============================================================

def test_empty_db_returns_empty_list(db_session):
    """空 vps_record 表 → 返 [](不抛异常)."""
    result = queries.get_registered_vps()
    assert result == []


# ============================================================
# TC-14-d 工具注册 + 三处对齐 + 只读标记
# ============================================================

def test_tool_registered():
    from tools import ALL_TOOLS
    from tools.get_registered_vps import TOOL

    names = {t.name for t, _ in ALL_TOOLS}
    assert "get_registered_vps" in names
    # name == 文件 stem (spec §8 不变量 #1)
    assert TOOL.name == "get_registered_vps"
    # 只读查询工具
    assert TOOL.annotations.readOnlyHint is True
    assert TOOL.annotations.destructiveHint is False
