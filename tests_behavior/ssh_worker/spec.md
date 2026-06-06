# SSHWorker 行为规约（spec.md）

**版本**:v2（2026-06-06）
**模块**:`workers/ssh_worker.py`
**类型**:同步入口工人（住在 rgvps 入口内）
**对应 ADR**:
- `docs/adr/0001-workers-replace-services.md`(worker 架构)
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md`(纳管由下游处理)

---

## 一、整理后的要点（Claude 整理，用户审过）

### 1. 工人定位

SSHWorker 是**敲门工**:
- rgvps MCP 工具的同步段(几秒内完成)
- agent 调 `rgvps(ip, user, pwd, port, ed, provider)` 时立即触发
- 主要职责:验账密能不能用 + 顺手登记机器基本信息 + 派下一活儿

### 2. 入口契约

**入参**:
| 字段 | 类型 | 说明 |
|------|------|------|
| ip | str | 服务器 IP |
| user | str | SSH 登录用户名 |
| pwd | str | SSH 登录密码 |
| port | int | SSH 端口（用户传什么就用什么，不要自作主张默认 22） |
| ed | date \| None | 到期日（可选） |
| provider | str | 服务商域名（可选） |

**返回**(同步段几秒内):
```python
# 路线 A: DB 已有
{
    "status": "already_registered",
    "vps": {...},
    "xray": {...},
    "active_task": {...} | None,
}

# 路线 B: 新登记成功
{
    "status": "queued",
    "task_id": int,
    "vps": {"ip", "os", "xray_version", "stage": "connectable", ...},
    "message": "已确认账密 OK,已入库;后台 worker 会接手装 xray + 端口审计",
}

# 路线 C: 账密错
{"status": "auth_failed", "message": "..."}

# 路线 D: 连接超时 / 拒接（已入库标 unreachable）
{
    "status": "unreachable",
    "vps_id": int,
    "message": "请确认 X 端口是服务商指定的远程登录端口...",
}
```

### 3. 三条主路线

#### 路线 A:DB 已有这台 VPS

不 SSH,直接打包当前状态返回。**零写操作**。

返回内容包含:
- vps_record 基本信息
- xray 状态(stage / version / 是否在跑)
- 当前活跃 task(如果有的话:status / next_run_at / last_error 等)

让 agent 一次调用就能告诉用户"这台已登记,xray 在跑,X 个端口在用"。

#### 路线 B:DB 没有,SSH 探测成功

干**三件事**:

1. **SSH 连接探测**(3-5s 内)——同步阻塞,因为 agent 在等"账密对不对"
2. **同会话顺手采集**(不另起任务):
   - OS name/version
   - xray 是否装、版本号(**空 = 没装,有 = 装了**,不维护单独"装/没装"枚举)
   - xray 服务是否 running
3. **关闭 SSH** → **写 `vps_record` + 建 `task`**:
   - `vps_record.stage = connectable`
   - `vps_record.xray_version = <探测到的版本或空字符串>`
   - 新增 task: `(type='install_xray', vps_id=N, status='pending', next_run_at=now)`

立刻返回 `status=queued + task_id`。

#### 路线 C:SSH 失败

**密码错**(auth_failed)→ 不入库,直接抛错。

**超时 / 拒接**(timeout / refused)→ 内部短时重试(次数/间隔走 `config.py`,
不写死)。仍失败 → **入库**:
- `vps_record.stage = unreachable`
- `vps_record.xray_status_message = "请确认 X 端口是服务商指定的远程登录端口..."`
- **不建** install_xray task(因为机器都连不上,装啥)
- **不要**指引用户去防火墙——SSH 端口被防火墙拦的概率远低于用户填错端口

返回 `status=unreachable + vps_id`,让 agent 提示用户去服务商控制台核对端口。

### 4. 不做的事

SSHWorker **绝不**做以下:
- ❌ 装 xray / 改 xray 配置 / 启停服务(那是 XrayWorker 的活儿)
- ❌ 端口审计 / 内 ping / 外 ping(取消该职责,见 ADR-0001)
- ❌ 锁 VPS(同步段不需要锁,VPS 还没被分配给别的 worker)
- ❌ 第二次 SSH 连接(一次连接顺手采集完所有信息,不要重连)
- ❌ **永远不把 stage 标成 `running`**(见 ADR-0002):
    - 即使探测到 xray 已装并且服务在跑,也不越权判断 "可投产"
    - 是否纳入投产由 XrayWorker 决定(它可能需要做纳管 / 配默认入口等)
    - SSHWorker 入库的 stage **只可能是** `connectable` 或 `unreachable`

### 5. 不变量(invariants)

跑完 SSHWorker 后必须满足:
- `vps_record.ip` 在 DB 唯一(UniqueConstraint 兜底)
- `vps_record.stage` **只能是 `connectable` 或 `unreachable`** —— SSHWorker
  不会写出其他 stage 值(尤其不是 `running`,见 ADR-0002)
- 如果 stage='connectable',必有对应 vps_task 行(status=pending 或已被
  XrayWorker 接走)
- 如果 stage='unreachable',`xray_status_message` 必非空
- 任何路径都**不会写入 xray 的实际配置**(那是 XrayWorker)
- 任何路径都**不会有"装/没装"二元状态字段** —— 靠 `xray_version` 字段空/非空 表达

### 6. 边界情况

| 情况 | 期望行为 |
|------|---------|
| 重复提交同一 IP(高频) | 路线 A 短路,不打 SSH |
| SSH 连上但读 `xray version` 报错 | xray_version 落空字符串,当作"没装"处理 |
| SSH 连上但读 OS 失败 | os_name / os_version 落空,**不影响入库**(可后续巡检补) |
| 用户传的 port 不是 SSH 端口(连不上) | 走路线 C → unreachable |
| DB 已有但 stage=unreachable | 走路线 A 返回现状,**不**尝试重新 SSH(让巡检工人后续处理) |

### 7. §工具清单(你审"功能 + 位置")

#### A. 原子工具(造完摆在工具包/共用模块里)

**通用工具:住 `core/` 下**

  SSH 通话手柄类(VPSSession, 有状态用类)     住 core/session.py
    绑住一台 VPS 的 SSH 通话, 出生时建连接、关闭时挂电话
    SSHWorker 用它去敲门看服务器能不能连
    用法: with VPSSession(ip, user, pwd, port) as sess:
             info = sess.get_system_info()
             xray = XrayManager(sess.client)

  采集系统信息(sess.get_system_info() 方法)  住 core/session.py
    自动调底层 cat /etc/os-release,返回 os_name / os_version / username
    SSHWorker 调一次就拿到 OS 信息,不用自己跑命令

**xray 软件工具箱: `xray/manager.py::XrayManager`**

  SSHWorker 只用查/测类一格(只看一眼,不动 xray):
    看核心版本   (返回空字符串 = 没装, 返回 "Xray xx.x.x" = 装了)

(SSHWorker 不调改配置/启停那两格 —— 那是 XrayWorker 的事)

---

#### B. 工具编排(几个原子打包成一行调)

住 `workers/ssh_worker.py` 内部, 作为 `SSHWorker` 类的私有方法
(下划线开头, 只 SSHWorker 自己用)

  查重
     干啥: 看 vps_record 表有没有这个 ip
     步骤: SQL SELECT → 命中返回打包好的现状(含关联活跃 task), 没命中返回 None

  敲门看一眼
     干啥: SSH 探测 + 顺手采集
     步骤:
       ① SSHSession 实例化 → 建连接
       ② 跑命令看 OS 版本
       ③ XrayManager(session.client) → 看核心版本
       ④ 关闭 session
       ⑤ 返回采集结果 dict

  入库 + 派任务
     干啥: 路线 B 成功路径的尾巴动作
     步骤:
       ① 写 vps_record (stage=connectable + 采集到的字段)
       ② 写 vps_task (type=install_xray, status=pending)
       ③ 返回 {status: queued, task_id, vps}

  失败路径处理
     干啥: SSH 探测失败时分两种处理
     步骤:
       密码错 → 直接返回 auth_failed, 不入库
       超时/拒接 → 内部重试 2 次仍失败 → 入库 stage=unreachable + 提示语
                  返回 {status: unreachable, vps_id, message}

---

## 二、用户口述原话(金标准,审查时翻这里)

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

## 三、修订历史

- **v1 2026-06-06** 初版。对应 ADR-0001 worker 架构。
  - 三条主路线:已存在/新登记/SSH 失败
  - auth 不入库,timeout/refused 短时重试仍失败入库标 unreachable
  - 不做端口审计 / 不做内外 ping(职责瘦身)
  - xray_version 空/非空表达"装/没装",不维护单独枚举

- **v2 2026-06-06** 补"不越权改 running"约束(对应 ADR-0002 纳管模式)
  - §4 新增反禁项:SSHWorker 永远只写 stage='connectable' 或 'unreachable',
    绝不写 'running'(即使探测到 xray 已装并在跑)
  - §5 不变量新增:stage 只能是这两个值
  - 原因:是否可投产由 XrayWorker 决定(它要做纳管 / 配默认入口等),
    SSHWorker 越权改 running 会让 ProxyDeployWorker 抢到一台没纳管的机器,
    撞到未知占用

- **v3 2026-06-06** 补 §7 §工具清单(用 XrayManager 类视角)
  - 原子工具:core.session.VPSSession 类(已存在,沿用) +
            XrayManager.version(只用这一个方法,不动 xray)
  - 工具编排:住 SSHWorker 类内部 _查重 / _敲门看一眼 / _入库派任务 / _失败路径
  - 对应 CLAUDE.local.md §1 v3 (取消 kits/) 和 CLAUDE.md §7 (类的实例方法主推)
  - T-A 现状盘点发现:VPSSession 已存在(core/session.py),无需新建 SSHSession 类
