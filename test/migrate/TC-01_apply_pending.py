"""TC-30-01 db/migrate.apply_pending 行为 (验收矩阵 2/3/4 条).

验收矩阵:
  ②old DB:    有 proxy_record 无 method → migrate 后 method 列出现 + 默认 '' + 台账有 0001
  ③idempotent: 连跑两次 migrate, 第二次 applied 为空 (no-op)
  ④0001 probe: 库已手工有 method 列但台账空 → stamp 0001 但不执行 SQL (不 duplicate column)

每个 TC 用独立 tmp_path SQLite 文件库 (PRAGMA table_info / sqlite_master 都要真 SQLite)。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from db.migrate import apply_pending


def _make_old_db(tmp_path, with_method: bool = False):
    """造一个只有 proxy_record 表的旧库; with_method 控制是否手工带 method 列。"""
    db_file = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_file}")
    if with_method:
        ddl = (
            "CREATE TABLE proxy_record ("
            "id INTEGER PRIMARY KEY, "
            "method VARCHAR(32) NOT NULL DEFAULT ''"
            ")"
        )
    else:
        ddl = "CREATE TABLE proxy_record (id INTEGER PRIMARY KEY)"
    with engine.begin() as conn:
        conn.execute(text(ddl))
    return engine


def _columns(engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


def _applied_in_ledger(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


# ============================================================
# 矩阵② old DB: migrate 加 method 列 + 默认 '' + 台账记 0001
# ============================================================

def test_old_db_migrate_adds_method_column(tmp_path):
    engine = _make_old_db(tmp_path, with_method=False)
    assert "method" not in _columns(engine, "proxy_record")

    result = apply_pending(engine)

    assert result["applied"] == ["0001"]
    assert result["skipped"] == []
    assert "method" in _columns(engine, "proxy_record")
    assert "0001" in _applied_in_ledger(engine)


def test_old_db_method_default_empty_string(tmp_path):
    """migrate 后插入不带 method 的行, method 默认 ''。"""
    engine = _make_old_db(tmp_path, with_method=False)
    apply_pending(engine)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO proxy_record (id) VALUES (1)"))
    with engine.connect() as conn:
        method = conn.execute(
            text("SELECT method FROM proxy_record WHERE id=1")
        ).scalar()
    assert method == ""


# ============================================================
# 矩阵③ idempotent: 连跑两次, 第二次 applied 为空
# ============================================================

def test_migrate_idempotent_second_run_noop(tmp_path):
    engine = _make_old_db(tmp_path, with_method=False)

    first = apply_pending(engine)
    second = apply_pending(engine)

    assert first["applied"] == ["0001"]
    assert second["applied"] == []
    assert second["skipped"] == ["0001"]


# ============================================================
# 矩阵④ 0001 probe: 列已手工存在但台账空 → stamp 不执行 SQL (无 duplicate column)
# ============================================================

def test_migrate_probe_skips_sql_when_column_exists(tmp_path):
    """库已手工 ALTER 加过 method 但台账空 → migrate stamp 0001 不重复执行。"""
    engine = _make_old_db(tmp_path, with_method=True)
    # 台账此刻空 (没建过 schema_migrations)
    assert "method" in _columns(engine, "proxy_record")

    # 不应抛 duplicate column 错
    result = apply_pending(engine)

    assert result["applied"] == ["0001"]
    assert "0001" in _applied_in_ledger(engine)
    # 列仍在, 没被破坏
    assert "method" in _columns(engine, "proxy_record")


def test_migrate_probe_then_idempotent(tmp_path):
    """probe stamp 后再跑一次仍是 no-op。"""
    engine = _make_old_db(tmp_path, with_method=True)
    apply_pending(engine)
    second = apply_pending(engine)
    assert second["applied"] == []
    assert second["skipped"] == ["0001"]
