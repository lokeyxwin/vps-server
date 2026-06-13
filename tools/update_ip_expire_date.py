"""MCP 工具: update_ip_expire_date —— 改某条已登记 IP 的到期日 (白名单 patch 单字段)。

这文件装啥:
  update_ip_expire_date 的协议适配层 —— 把 MCP 调用转成 db.queries.update_ip_expire_date
  调用。只做协议转换, 不写业务逻辑。

  项目第一个 update_* 写入工具, 由 ADR-0008 §3.3 ABCD 背书:
  主键精准定位 (ip_id) + 白名单单列 patch (只 expire_date) + 不整对象覆盖 +
  命名反映约束 (update_<对象>_<字段>)。

谁调我: admin MCP 客户端 (写入修改工具, admin/user 分层见 CLAUDE.local.md §14.1)

业务规约金标准: test/mcp_tools/spec.md §6.6
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations

from db.queries import update_ip_expire_date


TOOL = Tool(
    name="update_ip_expire_date",
    title="更新某条已登记 IP 的到期日",
    description=(
        "把某条已登记 IP 的到期日精准改成指定日期 (白名单 patch, 只动 expire_date "
        "单字段, 不碰其他任何字段)。同步返回, 立即生效。\n"
        "ip_id 必须精准 (主键定位), expire_date 必须是 YYYY-MM-DD。\n"
        "\n"
        "典型场景:\n"
        "- 用户说 '把这条 IP 的到期日改成 2026-06-18' → 拿到 ip_id 调本工具。\n"
        "- 用户说 '过期还有 7 天' 之类 → agent 自己换算成具体日期再填。\n"
        "- 批量看图补到期日: 用户甩一张供应商面板截图 (含出口IP + 到期时间), 说 "
        "'补一下这些 IP 的到期时间, 只补已登记的'。agent 对截图每一行:\n"
        "  1. 用出口IP 在 get_available_proxy_nodes 结果里匹配 → 拿到 ip_id;\n"
        "  2. 匹配到 (已登记可用) → 调本工具精准补该行到期日;\n"
        "  3. 匹配不到 (过期/没挂, 不在可用节点列表) → 跳过, 绝不自动登记, "
        "记入'未登记跳过'清单, 提示用户'这几条要纳入管理请走 register_ip, "
        "下次纳管时再进来';\n"
        "  4. 本工具一次只改一条, agent 逐行循环调, 跑完给三段汇总 "
        "(✅已补 N 条 / ⏭️没登记跳过 M 条 / 让用户决定 M 条要不要 register_ip)。\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- ok: 命中 ip_id, 到期日已更新。"
        "  转告 '已把 IP <egress_ip> 的到期日更新为 <expire_date>'。\n"
        "- not_found: ip_id 不存在。"
        "  转告 '没找到这条 IP, 确认 ip_id; 可能它根本没登记, 要登记走 register_ip'。\n"
        "- invalid_date: expire_date 不是合法 YYYY-MM-DD。"
        "  转告 '日期格式要 YYYY-MM-DD (如 2026-06-18)'。\n"
        "\n"
        "反例 (明确禁止):\n"
        "- 不准拿 egress_ip 字符串模糊定位, 必须用 ip_id (规则 A 主键精准)。\n"
        "- 批量场景查不到 ip_id 的别硬造、别自动登记, 跳过并提示走 register_ip。\n"
        "- 不要一次想传一个 list 批量改, 本工具一次一条, agent 逐行循环。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "ip_id": {
                "type": "integer",
                "description": "IP 主键 id (精准定位, 必填)。register_ip / 查询工具返回的 id。",
            },
            "expire_date": {
                "type": "string",
                "description": "到期日 YYYY-MM-DD (必填, 如 2026-06-18)。",
            },
        },
        "required": ["ip_id", "expire_date"],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    """MCP tools/call 入口. 协议适配层, 不写业务."""
    args = arguments or {}
    result = update_ip_expire_date(
        ip_id=args.get("ip_id"),
        expire_date=args.get("expire_date"),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
