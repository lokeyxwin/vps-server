# T-17 MCP 剩余 4 件套改造 — register_ip 改名 + 2 个状态查询新建 + __init__ 整合

**ID**: T-17
**状态**: waiting
**前置依赖**:
  - T-06 (register_vps 实现 + tools/__init__.py 已加 register_vps) **必须先 done**
  - T-15 / T-16 不强依赖, 但若 T-16 已 done, `get_ip_registration_status` 可端到端验证(否则只能 mock)
**后续依赖**: 无(本任务完工 = MCP 5 件套全部就位)
**关联 ADR**: `docs/adr/0007-mcp-tools-naming-and-conventions.md`
**关联 spec**: `test/mcp_tools/spec.md` v1.1(主依据)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] T-06 已 done
- [ ] 本任务仍是 `waiting`
- [ ] 写代码前改名为 `task/doing_17_mcp_tools_4_set_overhaul.md`

### 必读清单

- [ ] `CLAUDE.md` / `CLAUDE.local.md`(尤其 §MCP 模块组织)
- [ ] `docs/adr/0007-mcp-tools-naming-and-conventions.md`(全文)
- [ ] `test/mcp_tools/spec.md` v1.1(尤其 §3 范式 + §6.2 / §6.3 / §6.4 status 表)
- [ ] `tools/register_vps.py`(T-06 实现样板, 学结构)
- [ ] `tools/rgip.py`(待改名的源文件)
- [ ] `tools/get_available_proxy_nodes.py`(read-only 查询工具样板)
- [ ] `db/models.py::VPSRecord` / `VPSTask` / `IPRecord` / `IPTask` / `ProxyRecord`(查询函数要 join)
- [ ] `services/proxy_query.py`(看现有 read-only 查询的位置约定)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "模拟一下场景 ... agent 上下文可以拿到是哪条 IP, 然后去查 IP 表确认可以使用, 任务表确认完成, proxy 把配置查出来, 一条龙服务"
> "5 个不是很多, 先这样定义 MCP 工具吧"
> "1b2同意3a" (06 单独, 其他 4 件套放 T-17)

### 业务目标

完成 ADR-0007 §2 §3 落地剩余 4 件套:

1. `rgip` → `register_ip` 改名
2. 新建 `get_vps_registration_status`(查 VPS 装机进度)
3. 新建 `get_ip_registration_status`(查 IP 配置进度 + 配好时一条龙返代理节点)
4. `tools/__init__.py` 整合 5 个工具 + 三段顺序排序

### 本任务要解决什么

- agent 调 register_ip 跟之前调 rgip 行为一样, 但工具名"一眼看懂"
- agent 调 `get_vps_registration_status(vps_id 或 task_id)` 一次拿全 VPS + 最新 task 状态
- agent 调 `get_ip_registration_status(ip_id 或 task_id)` 一次拿全 IP + task + proxy_node(配好时)

### 本任务不解决什么

- ❌ 不动 SSHWorker / IPProbeWorker / ProxyDeployWorker / XrayWorker 代码
- ❌ 不动 register_vps(T-06 已做)
- ❌ 不动 get_available_proxy_nodes(已合规)
- ❌ 不引入 admin / user 分层 server(ADR-0007 §8 标了"留下波")

---

## 2. 实现参考

### 验收锚点

- `test/mcp_tools/spec.md` §3 实现范式(全 4 件都按此结构)
- `test/mcp_tools/spec.md` §6.2 register_ip 7 种 status(沿用 rgip.py 现有 description)
- `test/mcp_tools/spec.md` §6.3 get_vps_registration_status 返回 JSON 形状
- `test/mcp_tools/spec.md` §6.4 get_ip_registration_status 返回 JSON 形状(一条龙含 proxy_node)
- `test/mcp_tools/spec.md` §7 注册规约 ALL_TOOLS 三段顺序
- `test/mcp_tools/spec.md` §8 不变量 6 条

### 改动文件清单

#### 改名 `tools/rgip.py` → `tools/register_ip.py`

```bash
git mv tools/rgip.py tools/register_ip.py
```

文件内容**仅改 1 处**:

```python
TOOL = Tool(
    name="rgip",                # → 改 "register_ip"
    ...
)
```

description 大段沿用(7 种 status 跟 spec.md §6.2 一致), 可选在 description 顶部加一行映射注释("旧名 rgip 仍被部分文档引用, 标准名 register_ip")。

#### 新建 `tools/get_vps_registration_status.py`

按 `tools/rgip.py` 范式写:

```python
"""MCP 工具:get_vps_registration_status —— 查 VPS 装机进度。

这文件装啥:
  状态查询工具, 给 agent 在 register_vps 之后回来追问"装好了吗"用。
  join vps_record + vps_task 一次拿全, 不让 agent 多轮调。

谁调我: admin MCP 客户端
业务规约金标准: test/mcp_tools/spec.md §6.3
"""
from __future__ import annotations
import json
from mcp.types import TextContent, Tool, ToolAnnotations
from services.registration_query import query_vps_status   # 新建查询函数, 位置见下


TOOL = Tool(
    name="get_vps_registration_status",
    title="查询 VPS 装机进度",
    description=( ... ),         # 按 spec.md §4 三段式 + §6.3 教 agent 怎么转告
    inputSchema={
        "type": "object",
        "properties": {
            "vps_id": {"type": "integer", "description": "VPS 主键 id (跟 task_id 二选一)"},
            "task_id": {"type": "integer", "description": "task 主键 id (跟 vps_id 二选一)"},
        },
        "required": [],          # 二选一, properties 都不强制(handler 内部验)
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    result = query_vps_status(
        vps_id=args.get("vps_id"),
        task_id=args.get("task_id"),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
```

description 内容必须含 spec.md §6.3 列的转告规则:
- `task.status=done` + `vps.stage=connectable` + xray_version!="" → "VPS 装机完成, 可挂代理"
- `task.status=in_progress` → "还在装, 等几分钟再问"
- `task.status=failed` → 按 last_error_code 转告(同 register_vps 的 §6.1)
- `status=not_found` → "没查到这台 VPS / 任务"

#### 新建 `tools/get_ip_registration_status.py`

同范式, 入参 `ip_id` 或 `task_id` 二选一; description 按 spec.md §6.4 列**一条龙**转告规则:
- `task.status=done` + `proxy_node.status=using` → 配好啦, 节点 VPS_IP:port socks5 账密 user/pwd, 完全可用
- `task.status=done` + `proxy_node.status=pending_fw` → 代理已挂上但外部进不来, 请到 VPS 厂商面板放行端口 X
- `task.status=failed` + `last_error_code=no_vps_capacity` → VPS 池子满了, 请加机器或停过期 VPS
- `task.status=failed` + `last_error_code=inner_ping_failed` → 代理配上去了但内部不通, 上游 IP 可能已过期
- 其他失败码同 register_ip §6.2 转告
- `task.status=in_progress` → 还在配置, 等几分钟再问
- `status=not_found` → 没查到

handler 调 `query_ip_status(ip_id=..., task_id=...)`(新建, 见下)。

#### 新建查询函数 — 位置待实现者拍

两个查询工具的 handler 都不能写 SQL(spec §8 不变量 5: handler 不写业务)。新建 read-only 查询函数:

```python
# services/registration_query.py  ← 推荐(跟现有 services/proxy_query.py 同位)

def query_vps_status(vps_id: int | None, task_id: int | None) -> dict:
    """join vps_record + vps_task(最新一条) → spec §6.3 JSON 形状。"""
    ...

def query_ip_status(ip_id: int | None, task_id: int | None) -> dict:
    """join ip_record + ip_task(最新一条) + proxy_record(若 task.status=done) → spec §6.4 JSON 形状。

    proxy_node 字段在 task.status=done 时填; 否则 None。
    """
    ...
```

⚠️ 位置选择(实现者拍, 在 "实现过程记录" 里说明):
- `services/registration_query.py`: 跟 `services/proxy_query.py` 同位, read-only 查询保留在 services/(跟 ADR-0001 "新代码不 import services/" 有冲突, 但 read-only 查询语义不同, **ADR-0007 §影响清单已标注暂保留**)
- `db/queries.py`: 新建一处装 read-only 查询(可选)
- 实现者拍后在记录里说明

#### 改 `tools/__init__.py` — 整合 5 工具 + 三段顺序

```python
"""MCP 工具注册中心(对齐 ADR-0007 §决策 §7 + spec.md §7)。"""

from tools.register_vps import TOOL as _register_vps_tool, handler as _register_vps_handler
from tools.register_ip import TOOL as _register_ip_tool, handler as _register_ip_handler
from tools.get_vps_registration_status import TOOL as _get_vps_status_tool, handler as _get_vps_status_handler
from tools.get_ip_registration_status import TOOL as _get_ip_status_tool, handler as _get_ip_status_handler
from tools.get_available_proxy_nodes import TOOL as _get_nodes_tool, handler as _get_nodes_handler


ALL_TOOLS = [
    # 写入意图工具
    (_register_vps_tool, _register_vps_handler),
    (_register_ip_tool, _register_ip_handler),
    # 状态查询工具
    (_get_vps_status_tool, _get_vps_status_handler),
    (_get_ip_status_tool, _get_ip_status_handler),
    # 数据查询工具
    (_get_nodes_tool, _get_nodes_handler),
]

__all__ = ["ALL_TOOLS"]
```

⚠️ 旧 `_rgip_tool` / `_rgip_handler` 引用全部删除, 改 `_register_ip_*`(因为 T-06 完工时只加了 register_vps, rgip 那条还在旧名)。

#### 不动

- `tools/register_vps.py`(T-06 完成)
- `tools/get_available_proxy_nodes.py`(已合规)
- `mcp_server.py`(自动从 ALL_TOOLS 注册)
- `workers/*`(本任务只动 tools/ 和 services/registration_query.py)

### 缺工具 / 缺信息先报告

- 如发现 `query_vps_status` / `query_ip_status` 需要 join 的字段在 ORM 模型上缺(如 `vps_record.ip` 是不是叫这个) → 报告
- 如发现 spec §6.3 / §6.4 JSON 形状跟 ORM 字段对不上 → 报告
- 如发现 `tools/rgip.py` 的 description 列的 7 种 status 跟 IPProbeWorker 实际返的不一致 → **不要自己改 description**, 报告(spec.md §6.2 是基于代码现状校准过的, 如果出现新出入说明 worker 改了)

---

## 3. 验收交付

### 测试用例

#### `test/mcp_tools/TC-*.py`

- **TC-01 5 个工具全部注册**: `from tools import ALL_TOOLS; names = [t.name for t,_ in ALL_TOOLS]; assert set(names) == {"register_vps", "register_ip", "get_vps_registration_status", "get_ip_registration_status", "get_available_proxy_nodes"}`
- **TC-02 三段顺序**: ALL_TOOLS 列表前 2 是写入, 中 2 是查询, 末 1 是数据
- **TC-03 register_ip 改名后行为不变**: mock IPProbeWorker.process 返 7 种 status, handler 透传, JSON parse 后 status 字段对得上
- **TC-04 get_vps_registration_status handler 调 query_vps_status**: mock 查询函数, 验入参 vps_id/task_id 透传
- **TC-05 get_vps_registration_status JSON 形状**: mock 查询返 dict, JSON parse 后字段对齐 spec §6.3
- **TC-06 get_ip_registration_status 一条龙**: mock 返 task.status=done + proxy_node 非空, JSON parse 后 proxy_node 字段齐(vps_ip/vps_port/inbound_user/inbound_pwd/status)
- **TC-07 get_ip_registration_status not_found**: mock 查询返 not_found, JSON parse 后 status='not_found', 其他字段不在或为 null
- **TC-08 description 列 status 全集**: 每个工具 description 含 spec.md §6.x 列的所有 status 名(grep description 字符串)
- **TC-09 ⭐ 防回退 — 没有 rgip 残留**: ALL_TOOLS names 里不含 "rgip"; tools/ 目录下不存在 rgip.py 文件
- **TC-10 ⭐ 防回退 — 文件 stem == TOOL.name**: 每个 tools/<name>.py 的 stem 跟 TOOL.name 一致(三处对齐)

### 必跑测试命令

```bash
PYTHONPATH=. pytest test/mcp_tools/TC-*.py -v
# 启动 MCP server 不报错
PYTHONPATH=. python -c "from tools import ALL_TOOLS; print([t.name for t,_ in ALL_TOOLS])"
# 期望输出: ['register_vps', 'register_ip', 'get_vps_registration_status', 'get_ip_registration_status', 'get_available_proxy_nodes']
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`

- [x] T-06 已 done
- [x] 任务文件改为 `doing`
- [x] `git mv tools/rgip.py tools/register_ip.py`, name 字段改 `"register_ip"`
- [x] 新建 `tools/get_vps_registration_status.py` 实现完
- [x] 新建 `tools/get_ip_registration_status.py` 实现完
- [x] 新建查询函数(位置: `services/registration_query.py`, 选择理由见完成记录)
- [x] `tools/__init__.py` 整合 5 工具 + 三段顺序排序, **没有** rgip 残留 import
- [x] TC-01 ~ TC-10 全过 (26 子测一并通过)
- [x] description 不变量(spec.md §8): 每工具 description 列了 §6.x 全部 status 名, 不漏不多
- [x] 完成记录段已填

---

## 完成记录(done 时追加)

```text
完成日期: 2026-06-09
完成 commit: (本次 feat(tools): T-17 MCP 5 件套整合 提交后填入)
任务状态: doing -> done

改动摘要:
  - git mv tools/rgip.py tools/register_ip.py; 改 name="register_ip" + 顶部 docstring
    标"旧名 rgip 已弃, ADR-0007 §2"映射说明; description / inputSchema 全沿用.
  - 新建 services/registration_query.py: query_vps_status + query_ip_status 两个
    read-only join 查询函数. 输出形状对齐 spec.md §6.3 / §6.4.
    query_ip_status 在 task.status=done 时一条龙拉 proxy_record + vps 拼 proxy_node
    dict 一次返 (含 vps_ip / vps_port / inbound_user / inbound_pwd / status).
  - 新建 tools/get_vps_registration_status.py (~90 行): 按 rgip.py 范式,
    readOnlyHint=True, description 教 agent 怎么按 task.status / last_error_code
    转告用户; 入参 vps_id 或 task_id 二选一.
  - 新建 tools/get_ip_registration_status.py (~110 行): ⭐ 一条龙,
    readOnlyHint=True, description 含 ProxyDeployWorker 6 种失败码 + pending_fw +
    using 的转告规则; 入参 ip_id 或 task_id 二选一.
  - 改 tools/__init__.py: 5 工具三段顺序排序 + 顶部注释更新 (写入意图 → 状态
    查询 → 数据查询).
  - 新建 test/mcp_tools/__init__.py + 6 个 TC 文件 (TC-01/03/04/06/08/09) 含 26
    子测, 覆盖任务单的 TC-01 ~ TC-10 全部要求.

查询函数位置选择: services/registration_query.py
  理由:
    1. 跟现有 services/proxy_query.py 同位 (都是 read-only 查询).
    2. ADR-0007 §影响清单已标注 "services/proxy_query.list_available_proxies
       暂保留 services/ 引用" — read-only 查询语义跟"业务编排"不同,
       不在 ADR-0001 §决策 §5 "新代码不 import services/" 禁令范围内.
    3. db/queries.py 不存在, 建新位置反而增加心智成本; 后续如果 services/
       要清理, registration_query 和 proxy_query 一起搬即可.

测试命令: PYTHONPATH=. VPS_SERVER_TESTING=1 uv run pytest test/mcp_tools/TC-*.py -v

测试结果原样贴: 26 passed in 0.46s
  TC-01 5 工具全部注册 + 每条目 (Tool, handler) 二元组
  TC-02 三段顺序: 写入(2) → 状态查询(2) → 数据查询(1)
  TC-03 register_ip 改名: name='register_ip' + 7 种 status + 透传参数 + JSON 透传
  TC-04 get_vps_registration_status handler: 透传 vps_id/task_id, no_args→not_found
  TC-05 get_vps_registration_status JSON 形状: status + vps 5 字段 + task 4 字段
  TC-06 get_ip_registration_status ⭐ 一条龙: task.done + proxy_node 7 字段齐
  TC-07 get_ip_registration_status not_found / in_progress→proxy_node=null
  TC-08 description status 全集: 4 工具不漏不多
  TC-09 防回退 rgip/rgvps: ALL_TOOLS names + 文件系统双重检查
  TC-10 防回退 stem == TOOL.name: 5 工具三处对齐 importlib 反向校验

启动命令验证:
  PYTHONPATH=. uv run python -c "from tools import ALL_TOOLS; print([t.name for t,_ in ALL_TOOLS])"
  → ['register_vps', 'register_ip', 'get_vps_registration_status',
     'get_ip_registration_status', 'get_available_proxy_nodes'] ✅

全套回归 (ssh_worker + proxy_deploy_worker + xray_worker + ip_probe_worker):
  178 passed, 2 skipped (TC-14 真机 / TC-14 真机), 0 failed

未覆盖风险:
  1. handler 跟 MCP 框架的真实交互未端到端测 (mcp_server.py 启动 + tools/list +
     tools/call 协议层未跑), 等 dev_smoke / e2e 兜.
  2. query_vps_status / query_ip_status 走 SQLAlchemy + session_scope, 测试仅
     mock query 验 handler, 没单测 query 函数本身的 SQL 正确性 (信赖 ORM +
     上下游 worker 端到端跑过). 后续可补 services/registration_query 的单测.
  3. ADR-0007 §8 "admin/user MCP 分层"留下波, 当前 server instructions 跟实际
     注册不一致 (mcp_server.py L46-52), 仍是 stale 问题, 等单独 ADR.
  4. get_ip_registration_status 返 inbound_pwd 明文 — 这是给 agent 转告用户的
     凭据, 必须明文; 但 MCP 客户端 / 日志中可能落明文. 当前业务接受
     (跟 get_available_proxy_nodes 已有姿态一致). 后续如果走多租户场景,
     单独评估脱敏.

后续任务: admin/user MCP 分层 (ADR-0007 §8 留下波, 后续单独 ADR)
```
