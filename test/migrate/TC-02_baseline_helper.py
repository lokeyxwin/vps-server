"""TC-30-02 db/migrate.init_db_with_baseline_if_fresh 行为 (验收矩阵 1 条 + 安全约束).

验收矩阵:
  ①fresh DB: init-db 建最新 schema (含 method) + stamp 0001; 再 migrate = no-op

额外覆盖 ADR-0012 §3 核心安全约束:
  - 全新库 → fresh=True + baseline 0001 (否则后续 migrate duplicate column)
  - 已有库(缺 method) → fresh=False + 绝不 stamp (否则 method 永远加不上 → 炸)
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from db.migrate import apply_pending, init_db_with_baseline_if_fresh


def _columns(engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


def _applied_in_ledger(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


# ============================================================
# 矩阵① fresh DB: 建最新 schema (含 method) + baseline 0001; 再 migrate no-op
# ============================================================

def test_fresh_db_creates_latest_schema_and_baselines(tmp_path):
    db_file = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_file}")

    result = init_db_with_baseline_if_fresh(engine)

    assert result["status"] == "ok"
    assert result["fresh"] is True
    assert result["baselined"] == ["0001"]
    # 最新 schema 含 method 列
    assert "method" in _columns(engine, "proxy_record")
    # 台账已 stamp 0001
    assert "0001" in _applied_in_ledger(engine)


def test_fresh_db_then_migrate_is_noop(tmp_path):
    db_file = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_file}")

    init_db_with_baseline_if_fresh(engine)
    result = apply_pending(engine)

    assert result["applied"] == []
    assert result["skipped"] == ["0001"]


# ============================================================
# ADR-0012 §3 安全约束: 已有库 init-db 不 stamp, migrate 仍能加列
# ============================================================

def test_existing_db_not_baselined(tmp_path):
    """已有库(缺 method) 经 init-db → fresh=False + 不 stamp 任何迁移。"""
    db_file = tmp_path / "existing.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE proxy_record (id INTEGER PRIMARY KEY)"))

    result = init_db_with_baseline_if_fresh(engine)

    assert result["fresh"] is False
    assert result["baselined"] == []
    # 台账空 (没 stamp 任何迁移)
    assert _applied_in_ledger(engine) == set()


def test_existing_db_init_then_migrate_adds_column(tmp_path):
    """已有库 init-db 后, migrate 仍能把 method 加上 (没被错 stamp 挡住)。"""
    db_file = tmp_path / "existing.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE proxy_record (id INTEGER PRIMARY KEY)"))

    init_db_with_baseline_if_fresh(engine)
    result = apply_pending(engine)

    assert result["applied"] == ["0001"]
    assert "method" in _columns(engine, "proxy_record")
