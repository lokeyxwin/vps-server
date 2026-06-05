"""MCP 工具：list_available_proxies。

列出当前可用的代理节点（user / admin 两套 MCP 都会暴露这个工具）。

「可用」定义见 services/proxy_query.list_available_proxies 文档。
本模块只负责 MCP 协议适配：
- 工具元数据（Tool）按 MCP spec 写好 inputSchema（JSON Schema）
- handler 调业务函数 + 包成 TextContent 返回
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool

from services.proxy_query import list_available_proxies


TOOL = Tool(
    name="list_available_proxies",
    title="列出可用代理节点",
    description=(
        "列出当前所有可用的代理节点（VPS + 上游 IP 都未过期且 active 的 USING 绑定）。"
        "返回 JSON 数组，每项含 protocol/host/port/username/password/egress_ip/"
        "country_code/country_name/city 等字段，可直接用于客户端导入或人工拷贝。"
        "可选 country_code 过滤（如 'SG' / 'US'）。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "country_code": {
                "type": "string",
                "description": (
                    "ISO 国家代码过滤（大写两字母，如 'SG' / 'US' / 'JP'）。"
                    "留空或不传则返回所有可用节点。"
                ),
                "default": "",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
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
