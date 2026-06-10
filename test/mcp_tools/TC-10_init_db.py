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
    """handler 跑 create_all 成功 → {"status":"ok","tables":[...]}"""
    # 不实际跑 create_all (避免动磁盘), mock 它
    with patch("db.base.Base") as mock_base:
        mock_engine = object()
        mock_base.metadata.tables = {"vps_record": None, "ip_record": None}

        with patch.dict(
            "sys.modules",
            {"db.engine": type("M", (), {"engine": mock_engine})()},
        ):
            with patch.object(mock_base.metadata, "create_all"):
                out = asyncio.run(handler({}))

    assert len(out) == 1
    assert isinstance(out[0], TextContent)
    result = json.loads(out[0].text)
    assert result["status"] == "ok"
    assert isinstance(result["tables"], list)


def test_handler_failure_returns_failed_with_message():
    """create_all 抛 → handler 兜底 {"status":"failed","message":...}"""
    with patch("db.base.Base") as mock_base:
        mock_base.metadata.create_all.side_effect = RuntimeError("db down")
        out = asyncio.run(handler({}))

    result = json.loads(out[0].text)
    assert result["status"] == "failed"
    assert "db down" in result["message"]
    assert "RuntimeError" in result["message"]


def test_handler_accepts_none_arguments():
    """arguments=None 也能正常跑 (没必填参数)."""
    with patch("db.base.Base") as mock_base:
        mock_base.metadata.tables = {}
        mock_base.metadata.create_all.return_value = None
        out = asyncio.run(handler(None))
    result = json.loads(out[0].text)
    assert result["status"] == "ok"
