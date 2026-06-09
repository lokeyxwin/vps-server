# ProxyDeployWorker 行为规约（spec.md）

**版本**: v1（2026-06-09 初版）
**模块**: `workers/proxy_deploy_worker.py`
**类型**: 异步 task 工人
**对应 ADR**:
- `docs/adr/0001-workers-replace-services.md`（worker 架构）
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md` §3（端口排除清单 + 高位随机）
- `docs/adr/0005-vps-stage-as-resource-lock.md`（vps.stage 资源锁, task 是并发锁, 两层分离）
- `docs/adr/0006-proxy-deploy-worker.md`（**本 spec 主依据**: 挑机 / 端口 / 收尾 / status 三档）

---

## 一、整理后的要点

### 1. 工人定位

ProxyDeployWorker 是把已登记的上游 IP **真正挂到一台生产 VPS 上**对外开 socks5 入口的工人。

- 异步消费 `ip_task`（IPProbeWorker 同步段验证 IP 通过后建 pending）
- **抢到 task → 立刻挑 VPS → 同事务把 `vps.stage` 标 `running`**（占资源锁, ADR-0005 §1）
- SSH 进入 VPS, 配 xray inbound+outbound 三件套 + 防火墙放行
- 内 ping + 外 ping 验证
- **成功后 `vps.stage` 释放回 `connectable`**, 让别的工人/业务能再用这台
- **失败时 `vps.stage` 保持 `running`**（锁住等"维修工人"或人工介入, ADR-0005 §3）

不面向用户。agent / 用户通过查询工具看 `ip_task.status` 看进度（查询工具归下一波 MCP ADR）。

### 2. 入口契约

**触发**: 扫 `ip_task` 中到期可执行的任务。

**抢锁**:

```sql
UPDATE ip_task
   SET status='in_progress',
       worker_id='<本 worker 标识>',
       locked_until=now + 5min
 WHERE id=? AND status IN ('pending', 'pending_retry')
```

影响行数:
- `1`: 抢到任务
- `0`: 已被别人抢走, 换下一条

**输入**: `ip_task.ip_id` 指向的 `ip_record`（含上游凭据）。`ip_task.vps_id` 此时为 NULL（建任务时还不知道挂哪台）。

**输出**:
- 成功: `task.status='done'`, `vps.stage='connectable'`（资源锁释放）, 新增一行 `proxy_record`
- 失败: `task.status='failed'`, `last_error_code` 标准化, `vps.stage` 保持 `running`（若已抢机）

### 3. 业务主流程（6 步）

```
步骤 1: 抢 task
   ↓
步骤 2: 挑一台 VPS（4 条件 + 最闲优先）
   ├─ 找到 → 同事务两写: vps.stage='running' + ip_task.vps_id=<vps.id>
   └─ 找不到 → 任务 failed + last_error_code='no_vps_capacity'（终态, 不重试）
   ↓
步骤 3: 挑一个端口（排除清单 + 高位随机, SSH 进 VPS 后做）
   ├─ 找到 → 继续
   └─ 候选池空 → 任务 failed + last_error_code='no_port_available'（终态, 不重试）
   ↓
步骤 4: 配上线
   - XrayManager.apply_proxy_binding(vps_port, user, pwd, upstream_host, upstream_port, upstream_user, upstream_pwd)
   - toolbox.firewall.open_tcp_port_range(client, vps_port, vps_port)
   ↓
步骤 5: 验证（两次 ping）
   - 内 ping: toolbox.proxy_check.test_internal(client, vps_port, user, pwd)
   │   ├─ 不通 → 立刻 XrayManager.rollback_proxy_binding(vps_port, last_config)
   │   │       → 任务 failed + last_error_code='inner_ping_failed'
   │   │       → vps.stage 保持 running
   │   │       → 终态
   │   └─ 通  → 继续
   - 外 ping: toolbox.proxy_check.test_external(vps_ip, vps_port, user, pwd)
   │   ├─ 通  → status='using'
   │   └─ 不通 → status='pending_fw'
   ↓
步骤 6: 收尾（同事务一次性写, 详见 §6）
```

### 4. 步骤 2 详细 —— 挑机算法

**SQL**:

```sql
SELECT * FROM vps_record
 WHERE stage='connectable'              -- 没工人锁着
   AND xray_version != ''               -- 装好 xray
   AND is_active = 1                    -- 在保
   AND used_port_count < :MAX_PORTS_PER_VPS
 ORDER BY used_port_count ASC,
          RANDOM()                       -- 最闲优先, 同档随机
 LIMIT 1
 FOR UPDATE                              -- 防并发抢同一台
```

**容量阈值**: `config.py::MAX_PORTS_PER_VPS = 3`（ADR-0006 §3, 业务参数, 可调）。

**抢机时序**（同事务）:

```python
with session_scope() as s:
    vps = s.execute(挑机 SQL).first()
    if vps is None:
        # 没机, 走 failed 分支
        ...
        return
    vps.stage = VPSStage.RUNNING
    task.vps_id = vps.id
    # commit
```

**关键不变量**: `vps.stage='running'` 和 `ip_task.vps_id=vps.id` **必须同事务**, 不允许拆两个 commit（否则锁状态漂移）。

### 5. 步骤 3 详细 —— 挑端口算法

```python
used = toolbox.ports.get_used_ports(client, 1024, 65535)
# 已用端口集合 = 该 VPS 真实在监听的所有端口（含 18440 默认入口 + 已挂代理 + 系统服务）

exclude = (
    toolbox.ports.COMMON_RESERVED_PORTS    # 0-1023 well-known + 常用应用
    | {config.XRAY_DEFAULT_PORT}            # 18440 留给 xray 默认入口
    | set(已查 proxy_record.vps_port WHERE vps_id=:vps.id AND status='using')
)

available = toolbox.ports.compute_available_ports(used, 1024, 65535, exclude=exclude)

if not available:
    # 候选池空, 走 failed 分支
    ...
    return

vps_port = random.choice(list(available))  # 高位随机
```

**注**: `COMMON_RESERVED_PORTS` 是 toolbox/ports.py 已有的常量, 对应 ADR-0006 §6 的 `EXCLUDED_PORTS` 概念, 直接复用（不另起新常量名, 避免双轨）。

### 6. 步骤 6 详细 —— 收尾 DB 写入

**成功路径**（内 ping 通）, 同事务一次写全:

```python
# inbound 账密生成规则 (需求窗口拍板 2026-06-09, v1.1 写入 spec):
#   inbound_user = f"proxy_{ip.id}"   (例 ip_id=42 → "proxy_42", 排障时一眼看出挂的哪条 IP)
#   inbound_pwd  = uuid4().hex        (32 字符随机, 不可猜)

with session_scope() as s:
    # proxy_record: INSERT 新行
    proxy = ProxyRecord.from_new_deployment(
        vps_id=vps.id,
        vps_port=vps_port,
        ip_id=ip.id,
        inbound_user=f"proxy_{ip.id}",
        inbound_pwd=uuid4().hex,
        upstream_host=ip.entry_host,
        egress_ip=ip.egress_ip,
        egress_country=ip.country_code,
        protocol='socks5',
    )
    proxy.status = ProxyStatus.USING if 外通 else ProxyStatus.PENDING_FW
    s.add(proxy)

    # ip_record: usable → using
    ip.status = IPStatus.USING

    # vps: used_port_count +1, 释放资源锁
    vps.used_port_count += 1
    vps.stage = VPSStage.CONNECTABLE

    # ip_task: in_progress → done
    task.status = TaskStatus.DONE
    task.completed_at = func.now()
```

**关键边界**: 外 ping 不通**不算工人失败**, 仍走 done 路径, 仅 status 字段标 `pending_fw`。

### 7. 失败分支汇总

| 触发 | `task.status` | `last_error_code` | `vps.stage` | xray 配置 | 重试? |
|------|--------------|-------------------|-------------|----------|------|
| 没机可挑 | `failed` | `no_vps_capacity` | （没抢机, 不动）| 没碰过 | ❌ 终态 |
| 挑机后端口池空 | `failed` | `no_port_available` | 保持 `running` | 没碰过 | ❌ 终态 |
| 配上线失败（xray apply 报错）| `failed` | `apply_binding_failed` | 保持 `running` | 已回滚 | ❌ 终态 |
| 防火墙放行失败 | `failed` | `firewall_open_failed` | 保持 `running` | 已回滚 | ❌ 终态 |
| 内 ping 不通 | `failed` | `inner_ping_failed` | 保持 `running` | **已 rollback 三件套** | ❌ 终态 |
| SSH 中途断 | `pending_retry` 后失败终态 | `ssh_disconnected` | 保持 `running` | 状态不可知 | ✅ 内部重试 N 次 |

**统一规则**:
- 任何失败都不删 xray 配置以外的 DB 写入（因为还没写入）
- 失败时 `vps.stage` 不释放, 等维修工人/人工
- "没机"和"端口池空"是**结构性容量问题**, 不退避重试
- "SSH 断"才走标准 retry_count + next_run_at 退避

### 8. 边界 —— 不归本工人的事

- ❌ **外部安全策略组**（云厂商面板的防火墙规则）: 工人管不到, 配出 `pending_fw` 状态表达就完工
- ❌ **`proxy_record.status='inactive'` 的写入**: 归未来封存的 ExpiryWorker / CleanupWorker
- ❌ **`ip_record` 字段更新**（除 status usable→using 外）: 不动其他字段
- ❌ **task 失败信息怎么对外暴露**: 归下一波 MCP 查询工具 ADR

### 9. 不变量

1. **抢机两写同事务**: `vps.stage='running'` 和 `ip_task.vps_id` 必须在同一 commit 里
2. **失败 stage 不释放**: 工人失败时永不主动改 `vps.stage`
3. **内 ping 不通 = 三件套 rollback**: 不留废配置占端口（与 ADR-0004 §4 姿态一致）
4. **外 ping 不通 ≠ 失败**: 走 done 路径, status 标 `pending_fw`
5. **`used_port_count += 1` 只在 done 时发生**: 失败一律不 +1（即使 xray 配置短暂存在过, rollback 后端口实际没占）
6. **`MAX_PORTS_PER_VPS` 是软上限**: 业务规模变了直接改 `config.py` 一处, 工人代码不动

---

## §工具清单（实现者按此清单造/复用, 不另立位置）

### A. 原子工具（toolbox / xray.manager 已有, 直接用）

| 工具 | 大白话功能 | 位置 | 状态 |
|------|----------|------|------|
| 查 VPS 已用端口集 | SSH 上去 ss/netstat 看一段端口里哪些在监听 | `toolbox/ports.py::get_used_ports` | ✅ 已有 |
| 推算可用端口集 | 区间 - 已用 - 排除清单 = 可用集 | `toolbox/ports.py::compute_available_ports` | ✅ 已有 |
| 常用端口排除清单 | well-known + 常见应用端口 frozen set | `toolbox/ports.py::COMMON_RESERVED_PORTS` | ✅ 已有（即 ADR-0006 §6 的 EXCLUDED_PORTS）|
| 放行 TCP 端口（本地防火墙）| firewalld / ufw 加 inbound 规则 | `toolbox/firewall.py::open_tcp_port_range` | ✅ 已有（传 `(port,port)` 当单端口）|
| 内 ping socks5 代理 | 在 VPS 本机走 localhost 测 inbound 通不通, 返 `(ok, egress_ip)` | `toolbox/proxy_check.py::test_internal` | ✅ 已有 |
| 外 ping socks5 代理 | 从我们后端打 VPS_IP:port 测整链路 | `toolbox/proxy_check.py::test_external` | ✅ 已有 |
| 加 xray 代理三件套 | inbound + outbound + 路由三件套一次加 | `xray/manager.py::XrayManager.apply_proxy_binding` | ✅ 已有 |
| 回滚 xray 代理三件套 | 撤销刚加的 inbound + outbound + 路由 | `xray/manager.py::XrayManager.rollback_proxy_binding` | ✅ 已有 |

### B. 工具编排（工人内部私有, 不抽出来）

按 §2.7 "复用 + 外部 + 多步" 三选不齐 → 不抽公共工具, 全住 `workers/proxy_deploy_worker.py` 内部 `_xxx()` 私有方法:

| 编排 | 大白话 | 步骤 |
|------|--------|------|
| `_pick_vps()` | 挑一台 VPS, 同事务抢资源锁 | 走 §4 挑机 SQL + UPDATE stage + UPDATE task.vps_id |
| `_pick_port(client, vps)` | 在 VPS 上挑高位随机端口 | 走 §5 端口算法 |
| `_deploy_one_binding(...)` | 配上线 + 防火墙 + 内 ping + 外 ping | 走 §3 步骤 4-5 |
| `_mark_done(...)` | 成功收尾（含 stage 释放）| 走 §6 |
| `_mark_failed(error_code, error_msg)` | 失败收尾（stage 不释放）| 走 §7 |

---

## 二、用户口述原话（金标准, 审查时翻这里）

> "可以用B方案, 你给我的状态机来写, 你第三个字段我后续工人会用得到, IPtask干完就done 外部安全组问题这个表不承担了(划清边界) 任务完成配置上去 内ping通, 防火墙放行剩外部问题 vps的已用+1"
> —— 引出 §3 步骤 6 + §6 收尾 + §8 边界

> "status: str 三档枚举(B 方案): using / pending_fw / inactive 我说的会用到的是枚举字段 不是你说的那个 message 吧"
> —— 引出 §6 status 字段写入 + ADR-0006 §7 三档枚举

> "逻辑对了 你先给ADR和spec吧 然后我们再探讨一下没有可用服务器的情况 怎么把信息传出去"
> —— 确认 §7 "没机直接 failed 不重试" + §8 失败暴露归下一波 MCP

> "保持IP和VPS的task颗粒度一致?"
> —— 引出后续 MCP 查询工具 ADR（本 spec 范围外）

(对话场景: 2026-06-09 跟用户对齐 ProxyDeployWorker 业务故事时, 用户认可"挑机 → 挑端口 → 配上线 → 两次 ping → 收尾"6 步逻辑, 拍板 ⭐1~⭐5 决策点, 让我先落 ADR 和 spec, "没机怎么把信息传出去"留到下一波讨论。)

---

## 三、修订历史

- v1 2026-06-09 初版（对应 ADR-0006 落地）
- v1.1 2026-06-09 §6 inbound 账密生成规则敲定（user=f"proxy_{ip.id}", pwd=uuid4().hex），跟 T-16 实现 + TC 同 commit 落
