"""MCP 工具: get_registered_ips —— 列全部已登记 IP(过期 + 未过期)。

这文件装啥:
  get_registered_ips 的协议适配层 —— 把 MCP 调用转成
  db.queries.get_registered_ips() 查询. 只做协议转换, 不写业务逻辑。

谁调我: admin MCP 客户端

业务规约金标准: test/mcp_tools/spec.md §6.7
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations

from db.queries import get_registered_ips


TOOL = Tool(
    name="get_registered_ips",
    title="列出全部已登记 IP（过期+未过期）",
    description=(
        "列出系统里**全部**已登记的上游 IP（过期的 + 未过期的都在），用于「看面板"
        "截图批量补到期日」场景。跟 get_available_proxy_nodes 区分清楚：那个工具只"
        "列当前可用、能交付给用户连的代理节点（带账密）；本工具直接查 IP 登记表本身，"
        "**能看到过期 / 没挂出口的 IP**，且不返任何账密。"
        "典型场景：用户甩一张面板截图说「帮我补这些 IP 的到期时间，只补已经登记过的」。"
        "正确用法：先调本工具拿到全量已登记 IP 数组；对截图里的每一行出口IP，在数组里"
        "按 egress_ip 匹配 —— 匹配到的，拿那条的 ip_id 去调 update_ip_expire_date(ip_id, "
        "到期日) 精准改；匹配不到的（没登记），跳过并提示用户走 register_ip 登记。"
        "最后汇总「✅已补 N 条 / ⏭️没登记跳过 M 条」。"
        "字段说明：is_active=1 表示可用，is_active=0 表示已过期/停用；"
        "expire_date=null 表示这条是纳管进来的、到期日未知。空库返回空数组。"
        "反例：不要把本工具结果当「可用代理节点」直接给用户连（那是 "
        "get_available_proxy_nodes，且本工具不返账密）；不要用 egress_ip 去调 "
        "update_ip_expire_date —— 一定要用本工具结果里的 ip_id 定位。"
    ),
    inputSchema={
        "type": "object",
        "properties": {},
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

    参数：arguments 无参工具，忽略（None / 空 dict 均可）。
    返回：[TextContent]——把 get_registered_ips() 返回的 list[dict] 序列化成 JSON。

    业务函数不抛异常给上层（CLAUDE.md 业务契约），所以这里不做 try/except。
    """
    result = get_registered_ips()

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
