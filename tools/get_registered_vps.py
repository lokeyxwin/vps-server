"""MCP 工具: get_registered_vps —— 列全部已登记 VPS(装/未装、忙/闲、过期/未过期)。

这文件装啥:
  get_registered_vps 的协议适配层 —— 把 MCP 调用转成
  db.queries.get_registered_vps() 查询. 只做协议转换, 不写业务逻辑。

谁调我: admin MCP 客户端

业务规约金标准: test/mcp_tools/spec.md §6.8
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations

from db.queries import get_registered_vps


TOOL = Tool(
    name="get_registered_vps",
    title="列出全部已登记 VPS（装/未装、忙/闲、过期/未过期）",
    description=(
        "列出系统里**全部**已登记的 VPS（装了 xray 的 + 没装的 + 过期的都在），"
        "用于运维一眼看 VPS 池全貌，并拿到每台的 vps_id。跟 get_vps_registration_status "
        "区分清楚：那个工具按 vps_id / task_id 查**单台**的装机进度（带 task 状态）；"
        "本工具是**列全量**，不带 task 进度。"
        "典型场景：用户问「现在有几台服务器」「哪些机器装好 xray 了」「哪台空闲 / 忙」"
        "「哪些 VPS 过期了」「VPS 池还有多少容量」。"
        "字段说明：xray_version 为空字符串表示这台还没装 xray；"
        "stage=running 表示有工人正在操作这台（挑机会跳过），connectable 表示空闲；"
        "used_port_count 是这台已经挂了几条业务代理；"
        "is_active=1 表示可用，is_active=0 表示已过期/停用；"
        "expire_date=null 表示到期日未知。空库返回空数组。"
        "反例：本工具**不返任何 SSH 凭据**（密码 / 端口 / 登录名都不返），"
        "不要拿它的结果当「登录 VPS 的账密」；"
        "也不要拿它当「可用代理节点列表」给用户连（那是 get_available_proxy_nodes）。"
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
    返回：[TextContent]——把 get_registered_vps() 返回的 list[dict] 序列化成 JSON。

    业务函数不抛异常给上层（CLAUDE.md 业务契约），所以这里不做 try/except。
    """
    result = get_registered_vps()

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
