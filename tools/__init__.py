"""MCP 工具注册中心。

每个 `tools/<name>.py` 模块导出：
    TOOL    : mcp.types.Tool      — 工具元数据（name / description / inputSchema）
    handler : async (arguments: dict) -> list[ContentBlock]
              — 实际处理函数，吃工具调用参数，返回 MCP 标准的 content 列表

`ALL_TOOLS` 把所有工具汇总成 (Tool, handler) 对的列表，未来 mcp_server.py
注册时直接 for-loop。

约束：
- 不在 tools/ 层写业务逻辑——只做"协议转换"：从 MCP arguments 调 services/
  里的业务函数，把返回 dict 包成 TextContent。
- 工具名（Tool.name）= 模块文件名，便于排查。
"""

from tools.get_available_proxy_nodes import TOOL as _get_available_proxy_nodes_tool
from tools.get_available_proxy_nodes import handler as _get_available_proxy_nodes_handler


ALL_TOOLS = [
    (_get_available_proxy_nodes_tool, _get_available_proxy_nodes_handler),
]

__all__ = ["ALL_TOOLS"]
