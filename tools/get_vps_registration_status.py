"""MCP 工具:get_vps_registration_status —— 查 VPS 装机进度.

这文件装啥:
  状态查询工具, 给 agent 在 register_vps 之后回来追问 "装好了吗" 用.
  join vps_record + vps_task (最新一条) 一次拿全, 不让 agent 多轮调.

谁调我: admin MCP 客户端

业务规约金标准: test/mcp_tools/spec.md §6.3
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations

from services.registration_query import query_vps_status


TOOL = Tool(
    name="get_vps_registration_status",
    title="查询 VPS 装机进度",
    description=(
        "查 VPS 装机进度. join vps_record + vps_task (最新一条) 一次拿全, "
        "适合 agent 在调过 register_vps 后回来追问 '装好了吗' 时用. "
        "vps_id 或 task_id 二选一传; vps_id 优先(任务表更通用).\n"
        "\n"
        "典型场景:\n"
        "- 用户说 'X 那台装好了吗' / '上次登记的服务器现在啥状态' → 调本工具.\n"
        "- agent 上下文里有 vps_id 或 task_id → 直接传, 不要让用户重报 IP.\n"
        "- 拿到结果后按 status 转告, 不要把 JSON 原样甩用户.\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- ok + task.status=done + vps.stage=connectable + xray_version 非空: "
        "  VPS 装机完成, 可挂代理. 转告 'X 装好啦, xray 版本 Y, 可以挂代理出口了'.\n"
        "- ok + task.status=in_progress: 还在装 xray. "
        "  转告 '还在装 (装机一般 5-15 分钟), 等几分钟再问'.\n"
        "- ok + task.status=pending: 已派任务但 worker 还没领. "
        "  转告 '排队等装机 worker 领取, 等几分钟再问'.\n"
        "- ok + task.status=failed + last_error_code=auth_failed: 装机时 SSH 改密了. "
        "  转告用户'账密变了, 需要先 update 凭据'.\n"
        "- ok + task.status=failed + last_error_code=ssh_timeout/ssh_refused: "
        "  装机时连不上. 转告'网络异常, 已重试 N 次都不通, 需要核查 VPS 状态'.\n"
        "- ok + task.status=failed + 其他 last_error_code: 装机失败. "
        "  把 last_error_msg 原样转告用户排查.\n"
        "- ok + task=null: VPS 入库了但还没派 task (罕见, 通常 SSHWorker 同步派). "
        "  转告 '已入库, 但装机任务还没派, 联系管理员排查'.\n"
        "- not_found: 没查到这台 VPS / 任务. "
        "  转告 'agent 上下文里的 vps_id/task_id 可能错了, 让用户重报 IP 重新登记'.\n"
        "\n"
        "反例:\n"
        "- 不要把 vps.stage=running 转告 '装好了' —— stage=running 是资源占用锁,\n"
        "  装机进度看 task.status. (ADR-0005 资源锁语义)\n"
        "- 不要把 xray_version='' 转告 'xray 装好了' —— SSHWorker 阶段写空, "
        "  装好的标志是 task.status=done.\n"
        "- 不要用本工具查代理节点列表 (用 get_available_proxy_nodes)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "vps_id": {
                "type": "integer",
                "description": (
                    "VPS 主键 id (跟 task_id 二选一, vps_id 优先). "
                    "agent 调 register_vps 返回的 vps_id 字段."
                ),
            },
            "task_id": {
                "type": "integer",
                "description": (
                    "task 主键 id (跟 vps_id 二选一). "
                    "agent 调 register_vps 返回的 task_id 字段; "
                    "查询时会自动解析对应 vps_id."
                ),
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
    """MCP tools/call 入口. 协议适配层, 不写业务."""
    args = arguments or {}
    result = query_vps_status(
        vps_id=args.get("vps_id"),
        task_id=args.get("task_id"),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
