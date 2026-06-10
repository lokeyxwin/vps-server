"""MCP 工具:init_probe_vps (admin) — 装好测试 VPS xray 基础设施.

这文件装啥:
  init_probe_vps 这个 MCP 工具的协议适配层 —— 把 MCP 调用包装成
  probe_vps.bootstrap.ensure_ready 调用. 本文件只做"协议转换", 不写业务逻辑.

谁调我:
  - admin MCP 客户端 (运维 / agent 主动调).
  - 收到 IPProbeWorker 返 probe_vps_not_ready 后, agent 可主动调本工具修复.

业务规约金标准: ADR-0009 §决策 §6.3.
"""

from __future__ import annotations

import json

from mcp.types import TextContent, Tool, ToolAnnotations

from probe_vps import (
    NO_PROBE_VPS_MESSAGE,
    ProbeVPSSetupFailed,
    ProbeVPSUnreachable,
    bootstrap,
    get_probe_vps_pool,
)


TOOL = Tool(
    name="init_probe_vps",
    title="初始化测试 VPS (装 xray + 起 + inbound)",
    description=(
        "幂等装好测试 VPS, 给 IPProbeWorker 校验上游 IP 用. "
        "SSH 连测试机 + 装 xray + 起 xray + add socks5 noauth inbound 19000.\n"
        "\n"
        "典型场景:\n"
        "- 首次部署: 配好 PROBE_VPS_N_* env 后调一次.\n"
        "- 测试机替换 / xray 挂了: 重新调一次.\n"
        "- agent 收到 IPProbeWorker 返 probe_vps_not_ready 后, 主动调本工具修复.\n"
        "\n"
        "返回 status 含义 (照此转告用户):\n"
        "- ok + host + inbound_port: 测试机已就绪; 告诉用户 '测试机已装好'.\n"
        "- probe_vps_unreachable + message: SSH 都连不上 (检查 PROBE_VPS_N_* env "
        "  / 测试机宕机).\n"
        "- probe_vps_not_ready + message: SSH 通但 xray 装/起/配 失败 "
        "  (网络/磁盘/权限, 见 message 详情).\n"
        "\n"
        "反例:\n"
        "- 不要把测试机当生产 VPS 用 (它不入 vps_record).\n"
        "- 不要并发调 (测试机自身资源同步占用).\n"
        "- 不要拿测试机给客户挂代理 (违反 ADR-0009 §1 设计意图)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "slot": {
                "type": "integer",
                "description": "选 PROBE_VPS_POOL 第几条 (0-based, default 0).",
                "default": 0,
            },
        },
        "required": [],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,           # 涉及外部 (SSH + xray 装机走 GitHub)
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    """MCP tools/call 入口.

    入参 slot (可选, 默认 0). 跑 ensure_ready(pool[slot]).
    异常按 ADR-0009 §决策 §6.3 映射到 status; 任何其他异常兜底转 failed.
    """
    args = arguments or {}
    slot = int(args.get("slot", 0) or 0)

    try:
        pool = get_probe_vps_pool()
    except RuntimeError:
        result: dict = {
            "status": "probe_vps_unreachable",
            "message": NO_PROBE_VPS_MESSAGE,
        }
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        return [TextContent(type="text", text=payload)]

    if slot < 0 or slot >= len(pool):
        result = {
            "status": "probe_vps_unreachable",
            "message": (
                f"slot={slot} 越界 (pool 长度={len(pool)}). "
                "请检查 PROBE_VPS_N_* env 编号."
            ),
        }
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        return [TextContent(type="text", text=payload)]

    entry = pool[slot]
    try:
        handle = bootstrap.ensure_ready(entry)
        result = {
            "status": "ok",
            "host": handle.host,
            "inbound_port": handle.inbound_port,
        }
    except ProbeVPSUnreachable as exc:
        result = {"status": "probe_vps_unreachable", "message": str(exc)}
    except ProbeVPSSetupFailed as exc:
        result = {"status": "probe_vps_not_ready", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — admin 工具兜底转 failed
        result = {
            "status": "probe_vps_not_ready",
            "message": f"{type(exc).__name__}: {exc}",
        }

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
