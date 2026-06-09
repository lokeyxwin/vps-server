# 0008. main.py = worker 常驻入口 + 新 read-only/读写业务函数住 db/queries.py + MCP 工具分层 / 写入工具白名单 patch 规约

**日期**: 2026-06-09
**状态**: Accepted

---

## Supersedes / 补充

- 补充(非推翻) [[0001-workers-replace-services]] §决策 §1 worker 架构
  → 本 ADR 落实 **worker 调度入口的具体位置** (原 ADR 只定义工人形态, 没定调度入口住哪)
- 补充(非推翻) [[0007-mcp-tools-naming-and-conventions]] §影响清单 + §决策 §8
  → ADR-0007 把 `services/proxy_query.list_available_proxies` 标"暂保留", 把 admin/user 分层标"留下波"
  → 本 ADR 把"暂保留"消化掉(搬到 db/queries.py), 把 admin/user 分层心智模型 + 写入工具评估规则立起来(server 拆分留下波)

> 注: 被本 ADR 补充的旧 ADR 本身不动(永不改原则); 当下真相以本 ADR + 后续 ADR + spec.md 为准。

---

## 背景

T-17 完工后, 用户在对话里反推出三个心智不对齐的点:

### 1. MCP server 启动 ≠ 后端服务跑起来

当前只跑 `mcp_server.py` 的话, `register_vps` / `register_ip` 同步段返 `queued` 后, `vps_task` / `ip_task` 永远停在 `pending`, 没有 worker 消费。

grep 全项目验证:
- `XrayWorker.run_once()` / `ProxyDeployWorker.run_once()` 写好了
- **没有任何地方调用 `while True: worker.run_once()`**
- `main.py` 还是老的 `services/` CLI 入口 (rgvps / xrayinit / rgip 子命令直接 `from services.xxx import`), 跟新 worker 架构脱钩, 沦为"既不前台也不后端"的尴尬位置

### 2. T-17 新增 `services/registration_query.py` 让 services/ 边界又模糊一档

任务单允许两个备选位置 (services/registration_query.py / db/queries.py), 实现窗口选了 services/ 跟 proxy_query.py 同位姿态。但用户拍板:

> "services 我记得是旧业务逻辑也在里面的, 为啥任务还让你放进去"
> "查询工具都在 db, 更新工具也在 db, mcp 那边我也分了两套服务 一个 admin 一个 user 工具权限可以隔离"

意思: **新增就该划清边界**, 旧的"暂保留" ≠ 新的也走旧位置。`db/` 是"MCP 工具调的业务函数集合"(读写都在), `services/` 退出活跃路径。

### 3. MCP 写入工具的硬约束(用户拍板,这次一并立起来)

用户原话:

> "任务表不允许外部修改, 那个是历史日志事实, 能改的就只有 IP 表和 VPS 表 字段还是有限制的都有限制的没事"
> "不止字段受限, 还要求不允许覆盖更新只能白名单更新就是我要改日期要精准的找到那个 VPS 或 IP 去改"

意思:
- `vps_task` / `ip_task` 永不暴露写入工具(历史日志事实)
- 业务表写入工具必须 **主键精准定位 + 白名单字段 patch**, 禁止整对象覆盖

这条规则跟 CLAUDE.local.md §"DB 增量写入 / 字段所有权"(v4 追加, 原本针对 worker 间字段覆盖) 同源, 推广到 MCP 写入工具就是这条。

---

## 决策

### 1. main.py = worker 常驻调度入口

```
mcp_server.py    →  前台收单 (stdio MCP 协议, 分发 5 工具 handler)
main.py          →  后端常驻 (worker 调度循环)
```

具体形态:

- **删** main.py 老的 3 个子命令 (rgvps / xrayinit / rgip), 它们已被 MCP 工具替代 + 直接 import `services/` 跟新架构脱钩
- **新增** `worker-loop` 子命令(argparse 保留, 便于未来扩展):

  ```
  uv run python main.py worker-loop
  ```

- 内部实现轮廓:

  ```
  _stop = False
  signal(SIGTERM, lambda *_: _stop = True)
  signal(SIGINT,  lambda *_: _stop = True)

  while not _stop:
      busy = XrayWorker().run_once() + ProxyDeployWorker().run_once()
      if not busy:
          time.sleep(POLL_INTERVAL_SECONDS)
      # 有活立刻下一轮, 不 sleep
  logger.info("worker-loop 收到退出信号, 优雅退出")
  ```

- **worker-loop 只调度异步段 worker** (XrayWorker / ProxyDeployWorker)。
  SSHWorker / IPProbeWorker 是 MCP 入口工具的**同步段**, 由 `register_vps` /
  `register_ip` handler 直接调 `process()`, 不进 loop。

- `config.py::POLL_INTERVAL_SECONDS = 2` (跟 worker 默认 lock 超时 5min 不冲突)

- ADR 本身**不写 systemd / supervisor / docker compose 配置** — 这是部署关注点, 跟架构决策无关. ADR 只保证 `main.py worker-loop` 是**可独立拉起 + 可优雅退出**的可执行入口, 部署具体方式留给运维 / dev_smoke 决定

### 2. `db/queries.py` = MCP 工具调的业务函数集合(读写都在)

- 新建 `db/queries.py` 装"MCP 工具调的所有业务函数", 跟 ORM 模型同包, 导入路径短
- **本 ADR 范围内只搬 read-only 函数**(因为目前没有写入工具要建):
  - `query_vps_status(vps_id, task_id)` ← 从 `services/registration_query.py` 搬
  - `query_ip_status(ip_id, task_id)` ← 从 `services/registration_query.py` 搬
  - `list_available_proxies(country_code)` ← 从 `services/proxy_query.py` 搬
- **改 3 个 tools 文件** import 路径:
  - `tools/get_vps_registration_status.py`: `from services.registration_query import query_vps_status` → `from db.queries import query_vps_status`
  - `tools/get_ip_registration_status.py`: 同样改
  - `tools/get_available_proxy_nodes.py`: `from services.proxy_query import list_available_proxies` → `from db.queries import list_available_proxies`
- **删** `services/registration_query.py` + `services/proxy_query.py`
- 从此 `services/` 目录**没有任何 MCP 工具或 worker 在 import**, 完全退出活跃路径
- **`db/queries.py` 文件层面不区分读写**: 未来加写入函数(白名单 patch)也住这里, 跟读函数同包

### 3. MCP 工具 admin/user 分层 + 写入工具白名单 patch 硬规则

#### 3.1 admin/user 分层(立心智, 具体实现留下波)

- MCP 拆两套 server: admin / user, 各自暴露不同 ALL_TOOLS
- **新增任何 MCP 工具上线前, 实现者必须跟需求窗口确认**:
  - 影响面多广(查 / 改哪些表 / 哪些字段)
  - 暴露给 admin 还是 user
- 现有 5 工具暂时一锅暴露, **admin/user 真正拆 server 留后续 ADR**
- 本 ADR 立心智模型, 不动 mcp_server.py 实际代码

#### 3.2 任务表不暴露写入工具(硬约束)

- `vps_task` / `ip_task` = 历史日志事实, **永不暴露任何写入 MCP 工具**
- 任务状态机只由 `workers/` 内部推进 (pending → in_progress → done/failed)
- 任何"重跑任务"/"重置 retry_count"等需求 → 通过 `register_vps` / `register_ip` 重发请求, 不通过改 task 表

#### 3.3 业务表写入工具的 4 条硬规则

仅 `ip_record` / `vps_record` 允许写入工具暴露给 MCP, 每个写入工具必须满足:

**规则 A 主键精准定位**:
- 入参必须含目标记录主键 (`ip_id` / `vps_id`)
- 后端 UPDATE WHERE id=? 改单行
- 禁止模糊匹配 (egress_ip / IP 字符串 / 名字等)

**规则 B 白名单字段 patch**:
- 工具签名只列允许改的字段, 后端 UPDATE 只写这些列
- ✅ `update_ip_expire_date(ip_id, expire_date)`
- ❌ `update_ip(ip_id, payload: dict)` ← payload 整对象覆盖禁止

**规则 C 整对象不允许覆盖**:
- `session.merge(record)` / 整行替换 / `payload dict → ORM 整字段 update` 全部禁止
- 跟 CLAUDE.local.md §"DB 增量写入 / 字段所有权" (v4) 同源约束

**规则 D 工具命名反映约束**:
- 模板: `update_<对象>_<字段>` (例 `update_ip_expire_date` / `update_vps_is_active`)
- 反例: `update_ip` / `patch_vps` / `set_ip_field` (通用 update 必然走向整对象覆盖)

#### 3.4 现有工具回顾(本 ADR 不动)

- `register_vps` / `register_ip`: 入库新行 (INSERT), 不在 update 范畴, 不受 §3.3 约束
- 5 个查询工具: 全 read-only, 不受 §3.3 约束
- 本 ADR 起点没有任何 `update_*` 工具存在, §3.3 4 条规则是为后续新增写入工具立的约束

### 4. 心智模型(写进 CLAUDE.local.md)

```
mcp_server.py    前台收单     接 stdio MCP 协议, 分发 MCP 工具 handler
main.py          后端常驻     调度 worker 循环 (worker-loop 子命令)
workers/         异步业务     被 main.py worker-loop 调度
tools/           协议适配     被 mcp_server.py 调度, handler 一律调 db/queries 或 workers/
db/queries.py    业务函数     MCP 工具一律 from db.queries import (读写都在,白名单 patch)
db/models.py     ORM 表结构
services/        旧业务编排   不删不增不导入(本 ADR 起 services/ 退出活跃路径)
```

**两进程模型** (部署):
- 一个跑 `mcp_server.py` (MCP stdio 客户端拉起)
- 一个跑 `main.py worker-loop` (systemd / supervisor / 手动 nohup)

---

## 备选方案

### 方案 A (被否决): worker loop 嵌入 mcp_server.py

`mcp_server.py` 启动时 `asyncio.create_task(worker_loop())`。

**否决理由**:
- worker `run_once()` 是同步阻塞函数(SSH / DB / xray 都是阻塞调用), 嵌入 asyncio 必须 `asyncio.to_thread` 包, 增加复杂度
- 两个职责耦合: MCP server 挂了 worker 也挂, worker 卡了 MCP server 响应延迟
- 违反"单进程单一职责"

### 方案 B (被否决): 新增 `worker_runner.py` 独立文件

**否决理由**:
- main.py 已存在且空闲 (老 CLI 入口正好该删), 不再多文件
- 用户明确说"main 就是启动常驻服务", 顺着心智模型走最简单

### 方案 C (被否决): 新增 read-only 查询继续放 `services/`

**否决理由**:
- 加固历史漂移, 跟 CLAUDE.local.md §11 "新代码不 import services/" 边界冲突
- 用户明确拍板: "暂保留旧的" ≠ "新增也走旧位置"
- `db/queries.py` 跟 ORM 同包, 导入路径短, 语义清晰

### 方案 D (被否决): 只搬 T-17 新增的 registration_query.py, proxy_query.py 暂留 services/

**否决理由**:
- 跟 ADR-0007 §影响清单"暂保留"决策保持一致表面上对, 但实际上留尾巴: T-17 之后 services/ 还有 1 个文件被 tools 在 import, "新代码不 import services/" 仍是软规则
- 用户拍板: "查询工具都在 db, 更新工具也在 db" → **一锅搬干净, 不留尾巴**
- 一并搬等于把 ADR-0007 §影响清单的"暂保留"消化掉, 跟"新增不进 services/"边界一次性立清

### 方案 E (被否决): db/queries.py 只准 read-only, 写入函数住别处

**否决理由**:
- 用户明确否决: "查询工具都在 db, 更新工具也在 db"
- 权限隔离不靠文件层面区分, 靠 MCP 工具的 admin/user 分层
- 多一个文件层级 (db/queries / db/mutations) 增加心智, YAGNI

### 方案 F (被否决): 写入工具用通用 update_<对象>(payload dict) 形态

**否决理由**:
- 用户明确拍板: "不允许覆盖更新只能白名单更新就是我要改日期要精准的找到那个 VPS 或 IP 去改"
- 整对象覆盖是数据腐烂最大坑(同源 CLAUDE.local.md §"DB 增量写入 / 字段所有权" v4)
- 通用 update 工具必然滑向"前端传啥后端写啥", 失去白名单兜底

---

## 后果

### 好处

- 心智模型干净: 前台(mcp_server) + 后端(main) 二分, 跟用户业务直觉对齐
- main.py 终于做"统一编排"(CLAUDE.local.md §"main.py 留到所有业务都做完再统一编排"的兑现)
- `services/` 目录退出活跃路径, 后续清理就是单纯删文件不牵连
- `db/queries.py` 成为"MCP 工具调的所有业务函数"的明确住址, 心智一致
- admin/user 分层 + 写入工具白名单 patch 4 条硬规则把 MCP 暴露面长期约束立起来
- 两进程独立可重启: MCP server 重启不影响正在装机的 worker, 反之亦然

### 引入的新约束

- **部署**: 要拉两个进程 (`mcp_server.py` + `main.py worker-loop`), 需要在 README 或 dev_smoke 写清楚
- **worker loop 调度顺序**: 当前串行 `XrayWorker.run_once → ProxyDeployWorker.run_once`, 业务规模小够用; 若未来加多 worker 类型, 可能要拆并发(留给后续 ADR)
- **CLAUDE.local.md 加 2 节**: §心智模型 + §MCP 工具上线评估清单(含 §3.3 4 条写入工具规则)
- **新增 MCP 工具**: 上线前必须跟需求窗口确认影响面 + admin/user 暴露(本 ADR §3.1 硬规则)
- **未来新增 update_* 工具**: 必须遵循 §3.3 4 条规则(主键精准 / 白名单 / 不覆盖 / 命名反映约束)
- **当前没有 update_* 工具**: 4 条规则是未来约束, 本 ADR 范围内**不实现任何 update_* 工具**

### 风险

- **SIGTERM 处理写错** → 僵尸进程或 task 锁不释放
  缓解: T-18 实现时显式测 signal handler + 用 `try/finally` 兜底 worker 状态
- **worker loop 串行调度, XrayWorker 卡死时 ProxyDeployWorker 也跟着卡**
  缓解: worker run_once 内部已有 lock_timeout(5min), 卡死有上限; 后续真有问题再拆并发
- **`db/queries.py` 写入函数边界靠人/review 守, 没有自动检查**
  缓解: CLAUDE.local.md 加 §3.3 4 条规则, 后续可加 hook 自动检查 (本 ADR 不强制)
- **admin/user 实际分层留下波**, 当前 server instructions 跟实际注册不一致 (ADR-0007 §8 stale 问题继续存在)
  缓解: 单独 ADR 处理, 本 ADR 不动 mcp_server.py 实际代码

---

## 用户口述原话

> "main 能不能当做拉起服务的脚本, 我不想那么多心智负担, MCP 就是启动前台, main 就是启动常驻服务"
> — 引出 §决策 §1 main.py = worker runner + §4 心智模型

> "services 我记得是旧业务逻辑也在里面的, 为啥任务还让你放进去"
> — 引出 §决策 §2 read-only 查询位置 + §备选 §C 否决

> "17 任务封装的查询工具放到 db/ 查询 / MCP 查询工具都在 db 这里拿就完事了吧"
> — 引出 §决策 §2 db/queries.py + §备选 §D 否决 (一锅搬干净)

> "查询工具都在 db, 更新工具也在 db, mcp 那边我也分了两套服务 一个 admin 一个 user 工具权限可以隔离 再挑 MCP 工具的时候先看工具查询或修改的东西会不会影响到很广, 再考虑给到哪 跟我确认好后 边界就定了这个动态条的, 任务表不允许外部修改, 那个是历史日志事实, 能改的就只有 IP 表和 VPS 表 字段还是有限制的都有限制的没事"
> — 引出 §决策 §2 db 读写都在 + §备选 §E 否决 + §3.1 admin/user 分层 + §3.2 任务表不暴露写入

> "噢不止字段受限, 还要求不允许覆盖更新只能白名单更新就是我要改日期要精准的找到那个 VPS 或 IP 去改"
> — 引出 §决策 §3.3 写入工具 4 条硬规则 + §备选 §F 否决

---

## 影响清单(已锁定, T-18 落地)

| 文件 | 现状 | 改动 | 落地任务单 |
|------|------|------|---------|
| `main.py` | 老 services CLI 3 子命令 (rgvps / xrayinit / rgip), 直接 `from services.xxx import` | **删旧 3 子命令** + **新增 worker-loop 子命令** + SIGTERM/SIGINT 优雅退出 + 顶部 docstring 重写 | T-18 |
| `config.py` | 现有常量 | **新增 `POLL_INTERVAL_SECONDS = 2`** (worker-loop idle 时 sleep 秒数) | T-18 |
| `services/registration_query.py` | T-17 新建 (query_vps_status / query_ip_status) | **删** | T-18 |
| `services/proxy_query.py` | T-17 之前已存在 (list_available_proxies) | **删** | T-18 |
| `db/queries.py` | 不存在 | **新建**, 装 3 个函数: `query_vps_status` / `query_ip_status` / `list_available_proxies` (内容从 services 搬, 签名不变) | T-18 |
| `tools/get_vps_registration_status.py` | `from services.registration_query import query_vps_status` | 改 `from db.queries import query_vps_status` | T-18 |
| `tools/get_ip_registration_status.py` | `from services.registration_query import query_ip_status` | 改 `from db.queries import query_ip_status` | T-18 |
| `tools/get_available_proxy_nodes.py` | `from services.proxy_query import list_available_proxies` | 改 `from db.queries import list_available_proxies` | T-18 |
| `CLAUDE.local.md` | §11 "旧 services/ 处理" + §10 "MCP 工具暴露三类" | **加 §13 心智模型** (mcp/main/workers/tools/db/queries 各自职责) + **加 §14 MCP 工具上线评估清单** (admin/user 分层 + 任务表不暴露 + 业务表写入 ABCD 4 条规则) + 修订历史加 v5 | T-18 |
| `mcp_server.py` | L46-52 `Server name="vps-proxy-user" + instructions "Read-only"` | **不动** (ADR-0007 §8 admin/user 真正拆 server 留下波) | — |
| 部署文档 (README 或 dev_smoke) | 未明定双进程 | T-18 时可选加一段双进程拉起说明 | T-18 / 后续 |
