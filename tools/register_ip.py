"""MCP 工具:register_ip —— 登记一条上游 IP 代理凭据。

这文件装啥:
  register_ip 这个 MCP 工具的协议适配层 —— 把 MCP 调用包装成
  workers.ip_probe_worker.IPProbeWorker.process() 调用。

  本文件只做"协议转换",不写任何业务逻辑。
  业务逻辑全在 IPProbeWorker 里(同步段:校验账密 + 入 ip_record + 派 ip_task)。

  (旧名 rgip 已弃, T-17 改为标准名 register_ip;
   用户口头说 rgip 时 Claude 自动映射, 代码侧不留旧名 —— ADR-0007 §2)

谁调我:
  - admin MCP 客户端(agent 主动调)

我用到的工具:
  - mcp.types.Tool / ToolAnnotations / TextContent (MCP 标准)
  - workers.ip_probe_worker.IPProbeWorker (业务工人)

业务规约金标准:
  test/ip_probe_worker/spec.md v2
"""

from __future__ import annotations

import json
from datetime import date

from mcp.types import TextContent, Tool, ToolAnnotations

from workers.ip_probe_worker import IPProbeWorker


TOOL = Tool(
    name="register_ip",
    title="登记一条上游 IP 代理凭据",
    description=(
        "登记一条新的上游代理 IP 凭据。系统会用测试 VPS 临时挂这条凭据当 xray "
        "outbound, 内 ping 校验账号密码 + 入口端口能不能用; 通过则入库并派后台 "
        "worker 把它挂到生产 VPS, 失败则不入库并返回原因。\n"
        "\n"
        "⚠️ 重要: 多条 IP 凭据请一条一条提交, 等上一条返回 (一般 5-30 秒) 再提交 "
        "下一条 —— 后端测试 VPS 资源是同步使用的, 同时提交多条会互相干扰。\n"
        "\n"
        "典型场景:\n"
        "- 用户给一份服务商面板代理凭据 → 调本工具登记。\n"
        "- 用户给多条 → 一条调用一次, 串行处理, 每条返回后再调下一条。\n"
        "- 用户说 '这条 IP 有效期 3 天' → 你自己换算成 ISO 日期 (YYYY-MM-DD) "
        "  填到 expire_date。\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- queued: 已校验通过 + 入库, 返回 ip_id / task_id / egress_ip; 告诉用户 "
        "  '这条 IP 已登记 (出口 IP 是 X), 后台正在挂到生产 VPS'。\n"
        "- duplicate: 这条出口 IP 已在库 (返回的 egress_ip 字段就是被重复的 IP); "
        "  告诉用户 '出口 IP X 之前登记过了, 无需重复'。\n"
        "- proxy_auth_failed: 账密错; 转告用户校验易混字符 (0/O / 1/l/I / K/k / "
        "  c/C), 并提醒服务商面板登录密码 ≠ 代理认证密码。\n"
        "- proxy_timeout: 上游超时, 已自动重试 3 次仍失败; 转告用户稍后重试 / "
        "  核对入口端口是否填错。\n"
        "- proxy_refused: 上游拒接 (罕见); 转告用户上游服务可能已停用。\n"
        "- proxy_failed: 其他失败; 把返回 message 转告用户。\n"
        "- probe_vps_unreachable: 后端测试 VPS 全连不上 (SSH 都不通); 告诉用户 "
        "  联系管理员检查测试 VPS 凭据 / 网络。\n"
        "- probe_vps_not_ready: 测试 VPS SSH 通但 xray 基础设施挂了 (装/起/配 失败); "
        "  告诉用户 '后台测试机 xray 异常 (不是你的 IP 问题), 请管理员跑一次 "
        "  init_probe_vps 或检查测试机 xray 状态'。\n"
        "\n"
        "反例:\n"
        "- 不要并发调本工具 (多条 IP 必须串行)。\n"
        "- 不要在没有 queued 之前承诺 '已登记'。\n"
        "- 不要把 declared_egress_ip 当 '最终出口 IP' 转告 —— 系统会重新实测, "
        "  以 queued 返回的 egress_ip 为准。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "entry_host": {
                "type": "string",
                "description": (
                    "上游代理入口主机, 可以是 IP 也可以是域名。"
                    "例如 '1.2.3.4' 或 'proxy.miluproxy.com'。"
                ),
            },
            "entry_port": {
                "type": "integer",
                "description": "上游代理入口端口, 例如 5001。",
            },
            "username": {
                "type": "string",
                "description": "上游代理用户名, 由服务商面板提供。",
            },
            "password": {
                "type": "string",
                "description": "上游代理密码, 由服务商面板提供。",
            },
            "protocol": {
                "type": "string",
                "enum": ["socks5", "http"],
                "description": "上游代理协议, 当前支持 socks5 / http。",
            },
            "declared_egress_ip": {
                "type": "string",
                "description": (
                    "服务商声明的出口 IP, 仅用于早期查重短路 (命中则不实测直接 "
                    "返回 duplicate)。可空; 如果不知道就留空, 系统会内 ping 实测 "
                    "再二次查重。"
                ),
                "default": "",
            },
            "provider_domain": {
                "type": "string",
                "description": (
                    "服务商域名, 例如 'miluproxy.com', 便于运维归类。可空。"
                ),
                "default": "",
            },
            "expire_date": {
                "type": "string",
                "description": (
                    "凭据有效期截止日期, ISO 格式 YYYY-MM-DD。"
                    "用户说 '3 天' / '一个月' 时, 你自己换算成具体日期再填。"
                    "可空; 不知道就留空, 后续巡检会跳过这条 IP。"
                ),
                "default": "",
            },
            "user_label": {
                "type": "string",
                "description": (
                    "用户自定备注, 例如 '新加坡-机房 A' / '客户 X 专用'。"
                    "用于运维归类 / 排查, 不参与业务逻辑。可空。"
                ),
                "default": "",
            },
        },
        "required": ["entry_host", "entry_port", "username", "password", "protocol"],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    """MCP tools/call 入口。

    参数: arguments dict, 含 inputSchema 定义的字段; 必填 5 个 + 选填 4 个。
    返回: [TextContent], 内容是 IPProbeWorker.process() 返回 dict 的 JSON。

    业务工人不抛异常给上层 (CLAUDE.md 业务契约 + IPProbeWorker 整段 except 兜底),
    所以这里不做 try/except。
    """
    args = arguments or {}

    expire_str = args.get("expire_date", "") or ""
    expire = date.fromisoformat(expire_str) if expire_str else None

    result = IPProbeWorker().process(
        entry_host=args["entry_host"],
        entry_port=int(args["entry_port"]),
        username=args["username"],
        password=args["password"],
        protocol=args["protocol"],
        declared_egress_ip=args.get("declared_egress_ip", "") or "",
        provider_domain=args.get("provider_domain", "") or "",
        expire_date=expire,
        user_label=args.get("user_label", "") or "",
    )

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
