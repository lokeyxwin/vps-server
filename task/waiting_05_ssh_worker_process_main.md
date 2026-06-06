# T-05 SSHWorker.process 主入口实现 + 集成测试

**ID**: T-05
**前置依赖**: T-04 (SSHWorker 4 个私有方法实现)
**后续依赖**: T-06 tools/rgvps.py MCP 入口需要本任务完成

---

## 验收锚点

- `tests_behavior/ssh_worker/spec.md` §2 入口契约 + §3 三条主路线整体
- `docs/adr/0001-workers-replace-services.md` §决策(同步段快速返回)

## 改动文件清单

### 改 `workers/ssh_worker.py`

```
填实现 process() 方法.
保留 4 个私有方法不动(T-04 已实现).
保留顶部 docstring 不动.
```

### 新建测试

```
tests_behavior/ssh_worker/TC-05_main_flow.py
```

### 不动

```
不动 db/* / xray/* / core/* / services/* / tools/*
```

---

## 实现轮廓(给实现者参考)

### `process(self, ip, user, pwd, port, ed=None, provider="") -> dict`

按 spec.md §3 三条主路线写,直接调 4 个私有方法:

```python
def process(self, ip, user, pwd, port, ed=None, provider=""):
    # 路线 A:DB 已有
    existing = self._查重(ip)
    if existing is not None:
        return {
            "status": "already_registered",
            "vps": existing,
            # 不写 task_id,因为已经有的 task 在 existing.active_task 里
        }
    
    # 路线 B/C:DB 没有,探测
    probe = self._敲门看一眼(ip, user, pwd, port)
    
    if not probe["ok"]:
        # 路线 C:SSH 失败
        return self._失败路径处理(
            ip, user, pwd, port, ed, provider, probe["error"]
        )
    
    # 路线 B:探测成功,入库 + 派任务
    result = self._入库派任务(
        ip, user, pwd, port, ed, provider,
        os_name=probe["os_name"],
        os_version=probe["os_version"],
        xray_version=probe["xray_version"],
    )
    
    return {
        "status": "queued",
        "task_id": result["task_id"],
        "vps_id": result["vps_id"],
        "vps": {
            "ip": ip,
            "stage": "connectable",
            "xray_version": result["xray_version"],
            "os_name": probe["os_name"],
            "os_version": probe["os_version"],
        },
        "message": (
            "已确认账密 OK,已入库;后台 worker 会接手装 xray + 端口审计"
        ),
    }
```

---

## 测试用例(实现者按这些写 .py)

### TC-05 `tests_behavior/ssh_worker/TC-05_main_flow.py`

```
全部 case mock 4 个私有方法,验证 process 的编排正确性.

TC-05-a 已注册路径
  _查重 mock 返回非 None → process 返回 status='already_registered'
  _敲门看一眼 / _入库派任务 不应被调
  vps 字段含 existing 内容

TC-05-b 新登记成功路径
  _查重 mock 返回 None
  _敲门看一眼 mock 返回 ok=True + xray_version="26.x"
  _入库派任务 mock 返回 vps_id=1, task_id=10
  → process 返回 status='queued', task_id=10
  → message 含"账密 OK"

TC-05-c auth_failed 路径
  _查重 → None
  _敲门看一眼 → ok=False, error='auth_failed'
  → _失败路径处理 被调 with error='auth_failed'
  → process 返回 status='auth_failed', 不入库(由 _失败路径处理 保证)

TC-05-d timeout 路径
  _查重 → None
  _敲门看一眼 → ok=False, error='timeout'
  → _失败路径处理 被调 with error='timeout'
  → process 返回 status='unreachable', vps_id 填充

TC-05-e refused / failed 路径(同 timeout)

TC-05-f xray_version 空字符串(没装)路径
  _查重 → None
  _敲门看一眼 → ok=True, xray_version=""
  → 入库成功
  → process 返回 status='queued', vps.xray_version=""
```

---

## 实现者完工标准

```
- [ ] workers/ssh_worker.py process() 实现完(不再是 pass)
- [ ] TC-05 测试全过
- [ ] process() 不直接 import VPSSession / XrayManager / DB(全部走私有方法)
- [ ] 不动其他文件
- [ ] commit 标题: feat(workers): SSHWorker.process 主入口编排
```

---

## Claude 验收检查清单

```
□ 跑 TC-05 测试全过
□ git diff workers/ssh_worker.py:
    - process() 实现完
    - 仅调 4 个私有方法,不直接调底层
□ 跑 T-04 已有 TC-01~04 仍全过(没破坏)
□ 对照 spec.md §3 检查 3 条主路线对应正确
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回
```
