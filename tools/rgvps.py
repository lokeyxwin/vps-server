"""MCP 工具:rgvps —— 登记一台 VPS.

这文件装啥:
  rgvps 这个 MCP 工具的协议适配层 —— 把 MCP 调用包装成
  workers.ssh_worker.SSHWorker 的 process() 调用.

  本文件只做"协议转换",不写任何业务逻辑.
  业务逻辑全在 SSHWorker 里.

谁调我:
  - admin MCP 客户端(agent 主动调)
  - 未来可能也由 user MCP 暴露查询版

我用到的工具:
  - mcp.types.Tool + TextContent (MCP 标准)
  - workers.ssh_worker.SSHWorker (业务工人)

业务规约金标准:
  test/ssh_worker/spec.md

实现等任务单填,本文件目前空占位.
"""

from __future__ import annotations

# from mcp.types import TextContent, Tool, ToolAnnotations
# from workers.ssh_worker import SSHWorker


# TOOL = Tool(
#     name="rgvps",
#     title="登记一台 VPS",
#     description="(实现等任务单填,见 spec.md 补 description)",
#     inputSchema={
#         "type": "object",
#         "properties": {
#             # 等任务单填字段
#         },
#         "required": [],
#         "additionalProperties": False,
#     },
# )


async def handler(arguments: dict | None) -> list:
    """MCP tools/call 入口.

    arguments 形状(同步段入参):
      ip / user / pwd / port / ed(可选) / provider(可选)

    返回 [TextContent], 内容是 SSHWorker.process() 返回 dict 的 JSON.

    实现等任务单填.
    """
    pass    # 实现等任务单填
