# T-06 tools/rgvps.py MCP 入口实现 + 端到端测试（v4 对齐）

**ID**: T-06
**前置依赖**: T-05 (SSHWorker.process 主入口 v4)
**后续依赖**: 无（这是 rgvps 链路最后一环 —— 把 SSHWorker 包成 agent 可调的 MCP 工具）

---

## 验收锚点

- `test/ssh_worker/spec.md` **v4** §0 实现者硬约束（旧代码姿势 + 缺工具先报告）
- `test/ssh_worker/spec.md` **v4** §2 入口契约：
  - 入参：ip / user / pwd / port（**必填**）/ ed / provider（**可选**）
  - 返回 5 种 status（**没有** `unreachable`）
- `test/ssh_worker/spec.md` **v4** §3 三条主路线
- `tools/get_available_proxy_nodes.py` 的现有 MCP 协议适配模式（仅参考，不 import）
- `CLAUDE.local.md` §0 legacy 代码三档姿势表
- `CLAUDE.local.md` §模块组织 / MCP 工具命名和描述约束
- MCP 官方 SDK：`mcp.types.Tool` + `TextContent` + `ToolAnnotations`

---

## 改动文件清单

### 改 `tools/rgvps.py`（占位 → 完整实现）

```
① 顶部注释里的 `# from ... import ...` 改成真 import
② 实现 TOOL = Tool(...) 元数据（name / title / description / inputSchema / annotations）
③ 实现 async def handler(arguments) 编排：
   - 参数取值（port 必填）
   - ed 日期格式校验
   - 调 SSHWorker().process()
   - 把返回 dict 包成 [TextContent(text=json.dumps(result))]
```

### 改 `tools/__init__.py`

```
导入 rgvps 的 TOOL + handler, 加进 ALL_TOOLS 元组列表
```

### 新建测试

```
test/ssh_worker/TC-06_e2e_via_mcp.py
端到端集成测试, 通过 MCP handler 调用整条 rgvps 链路
```

### 不动

```
不动 mcp_server.py（它已经按 ALL_TOOLS 注册, 新工具自动生效）
不动 SSHWorker / db / xray / ssh / toolbox
```

---

## 实现轮廓（给实现者参考）

### TOOL 元数据（v4）

```python
TOOL = Tool(
    name="rgvps",
    title="登记一台 VPS",
    description=(
        "用户提供一台 VPS 的账密, 本工具会: "
        "① 查 DB 是否已登记 → 已登记直接返回现状, 不动 SSH; "
        "② 没登记则 SSH 探测并顺手采集 OS, 验账密能不能用; "
        "③ 入库 stage=connectable + 派任务给后台装机 worker 接力。"
        "适合场景: 用户说'帮我登记/添加这台服务器','把这台机加进来'。"
        "返回 5 种 status: "
        " - already_registered: DB 已登记, 返回现状(含活跃 task / 上次失败原因)不动 SSH; "
        " - queued: 新登记成功, 后台 worker 会接手装 xray; "
        " - auth_failed: 账密错, 不入库, 让用户校正后重新提交; "
        " - ssh_timeout: SSH 连接超时, 不入库, 提示用户核对端口/安全策略组; "
        " - ssh_refused: SSH 连接被拒, 不入库, 同上排查; "
        " - ssh_failed: SSH 未知失败, 不入库, message 含原始错误。"
        "反例: "
        " - 不要用本工具更新已有服务器密码; "
        " - 不要用本工具看节点列表(用 get_available_proxy_nodes); "
        " - 不要用本工具触发装 xray(本工具只负责连通验证 + 派任务, 装机异步)。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "ip": {
                "type": "string",
                "description": "服务器 IP, 例如 1.2.3.4 或 IPv6"
            },
            "user": {
                "type": "string",
                "description": "SSH 登录用户名, 通常 root"
            },
            "pwd": {
                "type": "string",
                "description": "SSH 登录密码"
            },
            "port": {
                "type": "integer",
                "description": (
                    "SSH 端口(**必填**, 请去服务商控制台核对远程登录端口, "
                    "不要默认 22 —— 填错会导致连不上)"
                ),
                # v4: 删 "default": 22
            },
            "ed": {
                "type": "string",
                "description": "到期日 YYYY-MM-DD(可选, 例如 2027-01-15)",
                "default": "",
            },
            "provider": {
                "type": "string",
                "description": "服务商域名(可选, 例如 aliyun.com / vultr.com)",
                "default": "",
            },
        },
        "required": ["ip", "user", "pwd", "port"],  # v4: 加 port
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

### handler 实现（v4）

```python
async def handler(arguments: dict | None) -> list[TextContent]:
    args = arguments or {}

    # 参数取值（v4: port 必填, 不再 .get("port", 22)）
    ip = args["ip"]
    user = args["user"]
    pwd = args["pwd"]
    port = int(args["port"])
    ed_str = args.get("ed", "")
    provider = args.get("provider", "")

    # ed 解析（可选）
    ed = None
    if ed_str:
        from datetime import date
        try:
            ed = date.fromisoformat(ed_str)
        except ValueError:
            return [TextContent(type="text", text=json.dumps({
                "status": "input_error",
                "message": f"ed 日期格式错误, 需 YYYY-MM-DD, 收到: {ed_str!r}"
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

⚠️ v4 关键变化：
- `port = int(args["port"])` （没有默认值，必填）
- inputSchema `required` 加进 `port`
- description 描述返回 status **5 种**（去掉 `unreachable`，加 `ssh_timeout / ssh_refused / ssh_failed`）
- description 改"顺手采集 OS"（不再说"顺手看一眼系统/xray"，SSHWorker 不查 xray）
- 反例加一条"不要用本工具触发装 xray"（说明边界）

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

## 测试用例（实现者按这些写 .py）

### TC-06 `test/ssh_worker/TC-06_e2e_via_mcp.py`

```
端到端走整条 rgvps 链路。mock SSHWorker.process 验证 handler 协议层。

TC-06-a TOOL 元数据校验
  - TOOL.name == "rgvps"
  - inputSchema.required == ["ip", "user", "pwd", "port"]  ← v4: 加 port
  - port 字段无 "default" 键               ← v4 防回退
  - ed / provider 在 properties 里, 但不必填
  - annotations.idempotentHint == True

TC-06-b handler 调用 SSHWorker.process 透传参数
  mock SSHWorker.process 验收 kwargs:
    args = {"ip": "1.2.3.4", "user": "root", "pwd": "x", "port": 8022}
    → process 收到 port=8022 (用户传的值, 不是 22)
    → ed=None / provider=""

TC-06-c handler 返回 [TextContent], text 是合法 JSON, parse 后符合 spec v4 §2 返回形状
  按 5 种 status 各跑一遍 mock:
    queued / already_registered / auth_failed / ssh_timeout / ssh_refused / ssh_failed
  验证 parse 后 status 字段对得上

TC-06-d ed 参数解析
  - args["ed"]="2026-12-31" → ed = date(2026,12,31)
  - args 不传 ed → ed = None
  - args["ed"]="bad" → 返回 status='input_error', 不调 process
  - args["ed"]="" → ed = None（空串等同未传）

TC-06-e port 字段处理 (v4)
  - args["port"]=8022 (int) → process 收到 int(8022)
  - args["port"]="8022" (字符串) → 转 int
  - ⭐ args 不传 port → handler 抛 KeyError 或 process 不被调
    (MCP server 会预先按 inputSchema 校验 required, 但 handler 应防御)

TC-06-f arguments=None / 空 dict → 报缺必填字段
  (MCP 框架级会校验 inputSchema, handler 在收到时应抛 KeyError on args["ip"])

TC-06-g 注册到 ALL_TOOLS
  from tools import ALL_TOOLS
  names = [t.name for t, _ in ALL_TOOLS]
  assert "rgvps" in names

TC-06-h ⭐ 防回退测试: returned message 字段不含旧 'unreachable' 字眼
  mock process 返回 status='ssh_timeout' →
  handler 输出 JSON parse 后 status='ssh_timeout' (不是 'unreachable')
  vps_id 字段不应在返回 dict 中（spec v4 §3 路线 C 不入库）

TC-06-i ⭐ 防回退测试: description 含 "port(必填)" / "服务商控制台核对"
  TOOL.description 应明确强调 port 必填和核对端口

TC-06-j ⭐ 防回退测试: description 不含 "查 xray 版本" / "顺手看 xray" 等字眼
  对照 spec v4 §3 路线 B SSHWorker 不查 xray, description 不应误导 agent

TC-06-k handler 真接整链路（可选, 标 @skip 等真 DB 环境）
  - 准备 mock SSHWorker, 让它返回 status='queued', task_id=1
  - 调 handler({"ip":"1.2.3.4","user":"r","pwd":"x","port":22})
  - 检查输出 JSON parse 后 status='queued'
```

---

## 实现者完工标准

```
- [ ] tools/rgvps.py 实现完（不再是注释）
- [ ] tools/__init__.py ALL_TOOLS 含 rgvps
- [ ] TC-06 测试全过（含 3 个防回退测试 TC-06-h/i/j）
- [ ] mcp_server.py 不需要改（自动从 ALL_TOOLS 注册）
- [ ] handler 不直接 import db / xray / ssh / toolbox（只走 SSHWorker）
- [ ] inputSchema.required 含 "port"
- [ ] inputSchema.properties.port 无 "default" 键
- [ ] description 描述 5 种 status（不含 unreachable）
- [ ] description 描述"顺手采集 OS"（不含"查 xray"误导）
- [ ] commit 标题: feat(tools): rgvps MCP 工具入口 (spec v4)
- [ ] 如遇 spec 里没写清楚 / 缺工具 → **停下来报告**, 不自己拍（spec v4 §0 第 2 条）
```

---

## 实现过程记录（实现者完工时填）

> 如果造了新工具 / 改了现有工具，按这个格式记录：

```
- 改/造了 <工具名>
  住 <文件路径>
  干啥 <一句话>
  测试 <TC 编号>
  审批 用户在 <对话/issue> 批准
```

如果只是按本任务单实现 TOOL + handler, 没造新工具, 写"无新增工具"即可。

---

## Claude 验收检查清单

```
□ 跑 TC-06 测试全过（含 3 个防回退 TC-06-h/i/j）
□ git diff tools/rgvps.py:
    - TOOL 元数据完整(name/title/description/inputSchema/annotations)
    - inputSchema.required 含 "port"
    - inputSchema.properties.port 无 "default" 键
    - description 文本含 5 种 status, 无 "unreachable" 字眼
    - description 文本无 "查 xray" / "看 xray 版本" 等字眼
    - handler 是 async 函数, 返回 [TextContent]
    - handler 中 port 取值是 args["port"] (不是 .get("port", 22))
    - 不直接调底层 (SSHWorker.process 才是入口)
□ git diff tools/__init__.py:
    - 加了 _rgvps_tool / _rgvps_handler
    - ALL_TOOLS 加一条
□ 跑 uv run python -c "from tools import ALL_TOOLS; print([t.name for t,_ in ALL_TOOLS])"
  能看到 ['get_available_proxy_nodes', 'rgvps']
□ 启动 MCP server (uv run python mcp_server.py) 不报错
□ 对照 spec v4 §2 入口契约逐字段验证
□ 实现过程记录段是否填了（造了啥/无新增工具）
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回
```

---

## 备注: rgvps 链路全部完工后能拿到什么

T-06 完工 = **rgvps 端到端链路通了**:

```
agent 调 rgvps(ip, user, pwd, port, [ed, provider])
  ↓
tools/rgvps.py::handler 协议适配
  ↓ (port 必填校验, ed 日期校验)
SSHWorker().process()
  ↓ 路线 A: DB 已有 → 返回 already_registered + 现状
  ↓ 路线 B: SSH 通 → 入库 stage=connectable + 派 vps_task pending → 返回 queued + task_id
  ↓ 路线 C: SSH 失败 → 4 种 status 抛回, **不入库**
```

接下来要做的（**不在本批 SSHWorker 链路范围**）：
- **T-07 XrayWorker 实现**：让 vps_task 真能被消费（装 xray + 纳管 + stage 切到 running 占用 + 干完释放）
- T-07 还要按 spec v4 同步改写（XrayWorker 自己查 xray、状态机 4 值、stage running 占用语义等）

---

## v4 vs v3 修订总结

| 项 | v3 | v4 |
|---|----|----|
| port 字段 default | `default: 22` | **删** default, 加进 required |
| inputSchema.required | `["ip", "user", "pwd"]` | `["ip", "user", "pwd", "port"]` |
| description 描述 status 数 | 5 种（含 `unreachable`）| **5 种** (`already_registered / queued / auth_failed / ssh_timeout / ssh_refused / ssh_failed`)|
| description "顺手看 xray" | ✅ 有 | ❌ **删**（SSHWorker 不查 xray）|
| description 反例 | 2 条 | 3 条（加"不要用本工具触发装 xray"）|
| handler port 取值 | `int(args.get("port", 22))` | `int(args["port"])` |
| 测试 TC 数 | 8 (a-h) | **11** (a-k，加 3 个防回退 + 改 e 测必填) |
| 防回退测试 | 无 | TC-06-h/i/j（status 文本 / port 必填 / 描述无 xray 误导）|
