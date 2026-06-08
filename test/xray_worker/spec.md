# XrayWorker 行为规约（spec.md）

**版本**: v5.1（2026-06-08）
**模块**: `workers/xray_worker.py`
**类型**: 异步 task 工人
**对应 ADR**:
- `docs/adr/0001-workers-replace-services.md`（worker 架构）
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md`（纳管端口不迁移）
- `docs/adr/0003-xray-worker-three-branches-unified-tail.md`（3 分支 + 统一收尾）
- `docs/adr/0004-xray-worker-flow-refinements.md`（**本版关键依据**：分支 B/C 补自启、端口让步、直进直出判定、内 ping 不通 remove）

---

## 一、整理后的要点

### 1. 工人定位

XrayWorker 是 VPS 装机和纳管工人:

- 异步消费 `vps_task`。
- 抢到任务后 SSH 进入目标 VPS。
- 根据 xray 现状走 3 个前置分支。
- 无论哪个分支, 最后都执行统一收尾。
- 成功后把 `vps_record.stage` 升级为 `running`。
- 只有 XrayWorker 能把 VPS 标成 `running`。

### 2. 入口契约

**触发**: 扫 `vps_task` 中到期可执行的任务。

**抢锁**:

```sql
UPDATE vps_task
   SET status='in_progress',
       worker_id='<本 worker 标识>',
       locked_until=now + 5min
 WHERE id=? AND status IN ('pending', 'pending_retry')
```

影响行数:

- `1`: 抢到任务。
- `0`: 已被别人抢走, 换下一条。

**输入**: `vps_task.vps_id` 指向的 `vps_record`。

**输出**:

- 成功: `task.status='done'`, `vps_record.stage='running'`。
- 失败: `task.status='failed'` 或 `'pending_retry'` 或 `'circuit_broken'`, `last_error_code` / `last_error_msg` 写任务表。
- 纳管时可能写入 `ip_record` 和 `proxy_record`。

### 3. 三个前置分支

抢到 task 后, XrayWorker 先判断 xray 现状, 决定前置动作。

#### 分支 A: xray 未安装（`xray_version` 空 或 `is_installed()` False）

1. `install()`
2. **如果 `is_config_blank()` 为 True → `write_default_config()`** ⭐ v5.1 补漏（裸装完 config 为空, 后续 `start()` 会 exit=23, 详见 [[xray.manager.ensure_installed_and_running]] 注释）
3. `start()`
4. `enable()`
5. `version()` 验证版本号非空
6. `is_running()` 验证服务已运行
7. 进入统一收尾

#### 分支 B: xray 已安装但未运行

1. **`is_enabled()` 看有没有设自启, 没设就 `enable()`** ⭐ v5 新增（ADR-0004 §1）
2. `start()`
3. `is_running()` 验证服务已运行
4. 进入统一收尾

#### 分支 C: xray 已安装且正在运行

1. **`is_enabled()` 看有没有设自启, 没设就 `enable()`** ⭐ v5 新增（ADR-0004 §1）
2. 不改服务状态
3. 进入统一收尾

**理由**: xray 进程在跑不代表设过 enable。VPS 重启后 xray 不自启 = 业务断流。补"看自启"是闭环必需。

### 4. 统一收尾

所有分支都必须执行统一收尾。统一收尾负责读取现有配置、判定/确保有直进直出入口、纳管已有代理出口、清理废条目、重载并验证 xray。

**步骤**:

1. `read_config()` 读取当前 xray 配置。

2. 扫配置, 把每条 inbound 按路由表关联到对应 outbound, 按 outbound 协议分两类:
   - **直进直出**(inbound 协议 `socks5` + 路由到 outbound 协议 `freedom`): 不入表, 不纳管, 只用来确认默认入口存在
   - **代理出口**(其他, 主要是路由到 `socks` outbound): 走纳管流程

3. **确保至少存在一条"直进直出" inbound**（ADR-0004 §2 §3）:
   - 配置里已有任何"直进直出"条目 → 借用现有的, **不补新的**
   - 配置里没有"直进直出" → 用 `add_proxy_binding` 加一条 socks5 noauth → freedom 三件套, 端口按**让步算法**：
     ```
     首选 18440 → 被占试 18439 → 18438 → ... → 下限 1024（不进 well-known 段）
     一路降到 1024 仍被占 → 任务失败 (last_error_code='no_default_port')
     ```
     - "被占" = 该端口已被任何 inbound 监听（不管是 freedom 还是其他）
     - 新加的"直进直出" inbound: 监听 `0.0.0.0`, 协议 socks5, noauth
     - 默认入口端口**不**进防火墙豁免列表（防扫描滥用）

4. 对**代理出口**类条目逐条处理:

   ```
   for each 代理出口 in 配置:
       ok, egress_ip = toolbox.proxy_check.test_internal(   # v5.1: 返 (bool, egress_ip)
           client, vps_port, inbound_user, inbound_pwd
       )
       # egress_ip = curl 通过该 inbound 访问 api.ipify.org 看到的真实出口 IP
       # 业务上: 上游 IP(entry_host) ≠ 出口 IP(egress_ip), 必须实测
       ├─ 通 → 走"纳管入库":
       │     · 用 lookup_egress(egress_ip) 查出口归属国家
       │     · 写 ip_record (upsert by egress_ip, expire_date=NULL, is_active=1
       │       入口 4 字段 entry_host/port/username/password ← 抠出来的上游凭据
       │       出口字段 egress_ip ← 内 ping 反推的真出口
       │       country_* ← lookup_egress 结果)
       │     · 写 proxy_record (vps_port 原样保留, status='using',
       │       egress_ip / egress_country 同步 ip_record)
       │
       └─ 不通 → 走"清理 remove":
             · 调 remove_proxy_binding(vps_port) 删三件套
             · ip_record / proxy_record **不记任何东西**
             · 共享 outbound 场景: 工具自动判断
                 - 该 outbound 还被剩下的路由引用 → 保留 outbound
                 - 不再被引用 → 工具连带删 outbound
             · 已知风险: 共享 outbound 的所有 inbound 同时配错账密 → 整组误删 (ADR-0004 §5)
   ```

5. `upload_config()` → `validate_config()` → `reload()`。

6. `is_running()` 验证 reload 后服务仍在运行。

**成功出口**:

```python
{
    "stage": "running",
    "task_status": "done",
    "xray_version": "<actual version>",
    "default_inbound_port": <实际占用的默认入口端口, 18440 或让步后的值>,
    "used_port_count": <纳管入库的"代理出口"数, 即内 ping 通的条数>,
}
```

⭐ v5.1 注: `default_inbound_port` **不写入 VPSRecord schema**（schema 没有此字段, 加字段在 T-07 范围外）, 只放在 worker 内部 tail_result 返回值里, 用于日志 + 调用方观测。需要长期持有时, 直接查 xray 配置 + 按 `_classify` 思路抠出 socks5→freedom 那条 inbound 的 port。

### 5. 端口规则

- **纳管已有"代理出口"端口**: 原端口保持不动（ADR-0002 §2）。
- **默认入口端口**: 按"让步算法"确定（首选 18440, 被占降 1, 下限 1024）。
- **默认入口监听 `0.0.0.0` + noauth**, 防扫描靠"防火墙不放行端口"兜（ADR-0004 §6）。
- 默认入口不算代理节点资产, **不写 `proxy_record`, 不写 `ip_record`**。
- 后续 ProxyDeployWorker 新分配代理端口时, 按项目排除清单 + 已用端口（含默认入口端口 + 所有 proxy_record 中端口）避让。

### 6. used_port_count

`vps_record.used_port_count` = 这台 VPS 上**纳管入库的代理出口数**(即统一收尾中内 ping 通的"代理出口"条数)。

```text
used_port_count = proxy_record 中本 VPS status='using' 的条数
```

- 没纳管出口 / 全不通 remove 干净 → 值为 `0`
- **不包含**默认入口("直进直出"不计入)

### 7. 失败处理

失败信息写 `vps_task`。⭐ v5.1 修订: 严格对齐 `db/models.py::TaskStatus` 4 值真相,删除字面上不存在的 `pending_retry` / `circuit_broken` 描述, 改为 worker 内部管理 retry_count + next_run_at, 重试时 task.status 回写 `pending`。

| 场景 | task.status | last_error_code | 字段配合 |
|---|---|---|---|
| 抢到 task | `in_progress` | — | worker_id, locked_until=now+5min |
| 成功 | `done` | — | completed_at=now, locked_until=NULL, worker_id="" |
| SSH 连接失败（账密类） | `failed`（不可重试） | `auth_denied` | locked_until=NULL |
| SSH 连接失败（网络类临时, retry_count < 5） | `pending`（回炉等下次扫到） | `ssh_timeout` / `ssh_refused` | retry_count+1, next_run_at=now+2^retry 分钟(上限 60), locked_until=NULL |
| 安装/启动/自启/验证失败（临时, retry_count < 5） | `pending`（回炉） | 具体阶段（`install_failed` / `service_not_active` / `verify_failed` 等） | 同上 |
| 同一/累计 retry_count >= 5（相当于"熔断"） | `failed`（终态） | 沿用最后一次 last_error_code | locked_until=NULL |
| 默认入口端口让步降到 1024 仍占满 | `failed`（不可重试） | `no_default_port` | locked_until=NULL |
| 纳管某条出口配置畸形（缺字段、JSON 坏） | 跳过该条继续, 记 warning 日志 | — | — |
| 配置读取、写入、校验、重载失败 | `failed` 或 `pending`（看 error 性质） | 写具体阶段到 `last_error_msg` | — |

注: spec v5 原表写 `pending_retry` / `circuit_broken` 是 v4 拍板"TaskStatus 只 4 值"之前的笔误。worker 内部的"退避重试" / "熔断"靠 retry_count + next_run_at 实现, task.status 字面只有 4 值。

### 8. 不做的事

XrayWorker 不做:

- 不迁移已有"代理出口"端口（ADR-0002 §2）。
- 不主动删除已有 inbound, **唯一例外（ADR-0004 §4）**:
  > 统一收尾纳管时, 某条"代理出口"内 ping 不通 → 调 `remove_proxy_binding(vps_port)` 删三件套。共享 outbound 场景工具自动保留还在被引用的 outbound。
- 不主动创建 `ip_task`。
- 不做外部巡检。
- 不在用户没提交 rgip 时主动部署新代理出口。

### 9. 不变量

跑完 XrayWorker 后必须满足:

- `task.status='done'` 时, `vps.stage='running'`。
- `vps.stage='running'` 时, xray 服务确认在跑。
- `vps.stage='running'` 时, xray 配置里**至少有一条 socks5 + 路由到 freedom 的 inbound**（端口优先 18440, 让步后可能是 18439/18438/...，记录在 `default_inbound_port`）。
- `used_port_count` 等于本 VPS `proxy_record` 中 `status='using'` 的条数。
- 纳管 `ip_record` 的 `expire_date` 为 `NULL`。
- 默认入口（socks5→freedom 那条）**不写入 `proxy_record`, 不写入 `ip_record`**。
- 内 ping 不通的"代理出口"在 xray 配置里**不留痕**（已被 `remove_proxy_binding` 删干净）, ip/proxy 表也**不留痕**。

### 10. 边界情况

| 情况 | 期望行为 |
|---|---|
| 抢锁失败 | 不处理该任务, 换下一条 |
| 进程中途退出 | 锁过期后由后续 worker 接手, 重头跑（流程每步幂等设计） |
| 配置里没有代理出口 | 跳过纳管处理, 仍执行 §3 步骤 3 确保默认入口存在 + reload |
| 某条 inbound 找不到对应路由 / outbound | 跳过该条, 记 warning, 继续处理其他条 |
| 同一上游被挂在多个端口 (共享 outbound) | 逐条内 ping → 通的纳管入库, 不通的 remove_proxy_binding（工具自动兜底 outbound 引用计数）|
| 别人挂着的 18440 上游代理 | 让步到 18439/18438/...，不抢别人 |
| 配置里有 socks5→freedom + 端口非 18440 | 借用它当默认入口, 不补新的 |
| 默认入口让步降到 1024 仍占满 | 任务失败 `no_default_port`, 等人介入 |

---

## 二、工具清单

### A. XrayManager 方法（`xray/manager.py`）

XrayWorker 用 XrayManager 包装的 SSH client 操作 xray 服务:

- 服务管理:
  - `install()` / `start()` / `enable()` / `is_running()` / `version()`
  - `is_enabled()` ⭐ v5 新增（ADR-0004 §1 引出）
- 配置管理:
  - `read_config()` / `upload_config()` / `validate_config()` / `reload()`
- 纳管相关:
  - `extract_existing_outbounds()` —— 抠出现有"出口"条目（区分直进直出 vs 代理出口）

### B. xray/config.py 配置层底层函数（XrayWorker 直接调或间接通过 XrayManager 调）

- `add_proxy_binding(...)` —— 加三件套 inbound + outbound + 路由（同时用于：加默认入口 socks5→freedom, 以及未来 ProxyDeployWorker 加代理出口 socks5→socks）
- `remove_proxy_binding(vps_port)` —— 删三件套, 智能处理共享 outbound

### C. toolbox 通用工具（`toolbox/`）

- `proxy_check.test_internal(client, port, user="", pwd="")` → `tuple[bool, str]` —— **内 ping + 顺手拿出口 IP** ⭐ v5.1 升级（T-07 实施时把 T-08 落定的 `-> bool` 升级为 `(bool, egress_ip)`, 通时 egress_ip 是 curl ipify 回显的真实出口 IP, 不通时空串）。第一参数是 `paramiko.SSHClient` 不是 `host`(v5 原描述笔误, v5.1 更正)。
- `proxy_check.test_external(host, port, user="", pwd="")` → `bool` —— **外 ping**（从外部探测 inbound, 给 ProxyDeployWorker 用）
- `geoip.lookup_egress(ip)` —— **pingIP**（查上游 IP 出口国家）, 现有, 直接用

### D. 工人内部私有编排

按 [[feedback-工具编排发现式抽取]] 原则: 私有编排 **住在工人 .py 内部**, 命名按实现者方便, spec 不强制锁死方法名。

只有跨多个 worker 重复出现的编排, 才往上抽到 XrayManager 类或 toolbox。

---

## 三、修订历史

- v5.1 2026-06-08: T-07 实施时盘点既有代码 + 字段事实, 补 4 处:
  - §3 分支 A 步骤补 ② `write_default_config()`（裸装完 config 为空, start 会 exit=23）+ ⑥ `is_running()` 验证
  - §4 步骤 4 改用 `test_internal(client, port, user, pwd) → (bool, egress_ip)` 签名（v5.1 升级 toolbox 内 ping 工具）
  - §4 成功出口加注: `default_inbound_port` **不写入 VPSRecord schema**（schema 无此字段, 加字段在 T-07 范围外）, 仅在 tail_result 返回值
  - §7 失败处理表对齐 `db/models.py::TaskStatus` 4 值真相（删 `pending_retry` / `circuit_broken` 字面）, 重试靠 retry_count + next_run_at + status=`pending` 回炉
  - §二 C `test_internal` 签名描述同步升级 + 修正第一参数是 client 而非 host

- v5 2026-06-07: 落 ADR-0004 决策。
  - §3 分支 B/C 各补"看自启没设就设"前置步
  - §4 统一收尾重写: 加"直进直出 vs 代理出口"分类、加默认入口让步算法、改"内 ping 不通"处理为 remove 三件套不记表
  - §5 端口规则改: 取消固定 18440, 改让步算法, 明定监听 0.0.0.0 + noauth
  - §7 失败处理表细化: 加 `no_default_port` / `pending_retry` / `circuit_broken` 等
  - §8 加"内 ping 不通时 remove 三件套"唯一例外
  - §9 不变量改: 18440 → "至少一条 socks5→freedom inbound, 端口按让步规则, 记在 `default_inbound_port`"
  - §10 边界情况补"18440 被占""降到 1024 全占"等
  - §二 工具清单细化: 加 `is_enabled()`, 加 `add_proxy_binding` / `remove_proxy_binding` 引用位置, 加 toolbox 的 `test_internal` / `test_external` / `lookup_egress`
  - §二 D 工人私有编排放宽: 不强制锁方法名, 按"工具编排发现式抽取"原则只锁原子工具

- v4 2026-06-07: 当前 `test/` 目录口径。规约只保留当前 3 分支 + 统一收尾行为, task 状态对齐当前 `TaskStatus`。
