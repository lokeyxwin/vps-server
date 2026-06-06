# T-05 SSHWorker.process 主入口实现 + 集成测试（v4 对齐）

**ID**: T-05
**前置依赖**: T-04 (SSHWorker 4 个私有方法实现 v4)
**后续依赖**: T-06 tools/rgvps.py MCP 入口需要本任务完成

---

## 验收锚点

- `tests_behavior/ssh_worker/spec.md` **v4** §0 实现者硬约束（旧代码姿势 + 缺工具先报告）
- `tests_behavior/ssh_worker/spec.md` **v4** §2 入口契约（入参 + 返回形状）
- `tests_behavior/ssh_worker/spec.md` **v4** §3 三条主路线 A/B/C（**没有 D**）
- `tests_behavior/ssh_worker/spec.md` **v4** §5 不变量：
  - 路线 C 永远不写库
  - SSHWorker 只 touch 两表
- `CLAUDE.local.md` §0 legacy 代码三档姿势表
- `docs/adr/0001-workers-replace-services.md` §决策（同步段快速返回）

---

## 改动文件清单

### 改 `workers/ssh_worker.py`

```
填实现 process() 方法。
保留 4 个私有方法不动（T-04 已实现）。
保留顶部 docstring 不动。
```

### 新建测试

```
tests_behavior/ssh_worker/TC-05_main_flow.py
```

### 不动

```
不动 db/* / xray/* / ssh/* / toolbox/* / services/* / tools/*
```
（core/ 已删，源码在 ssh/ + toolbox/，详见 `refactor(arch): core/ 拆为 ssh/ + toolbox/` commit）

---

## 实现轮廓（给实现者参考）

### `process(self, ip, user, pwd, port, ed=None, provider="") -> dict`

按 spec v4 §3 三条主路线写，直接编排 4 个私有方法：

```python
def process(self, ip, user, pwd, port, ed=None, provider=""):
    # 路线 A:DB 已有
    existing = self._查重(ip)
    if existing is not None:
        return {
            "status": "already_registered",
            "vps": existing,
            # active_task 已经在 existing["active_task"] 里
        }

    # 路线 B/C:DB 没有,探测
    probe = self._敲门看一眼(ip, user, pwd, port)

    if not probe["ok"]:
        # 路线 C:SSH 失败 → 抛回不入库（4 种 status 之一）
        return self._失败路径处理(
            error_type=probe["error_type"],
            error_message=probe["error_message"],
            ip=ip, user=user, port=port,
        )

    # 路线 B:探测成功 → 入库 + 派任务（v4: 不传 xray_version）
    result = self._入库派任务(
        ip=ip, user=user, pwd=pwd, port=port, ed=ed, provider=provider,
        os_name=probe["os_name"],
        os_version=probe["os_version"],
    )

    return {
        "status": "queued",
        "task_id": result["task_id"],
        "vps_id": result["vps_id"],
        "vps": {
            "ip": ip,
            "stage": "connectable",
            "xray_version": "",                  # v4: SSHWorker 永远写空
            "os_name": probe["os_name"],
            "os_version": probe["os_version"],
        },
        "message": (
            "已确认账密 OK,已入库;后台 worker 会接手装 xray"
        ),
    }
```

⚠️ v4 关键变化:
- **删** `xray_version=probe["xray_version"]` 参数传递（探测不查 xray）
- **删** `_失败路径处理` 路径中的 `vps_id`（路线 C 不入库, 无 vps_id 可返回）
- **路线 C 返回 4 种 status**: `auth_failed / ssh_timeout / ssh_refused / ssh_failed`
- **删** `status='unreachable'` 这条路径（v4 没有此 stage 值）

### 返回形状全集（5 种 status）

```python
# 路线 A: 已登记
{"status": "already_registered", "vps": {...含 active_task ...}}

# 路线 B: 新登记成功
{
    "status": "queued",
    "task_id": int,
    "vps_id": int,
    "vps": {"ip", "stage": "connectable", "xray_version": "", "os_name", "os_version"},
    "message": "已确认账密 OK..."
}

# 路线 C: SSH 失败（4 种，全部不入库，无 vps_id）
{"status": "auth_failed",  "message": "请核对账号密码..."}
{"status": "ssh_timeout",  "message": "SSH 端口 X 连接超时..."}
{"status": "ssh_refused",  "message": "SSH 端口 X 被拒绝..."}
{"status": "ssh_failed",   "message": "SSH 连接失败: ..."}
```

---

## 测试用例（实现者按这些写 .py）

### TC-05 `tests_behavior/ssh_worker/TC-05_main_flow.py`

```
全部 case mock 4 个私有方法, 验证 process 的编排正确性.

TC-05-a 已登记路径
  _查重 mock 返回非 None → process 返回 status='already_registered'
  _敲门看一眼 / _入库派任务 / _失败路径处理 都**不应被调**
  vps 字段含 existing 内容（含 active_task）

TC-05-b 新登记成功路径
  _查重 mock 返回 None
  _敲门看一眼 mock 返回 {ok=True, os_name, os_version}（v4: **不含 xray_version**）
  _入库派任务 mock 返回 {vps_id=1, task_id=10, stage="connectable", os_name, os_version}
  → process 返回 status='queued', task_id=10, vps_id=1
  → vps.xray_version == ""（防回退）
  → vps.stage == "connectable"
  → message 含"账密 OK"

TC-05-c auth_failed 路径
  _查重 → None
  _敲门看一眼 → {ok=False, error_type='auth_failed', error_message=...}
  → _失败路径处理 被调 with error_type='auth_failed'
  → process 返回 status='auth_failed'
  → 返回 dict 中**无 vps_id 字段**（v4: 不入库）

TC-05-d timeout 路径
  _查重 → None
  _敲门看一眼 → {ok=False, error_type='timeout', error_message=...}
  → _失败路径处理 被调 with error_type='timeout'
  → process 返回 status='ssh_timeout'（v4: 不再是 'unreachable'）
  → message 含 port 数字

TC-05-e refused 路径
  _敲门看一眼 → error_type='refused' → process 返回 status='ssh_refused'

TC-05-f failed 路径
  _敲门看一眼 → error_type='failed' → process 返回 status='ssh_failed'

TC-05-g ⭐ 防回退测试（路线 C 不入库）
  打开真 SQLite 内存 DB（不 mock _失败路径处理）, 调 process(ip, ..., bad_pwd)
  → SSHWorker._敲门看一眼 mock 返回 ok=False, error_type='timeout'
  → process 返回后, DB 的 vps_record 表 row count 不变
  → DB 的 vps_task 表 row count 不变

TC-05-h ⭐ 防回退测试（路线 B 入库后 xray_version 为空）
  打开真 SQLite 内存 DB（不 mock _入库派任务）
  _查重 → None
  _敲门看一眼 mock 返回 {ok=True, os_name="ubuntu", os_version="22.04"}
  → process 调完后, DB 中 vps_record.xray_version 必为 ""

TC-05-i ⭐ 防回退测试（路线 A 不写库）
  插入一条 VPSRecord
  调 process 同 ip, mock _敲门看一眼 / _入库派任务 抛错（不应被调用）
  → process 返回 status='already_registered'
  → DB row count 不变（包括无新增 vps_task）
```

---

## 实现者完工标准

```
- [ ] workers/ssh_worker.py process() 实现完（不再是 pass）
- [ ] TC-05 测试全过（含 3 个防回退测试 TC-05-g/h/i）
- [ ] process() 不直接 import VPSSession / XrayManager / DB（全部走 4 个私有方法）
- [ ] process() 不传 xray_version 给 _入库派任务（v4 删此参数）
- [ ] process() 路线 C 返回不含 vps_id（v4 不入库）
- [ ] process() 路线 B 返回 vps.xray_version == ""
- [ ] 不动其他文件
- [ ] 跑 T-04 已有 TC-01~04 仍全过（没破坏私有方法）
- [ ] commit 标题: feat(workers): SSHWorker.process 主入口编排 (spec v4)
- [ ] 如遇 spec 里没写清楚 / 缺工具 → **停下来报告**, 不自己拍（spec v4 §0 第 2 条）
```

---

## 实现过程记录（实现者完工时填）

> 如果造了新工具 / 改了现有工具，按这个格式记录：

```
- 改/造了 <工具名>
  住 <文件路径>
  干啥 <一句话>
  测试 <TC 编号>
  审批 用户在 <对话/issue> 批准
```

如果只是编排 4 个私有方法没造新工具，写"无新增工具"即可。

---

## Claude 验收检查清单

```
□ 跑 TC-05 测试全过（含 3 个防回退）
□ git diff workers/ssh_worker.py:
    - process() 实现完
    - 仅调 4 个私有方法，不直接调底层
    - 路线 B 返回的 vps.xray_version 字面值为 ""
    - 路线 C 返回字典无 vps_id 字段
    - 无 'unreachable' / 'install_xray' 等旧字符串残留
□ 跑 T-04 已有 TC-01~04 仍全过（私有方法未被破坏）
□ 对照 spec v4 §2 入口契约返回形状逐条验证 5 种 status
□ 对照 spec v4 §3 三路线 A/B/C 检查行为
□ 对照 spec v4 §5 不变量"路线 C 永不写库"逐条验证
□ 实现过程记录段是否填了（造了啥/无新增工具）
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回
```

---

## v4 vs v3 修订总结

| 项 | v3 | v4 |
|---|----|----|
| process() 是否传 xray_version 给 _入库派任务 | ✅ 传 probe["xray_version"] | ❌ **不传**（_入库派任务 删此参数）|
| 路线 B 返回 vps.xray_version | 探测值（可能非空）| **永远 ""** |
| 路线 C 返回 status 数 | 2 (`auth_failed` / `unreachable`) | **4** (`auth_failed` / `ssh_timeout` / `ssh_refused` / `ssh_failed`) |
| 路线 C 是否入库 | timeout/refused 入库 stage='unreachable' | ❌ **全部不入库** |
| 路线 C 返回是否含 vps_id | ✅ 部分（unreachable 路径有） | ❌ **无** |
| _失败路径处理 调用签名 | `(ip, user, pwd, port, ed, provider, error)` | `(error_type, error_message, ip=, user=, port=)` |
| 测试 TC 数 | 6 (a-f) | **9** (a-i，加 3 个防回退) |
| message 文案 | "请确认端口是服务商指定的远程登录端口" | 按 spec v4 §3 路线 C 分场景细化 |
