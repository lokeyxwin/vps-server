# T-06 tools/register_vps.py MCP 入口实现 + 端到端测试(对齐 ADR-0007)

**ID**: T-06
**状态**: waiting
**前置依赖**: T-05 (SSHWorker.process 主入口 v4) ✅ done
**后续依赖**: T-17 (MCP 剩余 4 件套改造) —— T-17 跟在本任务后面接 `tools/__init__.py`
**关联 ADR**: `docs/adr/0007-mcp-tools-naming-and-conventions.md`
**关联 spec**: `test/mcp_tools/spec.md` §3 实现范式 + §4 description 三段式 + §6.1 register_vps status 全集

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`
- [ ] 写代码前已将文件名改为 `task/doing_06_tools_register_vps_mcp_entry.md`

### 必读清单

领取后、写代码前必须显式 Read:

- [ ] `CLAUDE.md` / `CLAUDE.local.md`
- [ ] `docs/adr/0007-mcp-tools-naming-and-conventions.md`(本任务主依据)
- [ ] `test/mcp_tools/spec.md`(v1.1, **特别注意 §6.1 已对齐 SSHWorker 代码现状**)
- [ ] `test/ssh_worker/spec.md` v4 §2 入口契约 + §3 三条主路线
- [ ] `tools/rgip.py`(范式样板, **新工具直接抄它的结构**, 不抄旧的 `tools/rgvps.py`)
- [ ] `tools/get_available_proxy_nodes.py`(协议适配模式参考)
- [ ] `workers/ssh_worker.py::SSHWorker.process()`(确认实际返回的 6 种 status)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "rgVPS 和 rgip 改成标准写法吧, 把工具名写的一眼看就知道干嘛的" (2026-06-09)
> "5 个不是很多;先这样定义 MCP 工具吧"
> "1b2同意3a" → 06 文件改名 + 重写为 register_vps 入口

### 业务目标

把 SSHWorker(VPS 登记同步段)包成 agent 可调的 MCP 工具, 工具名按 ADR-0007 §2 标准化为 `register_vps`(替代旧 `rgvps`)。

### 本任务要解决什么

- agent 调 `register_vps(ip, user, pwd, port, [ed, provider])` 立刻拿到 status + task_id, 不阻塞
- 工具名 `register_vps`, 一眼看懂 = 登记一台 VPS, 不再是简写

### 本任务不解决什么

- ❌ 不改 SSHWorker.process() (它已经按 v4 spec 跑通, 是稳定 contract)
- ❌ 不改 `tools/rgip.py` 改名(T-17 处理)
- ❌ 不新建 2 个状态查询工具(T-17 处理)
- ❌ 不动 `mcp_server.py`(自动从 ALL_TOOLS 注册新工具)

---

## 2. 实现参考

### 验收锚点

- `test/mcp_tools/spec.md` §3 实现范式(TOOL + handler 元组, handler 只返 JSON)
- `test/mcp_tools/spec.md` §4 description 三段式(功能 + 重要事项 + 典型场景 + 返回 status 含义 + 反例)
- `test/mcp_tools/spec.md` §6.1 register_vps status **6 种**(对齐 SSHWorker 代码现状, **不是 5 种**, 已校准):
  - `queued` / `already_registered` / `auth_failed` / `ssh_timeout` / `ssh_refused` / `ssh_failed`
- `tools/rgip.py` 现有完整实现(把它当样板抄)

### 改动文件清单

#### 删 `tools/rgvps.py`

```
git rm tools/rgvps.py    # 空占位文件, ADR-0007 §2 把它推翻为 register_vps
```

#### 新建 `tools/register_vps.py`

按 `tools/rgip.py` 范式写, 三大部分:

```python
"""MCP 工具:register_vps —— 登记一台 VPS。

这文件装啥:
  register_vps 的协议适配层 —— 把 MCP 调用转成 SSHWorker.process() 调用。
  只做协议转换, 不写业务逻辑。

谁调我: admin MCP 客户端
业务规约金标准: test/ssh_worker/spec.md v4 / test/mcp_tools/spec.md §6.1
"""

from __future__ import annotations
import json
from datetime import date
from mcp.types import TextContent, Tool, ToolAnnotations
from workers.ssh_worker import SSHWorker


TOOL = Tool(
    name="register_vps",                # 必须等于文件 stem
    title="登记一台 VPS",
    description=( ... ),                # 按 spec.md §4 三段式 + §6.1 列 6 种 status
    inputSchema={
        "type": "object",
        "properties": {
            "ip":   {"type": "string",  "description": "服务器 IP, 1.2.3.4 或 IPv6"},
            "user": {"type": "string",  "description": "SSH 登录用户名, 通常 root"},
            "pwd":  {"type": "string",  "description": "SSH 登录密码"},
            "port": {"type": "integer", "description": "SSH 端口 **必填**, 请去服务商控制台核对远程登录端口"},
            "ed":   {"type": "string",  "description": "到期日 YYYY-MM-DD(可选)", "default": ""},
            "provider": {"type": "string", "description": "服务商域名 (可选)", "default": ""},
        },
        "required": ["ip", "user", "pwd", "port"],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=True,             # 重复调返 already_registered
        openWorldHint=False,
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    ed_str = args.get("ed", "") or ""
    ed = date.fromisoformat(ed_str) if ed_str else None
    result = SSHWorker().process(
        ip=args["ip"], user=args["user"], pwd=args["pwd"],
        port=int(args["port"]),
        ed=ed, provider=args.get("provider", "") or "",
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
```

#### description 三段式具体内容(必填)

按 `test/mcp_tools/spec.md` §4 + §6.1, description 必须包含:

1. **一句话功能**: "用户提供 VPS 账密, 本工具会 SSH 探测连通性 + 入库 + 派后台装机 worker 接力"
2. **典型场景**: 用户说"添加这台服务器"/"把这台机加进来" → 调本工具; ed 字段相对日期(如 "3 天") agent 自己换算
3. **返回 status 含义(6 种, 照此转告用户)**:
   - `queued`: SSH 通过 + 已入库 + 后台正在装 xray; 转告"VPS 已登记, 后台正在装 xray, 预计 5-15 分钟"
   - `already_registered`: 该 VPS IP 已在库; 转告"这台 VPS (IP=X) 之前登记过了"
   - `auth_failed`: SSH 账密错; 转告"账号密码不对, 请核对面板凭据"
   - `ssh_timeout`: SSH 连不上; 转告"服务器连不上, 请确认端口是不是面板给的远程登录端口(不是默认 22)"
   - `ssh_refused`: SSH 拒接; 转告"连接被拒, 同上确认端口"
   - `ssh_failed`: SSH 未知失败; 转告"连接失败(其他原因), 把 message 字段原样转告用户排查"
4. **反例(明确禁止)**:
   - 不要用本工具更新已有服务器密码
   - 不要用本工具看节点列表(用 get_available_proxy_nodes)
   - 不要用本工具直接触发装 xray(本工具只验连通 + 派任务)

#### 改 `tools/__init__.py`

**仅加 register_vps 一项**, 其他 4 件套(register_ip / 2 个查询 / 排序)放 T-17:

```python
from tools.register_vps import TOOL as _register_vps_tool, handler as _register_vps_handler
# 已有 rgip / get_available_proxy_nodes 导入保留, T-17 再统一改名

ALL_TOOLS = [
    (_register_vps_tool, _register_vps_handler),   # 本任务新加(写入意图)
    (_rgip_tool, _rgip_handler),                   # T-17 改名为 register_ip
    (_get_available_proxy_nodes_tool, _get_available_proxy_nodes_handler),
]
```

#### 不动

- `mcp_server.py`(自动从 ALL_TOOLS 注册)
- `workers/ssh_worker.py`(已经按 v4 跑通)
- `tools/rgip.py`(T-17 改名)
- `tools/get_available_proxy_nodes.py`

### 缺工具 / 缺信息先报告

- 如发现 SSHWorker.process() 实际返的 status 跟 spec.md §6.1 列的 6 种不一致 → 立刻报告, 不自己拍
- 如发现 description 写到一半 spec.md §4 模板有歧义 → 报告

---

## 3. 验收交付

### 测试用例

#### TC-06 `test/ssh_worker/TC-06_register_vps_mcp_entry.py`

(沿用老 06 测试结构, 改 rgvps → register_vps)

业务故事:

```
端到端走 register_vps MCP 工具链路, mock SSHWorker.process 验证 handler 协议层。
```

子测试:

- **TC-06-a TOOL 元数据校验**:
  - `TOOL.name == "register_vps"`(**不是 "rgvps"**)
  - `inputSchema.required == ["ip", "user", "pwd", "port"]`
  - `port` 字段无 `"default"` 键
  - `annotations.idempotentHint == True`
- **TC-06-b handler 透传参数**:
  - mock `SSHWorker.process`, 验 args dict → port=int(传入值, 不是 22)
- **TC-06-c handler 返 [TextContent], JSON parse 后 status 字段对得上 6 种**:
  - `queued` / `already_registered` / `auth_failed` / `ssh_timeout` / `ssh_refused` / `ssh_failed` 各跑一次
- **TC-06-d ed 解析**: `"2026-12-31"` → date / `""` → None / `"bad"` → 抛 ValueError(MCP 框架级会拦)
- **TC-06-e port 必填**: 不传 → KeyError; 传 string → int 转换
- **TC-06-f 注册到 ALL_TOOLS**: `"register_vps" in [t.name for t,_ in ALL_TOOLS]`
- **⭐ TC-06-g 防回退**: `TOOL.name` 不含 `"rgvps"` 字眼; description 含全部 6 种 status 名
- **⭐ TC-06-h 防回退**: description 不含 "查 xray" / "看 xray 版本" 误导(SSHWorker 不查 xray)

### 必跑测试命令

```bash
PYTHONPATH=. pytest test/ssh_worker/TC-06_register_vps_mcp_entry.py -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`

- [x] 开工前已将任务文件改为 `doing`
- [x] `tools/rgvps.py` 已 `git rm`
- [x] `tools/register_vps.py` 已实现完
- [x] `tools/__init__.py` ALL_TOOLS 含 `register_vps`(仅加一条, 不动其他)
- [x] TC-06 所有子测试 PASS(含 TC-06-g/h 防回退)
- [x] `TOOL.name == "register_vps"` 三处对齐(文件名 stem / `TOOL.name` / `tools/__init__.py` import 别名前缀)
- [x] description 列 6 种 status 全(对齐 spec.md §6.1)
- [x] 完成记录段已填(测试结果原样贴)

---

## 完成记录(done 时追加)

```text
完成日期: 2026-06-09
完成 commit: (本次 feat(tools): T-06 register_vps MCP entry 提交后填入)
任务状态: doing -> done

改动摘要:
  - 删 tools/rgvps.py (空占位, ADR-0007 §2 推翻为标准名)
  - 新建 tools/register_vps.py (~150 行): TOOL+handler 元组范式, 抄 rgip.py 样板.
    description 三段式 (功能+典型场景+6 种 status 转告+反例) 含全部 6 种 status:
    queued / already_registered / auth_failed / ssh_timeout / ssh_refused / ssh_failed.
    inputSchema 必填 ip/user/pwd/port (port 无 default), 选填 ed/provider.
    annotations: idempotentHint=True (重复调返 already_registered).
  - tools/__init__.py 仅加 register_vps 一条 (在 rgip 之前), 不动 rgip
    (T-17 再统一改名 + 三段顺序整合).

测试命令: PYTHONPATH=. VPS_SERVER_TESTING=1 uv run pytest test/ssh_worker/TC-06_register_vps_mcp_entry.py -v

测试结果原样贴: 8 passed in 0.42s
  TC-06-a tool_metadata
  TC-06-b handler_passes_args (含 port string→int)
  TC-06-c handler_returns_textcontent_json_status_6kinds (6 种 status 透传)
  TC-06-d ed_parse (空/缺省→None / 错格式→ValueError)
  TC-06-e port_required (缺 port→KeyError)
  TC-06-f registered_in_all_tools
  TC-06-g no_rgvps_in_name_description_has_all_status (防回退)
  TC-06-h no_xray_query_in_description (防回退 SSHWorker v4 §5 不变量)

未覆盖风险:
  1. inputSchema "required" 兜底由 MCP 框架运行时校验, 单测靠 handler 内部
     args["port"] KeyError 间接验, 没直接模拟 MCP 协议层 schema 拒绝. 当前
     范式 OK, 真实集成 MCP 时还要靠 MCP 框架.
  2. 真实 MCP 客户端调用未端到端验证 (mcp_server.py 启动 + list_tools / call_tool
     交互未跑), 等 dev_smoke 或后续 e2e 任务兜.

后续任务: T-17 (MCP 剩余 4 件套改造: rgip 改名 register_ip + 2 个状态查询 +
  __init__.py 三段顺序)
```
