# 0007. MCP 工具层 5 件套 — 命名 / 实现风格 / 对外文案规约

**日期**: 2026-06-09
**状态**: Accepted

---

## Supersedes / 补充

- 补充(非推翻) [[0001-workers-replace-services]] §决策 §5 "MCP 暴露三类工具, 绝不暴露内部子动作"
  → 本 ADR 把抽象的三类落到 **5 个具体工具清单** + 命名 + 实现风格 + 文案分工
- 配合 [[0006-proxy-deploy-worker]] §决策 §5 失败码 `no_vps_capacity`
  → 本 ADR 定 "该失败码怎么被 agent 转告给用户" 的形态

> 注: 被本 ADR 补充的旧 ADR 本身不动(永不改原则); 当下真相以本 ADR + spec.md 为准。

---

## 背景

ProxyDeployWorker(ADR-0006)完工后, 用户在一次模拟场景里把"agent 用户交互"过了一遍:

```
T0   用户: 登记 IP x.x.x.x ...
T0   agent: 是 IP 还是 VPS? → 用户答 IP → agent 调登记 IP 的工具
T0   后端同步段返回: 入库成功, 正在配置, 任务 id=87
T0   agent → 用户: "IP 校验通过, 正在配置中, 等 20 分钟"
T+20 用户: "配好了吗"
T+20 agent (上下文有 ip_id=42): 调一个综合查询工具一次拿全
T+20 agent → 用户: "配好了, 节点 VPS_A:8765 socks5 账密 u/p, 状态 using"
```

这个场景反推出几个待定的问题:

1. **工具到底几个**: 反推下来 5 个就够 (2 写 + 2 查 + 1 数据)
2. **命名怎么搞**: 现有 `rgvps` / `rgip` 简写不够"一眼看懂", 用户拍要换"业务直白"标准名
3. **实现长什么样**: 现有 `tools/rgip.py` 是 "TOOL + handler 元组" 风格(MCP 低阶 Server API), 不是 fastmcp `@mcp.tool` 装饰器; 要不要换
4. **对外文案谁产**: 之前在 cd5a5cba 拍过 "MCP 工具层产文案", 但 "产" 具体怎么形态化(handler 拼字符串? description 教 agent? DB 存模板?), 之前没定到代码层

本 ADR 一次性把命名 / 实现风格 / 文案分工锁死, 让 5 个工具长出统一形态。

---

## 决策

### 1. 工具命名规约

- 全部用 **业务直白 snake_case 动词+对象**, 一眼看懂用途
- `Tool.name == 文件名 == 模块路径里的 stem`, 三处对齐
- 反例: `rgip` / `rgvps` 简写、`list_proxies` 含义偏技术

### 2. rgvps / rgip 改名

| 旧 | 新 |
|---|---|
| `rgvps` (文件 `tools/rgvps.py`, 当前空占位) | `register_vps` (文件 `tools/register_vps.py`, 实现等任务单填) |
| `rgip` (文件 `tools/rgip.py`, 已完整实现) | `register_ip` (文件 `tools/register_ip.py`, **内容沿用, 仅改名 + 改 Tool.name**) |

**用户日常口头沟通**仍可说 `rgvps` / `rgip` (用户偏好), Claude 端自动映射到标准名, **不写回代码**。

### 3. 新增 2 个查询工具

| 工具 | 入参 (二选一) | 后端做什么 | 返回 |
|------|------------|----------|------|
| `get_vps_registration_status` | `vps_id` 或 `task_id` | join `vps_record` + `vps_task` 最新一条 | VPS 当前 stage / 装机进度 / xray 版本 / 失败码 |
| `get_ip_registration_status` | `ip_id` 或 `task_id` | join `ip_record` + `ip_task` 最新一条 + `proxy_record`(若有) | IP 状态 + task 进度 + 配好的代理节点 (VPS_IP:port + 账密 + status) |

### 4. 查询工具"一次拿全"原则

`get_ip_registration_status` 在 IP 配好时**同时**返代理节点字段(满足"一条龙服务"用户故事):

```jsonc
{
  "ip_id": 42,
  "task": {"status": "done", "last_error_code": ""},
  "ip": {"status": "using", "egress_ip": "1.2.3.4", "country_code": "SG"},
  "proxy_node": {              // task.status=done 时才有, 否则 null
    "vps_ip": "10.0.0.1",
    "vps_port": 8765,
    "inbound_user": "...",
    "inbound_pwd": "...",
    "status": "using"          // 或 "pending_fw"
  }
}
```

理由: agent 一次调用拿全 IP + task + proxy 信息, **不让 agent 再去拼 3 个工具**, 不增加多轮交互。

### 5. 实现风格规约 — 沿用 rgip.py 范式

5 个工具全部按 **TOOL + handler 元组** 风格:

```python
# tools/<name>.py
TOOL = Tool(name=..., title=..., description=..., inputSchema=..., annotations=...)

async def handler(arguments: dict | None) -> list[TextContent]:
    result = <worker / query 函数>(...)             # 调业务
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)] # 只返 JSON
```

`tools/__init__.py::ALL_TOOLS` 把所有 `(TOOL, handler)` 对汇总, `mcp_server.py` 通过 `@server.list_tools()` / `@server.call_tool()` 派发。

**不引入** `mcp.server.fastmcp` 的 `@mcp.tool` 装饰器 —— 现架构已是 MCP 官方标准之一, rgip.py 模板已验证 work, 切换收益低。

### 6. 对外文案规约 — 三层分工

| 层 | 职责 | 不做什么 |
|----|------|---------|
| **DB (status_code)** | 存稳定 contract: `no_vps_capacity` / `proxy_auth_failed` / `inner_ping_failed` ... | 不存自然语言 message |
| **`Tool.description`** | 列 "返回 status 含义" 映射表, 教 agent 怎么把 status_code 转告用户 (典型场景 + 反例) | 不放在 handler 里 |
| **`handler`** | 调 worker / 查询函数 → `json.dumps` 结构化字段 → `TextContent` | **不拼自然语言 message** |

**结果**: agent 看 description 学会怎么翻, 拿到 handler 的 JSON 后**自己**按 description 规则回译给用户。

参考样板: `tools/rgip.py` description 里已经写好 "返回 status 含义" 映射表(7 个 status × 转告话术 + 典型场景 3 条 + 反例 3 条)。新工具按这个范式抄。

### 7. status_code 列表归属

每个工具的 status_code 全集 + 对应文案规则**住在 `test/mcp_tools/spec.md`**, 不住在代码注释里。

DB 里出现新的 last_error_code → spec.md 加一行 → 对应工具 description 同步加一条转告规则。

### 8. admin / user MCP 分层 — 留下波

当前 `mcp_server.py` 一套 server, instructions 说 "Read-only" 但实际注册了写入工具 `rgip`, **是 stale 问题**。

本 ADR **不动 server 分层**, 5 个新工具全注册到现有 server。admin / user 分两套 server + 各自暴露不同 ALL_TOOLS 留给后续 ADR。

理由: 分层涉及 permission 边界 / 入口配置 / 部署模式, 跟"命名 + 文案"是两个独立问题, 单独 ADR 更干净。

---

## 备选方案(被否决)

### 方案 A: 切 fastmcp `@mcp.tool` 装饰器风格(被否决)

把 `tools/<name>.py` 改成:

```python
@mcp.tool()
async def register_ip(entry_host: str, ...) -> str:
    """docstring 作 description, type hints 作 inputSchema"""
    ...
```

**否决理由**:
- 推翻现有 `tools/__init__.py` + `mcp_server.py` 架构, 3 个已实现工具(rgip / get_available_proxy_nodes)都得重写
- 低阶 Server API 也是 MCP 官方标准, 不是非标
- `rgip.py` description 模板已 work, 切换收益低

### 方案 B: 查询工具拆 3 个分别查 task / record / proxy_node(被否决)

`get_ip_task_status(task_id)` + `get_ip_record(ip_id)` + `get_proxy_node_by_ip(ip_id)`。

**否决理由**:
- 违反用户"不增加多轮交互"原则
- agent 上下文要记 3 个 id, 容易丢
- "我的 IP 配好没"是一个业务意图, 对应一个工具最自然

### 方案 C: handler 里拼自然语言 message 返回(被否决)

```python
async def handler(arguments):
    result = worker(...)
    msg = f"IP 配好啦, 节点 {result['vps_ip']}:{result['vps_port']}"
    return [TextContent(type="text", text=msg)]
```

**否决理由**:
- 业务改一处 → 文案也得改 → handler 跟业务漂移
- agent 拿到自然语言反而不好二次处理(如果 agent 要再追问, 它看 JSON 字段更明确)
- description 教 agent + handler 返 JSON 这套, 把"稳定的"(JSON contract)和"可变的"(自然语言)分开

### 方案 D: 把 admin / user 分层一并塞进本 ADR(被否决)

**否决理由**:
- 范围膨胀, 分层涉及两个 server + permission 边界 + 部署
- 跟"命名 + 文案"是两个独立问题
- 单独 ADR 更干净

---

## 后果

### 好处

- 5 个工具长成统一形态, agent 学一个就学全部
- 命名一眼看懂, agent 选工具不会选错
- description 模板化, 新工具抄一份就行
- 文案分工三层清晰: 业务变 status_code 时只改 spec.md + description, handler / DB 不动
- 状态查询工具"一次拿全"让用户故事跑通("等 20 分钟回来问一次")

### 引入的新约束

- `tools/rgvps.py` 必须删除 + 重建为 `tools/register_vps.py`(因当前是空占位, 直接 rename 不解决 handler=pass 问题)
- `tools/rgip.py` 改名 `tools/register_ip.py`; 文件内容仅改 `name="rgip"` → `name="register_ip"`, 其他都沿用
- `tools/get_vps_registration_status.py` 新建
- `tools/get_ip_registration_status.py` 新建
- `tools/__init__.py` 改 import + 改 ALL_TOOLS 注册 + 更新顶部注释分类
- 新增 `test/mcp_tools/spec.md` 落地 5 个工具的命名 / 实现 / 文案 / status_code 规约
- 后续新增任何 MCP 工具必须按本 ADR + spec.md 走

### 风险

- **status_code 列表会扩张**: 业务演化时 worker 加新失败码, spec.md + 对应 description 要同步加
  缓解: spec.md 把映射表作为 single source of truth, 不允许 description 出现 spec.md 没列的 status_code
- **`get_ip_registration_status` join 3 表性能**: 当前规模(个人/小团队)完全不是问题
  缓解: 真有性能问题再优化, YAGNI
- **admin / user 分层延后**: 当前 server instructions 跟实际注册不一致, 暴露给 user 端可能误用
  缓解: 短期接受(只 admin 端在用), 中期单独 ADR 修

---

## 用户口述原话(关键节选)

> "模拟一下场景,用户传入一个ip和vps,agent先问是IP还是服务器,然后用对应工具启动,然后同步段就及时交互了 ... 用户再来问,配好了吗,agent上下文可以拿到是哪条IP,然后去查IP表确认可以使用,任务表确认完成,proxy把配置查出来,一条龙服务,就不增加多轮交互?你觉得这几个动作需要多少工具"
> —— 引出 §决策 §3-§4 查询工具一次拿全

> "rgVPS 和rgip 改成标准写法吧,后面我说rgvps 和rgip你自动映射吧,把工具名写的一眼看就知道干嘛的"
> —— 引出 §决策 §1-§2 命名规约 + 改名

> "5个不是很多;先这样定义MCP工具吧"
> —— 拍板工具总数 5 个

> "噢懂了点点,就是元祖看数据形状, description拼业务语言 可以继续吧"
> —— 拍板 §决策 §5 + §6 文案三层分工

> 历史 cd5a5cba:
> "MCP工具放在一个地方,但是修改返回信息我只想改一处 就这样简单的心智模型"
> —— 引出 §决策 §6 "改一处 = 改 description"

---

## 影响清单(已读代码现状, 已锁定)

| 文件 | 现状 | 改动 | 落地任务单 |
|------|------|------|----------|
| `tools/rgvps.py` | 空占位(handler=pass), 未注册到 ALL_TOOLS | **删** + 新建 `tools/register_vps.py`(实现等任务单) | T-NN |
| `tools/rgip.py` | 完整实现, 167 行, description 已是好模板 | **改名** → `tools/register_ip.py`; 改 `name="rgip"` → `"register_ip"`; description 顶部加"映射 rgip 旧名"提示行(可选) | T-NN |
| `tools/get_vps_registration_status.py` | 不存在 | 新建(按 rgip.py 范式) | T-NN |
| `tools/get_ip_registration_status.py` | 不存在 | 新建(按 rgip.py 范式), handler 内 join 3 表 | T-NN |
| `tools/get_available_proxy_nodes.py` | 已存在, 命名合规, 实现合规 | **不动** | — |
| `tools/__init__.py` | ALL_TOOLS 缺 rgvps; 顶部注释说"rgvps 待实现" | 改 import + ALL_TOOLS 注册全 5 个新名字; 注释更新 | T-NN |
| `mcp_server.py` L46-52 | server name "vps-proxy-user" + instructions 说"Read-only"但实际有写入工具 | **不动**(留给后续 admin/user 分层 ADR) | — |
| `services/proxy_query.list_available_proxies` | `get_available_proxy_nodes` 在 import 它, 跟"新代码不 import services/" 有冲突 | **不动**(read-only 查询语义不同于业务编排, 后续单独评估) | — |
| 新增 `test/mcp_tools/spec.md` | 不存在 | 新建: 5 工具清单 + 命名 / 实现 / 文案规约 + status_code 全集映射表 | (跟本 ADR 同批落, 不算独立 task) |
| 新增 `task/waiting_NN_*.md` | — | T-NN: 重命名 + 新建 4 个工具文件 + 改 __init__ | 待后续单独落 |

