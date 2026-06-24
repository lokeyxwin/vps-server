"""轻量 SQLite migration runner (ADR-0012).

这文件装啥:
  项目自己的迷你迁移机制 —— 不上 alembic (YAGNI), 用编号 .sql 文件 + 一张台账表
  (schema_migrations) 记"哪些迁移跑过了"。生产库有数据不能 drop, 靠这套增量演化。

工具清单:
  - apply_pending(engine)                  扫 db/migrations/*.sql 按号跑未应用的, 返回 applied/skipped
  - init_db_with_baseline_if_fresh(engine) 全新库 create_all + baseline; 已有库幂等不 stamp

谁拿来用:
  - main.py migrate 子命令     → apply_pending
  - main.py init-db 子命令     → init_db_with_baseline_if_fresh
  - tools/init_db.py (MCP)     → init_db_with_baseline_if_fresh (跟 CLI 同一套)

迁移文件约定:
  每个 NNNN_*.sql 只放**一条** SQL 语句(SQLAlchemy text() 对多语句 SQLite 会抛
  "execute one statement at a time")。需要多步操作就拆多个编号文件。

业务规约金标准: docs/adr/0012-sqlite-migration-runner-and-drop-services.md §决策 §1-§5。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from log import get_logger


logger = get_logger("db.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# 全新库判断锚点: 这些业务表一个都不存在 = 全新库 (见 ADR-0012 §3)。
# 用 proxy_record 等真实业务表存在性判断, 不靠 schema_migrations。
_BASELINE_PROBE_TABLE = "proxy_record"

# 迁移版本号 → 该迁移要新增的 (表, 列)。
# apply_pending 应用前做轻量幂等 probe (ADR-0012 §5): 目标列已存在 → stamp 但不执行 SQL,
# 吃掉历史手工迁移状态, 防 duplicate column。
_MIGRATION_COLUMN_PROBE: dict[str, tuple[str, str]] = {
    "0001": ("proxy_record", "method"),
}

_VERSION_RE = re.compile(r"^(\d{4})_.+\.sql$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema_migrations_table(engine: Engine) -> None:
    """没有就建 schema_migrations 台账表 (version TEXT PK, applied_at TEXT)。"""
    ddl = text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version VARCHAR(32) PRIMARY KEY, "
        "applied_at VARCHAR(64) NOT NULL"
        ")"
    )
    with engine.begin() as conn:
        conn.execute(ddl)


def _applied_versions(engine: Engine) -> set[str]:
    """读台账里已应用的迁移号集合。"""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def _stamp(conn, version: str) -> None:
    """在当前事务连接上把 version 记入台账 (幂等: 已存在则跳过)。"""
    conn.execute(
        text(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (:v, :t)"
        ),
        {"v": version, "t": _now_iso()},
    )


def _discover_migrations() -> list[tuple[str, Path]]:
    """扫 migrations 目录, 返回按版本号升序的 [(version, path), ...]。"""
    if not MIGRATIONS_DIR.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for entry in MIGRATIONS_DIR.iterdir():
        if not entry.is_file():
            continue
        match = _VERSION_RE.match(entry.name)
        if match:
            found.append((match.group(1), entry))
    found.sort(key=lambda pair: pair[0])
    return found


def _column_exists(conn, table: str, column: str) -> bool:
    """PRAGMA table_info 检测某表是否已有某列 (SQLite)。"""
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    # PRAGMA table_info 第二列 (index 1) 是列名
    return any(row[1] == column for row in rows)


def _table_exists(engine: Engine, table: str) -> bool:
    """sqlite_master 检测业务表是否已存在 (SQLite)。"""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=:n"
            ),
            {"n": table},
        ).fetchone()
    return row is not None


def apply_pending(engine: Engine) -> dict:
    """扫 db/migrations/*.sql 按号跑未在台账的迁移, 逐个 stamp。

    返回 {"applied": [version, ...], "skipped": [version, ...]}。
    - applied: 本次真正生效的迁移号 (执行了 SQL 或仅 stamp 历史手工迁移)
    - skipped: 台账里已记录、本次跳过的迁移号

    0001 schema probe (ADR-0012 §5): 应用前若目标列已存在 (历史手工 ALTER),
    则 stamp applied 但不执行 SQL, 防 duplicate column。
    """
    _ensure_schema_migrations_table(engine)
    already = _applied_versions(engine)
    migrations = _discover_migrations()

    applied: list[str] = []
    skipped: list[str] = []

    for version, path in migrations:
        if version in already:
            skipped.append(version)
            logger.info("apply_pending: %s 已在台账, 跳过", version)
            continue

        sql = path.read_text(encoding="utf-8").strip()
        probe = _MIGRATION_COLUMN_PROBE.get(version)

        with engine.begin() as conn:
            run_sql = True
            if probe is not None:
                table, column = probe
                if _column_exists(conn, table, column):
                    run_sql = False
                    logger.info(
                        "apply_pending: %s 目标列 %s.%s 已存在 (历史手工迁移), "
                        "仅 stamp 不执行 SQL",
                        version, table, column,
                    )
            if run_sql:
                conn.execute(text(sql))
                logger.info("apply_pending: %s 执行 SQL 成功", version)
            _stamp(conn, version)

        applied.append(version)

    logger.info(
        "apply_pending 完成: applied=%s skipped=%s", applied, skipped,
    )
    return {"applied": applied, "skipped": skipped}


def init_db_with_baseline_if_fresh(engine: Engine) -> dict:
    """全新库 create_all + baseline; 已有库 create_all 幂等且绝不 stamp 迁移。

    CLI (main.py init-db) 和 MCP (tools/init_db.py) 共享这一个 helper (ADR-0012 §4),
    避免一个加 baseline 一个不加导致全新库迁移未 stamp 的 duplicate column 风险。

    返回 {"status": "ok", "fresh": bool, "tables": [...], "baselined": [version, ...]}。
    - fresh=True:  全新库 (业务表都不存在) → create_all 含最新 schema + baseline 现有迁移全 stamp
    - fresh=False: 已有库 → create_all 幂等 (不动旧表) + 绝不 stamp 迁移 (留给 migrate 演化)
    """
    import db  # noqa: F401 — 触发 db/__init__.py 注册所有 ORM 表
    from db.base import Base

    is_fresh = not _table_exists(engine, _BASELINE_PROBE_TABLE)

    Base.metadata.create_all(engine)
    _ensure_schema_migrations_table(engine)

    baselined: list[str] = []
    if is_fresh:
        # 全新库: create_all 已含所有迁移产物 (含 method 列), 把现有迁移全 stamp,
        # 否则后续 migrate 会 duplicate column。
        migrations = _discover_migrations()
        with engine.begin() as conn:
            for version, _path in migrations:
                _stamp(conn, version)
                baselined.append(version)
        logger.info(
            "init_db: 全新库 → create_all + baseline 迁移 %s", baselined,
        )
    else:
        # 已有库: 绝不 stamp 迁移, 否则缺列的旧库被错标已应用 → migrate 永远加不上列 → 炸。
        logger.info("init_db: 已有库 → create_all 幂等, 不 stamp 任何迁移")

    return {
        "status": "ok",
        "fresh": is_fresh,
        "tables": sorted(Base.metadata.tables.keys()),
        "baselined": baselined,
    }
