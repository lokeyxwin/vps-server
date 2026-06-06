# SSHWorker 行为规约（spec.md）

**版本**:v4（2026-06-06）
**模块**:`workers/ssh_worker.py`
**类型**:同步入口工人（住在 rgvps 入口内）
**对应 ADR**:
- `docs/adr/0001-workers-replace-services.md`（worker 架构）
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md`（纳管由 XrayWorker 处理）
- `docs/adr/0003-xray-worker-three-branches-unified-tail.md`（XrayWorker 3 分支统一收尾）

---

## §0 给实现者的硬约束（实现前必读）

### 1. 旧代码只做参考，不直接 import

新 SSHWorker 实现时，下列旧代码**只允许打开看思路 / cp 片段过来用**，
**绝不**通过 `from xxx import yyy` 拉进来：

| 旧位置 | 怎么对待 |
|--------|---------|
| `services/`（旧业务编排层：vps_register / vps_init / ip_register） | 只打开看思路、必要时 cp 片段到 `workers/ssh_worker.py` |
| 旧 `main.py` / 旧 rgvps 入口脚本 | 同上，只参考实现思路 |
| `xray/service.py` + `xray/config.py`（旧函数） | 只参考思路；XrayWorker 那边新方法全写在 `xray/manager.py::XrayManager` 类里 |
| `test/`（旧测试） | 只参考 mock 模式；**新测试一律住 `tests_behavior/ssh_worker/`** |

详见 `CLAUDE.local.md` §0 legacy 代码姿势表。

### 2. 工具优先复用，缺了实现者直接造

#### 已有工具（直接 import 用）

下面这些已经造好了，**优先复用**：

| 工具 | 住哪 | 拿来干啥 |
|------|------|---------|
| `VPSSession` 类（绑 SSH 会话） | `ssh/session.py` | SSHWorker 拿来敲门，`with VPSSession(ip,user,pwd,port) as sess:` |
| `sess.get_system_info()` | `ssh/session.py`（VPSSession 的方法） | 一次拿 OS name/version |
| 加密 / 解密 | `toolbox/security.py` | 入库密码加密、读库解密（VPSRecord 工厂方法已经包好） |
| ORM 模型 | `db/models.py`（待 task/01 重塑） | VPSRecord / VPSTask / VPSStage / TaskStatus |
| 日志 | `log.get_logger(__name__)` | 现有的 LayeredFormatter 自动分两层风格 |

#### 找不到工具？**先报告，等批准，再造**

业务金标准（本 spec）已经定好。但**工具该不该造、造在哪、怎么命名 → 这些是用户决策**，
实现者**不要先斩后奏**。

实现者发现缺工具时按这个流程走：

```
① 停下来（不要自己拍板就造）
② 报告给用户：
     "我发现实现 spec §X.Y 需要一个 <工具名>"
     "它要实现 <功能，一句话>"
     "因为 <为什么需要这个工具，业务上下文>"
     "我倾向住在 <候选位置>"（可选，给用户参考）
③ 等用户决策：
     可能批准你造、可能让你换方式、可能给你别的思路
④ 批准后才动手造
⑤ 造完按下面格式记一条到任务单尾部"实现过程记录"
```

**禁止造的位置**（这条用户已拍板，不需要逐条问）：
- ❌ `services/`（legacy 业务层，新代码不动）
- ❌ `core/`（目录已删除）
- ❌ `xray/service.py` / `xray/config.py`（legacy 函数）
- ❌ 顶层散文件

**可造的位置**（用户批准后可选）：
- `toolbox/<name>.py`（跨领域无状态函数）
- `ssh/<name>.py` 或 `ssh/session.py::VPSSession` 加方法（SSH 相关）
- `xray/manager.py::XrayManager` 加方法（xray 软件操作）
- 工人 `.py` 内部下划线开头的私有方法（仅工人自用）

#### 造完报告（写在任务单尾部"实现过程记录"段）

每造一个工具，按这个格式记一条：

```
- 造了 <工具名>（类 / 函数 / 方法）
  住 <文件路径>
  干啥 <一句话功能>
  测试 <对应 TC 编号>
  审批 用户在 <对话/issue 等位置> 批准
```

Claude 验收时把这些工具同步到下次任务单的"已有工具"清单里。
**Claude 自己不写代码、不改实现，只同步状态 + 维护决策记录。**

### 3. 严格遵守本 spec § 不变量（§5）

代码必须验证 §5 列的每一条不变量。测试用例必须覆盖。

---

## 一、整理后的要点（Claude 整理，用户审过）

### 1. 工人定位

SSHWorker 是**敲门工**：
- rgvps MCP 工具的同步段（几秒内完成）
- agent 调 `rgvps(ip, user, pwd, port, ed, provider)` 时立即触发
- 主要职责：验账密能不能连上 + 顺手登记机器基本信息 + 派下一活儿
- **只 touch `vps_record` + `vps_task` 两张表**（不动 `ip_record` / `proxy_record` / 其他）

### 2. 入口契约

**入参**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ip | str | ✓ | 服务器 IP |
| user | str | ✓ | SSH 登录用户名 |
| pwd | str | ✓ | SSH 登录密码 |
| port | int | ✓ | SSH 端口（**必填**，不默认 22，错了让用户去服务商面板核对） |
| ed | date \| None | ✗ | 到期日（可选，入库时记录；main 可不给）|
| provider | str | ✗ | 服务商域名（可选，入库时记录；main 可不给）|

**返回**（同步段几秒内）：

```python
# 路线 A: DB 已有
{
    "status": "already_registered",
    "vps": {...},                     # vps_record 现状
    "active_task": {...} | None,      # 关联活跃 vps_task（含 status/last_error_msg/retry_count）
}

# 路线 B: 新登记成功
{
    "status": "queued",
    "task_id": int,
    "vps": {"ip", "os_name", "os_version", "stage": "connectable", ...},
    "message": "已确认账密 OK，已入库；后台 worker 会接手装 xray",
}

# 路线 C: SSH 失败 —— 全部抛回不入库（三种细分）
{"status": "auth_failed", "message": "..."}
{"status": "ssh_timeout", "message": "..."}
{"status": "ssh_refused", "message": "..."}
```

### 3. 三条主路线

#### 路线 A：DB 已有这台 VPS

**不 SSH**，直接查表打包返回。**零写操作**。

返回内容：
- `vps_record` 基本信息（ip / os / stage / xray_version / 时间戳 / ...）
- 当前活跃 `vps_task`（如果有：status / next_run_at / retry_count / last_error_code / last_error_msg）

让 agent 一次调用就能告诉用户"这台已登记，任务进度 X / 错误 Y"。

#### 路线 B：DB 没有，SSH 探测成功

干**三件事**：

1. **SSH 连接探测**（3-5s 内）—— 同步阻塞，因为 agent 在等"账密对不对"
2. **同会话顺手采集 OS**（不另起任务）：
   - 跑 `cat /etc/os-release` 拿 OS name / version
   - **不查任何 xray 信息**（版本号、是否运行、配置 —— 全部不查、不跑命令）
3. **关闭 SSH** → **写 `vps_record` + 建 `vps_task`**：
   - `vps_record.stage = connectable`
   - `vps_record.xray_version = ""`（SSHWorker 永远不写这字段，留给 XrayWorker）
   - `vps_record.os_name / os_version`：拿到就写，拿不到留空
   - 新增 `vps_task`：`(vps_id=N, status='pending', next_run_at=now)`

立刻返回 `status=queued + task_id + vps 现状`。

#### 路线 C：SSH 失败（账密错 / 超时 / 拒接）

**全部抛回去，不入库**。具体处理：

| 失败类型 | 返回 status | 重试 | 提示用户怎么办 |
|---------|-----------|------|--------------|
| 密码错（auth_failed） | `auth_failed` | ❌ 不重试，账密错重试无意义 | "请核对账号密码。OCR 可能看错 0/o、l/I/1；服务商面板密码 ≠ SSH 密码" |
| 超时（timeout） | `ssh_timeout` | ✅ 3 次重试 / 10s 间隔 / 连接超时延长兜底（慢网络/速率限制） | "可能端口错 → 服务商控制台核对远程登录端口；端口对的话 → 安全策略组开放入方向（含 22 或远程登录端口）；都对还不行 → 服务商面板自查" |
| 拒接（refused） | `ssh_refused` | ✅ 同上 | 同上 |

**不熔断**：入口同步段不熔断，任何失败立刻抛回让用户处理。

### 4. 不做的事

SSHWorker **绝不**做以下：
- ❌ 装 xray / 改 xray 配置 / 启停服务（XrayWorker 的活）
- ❌ **查任何 xray 信息**（版本号 / 是否运行 / 配置 / 端口绑定 —— 全部不查不写、不跑相关命令）
- ❌ 端口审计 / 内 ping / 外 ping
- ❌ 锁 VPS（同步段不需要，VPS 还没被分配给别的 worker）
- ❌ 第二次 SSH 连接（一次连接顺手采集 OS，不要重连）
- ❌ **写入 `vps_record.stage = running`**（running 是占用语义，由抢这台机的工人写；SSHWorker 路径只写 `connectable`）
- ❌ **入库 `stage = unreachable`**（此值已删除；连不上一律抛回不入库）
- ❌ **写入 `vps_record.xray_version`**（永远留空字符串；XrayWorker 第一次干完才写）

### 5. 不变量（invariants）

跑完 SSHWorker 后必须满足：
- `vps_record.ip` 在 DB 唯一（UniqueConstraint 兜底）
- **SSHWorker 写入的 `vps_record.stage` 只能是 `connectable`**（不再有 `unreachable`）
- **SSHWorker 写入的 `vps_record.xray_version` 永远为空字符串**
- 如果 `stage='connectable'` 入库 → 必有对应 `vps_task` 行（status=pending 或已被 XrayWorker 接走）
- SSHWorker 路径**永远不会写入 xray 的实际配置**（那是 XrayWorker）
- **错误信息只住 `vps_task` 表**（`last_error_code` / `last_error_msg`），**不住 `vps_record`**
- **SSHWorker 只 touch `vps_record` + `vps_task` 两张表**
- 路线 C（SSH 失败）**永远不写库**（任何错都抛回）

### 6. 边界情况

| 情况 | 期望行为 |
|------|---------|
| 重复提交同一 IP（高频） | 路线 A 短路，不打 SSH |
| SSH 连上但读 OS 失败 | `os_name / os_version` 落空字符串，**不影响入库**（巡检后续可补） |
| 用户传的 port 不是 SSH 端口（连不上） | 走路线 C → 抛回 `ssh_timeout` / `ssh_refused`（**不入库**） |
| DB 已有但 `vps_task.status='failed'` | 走路线 A，返回现状（含 last_error_code/msg 让 agent 转告用户） |

### 7. §工具清单（你审"功能 + 位置"）

#### A. 原子工具（住通用模块，SSHWorker 按需 import）

**SSH 这一摊：住 `ssh/` 下**

  SSH 通话手柄类（VPSSession，有状态用类）        住 `ssh/session.py`
    绑住一台 VPS 的 SSH 通话，出生时建连接、关闭时挂电话
    SSHWorker 用它去敲门看服务器能不能连
    用法：
      with VPSSession(ip, user, pwd, port) as sess:
          info = sess.get_system_info()

  采集系统信息（sess.get_system_info() 方法）     住 `ssh/session.py`
    自动跑底层 cat /etc/os-release，返回 os_name / os_version / username
    SSHWorker 调一次就拿到 OS 信息，不用自己跑命令

（SSHWorker **不调 XrayManager 任何方法** —— xray 那一摊是 XrayWorker 的事）

---

#### B. 工具编排（几个原子打包成一行调）

住 `workers/ssh_worker.py` 内部，作为 `SSHWorker` 类的私有方法
（下划线开头，只 SSHWorker 自己用）

  _查重
     干啥：看 `vps_record` 表有没有这个 ip
     步骤：SQL SELECT → 命中返回打包好的现状（含关联活跃 vps_task），没命中返回 None

  _敲门看一眼
     干啥：SSH 探测 + 顺手采集 OS
     步骤：
       ① VPSSession 实例化 → 建连接（失败按路线 C 抛回；详见 _失败路径处理）
       ② sess.get_system_info() 拿 OS 信息
       ③ 关闭 session
       ④ 返回采集结果 dict（含 os_name / os_version）
     注：**不查任何 xray 信息**

  _入库派任务
     干啥：路线 B 成功路径的尾巴动作
     步骤：
       ① 写 vps_record（stage=connectable + os_name/version + xray_version="" + ed/provider 入参直写）
       ② 写 vps_task（vps_id=N, status=pending, next_run_at=now）
       ③ 返回 {status: queued, task_id, vps}

  _失败路径处理
     干啥：SSH 探测失败时分场景抛回（**不入库**）
     步骤：
       密码错 → 返回 {status: 'auth_failed', message: '请核对账密...'}
       超时 → 内部 3 次重试 / 10s 间隔 / 连接超时延长兜底 → 仍失败返回 {status: 'ssh_timeout', message: '端口/安全策略组提示...'}
       拒接 → 同上 → 返回 {status: 'ssh_refused', message: '...'}

---

## 二、用户口述原话（金标准，审查时翻这里）

> "我应该站在一台服务器的全生命周期管理上面的角度去思考问题。我先不管外部传进来是
> 服务器 IP 还是 IP 的出口 IP。VPS 服务器始终都是作为那个被操作的对象。"

> "第一,服务器我能不能连上?能连上,OK,好,入库。就是说先测试能不能连接的一个工人"

> "我把信息塞进表里告诉 main:这台服务器可以用了,xray 那块版本号 X、配置是 Y → 我撤了"

> "诶,前面几个状态没有问题,就关于这一个账密错和拒接的问题。可能内部还要再区分一下,
> 只有账密错了才抛出去,不写入库,因为错误的信息就不要进来了嘛。但如果只是 Timeout 和
> 拒接的话,是可以入库的。"

> "如果是拒接,是不是会有可能就是远程 SSH 端口,云厂商不会默认开放,是不是有一些有
> 可能?但是我用下来那么多服务厂商的话,他的服务器,他说开放了哪个,用哪个端口来远程,
> 他就是默认开放的,安全策略组也是开放的,基本都能连的。"

> "你得先确认账号密码能用,且这个端口确实是开放的端口啊。那我想想,如果我内部拿到
> 一个端口,那可能要让如果说拒接的话,不是说安全策略组问题了,是说请你回去确认这一个
> 端口是服务商开放给远程登录的端口,这样子才对。"

> "ConnectionWorker 只关心:能不能连 + 看 xray_version 字段(空=没装,有=装了,不写
> '装/没装'状态)"

> "第一个跟第二个 Worker,它是交付一台能够给到 IP Worker 去使用的服务器"

> "如果数据库里面已经有了的话 不是我说的太果断了不是有了直接调更新工具,而是列出
> 可选工具例如查这台服务器的状态啦或者就是直接做一次查询把状态和信息拿出来,这样
> 查一次又知道登记没和目前这台服务器的业务阶段搞定没有 安装没安装 xray,xray 有
> 没有启动,xray 的配置情况(用了哪些端口等)这些信息"

> "你说 ssh 是重资源那能不能 ssh 连接成功后那个会话不中断但是先抛一个信息回去
> 我已经连上这个服务器 账号密码没问题,然后继续干后面的任务"
>
> (Claude 注:MCP 单次 call 只能返回一次,折中方案是把"连通 + 入库"做完再返回,
>  这一声返回就是"账密 OK"。后续装 xray 由 task 异步接力。)

---

### v4 追加（2026-06-06 业务图重审）

> "rgvps 拿到参数先校验在不在数据库,在数据库直接抛回去(原一次性脚本的逻辑直接复用的)
> 没重复那就启动工人去连接服务器,能连接 OK 可以入库"

> "如果所以 ssh 要启动的话就表示数据库是没的,可以无脑写入不怕重复"

> （前期版本：）"OS 必拿,xray-v 拿到就写,拿不到就不写"
>
> （**本条已被下面这条覆盖**：）

> "第一个工人上去之后只查系统版本和就只用 OS,然后 XRAY 核心的版本他就不获取了,
> 这个动作交出去,他不干,他不要做这个事情" —— **SSHWorker 连查 xray 的动作都不做**

> "密码错的话直接抛回去,旧逻辑都有的,都有写的,这些东西,直接 cp 啦。
> 将密码错了，连都连不上,那你还尝试个啥呢?直接抛回去啊。超时拒绝的、拒接的话,
> 重试三次吧,隔十秒钟,不要太长。
> 然后我们把连接的时间延长一点点来兜底吧。
> 然后还有速率限制的问题。
> 然后最主要还是要确认账号密码的问题,然后还有端口是不是正确的,主要是端口。
> 抛回去,都抛回去...只要第一次服务器第一次进来,只要连不上的,全部抛回去,不要入库了"

> "如果是账号密码错误,就及时校正。然后如果是连接超时拒接的话,拒接的话估计是端口错了,
> 那就换端口。然后端口也确认是没有问题,但是就是连不上的话,那就让用户去看一下是不是
> 安全策略组的问题,让他去开放这个端口,就是入方向所有。然后把二二端口开放再来。
> 然后还有远程登录的端口也开放看一下喽,让用户直接去服务商的网站自己去看,就不管了,
> 不要入库。SSH 的最终职责就是,它必须验证一台服务器是能够连接的,只有它能连接了,
> 后面的业务才能继续进行。所以它入口的话,不要不需要熔断,只要有任何一个错误,
> 全部抛回去,让用户去自己去解决。"

> "我说的复用的意思,CP 一份呐,不是导入的那种复用啊。是直接把代码片段拿过去
> 新文件去使用,不是直接导入。"

> "VPS 表里面的状态机是这样子。第一个,connectable 这一个状态就表示说第一个工人
> 他的活干完了,然后他验证过这台服务器是可以连接的,是正常能用的了...
> 这一台服务器这个 moment 是没有任何工人在连接它的,它是闲置的,
> 并且验证过它是能够连接的,那就是直接拿去用。
> 然后 running 这一个状态机就表示说有任意工人拿了这台服务器去使用了,先不管任务表"

> "失败就等下一次重做,那就是他自己的事情了,第二个工人自己自己掌管自己的从事时间
> 就可以了"（下游 XrayWorker 范畴，归档作设计意图）

> "他只要两个表一个 vps 一个 vpstask"（SSHWorker 职责边界）

> "错误 message 直接写在任务表中,反正 task 有 id 指针指向对应的服务器了,
> message 在 vps 表是冗余字段"

---

## 三、修订历史

- **v1 2026-06-06** 初版。对应 ADR-0001 worker 架构。
  - 三条主路线：已存在/新登记/SSH 失败
  - auth 不入库，timeout/refused 短时重试仍失败入库标 unreachable
  - 不做端口审计 / 不做内外 ping（职责瘦身）
  - xray_version 空/非空表达"装/没装"，不维护单独枚举

- **v2 2026-06-06** 补"不越权改 running"约束（对应 ADR-0002 纳管模式）
  - §4 新增反禁项：SSHWorker 永远只写 stage='connectable' 或 'unreachable'，
    绝不写 'running'
  - §5 不变量新增：stage 只能是这两个值
  - 原因：是否可投产由 XrayWorker 决定

- **v3 2026-06-06** 补 §7 §工具清单（用 XrayManager 类视角）
  - 原子工具：`ssh.session.VPSSession` 类（v3 当时仍叫 core/session.py，后在
    `refactor(arch): core/ 拆为 ssh/ + toolbox/` commit 中迁至 `ssh/session.py`）
    + XrayManager.version（v4 取消，SSHWorker 不再用）
  - 工具编排：住 SSHWorker 类内部 _查重 / _敲门看一眼 / _入库派任务 / _失败路径

- **v4 2026-06-06** 业务图重审 + 大改（用户拍板）
  - **§1** 加 "只 touch vps_record + vps_task 两表" 原则
  - **§2 入参** ：port 标必填（MCP 框架强制）；ed + provider 可选可空入库
  - **§2 返回** ：**删路线 D unreachable**；只留 A / B / C；C 拆 3 个 status
    （auth_failed / ssh_timeout / ssh_refused）
  - **§3 路线 B** ：**SSHWorker 连查 xray 的动作都不做**（原"采 xray_version"
    步骤整删），只采 OS；入库 xray_version=""
  - **§3 路线 C** 改写：连不上一律**抛回不入库**；3 次重试 / 10s 间隔 + 连接超时延长兜底
  - **§4** 加 "不查任何 xray 信息" + "不入库 stage=unreachable" + "不写 xray_version"
  - **§5 不变量** ：stage 只 connectable；xray_version 永远空；错误住 vps_task 表；
    只 touch 两表；路线 C 永不写库
  - **§6** OS 读不到留空入库；DB stage=unreachable 边界整删；
    DB 已有 + task.status=failed → 路线 A 返回 last_error
  - **§7 工具清单** ：XrayManager.version 删（SSHWorker 不再用）；
    路径从 `core/session.py` 改为 `ssh/session.py`（v3 取消 kits/ 后的目录拆分）

  **下游影响清单**（v4 spec 改后这些文件都受牵连，需要同步修订）：
  - `docs/adr/0002` 状态修正 `Accepted, partially superseded by ADR-0003`，
    VPSStage 改至 2 值（删 unreachable，加 running 占用语义）
  - `task/01` VPSRecord schema：删 stage_message / port 去 default /
    xray_version SSHWorker 不写
  - `task/02` vps_task schema：status 简化至 4 值（pending / in_progress /
    done / failed），删 pending_retry / circuit_broken（XrayWorker 内部管重试）
  - `task/04` SSHWorker 私有方法：删"看 xray 版本"步骤；失败 3 分支抛回
  - `task/05` SSHWorker.process：删 xray_version 采集；返回 5 status
  - `task/06` rgvps 入口：port 必填；返回 status 集合改 A/B/auth_failed/ssh_timeout/ssh_refused
  - `task/07` XrayWorker：状态机 4 值；XrayWorker 上去自己查 xray；
    干完成功 task.done + vps.stage 自管；失败 task.failed + vps.stage 保持 running 锁住
  - `CLAUDE.local.md`：加"错误信息住任务表不住资源表"架构原则；
    工人阵容（§9）更新 SSHWorker 不查 xray + XrayWorker 自己查
