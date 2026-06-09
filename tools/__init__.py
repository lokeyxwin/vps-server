"""MCP 工具注册中心。

每个 `tools/<name>.py` 模块导出:
    TOOL    : mcp.types.Tool      — 工具元数据(name / description / inputSchema)
    handler : async (arguments: dict) -> list[ContentBlock]
              — 实际处理函数, 吃工具调用参数, 返回 MCP 标准的 content 列表

`ALL_TOOLS` 把所有工具汇总成 (Tool, handler) 对的列表, mcp_server.py
注册时直接 for-loop。

工具暴露分类(见 ADR-0001 §决策 §5 + ADR-0007 §决策 §3):
- 写入意图工具: register_vps / rgip(T-17 改名 register_ip)
- 数据查询工具: get_available_proxy_nodes
- (状态查询工具 get_vps/ip_registration_status 由 T-17 补)

约束:
- 不在 tools/ 层写业务逻辑——只做"协议转换": 从 MCP arguments 调
  workers/ 里的业务函数, 把返回 dict 包成 TextContent。
- 工具名(Tool.name)= 模块文件名, 便于排查。
"""

from tools.get_available_proxy_nodes import TOOL as _get_available_proxy_nodes_tool
from tools.get_available_proxy_nodes import handler as _get_available_proxy_nodes_handler
from tools.register_vps import TOOL as _register_vps_tool
from tools.register_vps import handler as _register_vps_handler
from tools.rgip import TOOL as _rgip_tool
from tools.rgip import handler as _rgip_handler


ALL_TOOLS = [
    # 写入意图工具(意图工具在前)
    (_register_vps_tool, _register_vps_handler),
    (_rgip_tool, _rgip_handler),
    # 数据查询工具(查询工具在后)
    (_get_available_proxy_nodes_tool, _get_available_proxy_nodes_handler),
]

__all__ = ["ALL_TOOLS"]
