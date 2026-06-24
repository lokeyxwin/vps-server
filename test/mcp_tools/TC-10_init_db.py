"""TC-19-07 tools/init_db.py (admin) — 注册 + handler.

业务故事:
  - TOOL.name='init_db', description 非空 + 列 status 含义
  - TOOL 注册到 ALL_TOOLS
  - handler 成功返 {"status":"ok","tables":[...]}
  - handler 异常兜底转 {"status":"failed","message":...}
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from mcp.types import TextContent

from tools import ALL_TOOLS
from tools.init_db import TOOL, handler


def test_tool_meta():
    assert TOOL.name == "init_db"
    assert TOOL.title
    assert TOOL.description
    assert "init_db" in TOOL.name
    # description 教 agent 怎么转告 status
    assert "ok" in TOOL.description
    assert "failed" in TOOL.description
    assert "幂等" in TOOL.description
    # 不要求入参
    assert TOOL.inputSchema["required"] == []


def test_registered_in_all_tools():
    names = [t.name for t, _ in ALL_TOOLS]
    assert "init_db" in names


def test_handler_success_returns_ok_with_tables():
    """handler 调 init_db_with_baseline_if_fresh 成功 → {"status":"ok","tables":[...]}.

    ADR-0012: handler 改走共享 helper, 这里只测协议适配 + 兜底,
    mock helper 源头 (db.migrate.init_db_with_baseline_if_fresh), helper 内部逻辑归 test/migrate/.
    """
    fake_result = {
        "status": "ok",
        "fresh": True,
        "tables": ["vps_record", "ip_record"],
        "baselined": ["0001"],
    }
    with patch("db.migrate.init_db_with_baseline_if_fresh", return_value=fake_result):
        out = asyncio.run(handler({}))

    assert len(out) == 1
    assert isinstance(out[0], TextContent)
    result = json.loads(out[0].text)
    assert result["status"] == "ok"
    assert isinstance(result["tables"], list)


def test_handler_failure_returns_failed_with_message():
    """helper 抛 → handler 兜底 {"status":"failed","message":...}"""
    with patch(
        "db.migrate.init_db_with_baseline_if_fresh",
        side_effect=RuntimeError("db down"),
    ):
        out = asyncio.run(handler({}))

    result = json.loads(out[0].text)
    assert result["status"] == "failed"
    assert "db down" in result["message"]
    assert "RuntimeError" in result["message"]


def test_handler_accepts_none_arguments():
    """arguments=None 也能正常跑 (没必填参数)."""
    fake_result = {"status": "ok", "fresh": False, "tables": [], "baselined": []}
    with patch("db.migrate.init_db_with_baseline_if_fresh", return_value=fake_result):
        out = asyncio.run(handler(None))
    result = json.loads(out[0].text)
    assert result["status"] == "ok"
