# T-06 tools/rgvps.py MCP 入口实现 + 端到端测试

**ID**: T-06
**前置依赖**: T-05 (SSHWorker.process 主入口)
**后续依赖**: 无(这是 rgvps 链路最后一环)

---

## 验收锚点

- `tools/get_available_proxy_nodes.py` 的现有 MCP 协议适配模式(参考)
- `tests_behavior/ssh_worker/spec.md` §2 入口契约(返回 status 集合)
- `CLAUDE.local.md` §2 MCP 工具入口风格
- MCP 官方 SDK: mcp.types.Tool + TextContent

## 改动文件清单

### 改 `tools/rgvps.py`(占位 → 完整实现)

```
① 取消顶部注释里的 `# from ... import ...`,改成真 import
② 实现 TOOL = Tool(...) 元数据(name / title / description / inputSchema)
③ 实现 async def handler(arguments) 编排:
   - 参数校验
   - 调 SSHWorker().process()
   - 把返回 dict 包成 [TextContent(text=json.dumps(result))]
```

### 改 `tools/__init__.py`

```
导入 rgvps 的 TOOL + handler,加进 ALL_TOOLS 元组列表.
```

### 新建测试

```
tests_behavior/ssh_worker/TC-06_e2e_via_mcp.py
端到端集成测试,通过 MCP handler 调用整条 rgvps 链路.
```

### 不动

```
不动 mcp_server.py (它已经按 ALL_TOOLS 注册,新工具自动生效)
不动 SSHWorker / db / xray / core
```

---

## 实现轮廓(给实现者参考)

### TOOL 元数据

```python
TOOL = Tool(
    name="rgvps",
    title="登记一台 VPS",
    description=(
        "用户提供一台 VPS 的账密,本工具会:① 查 DB 是否已登记;② 没有则 SSH "
        "探测 + 顺手看一眼系统/xray;③ 入库并派任务给装机工人接力。"
        "适合场景:用户说'帮我登记/添加这台服务器','把这台机加进来'。"
        "返回 5 种 status:"
        " - already_registered: DB 已登记,返回现状不动 SSH"
        " - queued: 新登记成功,后台开始装 xray"
        " - auth_failed: 密码错,不入库,让用户重新提交"
        " - unreachable: 连不上(超时/拒接),已入库标记,提示用户确认端口"
        " - 其他: 不应出现,如出现请反馈"
        "反例:不要用本工具更新已有服务器密码;不要用本工具看节点列表。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "ip": {"type": "string", "description": "服务器 IP"},
            "user": {"type": "string", "description": "SSH 登录用户名(通常 root)"},
            "pwd": {"type": "string", "description": "SSH 登录密码"},
            "port": {
                "type": "integer",
                "description": "SSH 端口(服务商提供的,不要默认 22)",
                "default": 22,
            },
            "ed": {
                "type": "string",
                "description": "到期日 YYYY-MM-DD(可选)",
                "default": "",
            },
            "provider": {
                "type": "string",
                "description": "服务商域名(如 aliyun.com,可选)",
                "default": "",
            },
        },
        "required": ["ip", "user", "pwd"],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,    # 入库不算 destructive
        idempotentHint=True,       # 重复调返回 already_registered
        openWorldHint=False,
    ),
)
```

### handler 实现

```python
async def handler(arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    
    # 参数取值 + 默认
    ip = args["ip"]
    user = args["user"]
    pwd = args["pwd"]
    port = int(args.get("port", 22))
    ed_str = args.get("ed", "")
    provider = args.get("provider", "")
    
    # ed 解析
    ed = None
    if ed_str:
        from datetime import date
        try:
            ed = date.fromisoformat(ed_str)
        except ValueError:
            return [TextContent(type="text", text=json.dumps({
                "status": "input_error",
                "message": f"ed 日期格式错误,需 YYYY-MM-DD,收到: {ed_str!r}"
            }, ensure_ascii=False))]
    
    # 调工人
    worker = SSHWorker()
    result = worker.process(
        ip=ip, user=user, pwd=pwd, port=port,
        ed=ed, provider=provider,
    )
    
    # 包成 TextContent
    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2, default=str),
    )]
```

### tools/__init__.py 改动

```python
# 加这两行
from tools.rgvps import TOOL as _rgvps_tool
from tools.rgvps import handler as _rgvps_handler

# ALL_TOOLS 加一条
ALL_TOOLS = [
    (_get_available_proxy_nodes_tool, _get_available_proxy_nodes_handler),
    (_rgvps_tool, _rgvps_handler),
]
```

---

## 测试用例(实现者按这些写 .py)

### TC-06 `tests_behavior/ssh_worker/TC-06_e2e_via_mcp.py`

```
端到端走整条 rgvps 链路.mock SSHWorker.process 验证 handler 协议层.

TC-06-a TOOL 元数据校验
  - TOOL.name == "rgvps"
  - inputSchema.required = ["ip", "user", "pwd"]
  - port / ed / provider 在 properties 里,但不必填

TC-06-b handler 调用 SSHWorker.process 透传参数
  mock SSHWorker.process 验收 kwargs:
    ip / user / pwd / port=22 / ed=None / provider=""

TC-06-c handler 返回 [TextContent], text 是合法 JSON
  parse 后含 status / vps / message 等字段

TC-06-d ed 参数解析
  - args["ed"]="2026-12-31" → ed = date(2026,12,31)
  - args 不传 ed → ed = None
  - args["ed"]="bad" → 返回 status='input_error',不调 process

TC-06-e port 整数转换
  - args["port"]=22 → process 收到 int(22)
  - args["port"]="22"(字符串)→ 转 int

TC-06-f arguments=None / 空 dict → 报缺必填字段(MCP 框架级)
  (这个其实 MCP server 会校验 inputSchema,handler 不一定收到,
   但写个 case 验证 args.get(...) 不爆)

TC-06-g 注册到 ALL_TOOLS
  from tools import ALL_TOOLS
  names = [t.name for t, _ in ALL_TOOLS]
  assert "rgvps" in names

TC-06-h handler 真接整链路(可选,标 @skip 等真 DB 环境)
  - 准备 mock SSHWorker,让它返回 status='queued', task_id=1
  - 调 handler({"ip":"1.2.3.4","user":"r","pwd":"x"})
  - 检查输出 JSON parse 后 status='queued'
```

---

## 实现者完工标准

```
- [ ] tools/rgvps.py 实现完(不再是注释)
- [ ] tools/__init__.py ALL_TOOLS 含 rgvps
- [ ] TC-06 测试全过
- [ ] mcp_server.py 不需要改(自动从 ALL_TOOLS 注册)
- [ ] handler 不直接 import db / xray / core(只走 SSHWorker)
- [ ] commit 标题: feat(tools): rgvps MCP 工具入口
```

---

## Claude 验收检查清单

```
□ 跑 TC-06 测试全过
□ git diff tools/rgvps.py:
    - TOOL 元数据完整(name/title/description/inputSchema/annotations)
    - handler 是 async 函数,返回 [TextContent]
    - 不直接调底层(SSHWorker.process 才是入口)
□ git diff tools/__init__.py:
    - 加了 _rgvps_tool / _rgvps_handler
    - ALL_TOOLS 加一条
□ 跑 uv run python -c "from tools import ALL_TOOLS; print([t.name for t,_ in ALL_TOOLS])"
  能看到 ['get_available_proxy_nodes', 'rgvps']
□ 启动 MCP server (uv run python mcp_server.py) 不报错
□ 对照 spec.md §2 入口契约检查返回字段
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回
```

---

## 备注:rgvps 链路全部完工后能拿到什么

T-06 完工 = **rgvps 端到端链路通了**:

```
agent 调 rgvps(ip, user, pwd, port)
  ↓
tools/rgvps.py::handler 协议适配
  ↓
SSHWorker().process()
  ↓ (路线 B 走通)
SSHWorker._敲门看一眼 → VPSSession + XrayManager
  ↓
SSHWorker._入库派任务 → VPSRecord + VPSTask 入库
  ↓
返回 task_id 给 agent
```

接下来要做的:**T-07 XrayWorker 实现** 让 vps_task 真能被消费(才是完整的
"装 xray + 纳管 + 升级 stage=running" 链路)。
