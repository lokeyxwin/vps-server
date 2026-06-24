"""MCP 工具:init_db (admin) — 跑 Base.metadata.create_all(engine).

这文件装啥:
  init_db 这个 MCP 工具的协议适配层 —— 把 MCP 调用包装成 db.create_all 调用.
  本文件只做"协议转换", 不写任何业务逻辑.

谁调我:
  - admin MCP 客户端 (运维 / agent 主动调).

业务规约金标准: ADR-0008 §决策 §2 + ADR-0009 §决策 §6.2 + ADR-0012 §4 + main.py::_init_db.
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations


TOOL = Tool(
    name="init_db",
    title="初始化 DB schema (建表)",
    description=(
        "首次部署建好所有业务表. 全新库 (业务表都不存在) 建最新 schema 并 baseline "
        "(把现有迁移全部标记已应用); 已有库则幂等建表, 绝不改动旧表也不标记迁移. "
        "跟 CLI 'main.py init-db' 走同一套逻辑.\n"
        "\n"
        "典型场景:\n"
        "- 用户首次部署后调一次, 把空 DB 建好所有业务表 (含最新字段).\n"
        "- 改 db/models.py 加了新 ORM 类后调一次, 新表自动建出来.\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- ok + fresh + tables: <表清单>: 已就绪; fresh=true 表示全新库, "
        "  false 表示已有库幂等建表.\n"
        "- failed + message: 详细原因 (DB 连不上 / 权限不足等); "
        "  把 message 转告用户.\n"
        "\n"
        "反例:\n"
        "- 不要把它当 '重置 DB' 用 — 它不会 DROP 任何东西.\n"
        "- 给已有库加字段不要用它 (它不演化旧表); 加字段要跑 'main.py migrate'.\n"
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

    走 db.migrate.init_db_with_baseline_if_fresh (跟 CLI init-db 同一套 helper).
    成功返 {"status": "ok", "fresh": bool, "tables": [...], "baselined": [...]};
    任意异常 catch 转 {"status": "failed", "message": str(exc)}.
    """
    try:
        from db.engine import engine
        from db.migrate import init_db_with_baseline_if_fresh

        result = init_db_with_baseline_if_fresh(engine)
    except Exception as exc:  # noqa: BLE001 — admin 工具兜底转 status
        result = {
            "status": "failed",
            "message": f"{type(exc).__name__}: {exc}",
        }

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
