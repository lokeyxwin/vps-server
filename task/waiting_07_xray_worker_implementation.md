# T-07 XrayWorker 实现 (3 分支 + 统一收尾) — v2 对齐 spec v5 + ADR-0004

**ID**: T-07
**前置依赖**:
- T-01 (VPSRecord schema) ✅ committed
- T-02 (vps_task 表) ✅ committed
- T-03 v2 (XrayManager 加 `extract_existing_outbounds` + `is_enabled` 占位)
- T-08 (toolbox.proxy_check.test_internal / test_external 加好)
**后续依赖**: 无 (这是 rgvps 链路装机端的核心)
**复杂度**: 🔴 大(整个项目最复杂的工人)

> **v2 变化**(2026-06-08, 落 ADR-0004 + spec v5):
> - 分支 B/C 都加"看自启没设就设"前置步
> - 统一收尾改: 加"直进直出 vs 代理出口"分类 + 默认入口让步算法 + 内 ping 不通 remove
> - 工具入口换: 内 ping 走 `toolbox.proxy_check.test_internal` (不再走 XrayManager.test_internal)
> - 工具入口换: pingIP 走 `toolbox.geoip.lookup_egress`
> - 私有方法名实现者自决, 不锁死

---

## 验收锚点(按重要顺序)

- `test/xray_worker/spec.md` **v5** §3 / §4 / §5 / §7 / §8 / §9 / §10 — **核心金标准**
- `docs/adr/0004-xray-worker-flow-refinements.md` — 流程修订
- `docs/adr/0003-xray-worker-three-branches-unified-tail.md` §1 — 3 分支基础
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md` §4 §5 — 字段语义、纳管 IP expire_date=null
- `CLAUDE.md` §7 Python 风格(类方法主推,无状态用函数)
- `CLAUDE.local.md` §12 纳管模式

## 改动文件清单

### 新建 `workers/xray_worker.py` (完整实现 class XrayWorker)

```
class XrayWorker:

  def __init__(self): pass

  def run_once(self) -> int:
      """轮询一次: 抢一条 vps_task, 处理完返回 1; 无可领返回 0."""

  def process_task(self, task_id: int) -> None:
      """处理指定 task. SSHWorker 派任务测试时可直接调."""

  # ===== 私有编排 (方法名实现者自决, 这里列业务动作) =====

  · 抢任务 (原子 UPDATE 锁)
  · 现状判断 (返回 'A' / 'B' / 'C')
  · 前置 A (装 + 起 + 自启 + 版本验证)
  · 前置 B (看自启没设就设 + 起 + 验证 is_running) ⭐ v2 加自启
  · 前置 C (看自启没设就设)                          ⭐ v2 加自启
  · 统一收尾 (按 spec v5 §4 6 步: 读 → 分类 → 确保直进直出 → 纳管/remove → upload+reload → verify)
  · 找让步端口 (从 18440 降 1 找空位, 下限 1024)
  · 纳管入库 (通的: lookup_egress + upsert ip_record + write proxy_record)
  · 移除废条目 (调 xray.config.remove_proxy_binding)
  · 完工 / 失败分流 (写 vps_task 终态 + vps_record.stage)
```

### 实现 `xray/manager.py` 占位的真实现 (填 T-03 v2 的 2 个 stub)

```
extract_existing_outbounds() → 沿用旧 xray.config.extract_port_bindings 数据流
                              + 额外返回 outbound_protocol 字段 (freedom/socks)
                              空配置 / 配置错误 → return []
is_enabled()                  → SSH 跑 systemctl is-enabled xray
                              退码 0 + stdout 'enabled' → True; 否则 False
```

### 新建测试

```
test/xray_worker/TC-01_branch_classify.py        现状判断 3 分支
test/xray_worker/TC-02_branch_A_install.py       全新装机前置
test/xray_worker/TC-03_branch_B_start.py         已装停了前置 (含自启检查) ⭐
test/xray_worker/TC-04_branch_C_noop.py          已装跑着前置 (含自启检查) ⭐
test/xray_worker/TC-05_tail_empty_config.py      统一收尾空配置(没"直进直出"也没代理出口) → 加默认入口
test/xray_worker/TC-06_tail_has_direct.py        统一收尾配置里已有"直进直出" → 借用不补
test/xray_worker/TC-07_tail_takeover_ok.py       统一收尾纳管 (代理出口 + ping 通) → 写库
test/xray_worker/TC-08_tail_remove_unreach.py    统一收尾代理出口 ping 不通 → remove 三件套, 不记表 ⭐
test/xray_worker/TC-09_tail_shared_outbound.py   统一收尾共享 outbound (1 通 1 不通) → 只删坏的, 保留 outbound ⭐
test/xray_worker/TC-10_port_yield.py             默认入口让步 (18440 被占 → 试 18439) ⭐
test/xray_worker/TC-11_no_default_port.py        让步降到 1024 全占 → task failed last_error_code='no_default_port' ⭐
test/xray_worker/TC-12_failure_retry.py          失败退避重试
test/xray_worker/TC-13_circuit_break.py          同 error_code 连续 5 次 → 熔断
test/xray_worker/TC-14_lock_atomicity.py         ⚠️ skip 真机测
```

### 不动

```
不动 services/* / tools/* / SSHWorker / db/models.py
```

---

## 实现轮廓(高度精简, 详见 spec.md v5 §3 + §4)

### run_once 主循环

```python
def run_once(self) -> int:
    task_id = self._claim_task()
    if task_id is None:
        return 0
    self.process_task(task_id)
    return 1
```

### 抢任务 (原子锁)

```python
def _claim_task(self) -> int | None:
    now = datetime.utcnow()
    with session_scope() as s:
        # 找一条可抢的 task
        task = s.query(VPSTask).filter(
            VPSTask.status.in_([TaskStatus.PENDING, TaskStatus.PENDING_RETRY]),
            VPSTask.next_run_at <= now,
            or_(VPSTask.locked_until.is_(None), VPSTask.locked_until < now),
        ).order_by(VPSTask.next_run_at).first()

        if task is None:
            return None

        # 原子抢: WHERE 带 status, 避免两 worker 抢同条
        rows = s.query(VPSTask).filter(
            VPSTask.id == task.id,
            VPSTask.status.in_([TaskStatus.PENDING, TaskStatus.PENDING_RETRY]),
        ).update({
            "status": TaskStatus.IN_PROGRESS,
            "worker_id": f"xray_worker_pid{os.getpid()}",
            "locked_until": now + timedelta(minutes=5),
        }, synchronize_session=False)

        return task.id if rows else None
```

### process_task 主流程

```python
def process_task(self, task_id: int) -> None:
    with session_scope() as s:
        task = s.query(VPSTask).get(task_id)
        vps = s.query(VPSRecord).get(task.vps_id)
        ip, user, pwd, port = vps.ip, vps.username, vps.get_password(), vps.port

    try:
        with VPSSession(ip, user, pwd, port) as sess:
            xray = XrayManager(sess.client)

            # === 现状判断 ===
            branch = self._classify(xray, vps)

            # === 前置(分支差异) ===
            if branch == 'A':
                self._prepare_fresh(xray)
            elif branch == 'B':
                self._prepare_stopped(xray)   # ⭐ v2: 含自启检查
            else:  # C
                self._prepare_running(xray)   # ⭐ v2: 仅自启检查

            # === 统一收尾 ===
            tail = self._unified_tail(sess.client, xray, vps)

            self._mark_done(task_id, vps.id, tail)

    except AuthFailedError as e:
        self._mark_failed(task_id, "auth_denied", str(e), retriable=False)
    except NoDefaultPortError as e:    # ⭐ v2 新增异常
        self._mark_failed(task_id, "no_default_port", str(e), retriable=False)
    except RuntimeError as e:
        self._mark_retry(task_id, "install_failed", str(e))
```

### 现状判断

```python
def _classify(self, xray, vps) -> str:
    if vps.xray_version == "" or not xray.is_installed():
        return 'A'
    if not xray.is_running():
        return 'B'
    return 'C'
```

### 前置 B (装了没跑) ⭐ v2 加自启

```python
def _prepare_stopped(self, xray):
    if not xray.is_enabled():       # ⭐ 新增
        xray.enable()
    xray.start()
    if not xray.is_running():
        raise RuntimeError("start 后 xray 仍未在跑")
```

### 前置 C (装着跑着) ⭐ v2 加自启

```python
def _prepare_running(self, xray):
    if not xray.is_enabled():       # ⭐ 新增
        xray.enable()
    # 其他啥不做
```

### 统一收尾 (按 spec v5 §4 6 步) ⭐ v2 核心重写

```python
def _unified_tail(self, client, xray, vps) -> dict:
    # 1. 读配置
    cfg = xray.read_config()
    outbounds = xray.extract_existing_outbounds()  # 含 outbound_protocol 字段

    # 2. 分类
    direct_entries = [o for o in outbounds if o["outbound_protocol"] == "freedom"]
    proxy_entries  = [o for o in outbounds if o["outbound_protocol"] != "freedom"]

    # 3. 确保至少一条"直进直出"
    if direct_entries:
        default_port = direct_entries[0]["vps_port"]  # 借用现有的
    else:
        default_port = self._find_default_port(cfg)   # 让步算法
        cfg = add_proxy_binding(
            cfg,
            vps_port=default_port,
            inbound_protocol="socks",
            inbound_listen="0.0.0.0",
            inbound_user="",       # noauth
            inbound_pwd="",
            outbound_protocol="freedom",
            # ... 其他参数按 add_proxy_binding 签名
        )

    # 4. 处理代理出口 (纳管 OR remove)
    used_count = 0
    for entry in proxy_entries:
        ok = test_internal(   # toolbox.proxy_check.test_internal
            client=client,
            port=entry["vps_port"],
            user=entry["inbound_user"],
            pwd=entry["inbound_pwd"],
        )
        if ok:
            # 纳管入库
            self._upsert_managed(entry, vps_id=vps.id)
            used_count += 1
        else:
            # remove 三件套, 不记表
            cfg = remove_proxy_binding(cfg, entry["vps_port"])

    # 5. upload + validate + reload
    xray.upload_config(cfg)
    xray.validate_config()
    xray.reload()

    # 6. 验证还在跑
    if not xray.is_running():
        raise RuntimeError("reload 后 xray 未在跑")

    return {
        "xray_version": xray.version(),
        "default_inbound_port": default_port,
        "used_port_count": used_count,
    }
```

### 让步算法 ⭐ v2 新增

```python
def _find_default_port(self, cfg) -> int:
    """从 18440 降 1 找空位, 下限 1024. 找不到抛 NoDefaultPortError."""
    occupied = {inb["port"] for inb in cfg.get("inbounds", [])}
    for port in range(18440, 1023, -1):  # 18440 → 1024
        if port not in occupied:
            return port
    raise NoDefaultPortError(
        f"18440 → 1024 全部占满, 无法分配默认入口端口"
    )
```

### 纳管入库

```python
def _upsert_managed(self, entry, vps_id):
    geo = lookup_egress(entry["egress_ip"] or entry["upstream_host"])
    with session_scope() as s:
        # ip_record upsert by egress_ip
        ip_rec = s.query(IPRecord).filter_by(egress_ip=entry["egress_ip"]).first()
        if ip_rec is None:
            ip_rec = IPRecord(
                egress_ip=entry["egress_ip"],
                egress_country=geo["country_code"],
                upstream_host=entry["upstream_host"],
                upstream_port=entry["upstream_port"],
                upstream_user=entry["upstream_user"],
                upstream_pwd_encrypted=encrypt(entry["upstream_pwd"]),
                expire_date=None,           # 纳管 IP 不知到期日
                is_active=1,
            )
            s.add(ip_rec)
            s.flush()
        # proxy_record write
        s.add(ProxyRecord(
            vps_id=vps_id,
            ip_id=ip_rec.id,
            vps_port=entry["vps_port"],
            inbound_user=entry["inbound_user"],
            inbound_pwd_encrypted=encrypt(entry["inbound_pwd"]),
            status='using',
        ))
```

### 失败分流 (详见 spec v5 §7)

```python
def _mark_failed(self, task_id, error_code, error_msg, retriable=False):
    """不可重试 → failed; 同 error_code 连续 5 次 → circuit_broken."""
    ...

def _mark_retry(self, task_id, error_code, error_msg):
    """可重试 → pending_retry, 退避 2^retry_count 分钟 (上限 60)."""
    ...
```

---

## 测试用例(实现者按 spec.md v5 细节写)

### TC-01~04 分支决策与前置 ⭐ v2 加自启

mock `XrayManager.is_installed/is_running/is_enabled/version`。

- TC-03 (分支 B): mock is_enabled=False → 验证 enable() 被调一次
- TC-03 (分支 B): mock is_enabled=True → 验证 enable() **不**被调
- TC-04 (分支 C): mock is_enabled=False → 验证 enable() 被调一次

### TC-05 空配置(没"直进直出"也没代理出口) — 加默认入口

mock extract_existing_outbounds 返回 []。
验证:
- 加一条 18440 socks5 noauth → freedom 三件套
- 不写 ip / proxy 表
- vps.used_port_count = 0
- vps.default_inbound_port = 18440

### TC-06 配置已有"直进直出" — 借用不补

mock extract_existing_outbounds 返回 [{"vps_port": 8080, "outbound_protocol": "freedom", ...}]。
验证:
- **不**调 add_proxy_binding
- vps.default_inbound_port = 8080

### TC-07 纳管通的代理出口

mock extract_existing_outbounds 返回 1 条 socks outbound + test_internal=True。
验证:
- ip_record 写一条 (expire_date=NULL, is_active=1)
- proxy_record 写一条 status='using'
- vps.used_port_count = 1

### TC-08 ⭐ 代理出口 ping 不通 → remove

mock extract_existing_outbounds 返回 1 条 socks outbound + test_internal=False。
验证:
- 调 remove_proxy_binding(vps_port)
- ip_record / proxy_record **完全不写**
- vps.used_port_count = 0

### TC-09 ⭐ 共享 outbound (1 通 1 不通)

mock 2 条 inbound 共享同一 outbound, test_internal 返回 [True, False]。
验证:
- 通的 → 写 ip + proxy 表
- 不通的 → remove_proxy_binding(不通的 vps_port)
- outbound 因为还被通的 inbound 引用 → remove_proxy_binding 自动保留
- vps.used_port_count = 1

### TC-10 ⭐ 端口让步

mock 配置里 18440 已被另一条非 freedom inbound 占着。
验证:
- _find_default_port 返回 18439 (或下一个空位)
- add_proxy_binding 调用时 vps_port=18439
- vps.default_inbound_port = 18439

### TC-11 ⭐ 让步降到 1024 全占

mock 18440 → 1024 全部被占。
验证:
- _find_default_port 抛 NoDefaultPortError
- task.status = 'failed', last_error_code = 'no_default_port'
- vps.stage 保持 'connectable' (不升 running)

### TC-12 失败退避重试

mock 装机抛 RuntimeError。
验证:
- task.status = 'pending_retry'
- retry_count += 1
- next_run_at = now + 2^retry_count 分钟

### TC-13 熔断

同 error_code 连续 5 次 install_failed → task.status = 'circuit_broken'。

### TC-14 抢锁原子性 ⚠️ skip 真机测

---

## 实现者完工标准

```
- [ ] workers/xray_worker.py 完整实现
- [ ] xray/manager.py extract_existing_outbounds + is_enabled 填实现
- [ ] 14 个 TC 测试通过 (TC-14 skip 算通过)
- [ ] 不动 services/* / tools/* / SSHWorker / db/models.py
- [ ] commit 标题: feat(workers): XrayWorker 3 分支+统一收尾 (spec v5)
- [ ] 如遇 spec 没写清楚 → 停下报告 ([[feedback-缺工具先造]])
```

---

## Claude 验收检查清单

```
□ 跑 14 个 TC 全过
□ git diff workers/xray_worker.py:
    - 主循环 run_once / process_task / 分支前置 / 统一收尾 / 让步算法 都实现完
    - 抢锁原子性 UPDATE WHERE 含 status 条件
    - 统一收尾顺序对: 读 → 分类 → 确保直进直出 → 处理代理出口 → upload → validate → reload → verify
    - 分支 B 和 C 都有 is_enabled() 检查
    - 内 ping 走 toolbox.proxy_check.test_internal
    - pingIP 走 toolbox.geoip.lookup_egress
    - remove 走 xray.config.remove_proxy_binding
□ git diff xray/manager.py:
    - extract_existing_outbounds 返回字段含 outbound_protocol
    - is_enabled 实现走 systemctl is-enabled xray
□ 对照 spec.md v5 §4 检查统一收尾流程一致
□ 对照 spec.md v5 §7 检查失败分流策略
□ 对照 ADR-0004 §1 §2 §3 §4 §5 §6 逐条验证
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回
```

---

## 备注: T-07 完工后 rgvps 链路状态

完工后:
- SSHWorker(同步段)✅ 已 committed
- tools/rgvps MCP 入口 ⏳ 走 T-06
- **XrayWorker(异步装机+纳管段)✅ T-07 完工**
- xray 工具箱齐 (extract / is_enabled / add / remove / test_internal / test_external / lookup_egress)

T-06 + T-07 都完工 = rgvps 端到端打通, agent 调 rgvps 立刻拿 task_id, XrayWorker 后台装机+纳管完毕, vps 升 running。
