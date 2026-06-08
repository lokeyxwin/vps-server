# T-14 tools/rgip.py MCP 工具入口(rgip)

**ID**: T-14
**状态**: waiting
**前置依赖**: T-13(IPProbeWorker 实现 done)
**后续依赖**: 无(rgip 端到端跑通即完整闭环)
**关联 ADR**: [[0001-workers-replace-services]] §决策 §5(MCP 暴露三类工具,绝不暴露内部子动作)
**关联 spec**: [[test/ip_probe_worker/spec.md]] v2(同步段契约 + 7 种 status 文案)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 已确认 T-13 已 done(本任务依赖 IPProbeWorker 实现)。
- [ ] 开始写代码前, 已将文件名从 `waiting_14_...md` 改为 `doing_14_...md`。

### 必读清单

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`(尤其 §MCP 工具层必须和后端业务解耦 + 工具命名和描述约束)
- [ ] `docs/adr/0001-workers-replace-services.md` §决策 §5
- [ ] `test/ip_probe_worker/spec.md` v2 §2 入口契约
- [ ] `tools/rgvps.py` 当前实现(MCP 工具样板,1:1 对称写 rgip.py)
- [ ] `tools/__init__.py`(ALL_TOOLS 注册位)
- [ ] `tools/get_available_proxy_nodes.py`(纯只读工具样板)
- [ ] `mcp_server.py`(确认 list_tools / call_tool 分发逻辑不需要改)
- [ ] `workers/ip_probe_worker.py`(IPProbeWorker.process 入参签名,T-13 改后)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "对外只展示 rgvps 和 rgip 这两个 mcp 工具入口"
> "rgvps 和 rgIP 这两个工具和若干查询工具"

> "算了就统一同步吧, 反正对外 mcp 说明如果有多条等待第一条搞完再扔第二条, 让模型跟用户卡住"

### 整理后的业务理解

- **外部输入**: agent 通过 MCP 协议调 `rgip` 工具, 传入上游 IP 凭据
- **第一件事**: tools/rgip.py handler 接 MCP 参数 → 调 IPProbeWorker().process(...)
- **主要流程**:
  1. MCP handler 解析 arguments
  2. 调 `IPProbeWorker().process(...)` 同步段
  3. 把工人返回 dict 包成 MCP content 回去
- **判断分支**: 无(全部交给 IPProbeWorker)
- **数据流**: 无(adapter 不读不写 DB)
- **同步 / 异步边界**: 全同步(IPProbeWorker 同步段完后返回)
- **成功 / 失败返回**: 透传 IPProbeWorker 返回的 status dict

### 本任务要解决什么

- 实现 `tools/rgip.py` MCP 工具适配模块
- 注册到 `tools/__init__.py::ALL_TOOLS`
- Tool.description 必须告诉 agent: **多条 IP 要一条一条提交,等上一条返回再提交下一条**(MCP 边界外部串行)

### 本任务不解决什么

- ✗ 不动 mcp_server.py(它只 list_tools + call_tool 分发)
- ✗ 不动 IPProbeWorker 实现(T-13 已完成)
- ✗ 不在 adapter 层写任何业务判断(全部交给工人)
- ✗ 不暴露内部子动作(SSH / xray / 内 ping 等)给 agent
- ✗ 不实现 ProxyDeployWorker 端的状态查询工具(后续单独任务)

---

## 2. 实现参考

### 验收锚点

- `tools/rgvps.py` 完整实现(本任务 1:1 对称参照)
- `CLAUDE.local.md` §MCP 工具层(adapter 只写元数据 + 参数/返回包装)
- `CLAUDE.local.md` §工具命名和描述约束(Tool.name snake_case 一眼看懂用途;Tool.description 写典型用户问题 / 何时调用 / 参数怎么填 / 返回 0/1/多条时怎么回答)
- `test/ip_probe_worker/spec.md` v2 §2(IPProbeWorker.process 入参 + 返回 status 集)

### 改动文件清单

#### 新建 `tools/rgip.py`

```text
职责: MCP 工具 rgip 的协议适配模块。

包含:
- TOOL: mcp.types.Tool 实例
  - name = "rgip"
  - title = "登记一条上游 IP 代理凭据"
  - description = 大白话告诉 agent: 何时调 / 多条要一条一条提交 / 参数语义 / 7 种 status 怎么回答用户
  - inputSchema = 8 个参数:
      entry_host (str) — 上游代理入口 (IP 或域名)
      entry_port (int) — 上游代理入口端口
      username (str)
      password (str)
      protocol (str, 枚举 "socks5" / "http")
      declared_egress_ip (str, 可空) — 用户提交的"声明出口 IP", 用作早期查重弹药
      provider_domain (str, 可空) — 服务商域名 (便于运维归类)
      expire_date (str, ISO 日期, 可空) — 用户提交的"3 天"由 agent 换算成日期 YYYY-MM-DD
  - readOnlyHint = False (写表)
  - destructiveHint = False (只新加, 不删现有)

- handler(arguments) -> list[TextContent]
  - 解析 arguments dict (字段名跟 inputSchema 一致)
  - expire_date 字符串 → date 对象(date.fromisoformat)
  - 调 IPProbeWorker().process(...)
  - 把工人返回 dict 序列化成 JSON 或大白话文本作 TextContent
```

#### 改 `tools/__init__.py`

```text
import 新工具:
   from tools import rgip
ALL_TOOLS 加一项:
   (rgip.TOOL, rgip.handler)
```

#### 新建 `test/tools/test_rgip.py`

```text
TC: rgip adapter
- TOOL.name == "rgip"
- TOOL.description 含"一条一条提交"等关键字
- inputSchema 含 8 个字段
- handler 调用时 mock IPProbeWorker.process, 验证参数透传正确
- handler 返回 TextContent (不直接断言文本格式, 但 status 字段必须出现)
```

#### 不动

```text
- mcp_server.py(list_tools / call_tool 自动跟随 ALL_TOOLS)
- workers/ip_probe_worker.py(T-13 实现完, T-14 只调)
- tools/rgvps.py / tools/get_available_proxy_nodes.py
- 任何 db / xray / ssh / toolbox 代码
```

### 实现轮廓

```python
# tools/rgip.py

"""MCP 工具 rgip —— 登记一条上游 IP 代理凭据。

agent 调用 → IPProbeWorker.process 同步段 → 返回 7 种 status 之一。

多条 IP 必须一条一条提交 (Tool.description 已显式提示 agent),
内部不加锁(MCP 边界外部串行)。
"""

from __future__ import annotations

import json
from datetime import date

import mcp.types as types

from workers.ip_probe_worker import IPProbeWorker


TOOL = types.Tool(
    name="rgip",
    title="登记一条上游 IP 代理凭据",
    description=(
        "登记一条新的上游代理 IP 凭据。系统会用测试 VPS 校验账密 + 端口是否可用, "
        "通过则入库并派后台工人挂到生产 VPS, 失败则不入库并返回原因。\n"
        "\n"
        "⚠️ 重要: 多条 IP 凭据请一条一条提交, "
        "等上一条返回 (5-30 秒) 再提交下一条 —— 后端测试 VPS 资源是同步使用的, "
        "同时多条调用可能互相干扰。\n"
        "\n"
        "典型问题:\n"
        "- 用户给一份服务商面板的代理凭据 → 调本工具登记\n"
        "- 用户给多条 → 一条调用一次, 串行处理\n"
        "\n"
        "返回 status 含义:\n"
        "- queued: 已校验通过 + 入库, 后台 worker 会接手挂到生产 VPS\n"
        "- duplicate: 这条出口 IP 已经在库\n"
        "- proxy_auth_failed: 账密错(转告用户校验 0/O、1/l/I、K/k 等易混字符)\n"
        "- proxy_timeout: 上游超时, 3 次重试都失败(转告用户稍后重试 / 核对端口)\n"
        "- proxy_refused: 上游拒接(罕见, 转告用户上游服务可能停了)\n"
        "- proxy_failed: 其他失败(把 message 转告用户)\n"
        "- probe_vps_unreachable: 测试 VPS 都连不上(找管理员)\n"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "entry_host": {
                "type": "string",
                "description": "上游代理入口主机 (IP 或域名), 例 '1.2.3.4' 或 'proxy.miluproxy.com'。",
            },
            "entry_port": {
                "type": "integer",
                "description": "上游代理入口端口, 例 5001。",
            },
            "username": {
                "type": "string",
                "description": "上游代理用户名, 服务商面板提供。",
            },
            "password": {
                "type": "string",
                "description": "上游代理密码, 服务商面板提供。",
            },
            "protocol": {
                "type": "string",
                "enum": ["socks5", "http"],
                "description": "上游代理协议, 当前支持 socks5 / http。",
            },
            "declared_egress_ip": {
                "type": "string",
                "description": "服务商声明的出口 IP, 用于早期查重短路。可空。",
                "default": "",
            },
            "provider_domain": {
                "type": "string",
                "description": "服务商域名, 例 'miluproxy.com'。可空, 便于运维归类。",
                "default": "",
            },
            "expire_date": {
                "type": "string",
                "description": "凭据有效期截止日期 (ISO 格式 YYYY-MM-DD)。用户给'3 天'时, agent 自己换算成日期填入。可空。",
                "default": "",
            },
        },
        "required": ["entry_host", "entry_port", "username", "password", "protocol"],
    },
    readOnlyHint=False,
    destructiveHint=False,
)


def handler(arguments: dict) -> list[types.TextContent]:
    """MCP handler: 解析 arguments → 调 IPProbeWorker → 包返回。"""
    expire_str = arguments.get("expire_date", "")
    expire = date.fromisoformat(expire_str) if expire_str else None
    
    result = IPProbeWorker().process(
        entry_host=arguments["entry_host"],
        entry_port=int(arguments["entry_port"]),
        username=arguments["username"],
        password=arguments["password"],
        protocol=arguments["protocol"],
        declared_egress_ip=arguments.get("declared_egress_ip", ""),
        provider_domain=arguments.get("provider_domain", ""),
        expire_date=expire,
    )
    
    # 直接把 status dict 序列化成 JSON 作 MCP 返回(参照 rgvps 风格)
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
```

```python
# tools/__init__.py 改后片段:

from tools import rgip, rgvps, get_available_proxy_nodes


ALL_TOOLS = [
    (rgvps.TOOL, rgvps.handler),
    (rgip.TOOL, rgip.handler),                       # ⭐ 新增
    (get_available_proxy_nodes.TOOL, get_available_proxy_nodes.handler),
]
```

### 缺工具 / 缺信息先报告

- 如果发现 IPProbeWorker.process 入参签名跟 T-13 spec 锚点 / 本任务实现轮廓不一致 → 停下报告
- 如果 mcp.types 没有 readOnlyHint / destructiveHint 字段(老版本 SDK)→ 跟现有 rgvps 风格保持一致即可,不强加

---

## 3. 验收交付

### 测试用例

#### TC-14-a `test/tools/test_rgip.py`

业务故事:

```text
agent 调 rgip 工具, MCP handler 透传参数给 IPProbeWorker, 把结果包成 TextContent 返回。
```

输入 / 预期:

- TOOL.name == "rgip"
- TOOL.description 含 "一条一条提交" 关键字
- inputSchema.required 含 entry_host / entry_port / username / password / protocol 5 个
- inputSchema.properties 含 8 个字段
- handler 调用时 mock IPProbeWorker.process(return {"status":"queued","ip_id":1,"task_id":2,"message":"..."}):
  - process 收到 entry_host / entry_port (int) / username / ... 参数(类型正确)
  - expire_date 字符串"2026-06-15"被转成 date 对象
  - 返回 list[TextContent] 长度 1
  - TextContent.text 含 "queued" 字符串
- ALL_TOOLS 中含 (rgip.TOOL, rgip.handler) 一项

### 必跑测试命令

```bash
VPS_SERVER_TESTING=1 pytest test/tools/test_rgip.py -v
```

(如 test/tools/ 不存在, 新建 + __init__.py)

### 实现者完工标准

- [ ] 开工前文件名 waiting → doing
- [ ] T-13 已 done(本任务依赖)
- [ ] `tools/rgip.py` 新建, 含 TOOL + handler
- [ ] `tools/__init__.py` ALL_TOOLS 加 rgip
- [ ] `test/tools/test_rgip.py` 新建 TC 全 PASS
- [ ] Tool.description 含"一条一条提交"显式提示(MCP 边界外部串行)
- [ ] inputSchema 8 个字段齐, 必填 5 个
- [ ] expire_date 字符串自动转 date 对象
- [ ] adapter 层不写任何业务判断(纯透传)
- [ ] 不动 mcp_server.py / IPProbeWorker
- [ ] 完成记录段已填

### 实现过程记录

```text
改动文件:
- tools/rgip.py (新建)
- tools/__init__.py (改 ALL_TOOLS)
- test/tools/__init__.py (如不存在, 新建)
- test/tools/test_rgip.py (新建)

测试结果:
- VPS_SERVER_TESTING=1 pytest test/tools/test_rgip.py -v -> <result>

偏差 / 风险:
- <none | details>
```

### Claude 验收检查清单

□ 对照 rgvps.py 检查 1:1 结构对称
□ 对照 CLAUDE.local.md §MCP 工具层检查 adapter 不写业务
□ 对照 spec v2 §2 入口契约检查参数 / 返回 status 集
□ Tool.description 含"一条一条提交"硬约束
□ 跑 pytest 验证 PASS
□ 偏差但合理 -> 抛给用户决策
□ 偏差不合理 -> 打回实现者修改

---

## 完成记录(done 时追加)

```text
完成日期:
完成 commit:
任务状态: doing -> done
改动摘要:
测试命令:
测试结果:
未覆盖风险:
后续任务:
```
