# CLAUDE.md

这个项目对 AI 协作者（Claude / Codex / Cursor 等）的约束与契约。
**所有新代码必须遵守这些规则**——它们是 VPS 模块完整闭环后沉淀下来的真实经验。

---

## 项目身份

- **VPS 资产管理 + 代理出口自动化**（个人/小团队规模）
- 数据库：开发 SQLite / 生产 MySQL，业务代码零改动切换
- 入口：CLI（未来可能加 MCP / Web）
- VPS 模块已完整闭环，**IP / Proxy 是接下来的工作**（详见 TODO_IP_PROXY.md）

---

## 核心约束（按重要性排序）

### 1. **YAGNI**——绝对不允许过度设计

不要因为「未来可能用到」就预先抽象、预先建包、预先加字段。

- **Rule of Three**：重复出现 3 次才考虑抽象。一次写实现，两次停下想想，三次再抽。
- **抽象基类是「发现的」不是「设计的」**——只有看到 2 个以上具体实现共享同一契约，才考虑 ABC。
- **不要为了对称建空包/空文件**——如果一个领域只有一个文件，就一个文件；不要硬塞 atom.py + manager.py 凑形式。
- **不预留配置项**：硬编码到「真的有调的需要」才挪 config。

❌ 反例：「以后可能要加 Windows 服务器，先抽个 BaseRemoteClient」——禁止。
✅ 正例：真正要加 WinRM 那天，再抽。届时只两个实现，正好抽。

### 2. **严格自下而上的分层依赖**

```
入口（CLI / 未来 MCP / Web）
   ↓ 只 import 下一层
业务层（services/）  —— 编排+决策
   ↓
领域类（XrayManager / VPSSession / 未来 IPManager / ProxyManager）
   ↓
原子函数（core / xray atom / 未来 ip atom / proxy atom）
   ↓
基础设施（DB / 加密 / 日志 / 第三方库）
```

**禁止跨层跳跃**：
- ❌ main 直接 import 类层
- ❌ atom 之间跨领域 import（除了所有领域都依赖的 core）
- ❌ 类层调业务层

**唯一允许的横向依赖**：领域 atom → `core.ssh.execute_command`（因为 core 是基础设施层，所有领域都依赖）。

### 3. **业务函数返回 dict，不抛异常给上层**

```python
# 标准业务函数签名
def <动词>_<对象>(<参数>) -> dict:
    """返回 dict：必有 status 字段，失败附 message，成功附数据字段。"""
    return {"status": "ok", "ip": ..., ...}        # 成功
    return {"status": "duplicate", "message": ...}  # 已知失败
    return {"status": "auth_failed", "message": ...} # 已知失败
```

业务层**吃掉所有底层异常**，转成 status 字符串。CLI / 调用方只看 status 决定怎么做。

### 4. **错误必须细分 + 附排查指令**

任何会失败的操作，错误要分类（不是一个 status='failed' 兜底），且每个 message 必须告诉用户**具体怎么排查**。

VPS 模块已有的标准：
- 连接错误分 4 类：`auth_failed` / `timeout` / `refused` / `failed`，每个 message 列 3 条常见原因 + 排查命令
- xray 错误分 5 类：`install_failed` / `verify_failed` / `service_not_active` / `enable_failed` / `uninstall_failed`，同样有原因 + 命令

IP / Proxy 业务沿用这个模式：**每种失败都该有自己的 status code 和具体提示**。

### 5. **不要修复你没确认的问题**

发现「设计意图不完善的点」要先汇报，**等用户拍板再修**。不要：
- 自作主张加新字段
- 自作主张加抽象基类
- 自作主张引入新依赖

---

## 业务函数契约（IP / Proxy 沿用）

### 标准业务流程模板

```
def register_xxx(...) -> dict:
    logger.info("register_xxx 开始 ip=%s ...", ip)

    ① 查 DB：已存在？已完成？正在做？短路返回
    ② 标记「正在做」状态入库（并发保护 + 留痕）
    ③ SSH/网络 操作（用 manager/atom 层）—— catch 已知异常子类
    ④ 验证（内部 + 外部）
    ⑤ 写最终状态入库
    ⑥ return {"status": ..., ...}
```

### 失败也必须写 DB

业务执行中途失败，**也要把已探测到的状态写进 DB**——避免 message 里说 "version=X" 但字段为空的矛盾。

参考 `services/vps_install_xray._save_failure_with_context`：在 catch 内主动再问一次 manager 收集上下文。

### 反例

❌ `return {"status": "failed"}` 不带 message
❌ 直接抛异常让 main 去 catch
❌ 失败时不更新 DB

---

## 类与原子契约

### 命名约定

| 类型 | 后缀 | 例子 | 说明 |
|------|------|------|------|
| 资源管理类 | `Manager` | XrayManager, IPManager（未来）, ProxyManager（未来）| 单一领域的有状态管理类 |
| SSH 会话类 | `Session` | VPSSession | 例外：它管的就是 "session" 本身 |
| ORM 模型 | `Record` | VPSRecord, IPRecord（未来）, ProxyRecord（未来）| 一行 = 一个实例 |
| DB 表名 | 类名 snake_case | `vps_record`, `ip_record`, `proxy_record` | 跟类名对齐 |

### 类设计模式

1. **atom + Manager 双层**：领域里的纯函数放 `atom.py`，类方法包装它们
2. **高层 ensure_xxx 方法**：封装多步流程（如 `ensure_installed_and_running`），业务只调一行
3. **失败抛具体异常子类**：业务可分类 catch，每个子类带 `code` 属性
4. **client/session 注入**：Manager 接收 `paramiko.SSHClient` 或类似的连接对象，不自己建连接

### 原子函数契约

- 单一动作，无状态
- 错误抛**领域自定义异常**（不直接抛 builtin Exception）
- 文件传输 / 命令执行 / 网络请求超时必须可配（默认值放 config.py）

---

## 数据模型契约

### 状态字段标准结构（参考 VPSRecord.xray_*）

任何「有生命周期的资源」都加这 5 个字段：

```python
xxx_status: str              # 当前状态枚举（用常量类约束）
xxx_version: str             # 版本/标识（可空字符串）
xxx_installed_at: datetime   # 首次完成时间（nullable）
xxx_last_checked_at: datetime # 最近一次状态检查时间
xxx_status_message: str      # 人类可读的状态附加信息
```

`xxx_status` 用 `class XxxStatus: NOT_INSTALLED = ...` 常量类提供枚举，避免 typo。

### 敏感字段加密

- 密码/token 字段命名 `xxx_encrypted`（提示读者：直接读到的是密文 bytes）
- 通过 `record.get_xxx()` 方法解密拿明文，不暴露 `xxx` 属性
- `__repr__` 主动屏蔽密码字段
- 加密在 ORM 模型的 `from_form()` 工厂方法内发生，业务层不感知

### Reconcile 模式

**服务器是真相，DB 是它的影子。**

每次业务执行**先看服务器实际状态再决定动作**：
- 检测到「已装」就跳过装命令
- 检测到「服务挂了」就尝试启动
- 用「现状」纠正 DB 的认知

不要假设 DB 的状态就是当前真实状态。

---

## 测试契约

### 三层测试策略

| 层 | 测什么 | 怎么做 |
|----|-------|--------|
| **atom 测试** | 单个函数的分支 | mock 第三方调用（paramiko / requests），验证函数行为 |
| **Manager 测试** | 类内部的状态机分支 | mock `xray.manager.atom.*`，测 ensure 的 if/else 路径 |
| **业务测试** | 业务函数所有 status 路径 | mock 整个 Manager 类 + DB session，覆盖每种 status |

### 真服务器测试

默认 skip。配 `VPS_TEST_IP/USER/PASSWORD/PORT` 环境变量触发。
**不要让 CI 默认跑真服务器测试**。

### 安全场景必须有专项测试

- 密码落盘必须是密文（绕过 ORM 用原生 SQL 验证）
- `__repr__` 不能泄露明文
- 错误密钥解密必须失败
- 并发同一资源不能重复执行（用 DB 的「正在做」状态字段验证）

---

## 日志契约

业务层用 `services.<name>` logger，原子层用模块名 logger。LayeredFormatter 自动区分：

```
──────────────────────────────  ← 业务边界
HH:MM:SS ▶ services.xxx: ...     ← 业务事件（带分隔线 + ▶ 标记）
HH:MM:SS [INFO] core.ssh: ...    ← 原子事件（带 [LEVEL] 标记）
```

**业务层必须在每个分支决策点 log**：开始 / 跳过 / 失败 / 成功。原子层 log 是「报告我做了什么」。

---

## 工作流契约

### 开发顺序

```
新业务（如 IP 注册）：
  ① 在 ip/atom.py 加原子函数 + 单测 → 提交
  ② 在 ip/manager.py 加 Manager 类 + 单测 → 提交
  ③ 在 services/ip_register.py 加业务函数 + 单测 → 提交
  ④ ★ main.py 留到所有领域都做完，最后统一编排 ★
```

### Commit 规则

- **功能 + 测试 = 一个 commit**（原子和它的测试不分家）
- **业务 + 测试 = 一个 commit**
- 标题用 `feat(<scope>):` / `fix:` / `refactor:` / `test:` / `docs:` / `chore:`
- 摘要列出关键改动点，不堆代码（代码看 diff）

> ⚠️ **严禁 `git add -A` / `git add .` 图省事**
>
> 这两个命令会把当前未跟踪的所有文件一锅端进 commit，包括：
> - 还在调的 main.py / mcp_server.py（按本文件约定它们应当晚于业务最终落地）
> - 临时调试脚本、本地数据库 dump、.env 误漏
> - 上一次没跟踪意图的他人改动
>
> **必须按文件路径明确 `git add path/to/file ...`**，让每个 commit 的内容跟标题一一对应。
> 如要一次提多个文件，把路径一个个列出来，**不要用通配**。

### main.py 和 mcp_server.py

- **main.py**：等所有领域业务做完再统一写路由。每加一个业务**先写业务**，不要为单个业务改 main。
- **mcp_server.py**：留作未来的 MCP 接口入口，**当前不写**。

### 真服务器跑

完成新业务后，**业务作者负责跑一次真服务器**确认全流程通。挂在哪一步、DB 留了什么状态，是验证的核心。

---

## 反模式禁令

| 不要做的事 | 为什么 |
|----------|------|
| 给 atom 函数加 DB 操作 | atom 必须无状态 |
| 业务层 import paramiko 直接用 | 必须走 Manager / Session |
| 任何代码里 `print(...)` | 用 logger |
| 异常吞掉不传播也不记日志 | 至少 logger.warning |
| 加新依赖不更新 pyproject.toml | 必须可复现安装 |
| `try: ... except Exception: pass` 无注释 | 必须加 `# noqa: BLE001 — <意图说明>` |
| Mock 测试不验证 DB 状态变化 | 业务的核心副作用就是改 DB，必须验证 |
| 直接改测试通过断言来"修"挂掉的测试 | 先看为什么挂、是不是代码 bug |
| `git add -A` / `git add .` 图省事 | 会把未跟踪的所有文件一并提交，污染 commit 范围（详见 Commit 规则） |

---

## 给 IP / Proxy 业务开发者的速查

**写 IP/Proxy 业务前，照这套对一遍**：

1. **包结构**：`ip/atom.py + ip/manager.py + ip/__init__.py`（沿用 xray/ 模式）
2. **ORM**：`IPRecord` 类，表名 `ip_record`，含 5 字段生命周期
3. **加密**：代理 token / 密码字段命名 `xxx_encrypted`，加 `get_xxx()` 方法
4. **业务**：`services/ip_register.py` 含 `register_ip(...) -> dict`
5. **错误**：定义 `IPError` 基类 + 子类，每个带 `code` 和具体排查 message
6. **测试**：atom mock 测分支 + Manager mock 测状态机 + 业务 mock 测 status 全路径
7. **复用**：直接 `from core import VPSSession, open_tcp_port_range, test_socks_proxy`（VPS 阶段已经给你备好了）
8. **领域间联动**：IP 业务找一台 VPS 当测试机时，**通过 db 层查 VPSRecord**，再用 `VPSSession.from_record(rec)` 拿连接——不要直接 import VPS manager

按这个走，你写出来的 IP 业务会和 VPS 业务**风格一致、可读性等价、测试覆盖等价**。

---

## 一句话总结

**Don't over-engineer. Layer strictly. Catch specifically. Hint actionably. Commit cohesively. Defer the unknown.**

新代码不符合上面任意一条 → 停下来想想 → 而不是先写完再说。
