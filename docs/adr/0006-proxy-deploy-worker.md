# 0006. ProxyDeployWorker 工人形态 + 挑机/端口/验证/收尾流程

**日期**: 2026-06-09
**状态**: Accepted

---

## Supersedes / 补充

- 补充(非推翻) [[0001-workers-replace-services]] §决策 §1 工人清单
  → 原 ADR 只点名了 ProxyDeployWorker, 本 ADR 第一次定义它的完整行为
- 补充(非推翻) [[0002-takeover-mode-handled-by-xray-worker]] §3 端口策略
  → 本 ADR 把"排除清单 + 高位随机"具体落到 ProxyDeployWorker 挑端口算法
- 补充(非推翻) [[0005-vps-stage-as-resource-lock]] §1/§4
  → 本 ADR 是**第一个真正消费 `stage='connectable'`** 的工人, 落实挑机 SQL

> 注: 被本 ADR 补充的旧 ADR 本身不动(永不改原则); 当下真相以本 ADR + spec.md 为准。

---

## 背景

XrayWorker(T-07~T-09)完工后, 生产 VPS 池真正攒起来了 —— 装好 xray、自启设好、
默认入口存在、`stage='connectable'` 的机器排着队等业务。

下一步业务: 把 IPProbeWorker 已经登记入库的上游 IP 凭据**真正挂到一台生产 VPS 上**,
对外暴露一个带账密的 socks5 入口让客户端用。这就是 ProxyDeployWorker 的活。

本 ADR 把工人形态 / 挑机算法 / 挑端口算法 / 配上线 / 两次 ping 验证 / 收尾流程
**一次定清楚**, 不留含糊地带。

---

## 决策

### 1. 工人定位

- **消费哪张表**: `ip_task`(IPProbeWorker 同步段验证 IP 通过后建 pending)
- **干啥**: 把已登记的上游 IP 挂到一台合适的生产 VPS 上当 socks5 outbound, 对外暴露一个带账密的 socks5 inbound 端口
- **不面向用户**: 全异步, agent / 用户不直接调它, 通过查询工具看状态
- **机制**: 跟 XrayWorker 对称 —— 定时扫表 + 软锁 + retry_count + next_run_at 自管退避

### 2. 挑机算法 —— 4 条件 + 最闲优先

挑一台同时满足以下 4 个条件的 VPS:

| 条件 | 大白话 | SQL 表达 |
|------|--------|---------|
| 装好 xray | xray 已经跑起来了 | `xray_version != ''` |
| 还在保 | 用户买的 VPS 没过期 | `is_active = 1` |
| 没人正在用 | 没别的工人锁着 | `stage = 'connectable'` |
| 还有挂代理额度 | 没满 | `used_port_count < MAX_PORTS_PER_VPS` |

**排序**: `ORDER BY used_port_count ASC, RANDOM()`
**取一条**: `LIMIT 1`

理由: 最闲优先, 避免把鸡蛋堆一篮子(单机宕一切断); 同档随机打散并发竞争。

### 3. 容量阈值 `MAX_PORTS_PER_VPS = 3`

`config.py` 新增常量:

```python
MAX_PORTS_PER_VPS = 3  # 一台 VPS 最多挂几条业务代理(经验值, 可调)
```

**理由**: 业务规模小, 单机挂多了带宽 / xray 负载 / 故障半径都不划算。3 是用户拍的起步值, 后续真实负载下不够再调。

### 4. 抢机时序 —— 同事务两写

挑中 VPS 后, **同一个 DB 事务里**:

```
UPDATE vps_record SET stage='running' WHERE id=<vps.id>;
UPDATE ip_task SET vps_id=<vps.id> WHERE id=<task.id>;
COMMIT;
```

理由:
- 写 `vps.stage='running'` = 抢资源锁(ADR-0005 §1), 其他工人 / 其他业务部门挑机时跳过
- 回填 `ip_task.vps_id` = 后续 Reconcile / 失败追溯都能查"这条 IP 当时挂的哪台"
- 同事务保证原子性: 要么都成功, 要么都不动

### 5. 没机怎么办 —— 直接 failed, 不重试

挑机 SQL 返回 0 行 → 任务**直接终态**:

```
ip_task.status = 'failed'
ip_task.last_error_code = 'no_vps_capacity'
ip_task.last_error_msg  = (按业务模板填, 不在本 ADR 定文案)
```

**关键决策**: **不进 pending_retry 循环, 不退避重试**。

理由:
- "没机"是**结构性容量缺口**, 不是网络抖动 —— 用户得加机器 / 退订过期机 / 调高 `MAX_PORTS_PER_VPS`
- 退避重试只会堆积失败任务, 给运维制造噪音
- 用户面通过查询工具看到"failed + no_vps_capacity" → 自己判断怎么处理

(具体怎么把这个失败信息暴露给 agent / 用户, 由下一波 MCP 查询工具 ADR 单独定。)

### 6. 挑端口算法 —— 排除清单 + 高位随机

在选定的 VPS 上挑一个出口端口:

```
候选池 = range(1024, 65536)
       - EXCLUDED_PORTS                     (config.py 常用端口排除清单)
       - {XRAY_DEFAULT_PORT}                (18440 留给 xray 默认入口)
       - {该 VPS 已用端口}                   (SELECT vps_port FROM proxy_record
                                            WHERE vps_id=<vps.id> AND status='using')
随机挑一个
```

**理由**: 落实 ADR-0002 §3 "排除清单 + 高位随机", 不复用 18441-18450 段限定(已被 ADR-0002 取消)。

**新依赖**: `config.py::EXCLUDED_PORTS` —— ADR-0002 §3 提过但代码里没真正建出来, 本 ADR 顺带补落地(初稿: 0-1023 well-known 留排除清单注释提一句, 真实初稿列表见 spec.md / 后续 task)。

### 7. proxy_record.status 三档枚举(改名 + 加值)

**当前** `db/models.py::ProxyStatus` 只有 2 档:

```python
USING   = "using"
EXPIRED = "expired"
```

**改后 3 档**:

```python
USING       = "using"        # 内通 + 外通, 完全可用
PENDING_FW  = "pending_fw"   # 内通但外不通, 等用户去 VPS 放行端口/安全组
INACTIVE    = "inactive"     # 上游过期 / 主动停用 (吸收旧 EXPIRED 语义)
```

**写入归属**:

| 状态 | 谁写 | 时机 |
|------|------|------|
| `using` | ProxyDeployWorker | 收尾时, 外 ping 通 |
| `pending_fw` | ProxyDeployWorker | 收尾时, 内通外不通 |
| `inactive` | 封存的 ExpiryWorker / CleanupWorker(未来) | 上游过期 / 主动停用 |

**为什么 EXPIRED 改名 INACTIVE**:
- 旧 EXPIRED 注释说"对应 IP 已过期",但实际语义还包括"主动停用 / 资源释放"等场景
- INACTIVE 更通用, 不绑死"过期"原因

**安全**: `proxy_record` 投入业务使用前没有真实数据, 改枚举值不需要数据迁移。

### 8. 收尾流程

```
配上线 (xray add inbound+outbound + 防火墙放行端口)
   ↓
内 ping:
   ├─ 不通 → 立刻拆三件套 (xray remove inbound+outbound)
   │         → ip_task.status='failed' + last_error_code='inner_ping_failed'
   │         → vps.stage='running' 保持锁住 (ADR-0005 §3 失败保持)
   │         → 任务终态
   │
   └─ 通 → 继续外 ping ↓

外 ping:
   ├─ 通    → proxy_record.status = 'using'
   └─ 不通  → proxy_record.status = 'pending_fw'
              (代理配好了, 等用户去云厂商面板放安全策略组)
   ↓
任一外 ping 结果都算"工人完工", 同事务一次性写:
   - proxy_record           INSERT 新行 (含 vps_id / vps_port / ip_id / status)
   - ip_record.status       'usable' → 'using'
   - vps.used_port_count    +1 (半成功也 +1, 因为端口实际占了)
   - vps.stage              'running' → 'connectable' (释放资源锁)
   - ip_task.status         'in_progress' → 'done'
```

**关键边界**: **外 ping 不通不算工人失败**。

理由:
- 外 ping 不通 99% 是用户在云厂商面板(阿里云/腾讯云)的"安全策略组"没放行
- 这事工人管不到(没云厂商 API), 用户得自己去面板点
- 如果"等安全组放行"也算失败, 任务会永远 in_progress 等不到放行 —— 边界不清
- 工人边界是"我把代理配上 + 我能管的本机防火墙开了 = 我完工", 剩下的状态用 `pending_fw` 表达, 让用户/查询工具看到

---

## 备选方案

### 方案 A: 没机不直接 failed, 入队退避重试(被否决)

ip_task 不进失败终态, 改 `next_run_at = now + N 分钟` 重新等机。

**否决理由**:
- 退避重试治不了"机器结构性不够"的根因
- 任务堆积 in_progress / pending, 真实失败信号被淹没
- 用户加机器后, 老任务会自然被新一轮扫描捡起来跑(如果用户重发 rgip 的话), 不需要后台自动重试

### 方案 B: 挑机不看 `used_port_count`, 纯 RANDOM()(被否决)

直接随机一台符合 3 个硬条件的 VPS。

**否决理由**:
- 容易把所有 IP 都堆同一台机, 单机宕掉一切断流
- "最闲优先"是简单有效的负载均衡, 实现成本几乎为零(`ORDER BY` 一个字段)

### 方案 C: status 字段不上 `pending_fw`, 内通就算 `using`(被否决)

简化成 2 档, 内通即 using, 外通信息不进 DB。

**否决理由**:
- 半成功状态丢失, agent / 用户分不清"完全通了" vs "本机通但外面进不来"
- 后续运维"看哪些 IP 等用户放行" 没法查
- 加一档枚举的成本远低于丢失业务信息

### 方案 D: 内 ping 不通时保留废配置, 标 `is_active=0` 等巡检处理(被否决)

跟 ADR-0004 §4 一致处理(纳管时内 ping 不通 remove)。

**否决理由**:
- ADR-0004 §4 已经定了"内 ping 不通 = 这条配置实际已废 → 直接 remove"
- 跨场景保持一致: 纳管和新部署都用同一套姿态, 不留废配置占端口
- 留尾巴的"等巡检"路径价值低(用户不会回头处理疑似过期条目)

---

## 后果

### 好处

- ProxyDeployWorker 行为完全可预期, 没含糊地带
- 第一次真正消费 `vps.stage='connectable'` 资源锁(ADR-0005 闭环验证)
- 半成功状态 `pending_fw` 表达"代理配好等放行", 后续可被查询工具暴露给用户
- 没机直接 failed 让"加机需求"被运维明确看见, 不被退避重试掩盖
- 内 ping 不通立刻 remove 跟 ADR-0004 §4 姿态一致, xray 配置始终干净

### 引入的新约束

- `config.py` 新增两个常量:
  - `MAX_PORTS_PER_VPS = 3`
  - `EXCLUDED_PORTS = {...}`(具体列表见 spec.md / 后续 task, 至少含 0-1023 well-known + 常见应用端口 + 18440)
- `db/models.py::ProxyStatus` 由 2 档改 3 档:
  - `EXPIRED` → `INACTIVE`(吸收语义)
  - 新增 `PENDING_FW`
- `db/models.py::ProxyRecord` 类注释 stale("最多 10 条 / 18441-18450")需同步改成"最多 MAX_PORTS_PER_VPS 条 / 高位随机"
- 新增 `workers/proxy_deploy_worker.py` 实现
- 新增 `test/proxy_deploy_worker/spec.md` 行为规约(本 ADR 同批落)
- ProxyDeployWorker 抢机时序的同事务两写, **必须真在同一 transaction 里**, 不允许拆两个 commit(否则 stage 抢到了但 task.vps_id 没写, 锁状态漂移)

### 风险

- **`MAX_PORTS_PER_VPS = 3` 是经验值**, 真实带宽 / 负载下可能小了或大了
  缓解: 是 `config.py` 常量, 调一处即可
- **没机直接 failed 后用户感知滞后**: 如果用户没主动查 ip_task 状态, 不会知道有任务等加机
  缓解: 由下一波 MCP 查询工具 ADR 单独定 "task 失败信息怎么对外暴露"(本 ADR 范围外)
- **`EXCLUDED_PORTS` 不全**: 随机可能撞到某个真实跑着的服务(如自建 8080)
  缓解: 首版清单宽松一点, 业务跑出来发现哪个端口被占了再补
- **同事务两写在某些场景下被回滚后任务状态不确定**: e.g. ProxyDeployWorker 进程被 kill
  缓解: 软锁 `locked_until` 自动过期机制兜底; spec.md §9 不变量写清楚"task 锁过期后 stage 也跟着失效"由 ExpiryWorker(封存)未来兜

---

## 用户口述原话(关键节选)

> "可以用B方案, 你给我的状态机来写, 你第三个字段我后续工人会用得到, IPtask干完就done 外部安全组问题这个表不承担了(划清边界) 任务完成配置上去 内ping通, 防火墙放行剩外部问题 vps的已用+1"
> (引出 §决策 §7 三档枚举 + §决策 §8 收尾边界)

> "status: str 三档枚举(B 方案): using / pending_fw / inactive 我说的会用到的是枚举字段 不是你说的那个 message 吧"
> (引出 §决策 §7 旧 EXPIRED 改 INACTIVE)

> "逻辑对了 你先给ADR和spec吧 然后我们再探讨一下没有可用服务器的情况 怎么把信息传出去"
> (确认 §决策 §5 没机直接 failed 的姿态, 失败信息暴露归下一波 MCP ADR)

> "保持IP和VPS的task颗粒度一致?"
> (引出后续 MCP 查询工具 ADR: ip_task / vps_task 查询接口要不要对称暴露)

---

## 影响清单(已锁定, 下游 task 落地)

| 文件 | 现状 | 本 ADR 要的改动 | 落地任务单 |
|------|------|---------------|----------|
| `db/models.py::ProxyStatus` (L137-142) | 2 档 USING/EXPIRED, 注释"对应 IP 已过期" | 3 档 using / pending_fw / inactive, 注释重写 | 待建 T-15 |
| `db/models.py::ProxyRecord` 类注释 (L145-158) | "最多 10 条(端口范围决定)" / "18441-18450" 已 stale | 改"最多 MAX_PORTS_PER_VPS 条 / 高位随机避开 EXCLUDED_PORTS" | T-15 |
| `db/models.py::ProxyRecord.status` 默认值 (L194-196) | `default=ProxyStatus.USING` | 不动(新部署默认就是 using, 外 ping 不通时显式改 pending_fw) | — |
| `config.py` (L88 后) | 只有 `XRAY_DEFAULT_PORT = 18440` | 新增 `MAX_PORTS_PER_VPS = 3` + `EXCLUDED_PORTS = {...}` | T-15 |
| `xray/manager.py` | (待 spec 工具清单 §A 落地时查接口现状) | 可能需补防火墙工具方法, 详见 spec.md | T-16 |
| `workers/proxy_deploy_worker.py` | 不存在 | 新建工人, 实现轮廓写在任务单 | T-16 |
| `test/proxy_deploy_worker/spec.md` | 目录在但 spec.md 不存在 | 新建(本 ADR 同批落, 不算独立 task) | — |
| `db/models.py::IPTask` (L532-598) | 已建, `vps_id` nullable 对齐 | 不动(本 ADR 用法跟现状一致) | — |
| `tools/` (MCP 工具层) | 现状未读 | **本 ADR 不动**, "task 失败 / pending_fw 信息怎么对外暴露"归下一波 MCP ADR | — |
