# T-07 XrayWorker 实现 (3 分支 + 统一收尾 7 步)

**ID**: T-07
**前置依赖**: T-01 (VPSRecord) + T-02 (vps_task) + T-03 (XrayManager 新方法占位)
**后续依赖**: 无 (这是 rgvps 链路装机端的核心)
**复杂度**: 🔴 大(整个项目最复杂的工人,含纳管 7 步)

---

## 验收锚点

- `tests_behavior/xray_worker/spec.md` v3 §3 (3 分支 + 统一收尾) — **核心**
- `docs/adr/0003-xray-worker-three-branches-unified-tail.md` — **核心**
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md` §4 §5 (字段语义、expire_date=null)
- `CLAUDE.local.md` §7 (默认入口 18440) + §12 (纳管模式)

## 改动文件清单

### 新建 `workers/xray_worker.py`(完整实现 class XrayWorker)

```
class XrayWorker (沿用 SSHWorker 的类风格):
  
  def __init__(self): pass
  
  def run_once(self) -> int:
    """轮询一次:扫 vps_task 表抢一条 task,处理完返回 1;无可领返回 0。"""
  
  def process_task(self, task_id: int) -> None:
    """处理指定 task。 SSHWorker 派任务测试时直接调这个。"""
  
  # ===== 私有编排 =====
  
  def _抢任务(self) -> int | None: ...        # 抢一条 task,返回 task_id 或 None
  def _现状判断(self, xray, vps) -> str: ...  # 返回 'A' / 'B' / 'C'
  def _前置_装(self, xray): ...               # 分支 A
  def _前置_起(self, xray): ...               # 分支 B
  def _统一收尾(self, xray, vps) -> dict: ... # 7 步纳管
  def _失败分流(self, task, error): ...       # 失败处理
```

### 实现 `xray/manager.py` 3 个方法的真实现(填 T-03 占位)

```
extract_existing_outbounds() → 沿用旧 xray.config.extract_port_bindings
has_outbounds()              → 检查配置是否有非默认 tag 的 outbound
test_internal()              → 沿用旧 xray.service.test_internal_socks
                              (调用方式调整,返回 bool 而非 dict)
```

### 新建测试

```
tests_behavior/xray_worker/TC-01_branch_classify.py    现状判断 3 分支
tests_behavior/xray_worker/TC-02_branch_A_install.py   全新装机
tests_behavior/xray_worker/TC-03_branch_B_start.py     已装停了起服务
tests_behavior/xray_worker/TC-04_branch_C_noop.py      已装跑着不动
tests_behavior/xray_worker/TC-05_unified_tail_empty.py 统一收尾空配置
tests_behavior/xray_worker/TC-06_unified_tail_takeover.py 统一收尾接管
tests_behavior/xray_worker/TC-07_failure_retry.py      失败重试
tests_behavior/xray_worker/TC-08_circuit_break.py      熔断
tests_behavior/xray_worker/TC-09_lock_atomicity.py     ⚠️ skip 真机测
```

### 不动

```
不动 services/* / tools/* / SSHWorker / db/models.py
```

---

## 实现轮廓(高度精简,详见 spec.md §3)

### run_once 主循环

```python
def run_once(self) -> int:
    task_id = self._抢任务()
    if task_id is None:
        return 0  # 无可领
    self.process_task(task_id)
    return 1
```

### _抢任务 (原子锁)

```python
def _抢任务(self) -> int | None:
    now = datetime.utcnow()
    with session_scope() as s:
        # SELECT id WHERE status IN (pending, pending_retry)
        #         AND next_run_at <= now
        #         AND (locked_until IS NULL OR locked_until < now)
        # ORDER BY next_run_at LIMIT 1
        task = s.query(VPSTask).filter(
            VPSTask.status.in_([TaskStatus.PENDING, TaskStatus.PENDING_RETRY]),
            VPSTask.next_run_at <= now,
            or_(VPSTask.locked_until.is_(None), VPSTask.locked_until < now),
        ).order_by(VPSTask.next_run_at).first()
        
        if task is None:
            return None
        
        # UPDATE WHERE 加 status 条件确保原子(避免两 worker 抢同条)
        rows = s.query(VPSTask).filter(
            VPSTask.id == task.id,
            VPSTask.status.in_([TaskStatus.PENDING, TaskStatus.PENDING_RETRY]),
        ).update({
            "status": TaskStatus.IN_PROGRESS,
            "worker_id": f"xray_worker_pid{os.getpid()}",
            "locked_until": now + timedelta(minutes=5),
        }, synchronize_session=False)
        
        if rows == 0:
            return None  # 被别人抢了
        return task.id
```

### process_task 主流程

```python
def process_task(self, task_id: int) -> None:
    # 拿 task + vps_record
    with session_scope() as s:
        task = s.query(VPSTask).get(task_id)
        vps = s.query(VPSRecord).get(task.vps_id)
        ip = vps.ip
        user = vps.username
        pwd = vps.get_password()
        port = vps.port
    
    try:
        with VPSSession(ip, user, pwd, port) as sess:
            xray = XrayManager(sess.client)
            
            # 分支决策
            branch = self._现状判断(xray, vps)
            
            # 前置(分支差异)
            if branch == 'A':
                self._前置_装(xray)
            elif branch == 'B':
                self._前置_起(xray)
            # C 啥不做
            
            # 统一收尾(所有分支必经)
            tail_result = self._统一收尾(xray, vps)
            
            # 更新 VPS + task
            self._完工(task_id, vps.id, tail_result)
    
    except AuthFailedError as e:
        self._失败分流(task_id, "auth_denied", str(e), retriable=False)
    except (RuntimeError, Exception) as e:  # noqa: BLE001
        # 临时错 vs 永久错的细分见 spec §7
        self._失败分流(task_id, "install_timeout", str(e), retriable=True)
```

### _现状判断

```python
def _现状判断(self, xray, vps) -> str:
    if vps.xray_version == "" or not xray.is_installed():
        return 'A'
    if not xray.is_running():
        return 'B'
    return 'C'
```

### _统一收尾(7 步,**核心,实现者必读 spec.md §3 统一收尾**)

```python
def _统一收尾(self, xray, vps) -> dict:
    # 1. 读配置
    cfg = xray.read_config()
    
    # 2. 抠出现有出口
    outbounds = xray.extract_existing_outbounds()
    
    used_count = 0
    
    if outbounds:
        # 3. 逐条内 ping → 入库
        for entry in outbounds:
            ok = xray.test_internal(
                port=entry["vps_port"],
                user=entry["inbound_user"],
                pwd=entry["inbound_pwd"],
            )
            
            with session_scope() as s:
                # 4. 写 ip_record (upsert by egress_ip)
                ip_id = self._upsert_ip_record(s, entry, internal_ok=ok)
                # 5. 写 proxy_record
                self._upsert_proxy_record(s, vps.id, entry, ip_id, internal_ok=ok)
            
            if ok:
                used_count += 1
    
    # 6. 补默认入口(如果没有 18440)
    if not self._has_default_inbound(cfg):
        xray.add_inbound(
            port=config.XRAY_DEFAULT_PORT,
            user="", pwd="",
            tag="default-direct",
            route_to="direct",
        )
        # 这一步会改 in-memory cfg,然后 upload
    
    # 7. upload + validate + reload + verify
    xray.upload_config(cfg)
    xray.validate_config()
    xray.reload()
    
    if not xray.is_running():
        raise RuntimeError("reload 后 xray 不在跑")
    
    return {
        "xray_version": xray.version(),
        "used_port_count": used_count,
    }
```

### _完工

```python
def _完工(self, task_id, vps_id, tail_result):
    with session_scope() as s:
        # 更新 vps_record
        vps = s.query(VPSRecord).get(vps_id)
        vps.stage = VPSStage.RUNNING
        vps.xray_version = tail_result["xray_version"]
        vps.used_port_count = tail_result["used_port_count"]
        
        # 更新 task
        task = s.query(VPSTask).get(task_id)
        task.status = TaskStatus.DONE
        task.worker_id = ""
        task.locked_until = None
        task.completed_at = datetime.utcnow()
```

### _失败分流(详见 spec.md §7)

```python
def _失败分流(self, task_id, error_code, error_msg, retriable):
    with session_scope() as s:
        task = s.query(VPSTask).get(task_id)
        task.last_error_code = error_code
        task.last_error_msg = error_msg[:255]
        task.worker_id = ""
        task.locked_until = None
        
        if not retriable:
            task.status = TaskStatus.FAILED
            return
        
        task.retry_count += 1
        
        # 熔断:连续 5 次同 code
        if task.retry_count >= 5:
            task.status = TaskStatus.CIRCUIT_BROKEN
            return
        
        # 退避: 2^retry 分钟
        delay_minutes = min(2 ** task.retry_count, 60)
        task.next_run_at = datetime.utcnow() + timedelta(minutes=delay_minutes)
        task.status = TaskStatus.PENDING_RETRY
```

---

## 测试用例(实现者按 spec 写,本任务单只列纲要)

### TC-01~04 分支决策与前置

按 spec.md §3 三分支测,mock XrayManager.version/is_running。

### TC-05 统一收尾空配置

mock extract_existing_outbounds 返回 []
验证: 仅补默认入口 + reload,不写 ip_record / proxy_record
vps.used_port_count = 0
task.status = DONE

### TC-06 统一收尾接管(纳管核心)

mock extract_existing_outbounds 返回 2 条
mock test_internal 返回 [True, False](第一条通,第二条不通)
验证:
- ip_record 写 2 条,第 1 条 is_active=1,第 2 条 is_active=0
- proxy_record 写 2 条,status='using' / 'expired'
- vps.used_port_count = 1
- task.status = DONE

### TC-07 失败重试

mock 装机抛 RuntimeError → task 标 pending_retry, retry_count=1
next_run_at = now+2min

### TC-08 熔断

连续 5 次同 install_timeout → task 标 circuit_broken

### TC-09 抢锁原子性

⚠️ skip 真机测(同 T-02 TC-11)

---

## 实现者完工标准

```
- [ ] workers/xray_worker.py 完整实现(class XrayWorker)
- [ ] xray/manager.py 3 个新方法真实现(填 T-03 占位)
- [ ] 9 个 TC 测试全过(TC-09 skip 计入"全过")
- [ ] 不动 services/* / tools/* / SSHWorker / db/models.py
- [ ] commit 标题: feat(workers): XrayWorker 3 分支+统一收尾 7 步
```

---

## Claude 验收检查清单

```
□ 跑 9 个 TC 测试全过
□ git diff workers/xray_worker.py:
    - run_once / process_task / 6 个私有方法实现完
    - 抢锁原子性 UPDATE WHERE 含 status 条件
    - 统一收尾顺序对(读→抠→ping→入库→补默认→reload→verify)
□ git diff xray/manager.py:
    - 3 个新方法不再是 pass
    - extract_existing_outbounds 空配置返回 []
□ 对照 spec.md §3 + ADR-0003 检查流程一致
□ 对照 spec.md §7 检查失败分流策略
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回
```
