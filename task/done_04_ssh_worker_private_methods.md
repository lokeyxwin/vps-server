# T-04 SSHWorker 4 个私有方法实现 + 单测（v4 对齐）

**ID**: T-04
**前置依赖**: T-01 (VPSRecord schema v4) + T-02 (vps_task 新表, TaskStatus 4 值)
**后续依赖**: T-05 SSHWorker.process 主入口需要本任务完成

---

## 验收锚点

- `test/ssh_worker/spec.md` **v4** §0 实现者硬约束（旧代码姿势 + 缺工具先报告 + 工具优先复用）
- `test/ssh_worker/spec.md` **v4** §3 三条主路线（A/B/C，**没有 D**）
- `test/ssh_worker/spec.md` **v4** §5 不变量：
  - SSHWorker 只 touch vps_record + vps_task 两表
  - SSHWorker 写入的 stage 只能是 `connectable`
  - SSHWorker 写入的 xray_version 永远空字符串
  - 错误信息只住 vps_task 表，不住 vps_record
  - 路线 C（SSH 失败）**永远不写库**
- `test/ssh_worker/spec.md` **v4** §7 §工具清单（VPSSession 用法 + 不调 XrayManager）
- `CLAUDE.local.md` §0 legacy 代码三档姿势表（services / test / xray 旧函数禁直接 import）
- `CLAUDE.md` §7.2 主推类的实例方法

---

## 改动文件清单

### 改 `workers/ssh_worker.py`

```
填实现 class SSHWorker 的 4 个私有方法（下划线开头）:
  _查重(self, ip: str)                       → 见下面"实现轮廓"
  _敲门看一眼(self, ip, user, pwd, port)      → 见下面（**只采 OS，不查 xray**）
  _入库派任务(self, ip, user, pwd, port, ed,
              provider, os_name, os_version) → 见下面（**无 xray_version 参数**）
  _失败路径处理(self, error_type, error_msg,
                ip=None, user=None, port=None) → 见下面（**全部不入库**）

保留 process() 占位不动（T-05 实现）。
保留顶部 docstring 不动。
```

### 新建测试

```
test/ssh_worker/TC-01_query_existing.py     测 _查重
test/ssh_worker/TC-02_probe_ssh.py          测 _敲门看一眼
test/ssh_worker/TC-03_persist.py            测 _入库派任务
test/ssh_worker/TC-04_failure.py            测 _失败路径处理（**全部不入库**）
```

### 不动

```
不动 services/* / xray/* / ssh/* / toolbox/* / db/models.py / tools/*
```

（注意：core/ 已删除，源码已迁至 ssh/ + toolbox/，详见上一轮 commit
 `refactor(arch): core/ 拆为 ssh/ + toolbox/`）

---

## 实现轮廓（给实现者参考）

### `_查重(self, ip: str) -> dict | None`

```
查 vps_record 表有没有这个 ip.
命中 → 返回打包好的现状 dict:
   {
     "vps_id": int,
     "ip": str,
     "stage": str,                # VPSStage 当前值: connectable / running
     "xray_version": str,
     "os_name": str, "os_version": str,
     "is_active": int,
     "active_task": dict | None,  # 当前活跃 vps_task（若有）
        若有,内容:
        {
          "task_id": int,
          "status": str,           # TaskStatus: pending / in_progress / done / failed
          "retry_count": int,
          "next_run_at": str,      # ISO 格式
          "last_error_code": str,  # 错误信息住任务表
          "last_error_msg": str,
        }
   }
没命中 → 返回 None

⚠️ v4 变化:
  - 删 stage_message 字段（spec v4 §5 不变量：错误住任务表）
  - 加 last_error_code / last_error_msg（从 vps_task 来）
  - "活跃" status 集合只 2 个: [PENDING, IN_PROGRESS]（v4 没有 PENDING_RETRY）

实现:
  with session_scope() as s:
    rec = s.query(VPSRecord).filter_by(ip=ip).first()
    if rec is None: return None
    # 查活跃 task（v4 不含 PENDING_RETRY）
    task = s.query(VPSTask).filter(
        VPSTask.vps_id == rec.id,
        VPSTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
    ).order_by(VPSTask.created_at.desc()).first()

    # 注意: 即便没有活跃 task, 但最近一条 failed task 的错误信息也要带回
    # （便于 agent 告知用户"上次失败原因"）
    # 实现细节自定，可考虑：fallback 查最近 1 条 task 取 last_error_code/msg
    return {... 组装上面 dict ...}
```

### `_敲门看一眼(self, ip, user, pwd, port) -> dict`

```
返回 dict:
   {
     "ok": bool,                  # True=连通 False=失败
     "os_name": str,              # 拿不到留空
     "os_version": str,           # 拿不到留空
     "error_type": str | None,    # ok=False 时: 'auth_failed' / 'timeout' / 'refused' / 'failed'
     "error_message": str,        # ok=False 时给用户的提示文案
   }

⚠️ v4 关键变化（spec v4 §3 路线 B 步骤②、§4 不做的事）:
  - **删 xray_version 字段** —— SSHWorker 不查 xray
  - **绝不调用 XrayManager 任何方法**
  - SSH 连进去只跑 cat /etc/os-release 拿 OS
  - 返回 dict 中无 xray_version、无 client（VPSSession 在本方法内 with 包起来用完即关）

实现:
  try:
    with VPSSession(ip, user, pwd, port) as sess:
      info = sess.get_system_info()  # 返回 {os_name, os_version, username}
      return {
        "ok": True,
        "os_name": info.get("os_name", ""),
        "os_version": info.get("os_version", ""),
        "error_type": None,
        "error_message": "",
      }
  except AuthFailedError as e:
    return {"ok": False, "os_name": "", "os_version": "",
            "error_type": "auth_failed", "error_message": _build_auth_msg()}
  except ConnectTimeoutError as e:
    return {"ok": False, "os_name": "", "os_version": "",
            "error_type": "timeout", "error_message": _build_timeout_msg(port)}
  except ConnectRefusedError as e:
    return {"ok": False, "os_name": "", "os_version": "",
            "error_type": "refused", "error_message": _build_refused_msg(port)}
  except Exception as e:  # noqa: BLE001 — SSH 兜底
    return {"ok": False, "os_name": "", "os_version": "",
            "error_type": "failed", "error_message": str(e)}

⚠️ 重试策略（spec v4 §3 路线 C "3 次重试 / 10s 间隔 / 连接超时延长兜底"）:
  当前 ssh.ops.connect_server 已有内部重试（旧版 2 次 1s/2s）。
  spec v4 要求 3 次 / 10s 间隔 / 连接超时延长。
  → 实现者**停下来先报告**（spec v4 §0 第 2 条规则）:
       "我发现要满足 spec v4 §3 路线 C 的重试策略,
        需要调整 ssh/ops.py::connect_server 的重试参数(或加新方法)。
        请用户决策:改 connect_server 还是新增 connect_with_retry?"
  等用户批准后再动手。
```

### `_入库派任务(self, ip, user, pwd, port, ed, provider, os_name, os_version) -> dict`

```
写 vps_record（stage=connectable, xray_version=""）+ vps_task（pending）入库.
返回 dict:
   {
     "vps_id": int,
     "task_id": int,
     "stage": "connectable",
     "os_name": str,
     "os_version": str,
   }

⚠️ v4 变化:
  - **删 xray_version 入参** —— SSHWorker 不查 xray，xray_version 永远写空字符串
  - 不写 stage_message（字段已删除）

实现:
  with session_scope() as s:
    rec = VPSRecord.from_form(
        ip=ip, username=user, password=pwd, port=port,
        os_name=os_name, os_version=os_version,
        expire_date=ed, provider_domain=provider,
    )
    # stage 默认值就是 CONNECTABLE（VPSRecord 类级默认值），显式赋值更稳:
    rec.stage = VPSStage.CONNECTABLE
    # xray_version 默认空字符串（schema 默认），显式赋值更稳:
    rec.xray_version = ""
    s.add(rec)
    s.flush()  # 拿到 rec.id

    task = VPSTask(vps_id=rec.id, status=TaskStatus.PENDING)
    s.add(task)
    s.flush()  # 拿到 task.id

    return {
        "vps_id": rec.id,
        "task_id": task.id,
        "stage": VPSStage.CONNECTABLE,
        "os_name": os_name,
        "os_version": os_version,
    }

⚠️ from_form() 当前签名（task/01 v4 改完后）:
  port 无默认值，业务层必填
  无 stage 参数（外部赋值）
  如有变动让实现者按需调整，**绝不**回退到旧默认值。
```

### `_失败路径处理(self, error_type, error_message, ip=None, user=None, port=None) -> dict`

```
⚠️ v4 大改: SSH 失败全部抛回，**永远不入库**（spec v4 §3 路线 C + §5 不变量）

不分两种 case 了，**全部走"不入库 + 抛回"**:

  case error_type == "auth_failed":
    return {
      "status": "auth_failed",
      "message": "请核对账号密码。OCR 可能看错 0/o、l/I/1；"
                 "服务商面板密码 ≠ SSH 密码。"
    }

  case error_type == "timeout":
    return {
      "status": "ssh_timeout",
      "message": f"SSH 端口 {port} 连接超时。"
                 f"可能端口错 → 服务商控制台核对远程登录端口；"
                 f"端口对的话 → 安全策略组开放入方向（含 22 或远程登录端口）；"
                 f"都对还不行 → 服务商面板自查。"
    }

  case error_type == "refused":
    return {
      "status": "ssh_refused",
      "message": f"SSH 端口 {port} 被拒绝。"
                 f"可能端口错或服务未监听该端口 → 服务商控制台核对远程登录端口；"
                 f"端口对的话 → 安全策略组开放入方向。"
    }

  case error_type == "failed":
    return {
      "status": "ssh_failed",
      "message": f"SSH 连接失败: {error_message}"
    }

⚠️ 注意:
  - **永远不入库** — 不写 vps_record，不写 vps_task
  - 不熔断、不重试（重试已经在 _敲门看一眼 内部走完）
  - 提示文案不引导用户去防火墙作为首要排查（默认是端口错 / 安全策略组）

实现:
  # 直接返回 dict，没有 DB 操作
  ...如上...
```

---

## 测试用例（实现者按这些写 .py）

### TC-01 `test/ssh_worker/TC-01_query_existing.py`

```
TC-01-a  DB 空 → _查重 返回 None
TC-01-b  插入一条 VPSRecord → _查重 命中 → 返回 dict 含完整字段
         （字段含 stage / xray_version / os_* / is_active，**不含 stage_message**）
TC-01-c  插入一条 VPSRecord + 一条 VPSTask(PENDING) →
         active_task 字段填充正确，含 last_error_code / last_error_msg
TC-01-d  插入一条 VPSRecord + 一条 VPSTask(DONE) →
         active_task = None（done 不算活跃）
TC-01-e  插入一条 VPSRecord + 一条 VPSTask(FAILED) →
         active_task = None（failed 不算活跃，但 last_error_* 应能从最近 task 取到）
TC-01-f  插入一条 VPSRecord + 两条 VPSTask(PENDING + IN_PROGRESS) →
         返回最近的那条（按 created_at desc）
TC-01-g  ⭐ 防回退测试:
         尝试访问 _查重返回 dict 的 'stage_message' 字段应 KeyError
         （字段已删除）
```

### TC-02 `TC-02_probe_ssh.py`

```
所有 case mock VPSSession.

TC-02-a  SSH 通 → ok=True, os_name/version 拿到, **返回 dict 不含 xray_version**
TC-02-b  VPSSession context 抛 AuthFailedError →
         ok=False, error_type='auth_failed', error_message 非空
TC-02-c  抛 ConnectTimeoutError → error_type='timeout'
TC-02-d  抛 ConnectRefusedError → error_type='refused'
TC-02-e  抛 ConnectionError → error_type='failed'
TC-02-f  ⭐ 防回退测试:
         _敲门看一眼 内部**不应调用 XrayManager**
         (用 mock 验证 XrayManager 没被实例化/没被调用)
TC-02-g  SSH 通但 get_system_info 报错 →
         os_name/version 留空, ok 仍为 True（spec v4 §6 OS 读不到留空入库）
```

### TC-03 `TC-03_persist.py`

```
TC-03-a  探测成功 → 调 _入库派任务 →
         DB 里多了一条 VPSRecord(stage=CONNECTABLE, xray_version="")
         + 一条 VPSTask(status=PENDING)
TC-03-b  返回 dict 含 vps_id / task_id / stage="connectable" / os_name / os_version
         返回 dict **不含 xray_version**（SSHWorker 不写）
TC-03-c  password 落盘加密（原生 SQL 查 password_encrypted 不含明文）
TC-03-d  __repr__ 不输出密码
TC-03-e  ⭐ 防回退测试:
         入库后 grep DB 的 vps_record.xray_version 必为 ""
         入库后 vps_record 表无 stage_message 列（字段已删）
TC-03-f  ed=None 时入库正常，expire_date 落 NULL
TC-03-g  provider="" 时入库正常，provider_domain 落空字符串
```

### TC-04 `TC-04_failure.py`

```
⚠️ v4 大改: 全部抛回，**不入库**

TC-04-a  error_type='auth_failed' → 返回 {status: 'auth_failed', message: ...}
         message 含"请核对账号密码"
         **DB 无新增 vps_record / 无新增 vps_task**
TC-04-b  error_type='timeout' → 返回 {status: 'ssh_timeout', message: ...}
         message 含 port 数字 + 端口/安全策略组提示
         **DB 无新增**
TC-04-c  error_type='refused' → 返回 {status: 'ssh_refused', message: ...}
         **DB 无新增**
TC-04-d  error_type='failed' → 返回 {status: 'ssh_failed', message: ...}
         **DB 无新增**
TC-04-e  ⭐ 防回退测试:
         所有 _失败路径处理 调用前后, DB row count 不变
         (verify VPSRecord 和 VPSTask 都无新增)
TC-04-f  提示文案合规:
         - **不含**"防火墙"作为首要排查指引
         - 含"端口"或"安全策略组"作为主要排查方向
```

---

## 实现者完工标准

```
- [ ] workers/ssh_worker.py 4 个私有方法实现完（不再是 pass）
- [ ] 4 个 TC 测试文件全过
- [ ] 不引入新依赖
- [ ] 不动 services/* / xray/* / ssh/* (除非用户批准) / toolbox/* / db/models.py / tools/*
- [ ] _敲门看一眼 用 VPSSession 且 with 包起来用完即关
- [ ] _敲门看一眼 **绝不**调用 XrayManager（防回退测试 TC-02-f）
- [ ] _失败路径处理 **绝不**写 DB（防回退测试 TC-04-e）
- [ ] _入库派任务 写入的 xray_version **必为空字符串**（防回退测试 TC-03-e）
- [ ] 错误消息文案对照 spec v4 §3 路线 C 表
- [ ] commit 标题: feat(workers): SSHWorker 4 个私有方法 (spec v4)
- [ ] 如遇 spec 里没写清楚 / 缺工具的情况:
       **停下来报告给 Claude / 用户**, 不要自己拍
       (spec v4 §0 第 2 条规则)
```

---

## 已知缺工具点（实现者要先报告 + 等批准）

```
1. ssh.ops.connect_server 当前重试策略 vs spec v4 路线 C 要求
   当前: 2 次 / 1s & 2s 退避
   spec v4: 3 次 / 10s 间隔 / 连接超时延长兜底
   → 实现者先报告: "改 connect_server 重试参数 还是 加新方法?"

2. VPSSession 是否暴露 connection_timeout 参数可调?
   spec v4 要求"连接超时延长兜底"
   → 实现者检查 VPSSession.__init__ 签名;不够就停下报告
```

> 上面 2 点是已知的可能要造工具/改工具的地方。实现者按 spec v4 §0
> 第 2 条规则**先报告再动手**。其他不在此列表的设计点，遇到不清楚的
> 也按此规则停下报告。

---

## 实现过程记录（实现者完工时填）

> 实现期间如果造了新工具 / 改了现有工具 / 加了新方法，按这个格式记录:

```
- 改/造了 <工具名>（类 / 函数 / 方法）
  住 <文件路径>
  干啥 <一句话功能 / 改动点>
  测试 <对应 TC 编号>
  审批 用户在 <对话/issue> 批准
```

如果没改/造任何工具，写"无新增工具"即可。

### 本次实现记录（2026-06-07）

按 spec v4 §0 第 2 条规则,实现者识别出 2 个缺工具点,停下报告,用户拍板**方案 B**
(新方法隔离 legacy)。批准位置:本次对话(用户原话"保留 spec v4 §3 路线 C 重试机制。
按方案 B 实现 ...")。

```
- 造了 connect_with_retry 函数
  住 ssh/ops.py:148-258
  干啥 SSHWorker 专用入口探测连接:3 次尝试 / 10s 间隔 / timeout+refused 也走重试 /
       AuthFailedError 立即抛(不重试)。单次 paramiko connect 的 timeout 默认 30s
       (比 SSH_CONNECT_TIMEOUT=10 长,作慢网络/速率限制兜底)。抛同族异常
       (AuthFailedError / ConnectTimeoutError / ConnectRefusedError / ConnectionError),
       SSHWorker 上层按类型分场景处理。旧 connect_server 一字不改,旧 services/ 行为不变。
  常量 SSHWORKER_RETRY_ATTEMPTS=3 / SSHWORKER_RETRY_INTERVAL=10.0 / SSHWORKER_CONNECT_TIMEOUT_DEFAULT=30
  测试 间接走 TC-02 (mock VPSSession 验异常类型路径);本身不另设单测,
       因为是 paramiko 实网层逻辑,留给真机验证
  审批 用户在本次对话拍板方案 B + 30s + 10s

- 改了 VPSSession.__init__
  住 ssh/session.py:50-65
  干啥 新增可选入参 connect_timeout: int = 30 (默认值从 ssh.ops.SSHWORKER_CONNECT_TIMEOUT_DEFAULT 拿)。
       内部 connect() 改调 connect_with_retry(原调 connect_server),透传 connect_timeout。
       旧调用方不传 connect_timeout 也兼容(默认 30s),只是从此走"3 次重试 + 10s 间隔"路径。
  测试 TC-02 (mock VPSSession 验入口 with 包起来用完即关)
  审批 用户在本次对话拍板方案 B

- 实现了 SSHWorker 4 个私有方法
  住 workers/ssh_worker.py
  干啥 _查重 / _敲门看一眼 / _入库派任务 / _失败路径处理 按 spec v4 §3 三条主路线 +
       §5 不变量 落地。process() 仍保留 pass 占位留给 T-05。
  测试 TC-01 (7/7) + TC-02 (7/7) + TC-03 (7/7) + TC-04 (6/6) = 27/27 全过
  审批 任务单本身已经是金标准

不变量验证(spec v4 §5):
  ✓ SSHWorker 只 touch vps_record + vps_task 两表
  ✓ SSHWorker 写入的 stage 永远 connectable (TC-03-a/b)
  ✓ SSHWorker 写入的 xray_version 永远空字符串 (TC-03-e 防回退)
  ✓ 错误信息只住 vps_task 表 (TC-01-c/e 验证从 task 取 last_error_*)
  ✓ 路线 C(SSH 失败) 永远不写库 (TC-04-e 防回退)
  ✓ _敲门看一眼 内部不调 XrayManager (TC-02-f 防回退,顶层 namespace + 实例化双断言)
  ✓ _入库派任务 写入的 xray_version 显式为 "" (TC-03-e 防回退,原生 SQL 验)
```

跑通命令(参考):
```bash
VPS_SERVER_TESTING=1 python -m unittest \
  test.ssh_worker.TC-01_query_existing \
  test.ssh_worker.TC-02_probe_ssh \
  test.ssh_worker.TC-03_persist \
  test.ssh_worker.TC-04_failure
```

---

## Claude 验收检查清单

```
□ 跑 4 个 TC 测试全过
□ git diff workers/ssh_worker.py:
    - 4 个私有方法不再是 pass
    - process() 仍是 pass（留 T-05）
    - 顶部 docstring 不动
    - _敲门看一眼 内部无 XrayManager 引用
    - _失败路径处理 内部无 session_scope / DB add 调用
    - _入库派任务 写入的 xray_version 显式为 ""
□ 对照 spec v4 §3 路线 A/B/C 检查行为一致
□ 对照 spec v4 §5 不变量逐条验证:
    - 只 touch 两表 ✓
    - SSHWorker 写 stage=connectable ✓
    - SSHWorker 写 xray_version="" ✓
    - 错误信息不写 vps_record ✓
    - 路线 C 永不写库 ✓
□ 错误消息文案对照 spec v4 §3 路线 C 表
□ 实现过程记录段是否填了（造了啥/无新增工具）
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回让实现者改
```

---

## v4 vs v3 修订总结

| 项 | v3 | v4 |
|---|----|----|
| _敲门看一眼 是否查 xray_version | ✅ 查 | ❌ **不查**（spec v4 §4 不做的事）|
| _敲门看一眼 返回是否含 xray_version 字段 | ✅ 有 | ❌ 无 |
| _敲门看一眼 是否调用 XrayManager | ✅ 调 version() | ❌ **绝不调用**（防回退测试 TC-02-f） |
| _入库派任务 是否接收 xray_version 参数 | ✅ 接收 | ❌ 删参数 |
| _入库派任务 写入的 xray_version | 实参传入 | ❌ 永远 `""` |
| _失败路径处理 timeout/refused 是否入库 | ✅ 入库 stage=unreachable | ❌ **不入库**（spec v4 §3 路线 C 改写）|
| _失败路径处理 返回 status 数 | 2 (auth_failed / unreachable) | **4** (auth_failed / ssh_timeout / ssh_refused / ssh_failed) |
| 错误信息住哪 | vps_record.stage_message | **vps_task.last_error_code/msg**（_失败路径处理仍不入库，但 _查重 拿历史 task 错误带回）|
| 重试参数 | core.ssh 内部 2 次 1s/2s | spec v4 要求 3 次 10s + 连接超时延长（实现者先报告） |
| 测试 TC 数 | 4 节 ~15 条 | 4 节 ~25 条（加防回退测试） |
