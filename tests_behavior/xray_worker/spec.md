# XrayWorker 行为规约（spec.md）

**版本**:v1（2026-06-06）
**模块**:`workers/xray_worker.py`
**类型**:异步 task 工人(住在主进程的轮询循环里)
**对应 ADR**:
- `docs/adr/0001-workers-replace-services.md`(worker 架构)
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md`(纳管职责归属)

---

## 一、整理后的要点(Claude 整理,用户审过)

### 1. 工人定位

XrayWorker 是**安装工**:
- 异步轮询 `vps_task` 表,抢 `status='pending'` 的任务
- 上去看现状,**根据 4 种情况分支处理**(含纳管)
- 干完把 VPS 表的 stage 升级到 `running`(=可投产)
- **只有 XrayWorker 能把 VPS 标 running**,其他工人都没这个权限

### 2. 入口契约

**触发**:扫 `vps_task` 表 `WHERE status='pending' AND next_run_at <= now`

**抢锁**:
```sql
UPDATE vps_task
   SET status='in_progress',
       worker_id='<本 worker 标识>',
       locked_until=now + 5min
 WHERE id=? AND status='pending'
```
影响行数=0 → 别人抢到了,换下一条;=1 → 我抢到了。

**输入**:`vps_task.vps_id` 指向的 vps_record 行

**输出**:
- task.status → done / pending_retry / failed / circuit_broken
- vps_record.stage → running(成功) / 保持 connectable(可重试) / install_failed(放弃)
- 可能新增 ip_record + proxy_record(纳管时)

### 3. 三个分支 + 统一收尾(用户拍板版,简化自旧 4 分支)

抢到 task 后 SSH 上去,**所有路径尾巴都做"查配置纳管 + 补默认入口 + reload"**.
3 个差异分支只在前面"装/起"步骤不同。

#### 共同前置: 看现状

```
看 xray_version 字段(SSHWorker 探测时已写入) + probe.is_running()
→ 决定走 A / B / C
```

#### 分支 A:全新空白(xray_version 为空)

```
执行:
  ① 调 XrayManager.install()         → 装 xray
  ② 调 XrayManager.start()           → 起服务
  ③ 调 XrayManager.enable()          → 设开机自启
  ④ 调 XrayManager.version() 验证版本号非空
  → 进入"统一收尾"
```

#### 分支 B:已装但停了(xray_version 非空 + is_running()=False)

```
执行:
  ① 调 XrayManager.start()           → 起服务
  ② 调 XrayManager.enable()          → 设开机自启
  ③ 调 XrayManager.is_running() 验证起来了
  → 进入"统一收尾"
```

#### 分支 C:已装且跑着(xray_version 非空 + is_running()=True)

```
执行:
  (啥都不干)
  → 进入"统一收尾"
```

---

#### 统一收尾(ABC 三个分支跑完都走这段)

**核心简化**:旧 ADR-0002 4 分支版的 D"纳管"路径,现在合并进所有路径——
**只要存在配置,就接管;不存在配置,跳过接管步骤**。这样不再有"特殊纳管路径"。

```
执行(7 步):
  ① 调 XrayManager.read_config()      → 读 xray 当前配置
  ② 调 XrayManager.extract_existing_outbounds()  → 抠出现有出口配置
     ⭐ 抠信息类,返回 list[dict]:每条上游 IP 凭据 + 端口绑定
     字段大类(实现细节见 XrayManager 加方法任务单):
       - 服务器上的端口号
       - 入口账号 / 入口密码
       - 上游入口域名 / 入口端口
       - 上游账号 / 上游密码
       - 出口 IP (从 outbound 备注读,无则空)
       - 出口国家 (从 outbound 备注读,无则空——业务层后续走 core.geoip)

  ③ 如果 ② 返回 [] (没别人挂的出口):
        → 跳过 ④⑤,直接到 ⑥
     否则逐条做内 ping(调 XrayManager.test_internal(port,user,pwd)):
        通 → 标记此条"可用",计入 used_port_count
        不通 → 标记此条"疑似过期"

  ④ 写 ip_record 表(每条上游 IP 一行):
       entry_host = upstream_host
       entry_port = upstream_port
       username   = upstream_user
       password_encrypted = encrypt(upstream_pwd)
       protocol   = upstream_protocol(默认 socks5)
       egress_ip  = outbound 备注里的(若无则"")
       country_code / city = geoip.lookup_egress(若有 egress_ip)
       expire_date = NULL   ← 关键:纳管 IP 不知道到期日(ADR-0002 §5)
       is_active  = 内 ping 通=1 / 不通=0
       is_configured = 1    ← 纳管的 IP 一登记就是"已挂"
       如果 egress_ip 已存在 → upsert

  ⑤ 写 proxy_record 表(每条挂着的出口端口一行):
       vps_id     = 本 vps
       vps_port   = 别人挂的原端口(不迁移!例如 1080 还是 1080)
       protocol   = socks5
       inbound_user / inbound_pwd 加密
       upstream_host / egress_ip / egress_country
       ip_id      = 对应 ip_record.id
       status     = "using" if 内 ping 通 else "expired"

  ⑥ 补默认入口(端口固定 18440 noauth direct):
       如果当前配置里没有 18440 这条 → 调 XrayManager.add_inbound() 补
       不删除任何现有 inbound

  ⑦ 调 XrayManager.upload_config() → validate_config() → reload()
     最后 is_running() 验证仍在跑

出口:
  vps.stage = "running"
  vps.xray_version = 实际版本号(分支 A 装完后)
  vps.used_port_count = 内 ping 通的出口条数(可能为 0)
  task.status = "done"
```

**关键改动**(相比旧 4 分支版):
- 取消"分支 D 纳管"独立路径
- 所有分支统一走"读配置 → 抠出口 → 内 ping → 入库 → 补默认入口 → reload"
- 简化决策:只看现状决定前置装/起步骤,后续完全统一

### 4. 端口策略(关键约束,见 ADR-0002 §3)

- **纳管的端口不迁移**(分支 D 步骤 ④)。客户端可能正在用,迁移会断生产
- **新分配端口**(分支 A 默认入口 / 后续 ProxyDeployWorker 配新出口)走
  "排除清单 + 高位随机"策略:
  - 必排除:`config.py::EXCLUDED_PORTS`(well-known 0-1023 + 常用应用端口)
  - 必排除:该 VPS 已用端口(查 proxy_record `WHERE vps_id=X AND status='using'`)
  - 必排除:18440(我们自己的默认入口端口)
  - 剩下 1024-65535 高位随机挑
- **不再硬编码 18441-18450 段**(原 CLAUDE.md 规则,本 ADR 取消)

### 5. 默认入口(18440 noauth direct)

工具箱内嵌的约束(见 CLAUDE.local.md §7):
- 协议 socks5 noauth → direct(不走任何 outbound 代理)
- 端口固定 `XRAY_DEFAULT_PORT`(= 18440)
- **不入 proxy_record 表**(不是节点资产,是 xray 自启的内部组件)
- 任何分支结束时都要保证这条入口存在(分支 A/D 显式写,分支 B/C 缺则补)

### 6. used_port_count 字段

`vps_record.used_port_count`:**已配置且内 ping 通的代理出口数量**

- 分支 A/C 结束:写 0
- 分支 D 结束:写 = 内 ping 通的纳管出口条数
- 后续 ProxyDeployWorker 配新出口成功:+1
- 后续 CleanupWorker 清掉过期出口:-1

注:旧字段 `idle_port_count`(空闲端口数) **删除**(见 ADR-0002 §4)。

### 7. 失败处理

| 失败类型 | 行为 |
|---------|------|
| SSH 重连不上(密码改了) | task.status='failed',vps.stage 保持 connectable<br>last_error_code='auth_denied'<br>last_error_msg='SSH 密码可能变更,请用 update_vps 工具更新' |
| GitHub 拉取超时(网络抖动) | task.status='pending_retry'<br>next_run_at=now + backoff(2^retry_count 分钟)<br>last_error_code='install_timeout'<br>retry_count++ |
| 连续 5 次同 error_code | task.status='circuit_broken',停止重试 |
| 纳管步骤 ⑥ 验证失败(配置推坏了) | task.status='pending_retry'<br>**不**强行回滚原配置(避免动用户在用的东西)<br>last_error_msg 写明哪一步出错让人介入 |

### 8. 不做的事

XrayWorker **绝不**:
- ❌ 端口审计 / 端口段限定(本 ADR 取消)
- ❌ 迁移纳管端口到指定段(断生产)
- ❌ 删除别人挂的 inbound(只读 + 补,不删)
- ❌ 内/外 ping 巡检(那是 HealthCheckWorker 的活儿,封存中)
- ❌ 修改 ip_task 表(那是 ProxyDeployWorker 的事)
- ❌ 在分支 A 之后立刻派 deploy_proxy 任务(用户没主动 rgip 就不主动配出口)

### 9. 不变量(invariants)

跑完 XrayWorker 后必须满足:
- 若 task.status=done,则 vps.stage=running 且 xray 服务确认在跑
- 若 vps.stage=running,则 xray 配置里必有 18440 默认入口
- proxy_record 里所有 vps_id=X 的行的 vps_port 都不能跟 EXCLUDED_PORTS 冲突
  (纳管的旧端口除外,作为遗留兼容)
- ip_record 里所有 expire_date=null 的行都是纳管来的(原生 rgip 入口必填日期)
- used_port_count 必须等于 `proxy_record WHERE vps_id=X AND status='using' COUNT(*)`

### 10. 边界情况

| 情况 | 期望行为 |
|------|---------|
| 抢锁瞬间 worker_id 字段已被别人填 | UPDATE 影响行数=0,扫下一条 |
| 装到一半进程被杀(locked_until 没续) | 锁过期,下次 worker 看到当无锁,重新抢 |
| 纳管反推出 0 条上游(配置只有默认入口) | 走分支 C 路径(空配置)而非 D |
| 纳管反推出的 inbound 没找到对应 outbound(配置畸形) | 跳过这一条,log 警告,**继续处理其他条**,不全 fail |
| 配置里有重复 egress_ip(同一上游挂在两个端口) | ip_record upsert,proxy_record 分别记两条(两个端口) |

### 11. §工具清单(你审"功能 + 位置")

#### A. 原子工具(造完摆在工具包/共用模块里)

**xray 软件工具箱:住 `xray/manager.py` 里的 `XrayManager` 类**
(沿用现有,新方法直接加进 XrayManager 类,不再绕 service.py 函数。
 工人 import: `from xray.manager import XrayManager`)

  装机类方法:
    装核心
    起服务 / 停服务
    设开机自启
    重载配置(失败兜底走重启)

  改配置类方法:
    读 xray 配置文件
    写 xray 配置文件
    校验配置语法
    加一条入口
    抠出现有出口配置   ⭐ 抠信息类(字段大类见下)
    有没有非默认出口   (返回 True/False,纳管判断用)

  查 / 测类方法:
    看核心版本
    看服务跑没跑
    内 ping 端口

**通用工具:住 `core/` 下,工人按需 import**

  SSH 连接类(SSHSession,有状态用类)         住 core/ssh.py
    绑住一台服务器的 SSH 通话,出生时建连接、关闭时挂电话
  
  查 IP 地区函数(lookup_egress,无状态用函数)  住 core/geoip.py
    给一个 IP,调 ipinfo.io 查它是哪国哪城

---

#### 抠信息类工具的字段大类(spec 阶段对齐)

**抠出现有出口配置**这个方法,要抠的"字段大类":

  ✓ 服务器上的端口号(那个 inbound 挂在哪个端口)
  ✓ 入口账号 / 入口密码(客户端连这个端口要用的)
  ✓ 上游入口域名 / 入口端口(往哪个上游代理转发)
  ✓ 上游账号 / 上游密码(连上游代理的凭据)
  ✓ 出口 IP(走这条出去最终从哪个 IP 出去)
  ✓ 出口国家(出口 IP 是哪国的)

**实现分支(关键!)**:
  出口 IP 字段:
    ① 优先从 xray 配置 outbound 备注里读(如果有 _meta 标了)
    ② 没有就字段留空(等实现者补 ipinfo 查)
  
  出口国家字段:
    ① 优先从 xray 配置 outbound 备注里读
    ② 没有就调"查 IP 地区"工具实时查

**字段命名细节**等任务单阶段实现者列,在另一窗口跟用户对齐。

---

#### B. 工具编排(几个原子打包成一行调)

住 `workers/xray_worker.py` 内部,作为 `XrayWorker` 类的私有方法
(下划线开头,只 XrayWorker 自己用)

  现状判断
     干啥: 看一眼 4 种情况走哪条分支
     步骤: 看核心版本 → 看服务跑没跑 → 看有没有非默认出口
          → 决定 A 全新 / B 已装停了 / C 已装空配 / D 纳管

  纳管六步打包
     干啥: 把纳管整套流程串成一行调
     步骤:
       ① xray.读 xray 配置
       ② xray.抠出现有出口配置          ⭐ 抠信息类
       ③ 逐条 xray.内 ping 端口
       ④ 写 ip_record (每条上游 IP 一行)
       ⑤ 写 proxy_record (每个出口端口一行)
       ⑥ xray.加一条入口 18440 (没有则补默认入口)
       ⑦ xray.重载配置 + xray.校验配置 跑着

  失败分流
     干啥: 看错误类型决定重试 / 熔断 / 失败
     步骤:
       临时错(超时/网络抖) → vps_task.status = pending_retry + 退避
       永久错(密码改了/二进制坏) → vps_task.status = failed
       连续 5 次同 code → vps_task.status = circuit_broken

---

## 二、用户口述原话(金标准,审查时翻这里)

### 关于 4 分支整体

> "诶,前面我们说的那个业务是有一个安全策略组问题的吧... XrayWorker 这一个
> 没有问题。第一个功能的话,叫 SSH Worker 吧... 然后还缺了个东西,它要检查
> 系统版本的"

> "Install 安装工那一个状态没有问题,然后只做补充。这一个核心软件它必须要有
> 一个端口出去,就是配置了一条配置了之后,它才能算正确的正在运行。这个流程是
> 内嵌在里面的"

### 关于纳管(分支 D)

> "如果一台服务器是它能连核心也装了,然后它的端口有配置出口的话,这个怎么纳管
> 呢?是需要一个专门的工人吗?然后接在哪一个部分呢?还是说就给,都给他干呢?"

> "如果说是已经安装了的,并且服务已经在运行的,这种就要关键的,时时刻刻的注意。
> 就是已经安装,不管它服务有没有起来,只要初始化连接说这个核心已经在,已经有
> 这个软件服务的,那就去找。已经配置的端口"

> "纳管就是要把那些标记在别的端口的那些配置信息,把它搬到我们指定的范围内的
> 端口里面的配置"
> (Claude 注:**后被用户推翻**——见下一条,改为不迁移端口)

> "刚刚我没有考虑到的点是,如果把原有配置搬到我们限定的端口内的话,那用户用户
> 已经在使用的配置,他就用不了了呀。这样子会影响生产环境啊。要不把所,就是限定
> 端口给去掉吧,就直接纳管就好了,这样子就直接 add 进来。"

### 关于端口策略

> "限定端口给去掉吧,就直接纳管就好了,这样子就直接 add 进来。然后空闲端口数
> 的话,就不需要统计了。就是上去之后,我们先我们先限定一些,就是常用的端口,
> 就是已经被特殊定义的端口,就一个完整的合集,看看去哪里找吧。"

> "VPS 上面的可用端口数改成已用端口数,已用的定义就是配置了多少条代理,还正在
> 正常使用的。"

### 关于纳管时 used_port_count 统计 + 内 ping

> "他就去找这台服务器,然后去计算这台配置里面有多少个端口它是能够正常使用的。
> 对。但只要从内聘能聘的通,就代表这个这个代理出口它是能够正常去使用的"

### 关于纳管 IP 反推的到期日 null

> "他不知道这条代理什么时候过期,对吧?那写进表里面的时候,就直接写未知,对,
> 未知,那就知道这个就是已经配置好了纳管的 IP,但是他没有没有没有到期日期,
> 不知道,所以就对,然后需要等用户去那边说续费了什么再说"

> "巡检的时候就直接跳过就行了。"

### 关于内 ping 不通的纳管 IP

> "如果内聘不通,大概率就是,如果内聘不通且这条配置已经在 IP 表里面,那就代表
> 着啊这条 IP 已经过期了,那这个时候就可以标一个标一个日期了吧"

### 关于"获取账号密码工具"

> "IP 那边的话,入口账号密码,其实只要配置里面写了的话,我觉得大概率都是能找到
> 的。那可能就需要有一个获取账号数据密码的一个工具了,因为外部的人写的话,写进
> 的是那个 iP 的那个表里面,就是写到核心的数据库里面,那就需要另外一套获取账号
> 数据密码的工具,而不是从外部去拿了,直接从核心的配置里面去提取这些账号密码
> 信息"
>
> (Claude 注:这个工具不需要新发明,旧代码 `xray/config.py::extract_port_bindings`
>  已经实现这个能力,搬到新 `kits/install_xray/config.py` 即可)

---

## 三、修订历史

- **v1 2026-06-06** 初版。对应 ADR-0001 worker 架构 + ADR-0002 纳管模式
  - 4 个分支(A 全新 / B 已装停了 / C 已装空配 / D 纳管)
  - 纳管 6 步(抠配置 / 内 ping 逐条 / 写 ip_record / 写 proxy_record / 补默认入口 / 验证)
  - 端口策略:不限定 18441-18450,改"排除清单 + 高位随机"
  - 纳管端口不迁移(保护生产)
  - vps.used_port_count 由 XrayWorker 写
  - 纳管 IP 反推 expire_date=null
  - 纳管 IP 内 ping 不通标 is_active=0

- **v2 2026-06-06** 补 §11 §工具清单(用 XrayManager 类视角)
  - 原子工具:XrayManager 类方法(装机/改配置/查测三类) + core/ 通用工具
  - 抠出现有出口配置:标 ⭐ 抠信息类 + 列字段大类 + 实现分支(国家 2 种来源)
  - 工具编排:住 XrayWorker 类内部 _现状判断 / _纳管六步 / _失败分流
  - 取消 kits/ 目录,沿用现有 xray/manager.py(对应 CLAUDE.local.md §1 v3)

- **v3 2026-06-06** §3 由 4 分支简化为 3 分支统一流程(对应 ADR-0003)
  - 旧 4 分支(A/B/C/D)合并为 3 分支(A/B/C) + 统一收尾 7 步
  - 用户原话: "如果没有安装的,那他就从头干到尾,安装、常驻、拉起、配置、检查
    一系列跑通。然后如果安装了,那上去那就是先检查有没有运行,有运行。OK"
  - 用户原话: "ABC 全都查配置纳管"(纳管不再是特殊场景,是所有路径必经)
  - 改动: §3 整段重写;§10 边界情况关于"分支 D"的条目应解读为"统一收尾 ②~⑤"
