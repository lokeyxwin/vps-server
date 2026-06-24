"""TC-30-03 MCP init_db 与 CLI init-db 行为一致 (验收矩阵⑤).

矩阵⑤ MCP init: tools/init_db.py 跟 CLI init-db 行为一致 (都走共享 helper
              db.migrate.init_db_with_baseline_if_fresh)。

两条路径都 monkeypatch sys.modules['db.engine'].engine 指向同一 tmp_path 库,
断言建出的 schema / 台账一致。
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import create_engine, inspect, text

import db.engine  # 触发注册到 sys.modules['db.engine']
import main
from tools import init_db as init_db_tool


def _columns(engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


def _applied_in_ledger(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def test_mcp_init_db_fresh_creates_schema_and_baselines(monkeypatch, tmp_path):
    """MCP init_db handler 全新库 → 建最新 schema + baseline 0001 (走 helper)。"""
    db_file = tmp_path / "mcp_fresh.db"
    test_engine = create_engine(f"sqlite:///{db_file}")
    monkeypatch.setattr(sys.modules["db.engine"], "engine", test_engine)

    result = asyncio.run(init_db_tool.handler({}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "ok"
    assert payload["fresh"] is True
    assert payload["baselined"] == ["0001"]
    assert "method" in _columns(test_engine, "proxy_record")
    assert "0001" in _applied_in_ledger(test_engine)


def test_mcp_and_cli_init_db_parity(monkeypatch, tmp_path):
    """同样的全新库, MCP handler 与 CLI init-db 建出的 schema + 台账一致。"""
    # CLI 路径
    cli_db = tmp_path / "cli.db"
    cli_engine = create_engine(f"sqlite:///{cli_db}")
    monkeypatch.setattr(sys.modules["db.engine"], "engine", cli_engine)
    rc = main.main(["init-db"])
    assert rc == 0
    cli_cols = _columns(cli_engine, "proxy_record")
    cli_ledger = _applied_in_ledger(cli_engine)

    # MCP 路径
    mcp_db = tmp_path / "mcp.db"
    mcp_engine = create_engine(f"sqlite:///{mcp_db}")
    monkeypatch.setattr(sys.modules["db.engine"], "engine", mcp_engine)
    payload = json.loads(asyncio.run(init_db_tool.handler({}))[0].text)
    assert payload["status"] == "ok"
    mcp_cols = _columns(mcp_engine, "proxy_record")
    mcp_ledger = _applied_in_ledger(mcp_engine)

    # 两条路径行为一致
    assert cli_cols == mcp_cols
    assert cli_ledger == mcp_ledger
    assert "method" in cli_cols
    assert cli_ledger == {"0001"}


def test_mcp_init_db_existing_not_baselined(monkeypatch, tmp_path):
    """MCP init_db handler 已有库 → fresh=False + 不 stamp (跟 CLI 一致的安全约束)。"""
    db_file = tmp_path / "mcp_existing.db"
    test_engine = create_engine(f"sqlite:///{db_file}")
    with test_engine.begin() as conn:
        conn.execute(text("CREATE TABLE proxy_record (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(sys.modules["db.engine"], "engine", test_engine)

    payload = json.loads(asyncio.run(init_db_tool.handler({}))[0].text)

    assert payload["status"] == "ok"
    assert payload["fresh"] is False
    assert payload["baselined"] == []
    assert _applied_in_ledger(test_engine) == set()
