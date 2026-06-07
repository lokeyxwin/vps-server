# CLAUDE.local.md —— 本项目特有的长期规则

> **本文件 vs CLAUDE.md 的边界**
>
> - `CLAUDE.md` —— **全局通用规则**：项目目录骨架、协作节奏、决策判定、用户确认规则等。
>   换一个项目（爬虫 / IM 后端 / 任何新建项目）这些规则仍然适用。
> - `CLAUDE.local.md`（本文件） —— **本项目特有规则**：跟 VPS 管理 / xray / SSH / 代理出口
>   等业务和技术栈强相关的约束。换项目这些规则就废了。
>
> **新规则进哪份的自检口诀**：
> > 这条规则换到爬虫项目还能用 → CLAUDE.md
> > 换到爬虫项目就废了 → CLAUDE.local.md
> > 这是"我们当时为什么这么定" → docs/adr/NNNN-xxx.md
> > 这是"现在要去做" → task/<state>_NN_xxx.md
>
> 详细决策树见 `CLAUDE.md`。

---

> **说明**：以下内容是从原 `CLAUDE.md`（VPS 模块闭环后沉淀的真实经验）原样迁移过来的本项目特有规则。
> 后续 worker / kit / task 体系等新约束会以增量方式追加到本文件，每次追加前会先列 diff 给用户审。

---

# 原项目规则（来自原 CLAUDE.md，一字未改）

VPS 资产管理 + 代理出口自动化项目（个人/小团队规模）的 AI 协作契约。
**所有新代码必须遵守这些规则**——它们是 VPS 模块完整闭环后沉淀的真实经验。

VPS 模块已闭环。**IP 业务是接下来的工作**（流程 + 实现见 [todo/TODO_IP_PROXY.md](todo/TODO_IP_PROXY.md)）。

---

## §0 适用范围（2026-06-06 追加）

**本节（§0）以下到 §反模式禁令 为止，都是"原项目 services/ 同步阻塞业务函数"
时代的规则，统称 legacy 规则**。它们仍约束 `services/vps_register.py` /
`services/ip_register.py` / `services/vps_init.py` 等旧代码作为对照参考。

**新代码全部按本文末尾 §业务编排：worker / kit / task 体系 走**，不再遵循 legacy 规则。

**对待 legacy 代码的姿势**（2026-06-06 用户拍板）：

| legacy 位置 | 怎么用 | 绝不 |
|------------|--------|------|
| `services/`（旧业务编排层） | 打开看思路、必要时 cp 片段到 `workers/` | **`from services import` 直接导入** |
| `xray/service.py` + `xray/config.py`（旧函数） | 打开看实现思路 | **`from xray.service/config import` 直接导入**——新方法全部直接写在 `xray/manager.py::XrayManager` 类里 |

新代码 + 真机验证完工后，旧 `services/` 整体删除，本节也随之失效。

---

## 核心约束（按重要性排序）

### 1. YAGNI——绝对不允许过度设计

- **Rule of Three**：重复 3 次才考虑抽象
- **抽象基类是"发现的"不是"设计的"**——2+ 实现共享契约才抽 ABC
- **不要为对称建空包/空文件**：单文件就单文件
- **不预留配置项**：硬编码到真有需要再挪 config

### 2. 三层架构（严格自下而上）

```
入口（CLI / 未来 MCP / Web）
   ↓
业务层 services/         ← 编排 + 决策 + DB 状态机 + 错误兜底
   ↓
工具编排层 Manager 类     ← 把多步 atom 串成"一行就能调"的复合操作
   (XrayManager / VPSSession 既是 SSH 包装，更是工具编排层)
   ↓
原子层 atom 函数         ← 单一动作、纯函数、无状态、错误抛领域异常
   (xray.service / xray.config / core.ports / core.ssh / ...)
   ↓
基础设施（DB / 加密 / 日志 / 第三方库）
```

**禁止**：
- 跨层跳跃（业务直接调 atom 绕过 Manager 允许；atom 跨领域 import 不行）
- atom 之间跨领域 import（**唯一例外**：领域 atom → `core.*`，core 是基础设施）
- 类层调业务层（反向依赖）

### 3. 业务函数返回 dict，不抛异常给上层

```python
def <动词>_<对象>(...) -> dict:
    return {"status": "ok", ...}              # 成功
    return {"status": "duplicate", "message": ...}  # 已知失败
```

业务层吃掉所有底层异常，转成 status 字符串。CLI 只看 status 决定怎么做。

### 4. 错误细分 + 附排查指令

- 每种失败有自己的 status code（不要一个 `failed` 兜底）
- 每个 message 列**常见原因 3 条** + **排查命令**
- 已有模板：连接错误 4 类（auth/timeout/refused/failed）、xray 错误 7 类、config 错误 3 类

### 5. 不要修复你没确认的问题

发现"设计意图不完善"先汇报，等用户拍板。**禁止自作主张**加字段 / 抽基类 / 引依赖。

---

## 业务函数契约

```
def <业务>(...) -> dict:
    logger.info("开始XX...")                            # 口语化叙述
    ① 查 DB：已存在 / 已完成 / 正在做 → 短路返回
    ② 标记"正在做"入库（并发保护 + 留痕）
    ③ 通过 Manager 调工具编排层
       try: result = manager.高层方法()
       except 领域异常子类: 转 status 返回 + 写失败 DB
    ④ 验证（内部 + 外部）
    ⑤ 写最终状态入库
    ⑥ return {"status": ..., ...}
```

**失败也必须写 DB**——避免 message 说 "version=X" 但字段空的矛盾。
失败时主动再问 manager 收集上下文（参考 `services/vps_init._save_failure_with_context`）。

---

## 命名约定

| 类型 | 后缀/命名 | 例子 |
|------|----------|------|
| 资源管理类 | `Manager` | `XrayManager` |
| SSH 会话类 | `Session` | `VPSSession` |
| ORM 模型 | `Record` | `VPSRecord` / `ProxyRecord` |
| 状态常量类 | `XxxStatus` | `XrayStatus.RUNNING` / `ProxyStatus.USING` |
| DB 表名 | 类名 snake_case | `vps_record` / `proxy_record` |
| 业务函数 | `<动词>_<对象>` | `register_vps` / `init_vps_xray` |
| CLI 子命令 | 短缩写 | `rgvps` / `rgip` / `xrayinit` |
| 通用软件管理方法 | install / start / stop / enable / disable / is_running / is_enabled / version / reload | 跨软件（xray / caddy / ...）通用 |

---

## 模块组织（重要）

### 领域包内部拆 service.py + config.py

xray 模块的经验——**任何"有运行时 + 有配置文件"的软件领域**都套用：

```
<domain>/
├── service.py    ← 服务运行时操作（install/start/stop/enable/disable/is_*/version/test_*）
├── config.py     ← 配置层（纯函数 build_* + SSH 操作 upload/validate/read/...）
└── manager.py    ← Manager 类：薄包装 atom + 高层 ensure_* 编排
```

**触发拆分时机**：当一个 `atom.py` 同时承担"服务管理"+"配置管理"两类职责且 >200 行。

### 共享常量放 core，不要在各领域里冗余维护

如果两个领域都要用同一份常量/dict（例：默认 inbound 形状），抽到 `core.constants` 或 `config.py`。
**血泪教训**：曾经 ip/atom 和 xray/atom 各维护一份 default config，迟早会漂移。

### 包按业务流程命名，不按动作命名

✅ `services/vps_init.py`（VPS 初始化业务）
❌ `services/vps_install_xray.py`（"装 xray"是其中一个动作，不是业务全貌）

### MCP 工具层必须和后端业务解耦

MCP 是入口层的一种，不能把业务逻辑写进 MCP 入口或工具适配层。

```
mcp_server.py
   ↓
tools/                  ← MCP 协议适配层，只写工具元数据 + 参数/返回包装
   ↓
workers/                ← 新业务编排层（主动决策 / 抢锁 / 写表 / 扫 task）
   ↓
xray/manager.py + core/ + db/   ← 工具箱 + 基础设施 + ORM
```

**新增 MCP 工具固定流程**：

1. 后端能力先写在 `workers/` / `xray/manager.py` / `core/` / `db/` 等业务或工具层。
   （旧 `services/` 不再新增能力，新代码不 import。）
2. 在 `tools/<tool_name>.py` 新增 MCP 适配模块。
3. 每个工具模块只导出：
   - `TOOL: mcp.types.Tool`：工具元数据。
   - `handler(arguments)`：读取 MCP 参数，调用业务函数，把结果包成 MCP content。
4. 在 `tools/__init__.py` 把 `(TOOL, handler)` 加进 `ALL_TOOLS`。
5. `mcp_server.py` 只负责启动 MCP、`list_tools`、`call_tool` 分发，不写任何业务逻辑。

**工具命名和描述约束**：

- `Tool.name` 使用 Python 标准 `snake_case`，并且必须让 agent 一眼看懂用途。
  - ✅ `get_available_proxy_nodes`
  - ❌ `list_available_proxies`（含义偏技术，用户/agent 不够直观）
- `Tool.name` 尽量等于模块文件名，方便排查。
- `Tool.title` 写短标题，给 UI/人类看，不塞业务规则。
- `Tool.description` 必须服务于业务场景，而不是只描述工具功能。不要写成“本工具用于查询/创建/更新某资源”然后让外部模型自己推理怎么编排；要直接告诉 agent：典型用户问题、什么时候调用、参数怎么填、返回 0/1/多条时怎么回答、反例和禁止事项。
- `inputSchema.properties.<arg>.description` 写参数含义、格式、示例值；不要只写字段名。
- 查询工具应加只读标记：`readOnlyHint=True`、`destructiveHint=False`。

**admin / user 两套 MCP**：

- 共用后端业务函数，靠不同的 `tools` registry / MCP 入口决定暴露哪些工具。
- user 级 MCP 只暴露用户能用的查询工具，例如可用代理节点导出。
- admin 级 MCP 才暴露登记 VPS、登记 IP、初始化 xray、端口检查等管理工具。

---

## 数据模型契约

### 生命周期 5 字段（任何"有状态的资源"都加）

```python
xxx_status: str               # 当前状态枚举（XxxStatus 常量约束）
xxx_version: str              # 版本/标识
xxx_installed_at: datetime    # 首次完成时间 (nullable)
xxx_last_checked_at: datetime # 最近一次检查时间
xxx_status_message: str       # 人类可读的状态附加信息
```

### 敏感字段加密

- 字段命名 `xxx_encrypted: bytes`
- 解密走 `record.get_xxx()` 方法
- 加密在 ORM 的 `from_form() / from_xxx()` 工厂方法内发生
- `__repr__` 主动屏蔽密码字段
- 原生 SQL 读盘断言密文不含明文（专项测试）

### 事实表模式（固定大小）

某些表行数有自然上限（如 `proxy_record` 每台 VPS 上线数受端口策略约束，
具体见 ADR-0002 端口策略）：
- **过期不删行**，改 `status='expired'` 等下一条 IP 顶替（原地 UPDATE）
- 业务层 upsert：按唯一键查 → 存在则 UPDATE，不存在则 INSERT
- 双 UniqueConstraint 防错绑

### Reconcile 模式

**服务器是真相，DB 是它的影子**。每次业务先看实际状态再决定动作（参考 `XrayManager.ensure_installed_and_running`）。

### Dev DB 迁移

dev SQLite 加字段：`ALTER TABLE ADD COLUMN ... DEFAULT 0`
dev SQLite 加表：`Base.metadata.create_all(engine, tables=[X.__table__])`
dev SQLite 改结构：让用户手动 `DROP TABLE` 再 create_all（钩子会拦自动 drop）

---

## 日志契约（两层风格分工）

业务层用 `services.<name>` logger，工具/原子层用模块名 logger。LayeredFormatter 自动区分：
```
HH:MM:SS ▶ services.xxx: ...     ← 业务事件
HH:MM:SS [INFO] core.ssh: ...    ← 原子事件
```

### 业务层（services/*）——口语化叙述，给人看

像跟人讲故事：
```
"开始登记 VPS：ip=X 账号=Y 端口=Z"
"数据库里还没这台，准备 SSH 上去看看情况"
"端口审计：业务端口区间内可用 10/10 个，已被 xray 绑定 0 个"
"xray 全部搞定：状态=imported 版本=Xray 26.3.27 本次做的操作=[]"
"VPS 完整登记完成"
```

业务必须在**每个分支决策点 log**：开始 / 跳过 / 失败 / 成功。

### 工具/原子层（core/*、xray/*）——结构化 `func: in → out`，给 AI/排障看

格式：`<函数名>: <输入 k=v 空格分隔> → <结果>`
```
connect_server: ip=X port=22 user=root → ok
connect_server: ip=X user=root → auth_failed (...)
open_tcp_port_range: start=18440 end=18450 → detected_fw=firewalld
test_socks_proxy: target=X:Y url=... → ok=True http=200 egress=X
XrayManager.version: → Xray 26.3.27 (already installed)
XrayManager.is_running: → False → systemctl start xray
```

---

## 测试契约

| 层 | 测什么 | 怎么做 |
|----|-------|--------|
| atom | 函数的所有分支 | mock 第三方调用（paramiko / requests），断言返回值 / 抛错 |
| Manager | 类内部状态机 if/else | mock `xray.manager.service.*` / `xray.manager.xc.*`，测每个分支 |
| 业务 | 所有 status 路径 | mock 整个 Manager + DB session，覆盖每种 status + 验证 DB 写入 |

**安全场景必测**：
- 密码落盘是密文（原生 SQL 验）
- `__repr__` 不泄露明文
- 错误密钥解密失败
- 并发同资源不能重复（DB 状态字段验）

**真服务器测试默认 skip**，配 `VPS_TEST_*` 环境变量触发。**不要让 CI 默认跑**。

---

## 工作流

### 开发顺序

```
新业务：
  ① 原子函数 + 单测 → commit
  ② Manager 复合方法 + 单测 → commit
  ③ 业务函数 + 单测 → commit
  ④ 真服务器跑一次验证 → 修问题 → 再 commit
  ⑤ main.py 留到所有业务都做完，最后统一编排
```

### Commit 规则

- **功能 + 测试 = 一个 commit**
- 标题：`feat(<scope>):` / `fix:` / `refactor:` / `test:` / `docs:` / `chore:`
- 摘要列关键改动点，不堆代码

> 详见 CLAUDE.md §8 通用反模式表的 `git add -A` / `git add .` 禁令(已搬到通用层)。

### main.py / mcp_server.py

- main.py：等所有业务做完再统一编排路由。每加一个业务**先写业务，不要为单个业务改 main**
- mcp_server.py：MCP 启动入口，只从 `tools.ALL_TOOLS` 注册和分发工具。新增工具时优先改 `tools/` 和 `tools/__init__.py`，不要把业务塞进入口。

### 真服务器跑

完成新业务后业务作者负责跑一次真服务器确认全流程通。挂在哪、DB 留了什么状态是验证核心。

### 业务跑通了再敲表

新字段先在业务层 mock（log 出形状），业务跑通了再回头敲 schema + 接 ORM。
**参考**：proxy_record 表是「业务跑通看清数据形状 → 再设计 schema → 替换 stub」走完整周期的样板。

---

## 反模式禁令

| 不要做 | 为什么 |
|-------|------|
| 给 atom 函数加 DB 操作 | atom 必须无状态 |
| 业务层 import paramiko 直接用 | 必须走 Manager / Session |
| `print(...)` | 用 logger |
| 异常吞掉不传播也不记日志 | 至少 logger.warning |
| 加新依赖不更新 pyproject.toml | 必须可复现安装 |
| `try: ... except Exception: pass` 无注释 | 必须加 `# noqa: BLE001 — <意图>` |
| Mock 测试不验证 DB 状态变化 | 业务的核心副作用就是改 DB |
| 改测试断言"修"挂掉的测试 | 先看为什么挂、是不是代码 bug |
| 各领域包里冗余维护同一份常量 | 抽到 core 单一真相源 |
| atom.py / service.py 混装服务管理 + 配置管理 | >200 行时拆 service.py + config.py |
| atom 在 try/except 里默默吞错返回 default | atom 失败必须抛领域异常子类 |

---

## 一句话总结

**Don't over-engineer. Layer strictly. Catch specifically. Hint actionably. Commit cohesively. Log conversationally for humans, structurally for AI. Defer the unknown.**

---

# 业务编排：worker / kit / task 体系（决策见 docs/adr/0001）

本节定义本项目的异步业务编排层。**取代**原"业务函数返回 dict 同步阻塞"模型
（原模型规则仍保留在前面 §业务函数契约 / §数据模型契约 等节中，作为旧 services/
代码的对照参考；新代码全部按本节走）。

详细背景见 `docs/adr/0001-workers-replace-services.md`。

## 1. 目录布局

```
workers/                    ← 工人(每工人一个 .py 自包含,类风格)
├── ssh_worker.py           ← class SSHWorker
├── xray_worker.py          ← class XrayWorker
├── ip_probe_worker.py      ← class IPProbeWorker
├── proxy_deploy_worker.py  ← class ProxyDeployWorker
├── _shelved/               ← 封存的工人:health_check / expiry / cleanup
└── README.md

xray/                       ← xray 软件工具包(沿用现有目录,既私有又共用)
├── service.py              ← 底层函数(原子动作),旧代码沿用
├── config.py               ← 底层函数(改配置),旧代码沿用
├── manager.py              ← class XrayManager(对象式工具箱,工人主用)
└── __init__.py
                             (新方法直接写在 manager.py 类里,不再绕 service/config)

core/                       ← 通用底层(已有,不动)
├── ssh.py                  ← connect 等函数 + execute_command 等基础工具
├── session.py              ← VPSSession 类(SSH 会话包装,有状态用类)
├── geoip.py / security.py 等纯函数

test/<worker>/spec.md  ← 行为规约(验收标准金标准,single source of truth)
tools/                      ← MCP 协议适配层(已存在,不变)
services/                   ← 旧业务层,保留作对照,新代码不 import
proxy/  db/                 ← 不动
```

**关键变化**(对应这一轮架构演化):
- 取消 `kits/` 目录:本项目就 xray 一个软件领域,直接复用现有 `xray/`
- 工人是类(class),每工人一个 .py 文件自包含
- xray/ 里 manager.py 是工人对接的主入口(XrayManager 类)
- 新方法直接在 XrayManager 类里写实现,不再绕 service.py 函数

## 2. 工人 vs 工具包 vs 入口的命名分界

```
workers/<动作>_worker.py     → 工人(主动,class 风格)
                              主动决策 / 抢锁 / 写表 / 扫 task / 接力建下一 task
                              例:ssh_worker / xray_worker / ip_probe_worker

xray/manager.py::XrayManager → 软件工具箱(对象式,工人 import 它实例化)
                              不写表 / 不抢锁 / 绑 SSH client / 操作 xray 软件

core/                        → 通用底层(SSH / geoip / security / ...)
                              有状态用类(VPSSession,住 core/session.py),无状态用函数(lookup_egress)

tools/<工具名>.py            → MCP 协议适配(对外入口,函数即可)
                              只写 Tool 元数据 + 包装业务返回
                              例:rgvps / rgip / get_available_proxy_nodes

db/models.py::<Xxx>Record    → ORM 模型
db/models.py::VPSTask, IPTask → 任务表(异步协调媒介)
```

## 3. xray 工具包的组织(就一个领域,不拆 kits)

旧 `xray/` 目录沿用,内部:

| 文件 | 角色 | 谁用 |
|------|------|------|
| `service.py` | 底层函数(install/start/stop/...) | 旧 services/(对照),新代码不直调 |
| `config.py`  | 底层函数(read/upload/validate/add_inbound/...) | 旧 services/(对照),新代码不直调 |
| `manager.py::XrayManager` | **对象式工具箱**(class 包装 client) | **新工人主用** |

**新代码原则**:
- 工人 `from xray.manager import XrayManager` → `xray = XrayManager(client)` → `xray.xxx()`
- 不 import xray.service / xray.config 模块函数(那是旧代码的)
- **新方法直接在 XrayManager 类里写实现**(不必"先函数后封类"重复)
- 旧 service.py / config.py **留着不删**作对照

## 4. task 表 + worker 接力规则

- task 表是异步协调媒介。worker 从 task 表领活儿,写表,必要时建下一条 task
- **两张 task 表**:`vps_task`(VPS 装机)/ `ip_task`(IP 部署),职责单一
- **一台 VPS 同时只能被 1 个 worker 持锁** —— `task.locked_until` 软锁,自动过期
- 锁粒度 = task(不是 vps_record)。worker 抢到 task = 抢到 task.vps_id 那台机的操作权
- worker 之间**只通过 task 表接力**,不直接互调
- worker **只 import `xray/` + `core/` + `db/`**,不 import 旧 `services/`
- task.status 状态机:`pending / in_progress / pending_retry / done / failed / circuit_broken`
  (细节见各 worker spec.md)

## 5. 共通形状 + 差异工人（爬虫式架构指导）

入口的"账密包"形状两条业务线一样：

```python
EntryCredential:
  host: str              # IP 或域名
  port: int
  username: str
  password: str
  expire_date: date | None
  provider_domain: str
```

但**worker 不能合并**，因为被操作对象不同（VPS vs IP）→ 验证方式不同 →
失败重试逻辑不同。**共通在工具（core.ssh / install_xray.config + probe）+ 入口形状，
差异在工人**。

类比：xhs 和 dy 爬虫都用 requests + cookie 池，但 spider 类分开。

## 6. 入口工人失败入库规则（SSHWorker 同步段）

```
SSH 探测一次 → 失败:
  ├─ auth_failed       → 不入库（错的不要进来）
  ├─ timeout / refused → 短时内部重试仍失败 → 入库标 unreachable
  │                      + message 写"请确认端口是服务商指定的远程登录端口"
  │                      （不要指引去防火墙——SSH 端口被防火墙拦的概率远低于
  │                       用户填错端口）
```

重试次数/间隔参数走 `config.py`，不写死在 worker 里。

## 7. xray 必装默认入口（XrayWorker 内嵌约束）

XrayManager 装机时**必须**写入一条默认 noauth inbound 配置：

- 端口固定 `XRAY_DEFAULT_PORT`(=18440)
- 协议 socks5 noauth → direct（不走任何 outbound 代理）
- **不入 proxy_record 表**（不是节点资产，是软件自启内部组件）
- **不对外提供使用**

原因：xray 没 inbound 不能启动。这条配置永远固定 18440，绝不今天 22 明天 3306。

### 端口策略（见 ADR-0002）

- **取消**旧 "18441-18450 业务段" 限定（旧 CLAUDE.md 那条规则已废）
- 新分配端口策略：**排除清单 + 高位随机**
  - 必排除：`config.py::EXCLUDED_PORTS`（0-1023 well-known + 常用应用端口 + 18440）
  - 必排除：该 VPS 已用端口（查 proxy_record）
  - 剩下 1024-65535 高位随机
- **纳管端口不迁移**：客户端可能在用旧端口，强迁断生产。纳管时旧端口原样接管

## 8. 测试 VPS 配置化（IPProbeWorker 专用资源）

IPProbeWorker 验上游 IP 凭据时用的"测试 VPS"，**不进业务表**，写在 `config.py`：

```python
PROBE_VPS = {
    "ip": "x.x.x.x",
    "user": "root",
    "pwd": "...",
    "port": 22,
    "test_port_range": (19000, 19010),   # 跟生产 18441-18450 隔离
}
```

测完一条 IP **立即拆掉**临时配置，不污染测试 VPS。

## 9. 工人阵容（当前清单——会随业务变化）

> ⚠️ 本小节**是"当前清单"**，违反 CLAUDE.md "不在长期约束里写当前清单"的反模式，
> 但因为它是本项目业务理解的入口，暂时保留在这里。新增/调整工人时**同步更新本表**，
> 不要让本表过时。

### 上场（4 个）

| 工人 | 触发 | 同步/异步 | 主要职责 |
|------|------|----------|---------|
| SSHWorker | rgvps 入口 | 同步 | 敲门、看 OS/xray 版本、登记 VPS（stage 只标 connectable / unreachable，**不标 running**）、派 install_xray 任务 |
| XrayWorker | vps_task | 异步 | 4 分支处理（全新装 / 已装停了 / 已装空配 / **纳管**）+ 内嵌默认入口 18440 + 标 stage=running + 写 used_port_count |
| IPProbeWorker | rgip 入口 | 同步 | 用测试 VPS 验上游 IP 凭据通不通 |
| ProxyDeployWorker | ip_task | 异步 | 生产 VPS 池里挑机挂出口（"排除清单 + 高位随机"端口策略）+ 内外 ping |

### 封存（3 个）

| 工人 | 干嘛 |
|------|------|
| HealthCheckWorker | 定时探活（标 last_external_ping_ok 等字段） |
| ExpiryWorker | 定时看到期（标 is_active=0） |
| CleanupWorker | 过期 IP 在生产 VPS 上的配置清理 + 释放端口 |

## 10. MCP 工具暴露三类（绝不暴露内部子动作）

- **写入意图工具**:rgvps / rgip（提交意图立刻返回 task_id）
- **状态查询工具**:get_vps_registration_status / get_ip_registration_status
  （agent 看后端干啥的唯一窗口）
- **数据查询工具**:get_available_proxy_nodes 等纯只读
- **绝不暴露**:内部子动作（"先连 SSH"、"先装 xray"、"开个端口"）——
  这些是工人内部步骤，不给 agent

## 11. 旧 services/ 的处理

- **不删** —— 留作对照参考
- 新代码**不 import** `services/`
- 旧 services/ 里的"业务函数返回 dict 同步阻塞"那套约定（前面 §业务函数契约 节）
  仍约束旧代码，新代码按本节走

## 12. 纳管模式（见 ADR-0002）

**情况**：SSH 上去发现 xray 已装、在跑、且**有别人挂的出口配置**。

**职责归属**：XrayWorker 内部分支处理（不另起 TakeoverWorker）。

**核心规则**：
- 端口**原样接管**（不迁移到指定段，避免断生产）
- 反推上游 IP 凭据 → 写 ip_record（`expire_date=null` 因为不知道到期日）
- 每条出口逐条内 ping：通 → 算可用计入 used_port_count；不通 → 标 is_active=0 + 疑似过期
- 工具：XrayManager 新增方法 `extract_existing_outbounds()`（旧 `xray/config.py::extract_port_bindings` 提供原始能力，搬到 manager.py 类方法里实现）
- 巡检（ExpiryWorker，封存）见到 `expire_date=null` 直接跳过，不当过期处理

---

# 状态：本节已落定（v3）

- v1 2026-06-06 初版，对应 ADR-0001
- v2 2026-06-06 追加 ADR-0002 纳管模式：
  - §1 加 ADR-0002 引用
  - §7 加端口策略子节（排除清单 + 高位随机 + 纳管不迁移）
  - §9 工人阵容表更新 SSHWorker / XrayWorker 职责描述
  - 新增 §12 纳管模式
- v3 2026-06-06 取消 kits/ 目录，沿用现有 xray/：
  - §1 目录布局重写：去 kits/，xray/ 作为软件工具包（含 manager.py 类）
  - §2 命名分界更新：工人和 XrayManager 都是类风格，对象嵌套调用
  - §3 工具箱组织重写：xray/manager.py 是新工人对接主入口
  - §4 删"只 import kits/" → "只 import xray/"
  - §7 删"工具箱 kits/install_xray" → "XrayManager"
  - §12 工具说明对应改成 XrayManager 新方法
- 后续修订请按 CLAUDE.md §5.1 落文件前列 diff 给用户审
