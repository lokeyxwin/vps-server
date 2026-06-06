# T-04 SSHWorker 4 个私有方法实现 + 单测

**ID**: T-04
**前置依赖**: T-01 (VPSRecord schema v2) + T-02 (vps_task 新表)
**后续依赖**: T-05 SSHWorker.process 主入口需要本任务完成

---

## 验收锚点

- `tests_behavior/ssh_worker/spec.md` §3 三条主路线 + §5 不变量 + §7 §工具清单
- `docs/adr/0001-workers-replace-services.md` §决策(workers/ 是新业务层)
- `CLAUDE.md` §7.2 主推类的实例方法
- `CLAUDE.local.md` §1 目录布局 v3(sshworker 是 class)

## 改动文件清单

### 改 `workers/ssh_worker.py`

```
填实现 class SSHWorker 的 4 个私有方法(下划线开头):
  _查重(self, ip: str)                       → 见下面"实现轮廓"
  _敲门看一眼(self, ip, user, pwd, port)      → 见下面
  _入库派任务(self, ip, user, pwd, port, ed,
              provider, os_name, os_version,
              xray_version)                  → 见下面
  _失败路径处理(self, ip, user, pwd, port,
                ed, provider, error)         → 见下面

保留 process() 占位不动(T-05 实现)。
保留顶部 docstring 不动。
```

### 新建测试

```
tests_behavior/ssh_worker/TC-01_query_existing.py     测 _查重
tests_behavior/ssh_worker/TC-02_probe_ssh.py          测 _敲门看一眼
tests_behavior/ssh_worker/TC-03_persist.py            测 _入库派任务
tests_behavior/ssh_worker/TC-04_failure.py            测 _失败路径处理
```

### 不动

```
不动 db/* / xray/* / core/* / services/* / tools/*
```

---

## 实现轮廓(给实现者参考)

### `_查重(self, ip: str) -> dict | None`

```
查 vps_record 表有没有这个 ip.
命中 → 返回打包好的现状 dict:
   {
     "vps_id": int,
     "ip": str,
     "stage": str,                # VPSStage 当前值
     "stage_message": str,
     "xray_version": str,
     "os_name": str, "os_version": str,
     "is_active": int,
     "active_task": dict | None,  # 当前活跃 vps_task(若有)
        若有,内容:
        {
          "task_id": int,
          "status": str,           # TaskStatus
          "retry_count": int,
          "next_run_at": str,      # ISO 格式
          "last_error_code": str,
          "last_error_msg": str,
        }
   }
没命中 → 返回 None

实现:
  with session_scope() as s:
    rec = s.query(VPSRecord).filter_by(ip=ip).first()
    if rec is None: return None
    # 查活跃 task
    task = s.query(VPSTask).filter(
        VPSTask.vps_id == rec.id,
        VPSTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS,
                            TaskStatus.PENDING_RETRY])
    ).order_by(VPSTask.created_at.desc()).first()
    return {...组装上面 dict...}
```

### `_敲门看一眼(self, ip, user, pwd, port) -> dict`

```
返回 dict:
   {
     "ok": bool,                  # True=连通 False=失败
     "client": SSHClient | None,  # 仅 ok=True 时持有(给上游用,记得用完关)
     "os_name": str,
     "os_version": str,
     "xray_version": str,
     "error": str | None,         # ok=False 时,值为 'auth_failed' /
                                  # 'timeout' / 'refused' / 'failed'
     "error_message": str,        # 人话错误描述
   }

实现:
  try:
    sess = VPSSession(ip, user, pwd, port).connect()
  except AuthFailedError as e:
    return {"ok": False, "error": "auth_failed", "error_message": str(e), ...}
  except ConnectTimeoutError as e:
    return {"ok": False, "error": "timeout", ...}
  except ConnectRefusedError as e:
    return {"ok": False, "error": "refused", ...}
  except ConnectionError as e:
    return {"ok": False, "error": "failed", ...}
  
  # 通了,顺手采集
  info = sess.get_system_info()  # 返回 {os_name, os_version, username}
  xray = XrayManager(sess.client)
  xray_version = xray.version()  # 空字符串 = 没装
  
  return {
    "ok": True,
    "client": sess.client,        # 注意:用完上游负责 close
    # 或者更安全:返回 sess 让上游 close,但要 spec 拍定
    "os_name": info["os_name"],
    "os_version": info["os_version"],
    "xray_version": xray_version,
    "error": None,
  }

⚠️ 关键设计点(实现者注意):
  VPSSession 是 context manager.
  方案 A:_敲门看一眼 内部 with 包起来,提前关 → 但上游无法继续用 client.
  方案 B:返回 sess 给上游(_入库派任务后再关) → 接口稍复杂.
  方案 C:_敲门看一眼 关 SSH 后,_入库派任务 不需要 SSH 再连.
  推荐方案 C(更简单):
    探测 OK 后获取所有需要的信息(os / xray_version) → 立即 close
    后续 _入库派任务 不再 SSH
```

### `_入库派任务(self, ip, user, pwd, port, ed, provider, os_name, os_version, xray_version) -> dict`

```
写 vps_record(stage=connectable) + vps_task(install_xray, pending) 入库.
返回 dict:
   {
     "vps_id": int,
     "task_id": int,
     "stage": "connectable",
     "xray_version": xray_version,  # 透传
   }

实现:
  with session_scope() as s:
    # 用 VPSRecord.from_form()(加密密码)
    rec = VPSRecord.from_form(
        ip=ip, username=user, password=pwd, port=port,
        os_name=os_name, os_version=os_version,
        expire_date=ed, provider_domain=provider,
    )
    rec.stage = VPSStage.CONNECTABLE
    rec.xray_version = xray_version
    s.add(rec)
    s.flush()  # 拿到 rec.id
    
    task = VPSTask(vps_id=rec.id, status=TaskStatus.PENDING)
    s.add(task)
    s.flush()  # 拿到 task.id
    
    return {"vps_id": rec.id, "task_id": task.id, ...}

⚠️ from_form() 的 stage 参数兼容性:
  T-01 改名后,VPSRecord.from_form() 可能要加 stage 参数支持
  (或者外部赋 stage 后再 add)
```

### `_失败路径处理(self, ip, user, pwd, port, ed, provider, error) -> dict`

```
分两种处理:

case error == "auth_failed":
  → 不入库,直接返回:
     {"status": "auth_failed", "message": "..."}

case error in ("timeout", "refused", "failed"):
  → 入库 vps_record(stage=unreachable, stage_message=...),不建 task
  → 返回:
     {"status": "unreachable", "vps_id": vps_id,
      "message": "请确认 {port} 端口是服务商指定的远程登录端口..."}

实现(timeout/refused/failed 路径):
  with session_scope() as s:
    rec = VPSRecord.from_form(...)
    rec.stage = VPSStage.UNREACHABLE
    rec.stage_message = (
        f"SSH 探测失败({error})。请确认 {port} 端口是服务商指定的"
        f"远程登录端口。不要去防火墙找问题——SSH 端口被防火墙拦的"
        f"概率远低于用户填错端口。"
    )
    s.add(rec)
    s.flush()
    vps_id = rec.id
  
  return {"status": "unreachable", "vps_id": vps_id, "message": ...}

⚠️ 重试逻辑:
  内部短时重试(2 次,间隔 1s/2s)由 VPSSession.connect() 内部走
  (实际上 core.ssh.connect_server 已经有重试,详见 core/ssh.py).
  本方法只处理"探测后仍失败"的最终结果.
```

---

## 测试用例(实现者按这些写 .py)

### TC-01 `tests_behavior/ssh_worker/TC-01_query_existing.py`

```
TC-01-a  DB 空 → _查重 返回 None
TC-01-b  插入一条 VPSRecord → _查重 命中 → 返回 dict 含完整字段
TC-01-c  插入一条 VPSRecord + 一条 VPSTask(pending) →
         active_task 字段填充正确
TC-01-d  插入一条 VPSRecord + 一条 VPSTask(done) →
         active_task = None(done 不算活跃)
TC-01-e  插入一条 VPSRecord + 两条 VPSTask(pending + in_progress) →
         返回最近的那条(按 created_at desc)
```

### TC-02 `TC-02_probe_ssh.py`

```
所有 case mock VPSSession + XrayManager.

TC-02-a  SSH 通 + xray 装了 → ok=True, xray_version 非空
TC-02-b  SSH 通 + xray 没装 → ok=True, xray_version=''
TC-02-c  VPSSession.connect 抛 AuthFailedError →
         ok=False, error='auth_failed', error_message 非空
TC-02-d  抛 ConnectTimeoutError → error='timeout'
TC-02-e  抛 ConnectRefusedError → error='refused'
TC-02-f  抛 ConnectionError → error='failed'
```

### TC-03 `TC-03_persist.py`

```
TC-03-a  探测成功 → 调 _入库派任务 →
         DB 里多了一条 VPSRecord(stage=CONNECTABLE) + 一条 VPSTask(PENDING)
TC-03-b  返回 dict 含 vps_id / task_id / stage / xray_version
TC-03-c  password 落盘加密(原生 SQL 查 password_encrypted 不含明文)
TC-03-d  __repr__ 不输出密码
```

### TC-04 `TC-04_failure.py`

```
TC-04-a  error='auth_failed' → 不入库 → 返回 {status: auth_failed, message}
TC-04-b  error='timeout' → 入库 stage=UNREACHABLE + stage_message 非空 →
         返回 {status: unreachable, vps_id, message}
TC-04-c  message 含"请确认...端口...服务商指定的远程登录端口"提示文案
         不含"防火墙"(spec §7 v2 反禁项)
TC-04-d  refused / failed 走同 timeout 路径
```

---

## 实现者完工标准

```
- [ ] workers/ssh_worker.py 4 个私有方法实现完(不再是 pass)
- [ ] 4 个 TC 测试文件全过
- [ ] 不引入新依赖
- [ ] 不动 services/* / xray/* / core/* / db/models.py / tools/*
- [ ] _敲门看一眼 用 VPSSession + XrayManager(不直接 import paramiko)
- [ ] 错误消息含"请确认端口是服务商指定的远程登录端口"提示
       (不引导用户去防火墙)
- [ ] commit 标题: feat(workers): SSHWorker 4 个私有方法
```

---

## Claude 验收检查清单

```
□ 跑 4 个 TC 测试全过
□ git diff workers/ssh_worker.py:
    - 4 个私有方法不再是 pass
    - process() 仍是 pass(留 T-05)
    - 顶部 docstring 不动
□ 对照 spec.md §3 路线 A/B/C 检查行为一致
□ 错误消息文案对照 spec.md §7 不变量
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回让实现者改
```
