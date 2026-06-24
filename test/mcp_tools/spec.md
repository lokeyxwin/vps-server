# MCP 工具层行为规约（spec.md）

**版本**: v1（2026-06-09 初版）
**模块**: `tools/*.py` + `tools/__init__.py` + `mcp_server.py`
**类型**: 对外协议适配层(不写业务逻辑)
**对应 ADR**:
- `docs/adr/0001-workers-replace-services.md` §决策 §5(MCP 三类工具)
- `docs/adr/0006-proxy-deploy-worker.md` §决策 §5(失败码 `no_vps_capacity`)
- `docs/adr/0007-mcp-tools-naming-and-conventions.md`(**本 spec 主依据**: 命名 + 实现 + 文案分工)

---

## 一、整理后的要点

### 1. 工具总账（增量维护，当前清单见下表）

| # | Tool.name | 文件 | 业务意图 | 类别 |
|---|----------|------|---------|------|
| 1 | `register_vps` | `tools/register_vps.py` | 登记一台 VPS, 后端异步装 xray, 返 task_id | 写入意图 |
| 2 | `register_ip` | `tools/register_ip.py` | 登记一条上游 IP, 后端异步挂到生产 VPS, 返 task_id | 写入意图 |
| 3 | `get_vps_registration_status` | `tools/get_vps_registration_status.py` | 查 VPS 装机进度(适合 register_vps 之后追问) | 状态查询 |
| 4 | `get_ip_registration_status` | `tools/get_ip_registration_status.py` | 查 IP 配置进度 + 配好时返代理节点账密 | 状态查询 |
| 5 | `get_available_proxy_nodes` | `tools/get_available_proxy_nodes.py` | 列当前可用代理节点(给用户挑节点) | 数据查询 |
| 6 | `update_ip_expire_date` | `tools/update_ip_expire_date.py` | 改某条已登记 IP 的到期日(白名单 patch 单字段) | 写入修改 |
| 7 | `get_registered_ips` | `tools/get_registered_ips.py` | 列全部已登记 IP(过期+未过期), 给批量补到期日拿 ip_id | 数据查询 |
| 8 | `get_registered_vps` | `tools/get_registered_vps.py` | 列全部已登记 VPS(装/未装、忙/闲、过期/未过期), 看池子+拿 vps_id | 数据查询 |

### 2. 命名规约(强约束)

- 全部 **业务直白 snake_case 动词+对象**
- `Tool.name == 文件名 stem == 模块路径`, 三处对齐
- 一眼看懂:
  - ✅ `register_vps` / `get_ip_registration_status` / `get_available_proxy_nodes`
  - ❌ `rgip` / `rgvps`(简写, 含义不明)
  - ❌ `list_proxies`(技术语言, 不是业务意图)
- 用户日常口头说 `rgvps` / `rgip` 时 Claude 自动映射到标准名, **代码侧不留旧名**

### 3. 实现风格规约 — 一律按 rgip.py 范式

每个 `tools/<name>.py` 必须导出且仅导出两个符号:

```python
TOOL: mcp.types.Tool         # 工具元数据
async def handler(arguments: dict | None) -> list[TextContent]
```

骨架样板:

```python
"""MCP 工具: <name> —— <一句话业务意图>。

这文件装啥:
  <name> 的协议适配层 —— 把 MCP 调用转成 <业务函数 / worker.process()> 调用。
  只做协议转换, 不写业务逻辑。

谁调我: admin / user MCP 客户端

业务规约金标准: test/<对应 worker>/spec.md
"""

from __future__ import annotations

import json
from mcp.types import TextContent, Tool, ToolAnnotations

from <业务模块> import <业务函数 / Worker>


TOOL = Tool(
    name="<name>",
    title="<给人看的标题>",
    description="""<按 §4 写法规约填>""",
    inputSchema={...},
    annotations=ToolAnnotations(readOnlyHint=..., destructiveHint=..., ...),
)


async def handler(arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    result = <业务函数>(...)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    return [TextContent(type="text", text=payload)]
```

**handler 只做 3 件事**:
1. 解参(`args.get(...)`)
2. 调业务(worker.process / 查询函数)
3. `json.dumps` 包成 `TextContent`

**handler 不允许**:
- ❌ 拼自然语言 message
- ❌ 写业务逻辑(SQL / 状态机判断 / 业务规则)
- ❌ 吞业务异常(业务函数已经按"返 dict 不抛"契约写, handler 直接透传)

### 4. `Tool.description` 写法规约(三段式)

参考 `tools/rgip.py` 现有 description, 必须包含以下段落:

```
<一句话功能> + <同步段做什么 + 异步段做什么>。

⚠️ 重要事项(并发限制 / 串行要求 / 等待时间等, 若有):
- ...

典型场景(教 agent 什么时候调):
- 用户给一份 X → 调本工具。
- 用户给多条 X → 一条一条调, 等返回再下一条。
- 用户说"过期 3 天" → agent 自己换算日期再填。

返回 status 含义(照此转告用户)⭐:
- <status_code>: <什么意思> ; 转告用户"<人话>"
- <status_code>: <什么意思> ; 转告用户"<人话>"
- ...

反例(明确禁止):
- 不要并发调本工具。
- 不要在 <X> 之前承诺 <Y>。
- 不要把 <内部字段> 当 <终态> 转告用户。
```

**关键约束**:
- `返回 status 含义` 段必须列**所有**该工具可能返回的 status_code, 一个不能漏(列表见 §6 全集映射表)
- 转告话术用"告诉用户 X"这种祈使句, 让 agent 学会照抄
- 反例必须具体可执行("不要并发"比"小心使用"有用)

### 5. `handler` 返回 JSON 形状规约

每个工具的返回 JSON 在 §6 表里逐工具列。统一原则:

- 顶层字段必有 `status`(对应 `status_code`)
- 数据负载用嵌套字段而非平铺(`{"task": {...}, "proxy_node": {...}}`)
- 配套字段可空时返 `null` 不返 `""`(与 inputSchema 区分)
- 时间用 ISO 8601 字符串(便于 agent 转译)

### 6. status_code 全集映射表(single source of truth)

DB 里新增 last_error_code → 本表新增一行 → 对应工具 description 同步加一条转告规则。
**禁止 description 里出现本表没列的 status_code。**

#### 6.1 `register_vps`

| status | 后端含义 | description 教 agent 转告 |
|--------|---------|------------------------|
| `queued` | SSH 通过 + 已入 vps_record + 派 vps_task | "VPS 已登记, 后台正在装 xray, 预计 5-15 分钟" |
| `already_registered` | 该 VPS IP 已在库 | "这台 VPS (IP=X) 之前登记过了, 后端返回了它的现状(含活跃 task / 上次失败原因)" |
| `auth_failed` | SSH 账密错 | "账号密码不对, 请核对面板凭据" |
| `ssh_timeout` | SSH 连不上 | "服务器连不上, 请确认端口是不是面板给的远程登录端口(不是默认 22)" |
| `ssh_refused` | SSH 拒接 | "连接被拒, 同上确认端口" |
| `ssh_failed` | SSH 未知失败 | "连接失败(其他原因), 把 message 字段原样转告用户排查" |

> 注: 本表对齐 `workers/ssh_worker.py::SSHWorker.process()` 实际返回 6 种 status, 由 grep 代码现状校准(spec v1 初稿 5 种是 ADR-0007 编写时凭印象写的, 落地前已修正)。

#### 6.2 `register_ip`

(沿用 `tools/rgip.py` 现有 description 列表)

| status | 转告 |
|--------|------|
| `queued` | 已校验通过 + 入库, 后台正在挂到生产 VPS |
| `duplicate` | 出口 IP 之前登记过 |
| `proxy_auth_failed` | 账密错(提醒易混字符 0/O 1/l/I) |
| `proxy_timeout` | 上游超时, 已重试 3 次 |
| `proxy_refused` | 上游拒接(罕见) |
| `proxy_failed` | 其他失败, 把 message 转告 |
| `probe_vps_unreachable` | 后端测试 VPS 全连不上, 联系管理员 |

#### 6.3 `get_vps_registration_status`

返回字段:

```jsonc
{
  "status": "ok" | "not_found",
  "vps": {                                     // status=ok 时有
    "id": 1, "ip": "...", "stage": "running" | "connectable" | ...,
    "xray_version": "Xray 26.3.27", "is_active": 1
  },
  "task": {                                    // 最新一条 vps_task, 可能为 null
    "id": 87, "status": "done" | "in_progress" | "failed" | "pending",
    "last_error_code": "", "completed_at": "2026-06-09T13:40:00"
  }
}
```

教 agent 怎么转告:
- `task.status=done` + `vps.stage=connectable` + `xray_version != ""` → "VPS 装机完成, 可挂代理"
- `task.status=in_progress` → "还在装, 等几分钟再问"
- `task.status=failed` → 按 `last_error_code` 转告(同 §6.1)
- `status=not_found` → "没查到这台 VPS / 任务"

#### 6.4 `get_ip_registration_status` ⭐(一条龙)

返回字段:

```jsonc
{
  "status": "ok" | "not_found",
  "ip": {                                      // status=ok 时有
    "id": 42, "egress_ip": "1.2.3.4",
    "country_code": "SG", "status": "using" | "usable",
    "expire_date": "2026-12-31" | null
  },
  "task": {
    "id": 87, "status": "done" | "in_progress" | "failed" | "pending",
    "last_error_code": "", "completed_at": "..."
  },
  "proxy_node": {                              // task.status=done 时才有, 否则 null
    "vps_id": 1, "vps_ip": "10.0.0.1", "vps_port": 8765,
    "protocol": "shadowsocks",                 // 新部署 / "socks5"=存量
    "method": "aes-256-gcm",                   // SS 加密方式(socks5 节点留空)
    "inbound_user": "...", "inbound_pwd": "...",
    "share_link": "ss://...",                  // SS 标准 ss:// 链接(socks5 节点留空), ADR-0011 §8
    "status": "using" | "pending_fw"
  } | null
}
```

教 agent 怎么转告(交付优先用 share_link, ADR-0011 §8):
- `task.status=done` + `proxy_node.status=using` + protocol=shadowsocks → "配好啦, 复制这条 ss:// 链接导入小火箭/v2rayNG/Clash: <share_link>"(跨客户端通用)
- `task.status=done` + `proxy_node.status=using` + protocol=socks5(share_link 空) → "配好啦, 节点 VPS_IP:port 账密 user/pwd, 完全可用"
- `task.status=done` + `proxy_node.status=pending_fw` → "代理已挂上(链接 <share_link>), 但外部进不来, 请登录 VPS 厂商面板放行端口 X"
- `task.status=failed` + `last_error_code=no_vps_capacity` → "VPS 池子满了, 请加机器或停掉过期 VPS, 然后重新登记这条 IP"
- `task.status=failed` + `last_error_code=inner_ping_failed` → "代理配上去了但内部不通, 上游 IP 可能已过期"
- `task.status=in_progress` → "还在配置, 等几分钟再问"
- `status=not_found` → "没查到这条 IP / 任务"

#### 6.5 `get_available_proxy_nodes`

(沿用现有 description, 不在本 spec 重列)

返回 list[dict], 每条字段(ADR-0011 §8 加 `method` + `share_link`):
{proxy_id, vps_id, ip_id, protocol("shadowsocks"新部署/"socks5"存量),
 method(SS 加密方式, socks5 节点留空), host, port, username, password,
 share_link(SS 标准 ss:// 链接, socks5 节点留空), egress_ip,
 country_code, country_name, city}。

交付优先用 share_link: shadowsocks 节点的 share_link 是标准 ss:// 链接(SIP002),
直接发用户复制粘贴/扫码导入小火箭/v2rayNG/Clash, 跨客户端通用; socks5 存量节点
share_link 为空, 只能整理成手动填写格式(类型/IP/端口/账号/密码), 不要伪造 ss:// 链接。

#### 6.6 `update_ip_expire_date`

写入修改工具(项目第一个 update_*), 由 ADR-0008 §3.3 ABCD 背书。
入参: `ip_id`(主键精准, 必填) + `expire_date`(YYYY-MM-DD, 必填)。
只 patch `ip_record.expire_date` 单列, 不碰 is_active / 其他字段。

| status | 后端含义 | description 教 agent 转告 |
|--------|---------|------------------------|
| `ok` | 命中 ip_id, expire_date 已更新 | "已把 IP <egress_ip> 的到期日更新为 <expire_date>" |
| `not_found` | ip_id 不存在 | "没找到这条 IP, 确认 ip_id; 可能它根本没登记, 要登记走 register_ip" |
| `invalid_date` | expire_date 非合法 YYYY-MM-DD | "日期格式要 YYYY-MM-DD(如 2026-06-18)" |

> 批量看图场景(agent 编排): 用户甩面板截图「只补已登记的」→ agent 用
> `get_available_proxy_nodes` 按出口IP匹配拿 ip_id → 匹配到的逐条调本工具;
> 匹配不到(过期/没挂)→ 跳过, 不自动登记, 提示走 register_ip。
> 本工具一次一条, agent 逐行循环。

#### 6.7 `get_registered_ips`

数据查询工具(只读, admin)。列**全部**已登记 IP(过期 + 未过期), 单表 ip_record
不 join, 无过滤。跟 §6.5 get_available_proxy_nodes(只列 USING 可用代理节点)区分:
本工具能看到过期/没挂的 IP, 给「批量补到期日」拿过期 IP 的 ip_id。

返回 list[dict], 每条:
{ip_id, egress_ip, country_code, country_name, city,
 expire_date(null=纳管未知), is_active(1可用/0过期)}。
**绝不返上游密码/凭据**。空库返 []。

agent 用法: 拿每条 ip_id 喂给 update_ip_expire_date; 按 egress_ip 对用户截图那几行;
对不上的(没登记)跳过 → 提示走 register_ip。

#### 6.8 `get_registered_vps`

数据查询工具(只读, admin)。列**全部**已登记 VPS(装的+没装的+过期的都在), 单表
vps_record 不 join, 无过滤。跟 §6.3 get_vps_registration_status(按 id 查单台装机
进度)区分: 本工具是列全量、不带 task 进度。

返回 list[dict], 每条:
{vps_id, ip, os_name, os_version, xray_version(""=未装),
 stage(connectable空闲/running占用), used_port_count,
 expire_date(null=未知), is_active(1可用/0过期), provider_domain}。
**绝不返 SSH 凭据**(密码/端口/登录名都不返)。空库返 []。

agent 用法: 看 VPS 池全貌(谁装了xray/忙闲/挂了几条/到期); 拿 vps_id 备未来
update_vps_expire_date 用。

### 7. 注册规约 — `tools/__init__.py::ALL_TOOLS`

```python
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
```

写入意图 → 状态查询 → 数据查询 三段顺序, 跟 ADR-0001 §决策 §5 一致。

### 8. 不变量

1. **`Tool.name == 文件 stem`**: 每个工具三处对齐
2. **handler 只返 JSON**: 永不拼自然语言
3. **description 列全 status_code**: 跟 §6 映射表一对一, 不漏不多
4. **新增 status_code**: 必须先改 §6 映射表 + 对应 description, 再让 worker 写 DB
5. **handler 不写业务**: SQL / 状态机 / 业务规则全部在 worker 或查询函数里
6. **工具数量不是不变量**: 工具集随业务增量演进, §1 总账是当前快照; 新增工具 = §1 总账 +1 行 + §6 加 status_code 映射, **不为「多一个工具」开 ADR / 不在测试 assert 工具总数**(仅当工具引入新架构决策才开 ADR)。写入工具额外受 CLAUDE.local.md §14.3 ABCD 约束。当前每个工具的命名 + status_code 映射须有 ADR 背书(`update_ip_expire_date` 由 ADR-0008 §3.3 背书)

---

## §工具清单(协议适配层无原子工具)

MCP 工具层是**对外协议适配层**, 本身就是"工具"的暴露, 不再分原子工具 / 工具编排两层。

底层依赖工具:

| 依赖 | 位置 | 谁用 |
|------|------|------|
| `workers.ssh_worker.SSHWorker` | `workers/ssh_worker.py` | `register_vps` handler 调 `process()` |
| `workers.ip_probe_worker.IPProbeWorker` | `workers/ip_probe_worker.py` | `register_ip` handler 调 `process()` |
| `services.proxy_query.list_available_proxies` | `services/proxy_query.py` | `get_available_proxy_nodes` handler 调(read-only 查询, 暂保留 services/ 引用) |
| **新建查询函数**(本 spec 引出): join 多表组装 status dict | `services/registration_query.py` 或 `db/queries.py`(待定) | `get_vps_registration_status` + `get_ip_registration_status` handler 各调一个 |

**新建查询函数的位置**: 落地任务单时再定(read-only 查询走 services/ 还是 db/queries/, 不影响本 spec 行为)。

---

## 二、用户口述原话(金标准)

> "rgVPS 和rgip 改成标准写法吧, 后面我说rgvps 和rgip你自动映射吧, 把工具名写的一眼看就知道干嘛的"
> —— 引出 §2 命名规约 + 改名

> "5个不是很多;先这样定义MCP工具吧"
> —— 拍板工具总数 5 个

> "模拟一下场景, 用户传入一个ip和vps, agent先问是IP还是服务器 ... agent上下文可以拿到是哪条IP, 然后去查IP表确认可以使用, 任务表确认完成, proxy把配置查出来, 一条龙服务, 就不增加多轮交互"
> —— 引出 §1 5 工具清单 + §6.4 get_ip_registration_status "一条龙" 返回

> "噢懂了点点, 就是元祖看数据形状, description拼业务语言 可以继续吧"
> —— 拍板 §3 实现风格 + §4 description 写法 + §5 handler 返 JSON

> 历史 cd5a5cba:
> "MCP工具放在一个地方, 但是修改返回信息我只想改一处 就这样简单的心智模型"
> —— 引出 §6 status_code 映射表 single source of truth

(对话场景: 2026-06-09 用户模拟"用户登记 IP → 20 分钟后回来问"场景反推工具数量 + 拍板 rgvps/rgip 改标准名 + 拍板 description 拼业务语言 + handler 返数据形状。)

---

## 三、修订历史

- v1 2026-06-09 初版(对应 ADR-0007 落地)
- v1.1 2026-06-09 §6.1 status 列表对齐 SSHWorker 代码现状(grep 校准, 修正 ADR-0007 落地时凭印象写错的 3 处: `duplicate`→`already_registered` / `ssh_auth_failed`→`auth_failed` / 补 `ssh_failed`)
- v2 2026-06-13 加 `update_ip_expire_date`(项目第一个 update_* 写入工具, ADR-0008 §3.3 ABCD 落地): §1 总账 5→6 / 新增 §6.6 / §8 不变量 #6 改「加新工具需 ADR 背书」
- v2.1 2026-06-13 订正「工具数量不写死为不变量」(同源 CLAUDE.md §8 反模式): §1 标题去固定数字 / §8 不变量 #6 改「工具数量不是不变量, 加工具不开 ADR、不 assert 总数」(对齐 CLAUDE.local §14.5)
- v3 2026-06-14 加 `get_registered_ips`(列全量已登记 IP, 补「批量补到期日拿 ip_id」缺口): §1 总账 +1 行 / 新增 §6.7
- v4 2026-06-14 加 `get_registered_vps`(列全量已登记 VPS, 看池子+拿 vps_id): §1 总账 +1 行 / 新增 §6.8
- v5 2026-06-24 MCP 节点返回加 `method` + `share_link`(ADR-0011 §8, T-29, 不增工具只加字段): §6.4 proxy_node 形状 + §6.5 get_available_proxy_nodes 返回字段补 method/share_link, 转告话术改"优先用 ss:// share_link"(shadowsocks 节点); socks5 存量节点 share_link 留空, 走手动填写, 不伪造 ss://
