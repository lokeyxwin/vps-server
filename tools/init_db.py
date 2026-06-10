"""MCP 工具:init_db (admin) — 跑 Base.metadata.create_all(engine).

这文件装啥:
  init_db 这个 MCP 工具的协议适配层 —— 把 MCP 调用包装成 db.create_all 调用.
  本文件只做"协议转换", 不写任何业务逻辑.

谁调我:
  - admin MCP 客户端 (运维 / agent 主动调).

业务规约金标准: ADR-0008 §决策 §2 + ADR-0009 §决策 §6.2 + main.py::_init_db.
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations


TOOL = Tool(
    name="init_db",
    title="初始化 DB schema (建表)",
    description=(
        "幂等建好所有业务表 (CREATE TABLE IF NOT EXISTS, SQLite/MySQL 都生效). "
        "典型场景: 首次部署 / 引入新表后. 不演化老表 (加字段/改类型仍需手动 "
        "ALTER 或 alembic).\n"
        "\n"
        "典型场景:\n"
        "- 用户首次部署后调一次, 把空 DB 建好所有业务表.\n"
        "- 改 db/models.py 加了新 ORM 类后调一次, 新表自动建出来.\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- ok + tables: <表清单>: 已就绪; 告诉用户 '所有业务表已建好'.\n"
        "- failed + message: 详细原因 (DB 连不上 / 权限不足等); "
        "  把 message 转告用户.\n"
        "\n"
        "反例:\n"
        "- 不要把它当 '重置 DB' 用 — 它不会 DROP 任何东西.\n"
        "- 加字段时跑它没用 (不会改老表, 需手动 ALTER 或迁移工具).\n"
        "- 这是高危管理动作 (改 DB schema), 不要随便调."
    ),
    inputSchema={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,        # 幂等建表, 不毁数据
        idempotentHint=True,
        openWorldHint=False,
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    """MCP tools/call 入口.

    跑 Base.metadata.create_all(engine). 成功返 {"status": "ok", "tables": [...]};
    任意异常 catch 转 {"status": "failed", "message": str(exc)}.
    """
    try:
        import db  # noqa: F401 — 触发 db/__init__.py 注册所有 ORM 表
        from db.base import Base
        from db.engine import engine

        Base.metadata.create_all(engine)
        result = {
            "status": "ok",
            "tables": sorted(Base.metadata.tables.keys()),
        }
    except Exception as exc:  # noqa: BLE001 — admin 工具兜底转 status
        result = {
            "status": "failed",
            "message": f"{type(exc).__name__}: {exc}",
        }

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
