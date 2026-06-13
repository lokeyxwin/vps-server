"""MCP 工具注册中心 (对齐 ADR-0007 §决策 §7 + spec.md §7).

每个 `tools/<name>.py` 模块导出:
    TOOL    : mcp.types.Tool      — 工具元数据 (name / description / inputSchema)
    handler : async (arguments: dict) -> list[ContentBlock]
              — 实际处理函数, 吃工具调用参数, 返回 MCP 标准的 content 列表

`ALL_TOOLS` 把所有工具汇总成 (Tool, handler) 对的列表, mcp_server.py
注册时直接 for-loop.

工具暴露分类 (ADR-0001 §决策 §5 + ADR-0007 §决策 §3 + ADR-0009 §决策 §6):
- 写入意图工具: register_vps / register_ip
- 状态查询工具: get_vps_registration_status / get_ip_registration_status
- 数据查询工具: get_available_proxy_nodes
- 写入修改工具 (admin): update_ip_expire_date
- 运维工具 (admin): init_db / init_probe_vps

约束:
- 不在 tools/ 层写业务逻辑 —— 只做"协议转换": 从 MCP arguments 调
  workers/ 或 services/ 里的业务函数, 把返回 dict 包成 TextContent.
- 工具名 (Tool.name) = 模块文件名 stem, 三处对齐 (spec §8 不变量 #1).
- 运维工具 (init_*) 高危, 暴露给 admin (admin/user 真正拆 server 留下波,
  见 ADR-0007 §8 + ADR-0008 §3.1).
"""

from tools.get_available_proxy_nodes import TOOL as _get_available_proxy_nodes_tool
from tools.get_available_proxy_nodes import handler as _get_available_proxy_nodes_handler
from tools.get_ip_registration_status import TOOL as _get_ip_status_tool
from tools.get_ip_registration_status import handler as _get_ip_status_handler
from tools.get_vps_registration_status import TOOL as _get_vps_status_tool
from tools.get_vps_registration_status import handler as _get_vps_status_handler
from tools.init_db import TOOL as _init_db_tool
from tools.init_db import handler as _init_db_handler
from tools.init_probe_vps import TOOL as _init_probe_vps_tool
from tools.init_probe_vps import handler as _init_probe_vps_handler
from tools.register_ip import TOOL as _register_ip_tool
from tools.register_ip import handler as _register_ip_handler
from tools.register_vps import TOOL as _register_vps_tool
from tools.register_vps import handler as _register_vps_handler
from tools.update_ip_expire_date import TOOL as _update_ip_expire_date_tool
from tools.update_ip_expire_date import handler as _update_ip_expire_date_handler


ALL_TOOLS = [
    # ---------- 写入意图工具 ----------
    (_register_vps_tool, _register_vps_handler),
    (_register_ip_tool, _register_ip_handler),
    # ---------- 状态查询工具 ----------
    (_get_vps_status_tool, _get_vps_status_handler),
    (_get_ip_status_tool, _get_ip_status_handler),
    # ---------- 数据查询工具 ----------
    (_get_available_proxy_nodes_tool, _get_available_proxy_nodes_handler),
    # ---------- 写入修改工具 (admin) ----------
    (_update_ip_expire_date_tool, _update_ip_expire_date_handler),
    # ---------- 运维工具 (admin) ----------
    (_init_db_tool, _init_db_handler),
    (_init_probe_vps_tool, _init_probe_vps_handler),
]

__all__ = ["ALL_TOOLS"]
