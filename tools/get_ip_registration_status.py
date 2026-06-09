"""MCP 工具:get_ip_registration_status ⭐ 一条龙.

这文件装啥:
  状态查询工具, 给 agent 在 register_ip 之后回来追问 "配好了吗" 用.
  join ip_record + ip_task (最新一条) + proxy_record (task.status=done 时) 一次拿全,
  task.status=done 时直接返代理节点账密, 让 agent 一次告诉用户 "节点 X:Y 账密 u/p".

谁调我: admin MCP 客户端 (后续 user MCP 分层后也可能暴露)

业务规约金标准: test/mcp_tools/spec.md §6.4
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations

from db.queries import query_ip_status


TOOL = Tool(
    name="get_ip_registration_status",
    title="查询 IP 配置进度 + 配好时返代理节点",
    description=(
        "查 IP 配置进度. join ip_record + ip_task (最新一条) + proxy_record "
        "(task.status=done 时) 一次拿全 ⭐ 一条龙, 配好时直接返代理节点账密, "
        "agent 一次告诉用户 '节点 VPS_IP:port 账密 user/pwd', 不再追问.\n"
        "ip_id 或 task_id 二选一传; ip_id 优先(IP 表更通用).\n"
        "\n"
        "典型场景:\n"
        "- 用户说 '我那条 IP 配好了吗' / '挂上了吗' / '给我代理节点' → 调本工具.\n"
        "- agent 上下文里有 ip_id 或 task_id → 直接传, 不要让用户重报 egress IP.\n"
        "- task.status=done 时 proxy_node 字段必返, agent 直接整理给用户.\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- ok + task.status=done + proxy_node.status=using: 完全配好且通. "
        "  转告 '配好啦! 节点 VPS_IP:port, socks5 账号 X 密码 Y, 完全可用'.\n"
        "- ok + task.status=done + proxy_node.status=pending_fw: 代理挂上了但外部进不来. "
        "  转告 '代理已配好但外部进不来, 请登录 VPS 厂商面板 (阿里云/腾讯云等) "
        "  在安全策略组放行端口 PORT'.\n"
        "- ok + task.status=in_progress: 后台正在配置. "
        "  转告 '还在配置 (一般 1-3 分钟), 等几分钟再问'.\n"
        "- ok + task.status=pending: 排队等 worker 领. "
        "  转告 '排队等代理部署 worker 领取, 等几分钟再问'.\n"
        "- ok + task.status=failed + last_error_code=no_vps_capacity: VPS 池子满了. "
        "  转告 '没空闲 VPS 挂这条 IP, 需要加机器或停掉过期 VPS, "
        "  然后重新调 register_ip'.\n"
        "- ok + task.status=failed + last_error_code=inner_ping_failed: 代理配上去但内通不通. "
        "  转告 '代理装上去了但服务器内部不通, 上游 IP 可能已过期; 联系上游运营商'.\n"
        "- ok + task.status=failed + last_error_code=apply_binding_failed: xray 配置失败. "
        "  转告 '后台配 xray 出错, 把 last_error_msg 原样发给管理员排查'.\n"
        "- ok + task.status=failed + last_error_code=firewall_open_failed: 防火墙放行失败. "
        "  转告 '后台开端口失败, 把 last_error_msg 原样发给管理员排查'.\n"
        "- ok + task.status=failed + last_error_code=no_port_available: VPS 端口满了. "
        "  转告 '这台 VPS 端口候选池空 (1024-65535 全被占), 联系管理员排查'.\n"
        "- ok + task.status=failed + last_error_code=ssh_disconnected: SSH 中途断. "
        "  转告 '部署时 SSH 断了, 重试用完仍失败; 联系管理员核查 VPS'.\n"
        "- ok + task=null: IP 入库了但还没派 task (罕见). "
        "  转告 '已入库, 部署任务还没派, 联系管理员排查'.\n"
        "- not_found: 没查到这条 IP / 任务. "
        "  转告 'agent 上下文里的 ip_id/task_id 可能错了, 让用户重报 egress IP'.\n"
        "\n"
        "反例:\n"
        "- 不要把 ip.status=usable 转告 '配好了' —— usable 是入库后未挂阶段, "
        "  挂上了的标志是 task.status=done + proxy_node 非空.\n"
        "- 不要在 task.status=in_progress 时承诺 '马上好', 老老实实说 '等几分钟'.\n"
        "- 不要把 proxy_node.inbound_pwd 当 '永久密码' 转告 —— 这条凭据跟 VPS+端口 "
        "  绑死, 这条 IP 挂别的 VPS 时是另一对账密."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "ip_id": {
                "type": "integer",
                "description": (
                    "IP 主键 id (跟 task_id 二选一, ip_id 优先). "
                    "agent 调 register_ip 返回的 ip_id 字段."
                ),
            },
            "task_id": {
                "type": "integer",
                "description": (
                    "task 主键 id (跟 ip_id 二选一). "
                    "agent 调 register_ip 返回的 task_id 字段."
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
    result = query_ip_status(
        ip_id=args.get("ip_id"),
        task_id=args.get("task_id"),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
