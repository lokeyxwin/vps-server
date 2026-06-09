"""MCP 工具:register_vps —— 登记一台 VPS.

这文件装啥:
  register_vps 这个 MCP 工具的协议适配层 —— 把 MCP 调用包装成
  workers.ssh_worker.SSHWorker.process() 调用.

  本文件只做"协议转换", 不写任何业务逻辑.
  业务逻辑全在 SSHWorker 里 (同步段: 敲门 SSH + 采集 OS + 入库 + 派 xray task).

谁调我:
  - admin MCP 客户端 (agent 主动调)

我用到的工具:
  - mcp.types.Tool / ToolAnnotations / TextContent (MCP 标准)
  - workers.ssh_worker.SSHWorker (业务工人)

业务规约金标准:
  test/ssh_worker/spec.md v4 / test/mcp_tools/spec.md §6.1
"""

from __future__ import annotations

import json
from datetime import date

from mcp.types import TextContent, Tool, ToolAnnotations

from workers.ssh_worker import SSHWorker


TOOL = Tool(
    name="register_vps",
    title="登记一台 VPS",
    description=(
        "登记一台新的 VPS 服务器. 系统会 SSH 敲门探测连通性 + 采集 OS 信息, "
        "通过则入 vps_record + 派后台 xray 装机 worker 接力, 失败则不入库并返回原因.\n"
        "\n"
        "⚠️ 重要: 多台 VPS 请一台一台提交, 等上一台返回 (一般 5-30 秒) 再提交 "
        "下一台 —— SSH 资源同步使用, 并发提交会互相干扰; 部分服务商对频繁建连也会 "
        "fail2ban 短时拒接.\n"
        "\n"
        "典型场景:\n"
        "- 用户说 '帮我登记这台服务器 / 把这台机加进来' → 调本工具.\n"
        "- 用户给多台 → 一台调用一次, 串行处理, 每台返回后再调下一台.\n"
        "- 用户说 'ed 3 天' / '一周后到期' → 你自己换算成 ISO 日期 (YYYY-MM-DD) "
        "  填到 ed 字段.\n"
        "- 必填 port: 务必跟用户确认服务商面板给的远程登录端口, 不要默认填 22 "
        "  (很多服务商重定向到非 22 端口).\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- queued: SSH 通过 + 已入库 + 派后台 xray 装机 task; 告诉用户 "
        "  'VPS 已登记 (IP=X, OS=Y), 后台正在装 xray, 预计 5-15 分钟'.\n"
        "- already_registered: 这台 VPS 的 IP 之前已登记; 告诉用户 "
        "  '这台 VPS (IP=X) 之前登记过了, 后端返回了它的现状 (含活跃 task / "
        "  上次失败原因)'.\n"
        "- auth_failed: SSH 账密错; 告诉用户 '账号密码不对, 请核对面板凭据 "
        "  (注意 root 密码 ≠ 面板登录密码)'.\n"
        "- ssh_timeout: SSH 连不上, 超时; 告诉用户 '服务器连不上, 请确认 port "
        "  字段是面板给的远程登录端口 (服务商常重定向到非 22 端口)'.\n"
        "- ssh_refused: SSH 拒接; 告诉用户 '连接被拒, 同样确认 port 是不是 "
        "  面板给的端口, 或服务商 fail2ban 临时封了再试'.\n"
        "- ssh_failed: SSH 未知失败; 把返回 message 字段原样转告用户排查.\n"
        "\n"
        "反例 (明确禁止):\n"
        "- 不要并发调本工具 (多台 VPS 必须串行).\n"
        "- 不要用本工具更新已有服务器的密码 (本工具是登记, 重复调返 "
        "  already_registered).\n"
        "- 不要用本工具查节点列表 (用 get_available_proxy_nodes).\n"
        "- 不要用本工具直接触发装 xray (本工具只验连通 + 派 task, "
        "  xray 装机是后台 XrayWorker 接力).\n"
        "- 不要把 stage / xray_version 字段当 '装机已完成' 转告 —— SSHWorker "
        "  阶段永远写 stage=connectable + xray_version='', 装机进度看 "
        "  get_vps_registration_status 工具."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "ip": {
                "type": "string",
                "description": (
                    "服务器 IP 地址, IPv4 (1.2.3.4) 或 IPv6 (a:b::c). "
                    "不接受域名, 由 agent 自己解析 (避免本机 DNS 跟服务器 DNS 差异)."
                ),
            },
            "user": {
                "type": "string",
                "description": "SSH 登录用户名, 通常 root.",
            },
            "pwd": {
                "type": "string",
                "description": "SSH 登录密码, 由服务商面板提供.",
            },
            "port": {
                "type": "integer",
                "description": (
                    "SSH 端口, **必填**. 服务商常重定向到非 22 端口, "
                    "请去服务商控制台核对远程登录端口."
                ),
            },
            "ed": {
                "type": "string",
                "description": (
                    "到期日期, ISO 格式 YYYY-MM-DD. "
                    "用户说 '3 天' / '一个月' 时, 你自己换算成具体日期. "
                    "可空; 不知道就留空, 巡检会跳过这台机的到期判断."
                ),
                "default": "",
            },
            "provider": {
                "type": "string",
                "description": (
                    "服务商域名, 例如 'aliyun.com' / 'vultr.com', "
                    "用于运维归类 + 续费提醒. 可空."
                ),
                "default": "",
            },
        },
        "required": ["ip", "user", "pwd", "port"],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,           # 重复调返 already_registered, 不重复入库
        openWorldHint=False,
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    """MCP tools/call 入口.

    参数: arguments dict, 含 inputSchema 定义的字段; 必填 4 个 + 选填 2 个.
    返回: [TextContent], 内容是 SSHWorker.process() 返回 dict 的 JSON.

    业务工人不抛异常给上层 (CLAUDE.md 业务契约 + SSHWorker 已按 v4 spec 兜底),
    所以这里不做 try/except.
    """
    args = arguments or {}

    ed_str = args.get("ed", "") or ""
    ed = date.fromisoformat(ed_str) if ed_str else None

    result = SSHWorker().process(
        ip=args["ip"],
        user=args["user"],
        pwd=args["pwd"],
        port=int(args["port"]),
        ed=ed,
        provider=args.get("provider", "") or "",
    )

    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
