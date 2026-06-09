"""MCP 工具：get_available_proxy_nodes。

列出当前可用的代理节点（user / admin 两套 MCP 都会暴露这个工具）。

「可用」定义见 services/proxy_query.list_available_proxies 文档。
本模块只负责 MCP 协议适配：
- 工具元数据（Tool）按 MCP spec 写好 inputSchema（JSON Schema）
- handler 调业务函数 + 包成 TextContent 返回
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations

from db.queries import list_available_proxies


TOOL = Tool(
    name="get_available_proxy_nodes",
    title="查询可用代理节点",
    description=(
        "用于查询当前可交付给用户使用的代理节点。典型场景：用户说“给我一个新加坡节点”、"
        "“给我一个美国节点”、“有没有日本节点”、“列出可用节点”。正确用法：先调用本工具"
        "查询可用节点；如果用户指定地区，就传 country_code，例如新加坡传 SG，美国传 US，"
        "日本传 JP；如果用户没指定地区，就不传参数。返回 1 个节点时，直接把该节点整理给用户。"
        "返回多个同地区节点时，如果用户只要 1 个，就选择列表中的第一个给用户；如果用户说"
        "“都列出来”或“有哪些”，再全部列出。返回空数组时，不要编造节点，应告诉用户："
        "当前没有你要的地区节点，需要找管理员添加。回答用户时不要直接甩 JSON，应整理成"
        "小火箭可手动填写的格式：类型、IP、端口、账号、密码、出口IP、地区。反例：不要在"
        "未调用工具前承诺有节点；不要把过期节点、不可用节点或数据库外的节点告诉用户；"
        "不要用本工具登记 VPS、新增出口 IP、初始化 Xray 或检查端口。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "country_code": {
                "type": "string",
                "description": (
                    "可选。按国家或地区代码筛选可用节点，使用大写 ISO 两字母代码。"
                    "例如 SG 表示新加坡，US 表示美国，JP 表示日本。"
                    "留空或不传则返回全部可用节点。"
                ),
                "default": "",
                "examples": ["SG", "US", "JP"],
            },
        },
        "required": [],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    """MCP tools/call 入口。

    参数：arguments dict，可能为 None / 空 dict / 含 country_code 键。
    返回：[TextContent]——把业务函数返回的 list[dict] 序列化成 JSON 字符串。

    业务函数不抛异常给上层（按 CLAUDE.md 业务契约），所以这里不做 try/except。
    """
    args = arguments or {}
    country_code = args.get("country_code", "") or ""

    nodes = list_available_proxies(country_code=country_code)

    payload = json.dumps(nodes, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
