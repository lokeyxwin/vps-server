# CLAUDE.md

VPS 资产管理 + 代理出口自动化项目（个人/小团队规模）的 AI 协作契约。
**所有新代码必须遵守这些规则**——它们是 VPS 模块完整闭环后沉淀的真实经验。

VPS 模块已闭环。**IP 业务是接下来的工作**（流程 + 实现见 [TODO_IP_PROXY.md](TODO_IP_PROXY.md)）。

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

某些表行数有自然上限（如 `proxy_record` 每台 VPS 最多 10 条对应 18441-18450 端口）：
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

> ⚠️ **严禁 `git add -A` / `git add .` 图省事**——会一锅端未跟踪文件（main.py / .env / 临时脚本）。**必须按文件路径显式 `git add path/to/file ...`**。

### main.py / mcp_server.py

- main.py：等所有业务做完再统一编排路由。每加一个业务**先写业务，不要为单个业务改 main**
- mcp_server.py：未来 MCP 入口，**当前不写**

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
| `git add -A` / `git add .` 图省事 | 会把未跟踪文件一并提交 |
| 各领域包里冗余维护同一份常量 | 抽到 core 单一真相源 |
| atom.py / service.py 混装服务管理 + 配置管理 | >200 行时拆 service.py + config.py |
| atom 在 try/except 里默默吞错返回 default | atom 失败必须抛领域异常子类 |

---

## 一句话总结

**Don't over-engineer. Layer strictly. Catch specifically. Hint actionably. Commit cohesively. Log conversationally for humans, structurally for AI. Defer the unknown.**
