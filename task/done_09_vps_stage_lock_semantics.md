# T-09 vps.stage 锁语义校准(spec v5.1 → v5.2 + 代码 + 端到端验证)

**ID**: T-09
**状态**: waiting
**前置依赖**: T-07 ✅ done(XrayWorker 完整实现)
**后续依赖**: 无(ProxyDeployWorker 等后续工人按本任务校准后的 stage 语义挑机)
**关联 ADR**: `docs/adr/0005-vps-stage-as-resource-lock.md`(本任务连带落)
**关联 spec**: `test/xray_worker/spec.md` v5.1 → v5.2

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名从 `waiting_09_vps_stage_lock_semantics.md` 改为 `doing_09_vps_stage_lock_semantics.md`。

### 必读清单

领取后、写代码前必须显式读取:

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`
- [ ] `docs/adr/README.md`
- [ ] `docs/adr/0001-workers-replace-services.md`(§决策 §4 已被本任务关联 ADR-0005 supersede)
- [ ] `docs/adr/0005-vps-stage-as-resource-lock.md`(本任务核心依据)
- [ ] `test/xray_worker/spec.md`(改之前的 v5.1)
- [ ] `workers/xray_worker.py`(点名要改 2 处: process_task 抢到 task 之后, _mark_done)
- [ ] `db/models.py`(:23-34 stage 注释段)
- [ ] `test/xray_worker/TC-07_tail_takeover_ok.py` / `TC-08_tail_remove_unreach.py` 等(看完工断言)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "task表拿到的那个任务结束了, 数据写完了 vps对应的vps不得还回去吗还占着干啥呀 不给别人用吗"

> "任务表是任务的锁, 这个任务我拿了 你不要再跟我抢了; vps表 running 是为了告诉其他部门说 这台服务器有人正在 running 不要拿 能理解吗 后面还有 proxy 工人要干活呢"

> "失败了就继续锁住吧 后面再引入专门维修的"

### Claude 整理后的业务理解

- **VPS 资源占用锁** = `vps_record.stage`:
  - `connectable` = 没工人占着, 可被任意工人抢
  - `running` = 有工人占着, 别的工人/部门跳过
- **任务并发锁** = `vps_task.status + locked_until + worker_id`: 同一任务防多工人抢
- 工人状态迁移:
  1. SSHWorker 入库 → stage=`connectable`
  2. XrayWorker 抢到 task → stage=`running`(占资源, SSH 之前)
  3. 完工 → stage=`connectable`(释放回池子)
  4. 失败 → stage 保持 `running`(锁住等"维修工人"或人工介入)

### 本任务要解决什么

- spec / 代码 / 注释 / TC / smoke 脚本全面对齐 ADR-0005 两层锁语义
- 完整端到端 smoke 验证一次(清库 → SSHWorker → XrayWorker → 看 DB 落库符合新语义)

### 本任务不解决什么

- ProxyDeployWorker 怎么挑机(后续工人单独任务, 本任务只保证语义一致)
- 巡检 / 维修工人(封存在 `workers/_shelved/`, 后续单独 ADR)
- ADR-0001 文件内容(永不改, 本任务仅通过 ADR-0005 顶部 supersede 标注关联)

---

## 2. 实现参考

### 验收锚点

- `docs/adr/0005-vps-stage-as-resource-lock.md` §决策 §1/§2/§3
- `test/xray_worker/spec.md` v5.2(本任务改后)§1/§2/§4/§9
- `db/models.py:23-34` stage 注释段(已是正确语义, 不改逻辑只加 ADR-0005 引用)

### 改动文件清单

#### 改 `test/xray_worker/spec.md`(v5.1 → v5.2)

```text
§1 工人定位:
  旧 "成功后把 vps_record.stage 升级为 running。只有 XrayWorker 能把 VPS 标成 running"
  新 "抢到任务后写 stage=running 占资源, 完工写 stage=connectable 还回池子, 失败保持 running 等人介入"

§2 入口契约:
  抢锁段: 在"原子 UPDATE vps_task"段后追加"再写一次 vps.stage='running'(资源锁)"
  输出 成功: vps_record.stage='running' → 'connectable'
  输出 失败(任何路径): vps_record.stage 保持 running 不动

§4 成功出口: tail_result 内部仍含 default_inbound_port 等(不变);
            _mark_done 写库时 vps.stage 改 connectable(不是 running)

§9 不变量改:
  旧 "task.status='done' 时, vps.stage='running'"
  新 "task.status='done' 时, vps.stage='connectable'"
  新 "task.status='in_progress' 时, vps.stage='running'"
  新 "task.status='failed' 时, vps.stage='running'(锁住等维修)"

§三 修订历史加 v5.2 条目:
  - 2026-06-08 落 ADR-0005 两层锁语义
  - §1 §2 §4 §9 改 stage 状态机
  - 失败保持 running 等人介入(用户原话拍板)
```

#### 改 `workers/xray_worker.py`

```text
位置 1: process_task 主流程, 抢到 task_id 之后、SSH 之前追加一段
  with session_scope() as s:
      vps = s.get(VPSRecord, creds["vps_id"])
      if vps is not None:
          vps.stage = VPSStage.RUNNING

位置 2: _mark_done 第 442 行附近
  旧 vps.stage = VPSStage.RUNNING
  新 vps.stage = VPSStage.CONNECTABLE

位置 3: _mark_failed / _handle_retriable
  不改(失败保持 stage='running', 跟 ADR-0005 §决策 §3 一致)
```

#### 改 `db/models.py:23-34`(只加 ADR 引用, 不改逻辑)

```text
末尾追加一行: "详见 docs/adr/0005-vps-stage-as-resource-lock.md"
注释主体逻辑保留(本来就跟 ADR-0005 一致)
```

#### 改 `test/xray_worker/TC-*.py`

```text
TC-07 / TC-08 / 其他完工断言:
  凡是 assert vps.stage == VPSStage.RUNNING(完工后断言) → 改 CONNECTABLE

TC-11 / TC-12 / TC-13 / 其他失败断言:
  凡是 assert vps.stage 失败后 → 保持 RUNNING(原本就是, 不改或加显式断言)

新增 TC-15_lock_semantics.py:
  TC-15a: process_task 抢到 task 后立刻验证 vps.stage='running'(SSH mock 阻塞前断言)
  TC-15b: 完工后验证 vps.stage='connectable' + task.status='done'
  TC-15c: 失败后验证 vps.stage 保持 'running' + task.status='failed'/'pending'
```

#### 改 `dev_smoke_xray_worker.py`

```text
顶部 docstring 验收预期:
  旧 "vps_record: stage='running', xray_version 非空"
  新 "vps_record: stage='connectable', xray_version 非空, 已释放资源锁回池子"
```

#### 不动

```text
不动:
- services/*       (legacy, 留对照)
- xray/*           (工具箱, 锁语义跟它无关)
- toolbox/*        (工具, 锁语义跟它无关)
- workers/ssh_worker.py(SSHWorker 入库时 stage 默认就是 CONNECTABLE, 已对)
- ADR-0001 / ADR-0002 / ADR-0003 / ADR-0004(永不改原则)
- db/models.py 字段定义部分(只动 :23-34 注释段, 不动 schema)
```

### 实现轮廓

```python
# workers/xray_worker.py 改动示意

def process_task(self, task_id: int) -> None:
    creds = self._load_credentials(task_id)
    if creds is None:
        return

    # ⭐ 新增: 抢到 task 后立刻写 stage=running 占资源锁
    self._lock_vps_resource(creds["vps_id"])

    ip = creds["ip"]
    logger.info("开始处理 task_id=%s vps_id=%s ip=%s", task_id, creds["vps_id"], ip)

    try:
        with VPSSession(...) as sess:
            ...
            tail_result = self._unified_tail(...)
        self._mark_done(task_id, creds["vps_id"], tail_result)
    except AuthFailedError as exc:
        self._mark_failed(task_id, "auth_denied", str(exc))
    # ... 其他异常分支不动


@staticmethod
def _lock_vps_resource(vps_id: int) -> None:
    """抢到 task 后, 把 VPS 资源锁标为 running."""
    with session_scope() as s:
        vps = s.get(VPSRecord, vps_id)
        if vps is not None:
            vps.stage = VPSStage.RUNNING


@staticmethod
def _mark_done(task_id: int, vps_id: int, tail_result: dict) -> None:
    now = datetime.utcnow()
    with session_scope() as s:
        task = s.get(VPSTask, task_id)
        if task is not None:
            task.status = TaskStatus.DONE
            ...
        vps = s.get(VPSRecord, vps_id)
        if vps is not None:
            vps.stage = VPSStage.CONNECTABLE  # ⭐ 改: 完工释放资源锁
            vps.xray_version = tail_result["xray_version"]
            vps.used_port_count = tail_result["used_port_count"]
            ...
```

### 数据结构 / 状态迁移

| 字段 / 状态 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `vps_record.stage='connectable'` | 资源锁未占, 可被任意工人抢 | ProxyDeployWorker 挑机查询 / 巡检 | SSHWorker 入库默认 / 工人完工 |
| `vps_record.stage='running'` | 资源锁已占, 别的工人/部门跳过 | 同上 | 工人抢到 task 后 / 工人失败保持 |
| `vps_task.status='in_progress'` | 任务并发锁, 防多工人抢同一任务单 | XrayWorker._claim_task | XrayWorker._claim_task(原子 UPDATE) |
| `vps_task.status='done'` | 任务完工 | 用户 / 巡检 | XrayWorker._mark_done |
| `vps_task.status='failed' / 'pending'` | 任务失败终态 / 回炉等重试 | 同上 | XrayWorker._mark_failed / _handle_retriable |

### 缺工具 / 缺信息先报告

实现者遇到以下情况必须停下来报告:

- spec / ADR 没写清楚的 stage 迁移分支(比如某个新的失败路径)
- 发现 SSHWorker 入库时 stage 不是 connectable(目前预期是, 若不是需校准)
- 发现别的 worker(未来的 ProxyDeployWorker / 维修工人)挑机查询有冲突
- 发现 db/models.py 字段定义里 stage 默认值跟新语义不一致

---

## 3. 验收交付

### 测试用例

#### TC-07 / TC-08(改)

业务故事:
```text
XrayWorker 完工时, VPS 资源锁应该释放回 connectable, 不是停在 running.
```

预期:
- vps_task.status='done'
- **vps_record.stage='connectable'** ⭐ 改(原断言 'running')

#### TC-15a `test/xray_worker/TC-15_lock_semantics.py`(新增)

业务故事:
```text
工人抢到 task, SSH 之前应该立刻把 VPS 资源锁标为 running, 防止别的工人/部门同时操作.
```

输入:
- mock vps_task pending 一条
- mock VPSSession 阻塞(模拟 SSH 阶段)

预期:
- 抢锁阶段执行完, vps.stage = 'running'
- 整个 process_task 内部 SSH 进行时 stage 仍是 running

#### TC-15b(新增)

业务故事:
```text
工人完工时, 释放资源锁回 connectable, 让 ProxyDeployWorker 等后续工人能拿到这台机.
```

输入:
- mock 全流程 OK, _unified_tail 返回 success

预期:
- task.status='done'
- vps.stage='connectable'
- vps.xray_version / used_port_count 都写了

#### TC-15c(新增)

业务故事:
```text
工人失败时, 不释放资源锁, 保持 running 等"维修工人"或人工来处理.
```

输入:
- mock 任意失败路径(AuthFailedError / NoDefaultPortError / 临时错误)

预期:
- task.status='failed' 或 'pending'(看失败类别)
- **vps.stage='running'**(锁住等人)

### 必跑测试命令

```bash
# 单元测试
VPS_SERVER_TESTING=1 uv run python -m unittest discover test/xray_worker/

# 完整端到端 smoke(用户清库后跑)
rm db/vps_server.db
uv run python -m db.engine  # 或等 SSHWorker 首次写入自动 create_all
uv run python dev_smoke_ssh_worker.py
uv run python dev_smoke_xray_worker.py

# DB 验收
sqlite3 db/vps_server.db "SELECT id, ip, stage, used_port_count FROM vps_record;"
sqlite3 db/vps_server.db "SELECT id, vps_id, status FROM vps_task;"
sqlite3 db/vps_server.db "SELECT id, vps_id, vps_port, status FROM proxy_record;"
```

### 完整端到端验收预期

```text
跑完 SSHWorker(dev_smoke_ssh_worker.py):
  vps_record: stage='connectable'(SSHWorker 入库默认), xray_version=''
  vps_task:   status='pending', vps_id 指向新 record

跑完 XrayWorker(dev_smoke_xray_worker.py):
  vps_record: stage='connectable' ⭐(完工释放, 不再 'running')
              xray_version='Xray 26.3.27 ...', used_port_count=1
  vps_task:   status='done', last_error_code=''
  proxy_record: 1 行 status='using' egress_ip=非空
  ip_record:    1 行 expire_date=NULL is_active=1
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。

- [ ] 开工前已将任务文件从 `waiting` 改为 `doing`。
- [ ] `docs/adr/0005-*.md` 已创建(实际本任务起草时已创建, 实现者审一遍内容)。
- [ ] `test/xray_worker/spec.md` v5.1 → v5.2 改完(§1 / §2 / §4 / §9 / §三 修订历史)。
- [ ] `workers/xray_worker.py` 两处改完(process_task 加 _lock_vps_resource; _mark_done 改 CONNECTABLE)。
- [ ] `db/models.py:23-34` 注释末尾加 ADR-0005 引用。
- [ ] `test/xray_worker/TC-07 / TC-08` 完工断言改 connectable。
- [ ] `test/xray_worker/TC-15_lock_semantics.py` 新建, 含 3 个 testcase。
- [ ] `dev_smoke_xray_worker.py` 顶部 docstring 验收预期改 connectable。
- [ ] **必跑测试命令**单元测试全部 PASS。
- [ ] **完整端到端 smoke**(SSHWorker → XrayWorker)跑过, DB 落库符合验收预期。
- [ ] 对照 ADR-0005 §决策 §1/§2/§3 验证业务流程一致。
- [ ] 没有改动 "不动" 清单里的文件。
- [ ] 完成记录段已填(测试结果原样贴出, 不剪裁)。

### 实现过程记录(实现者完工时填)

```text
改动文件:
- <path>

新增工具/方法:
- 名字: _lock_vps_resource(static method)
  住: workers/xray_worker.py
  干啥: 抢到 task 后立刻把 vps.stage 改 RUNNING(资源锁)
  测试: TC-15a
  审批: 用户 2026-06-08 拍板 ADR-0005

测试结果:
- <command> -> <result>

端到端 smoke 结果:
- vps_record / vps_task / proxy_record / ip_record 各贴一遍 SELECT 输出

偏差 / 风险:
- <none | details>
```

### Claude 验收检查清单

- [ ] 对照 ADR-0005 §决策 §1/§2/§3 检查实现一致
- [ ] 对照 spec.md v5.2 §1/§2/§4/§9 检查业务流程 / 数据流 / 判断分支一致
- [ ] 单元测试跑过, 所有 PASS
- [ ] 完整端到端 smoke 跑过, DB 落库符合验收预期(stage=connectable / task=done / used_port_count 对)
- [ ] 失败路径手动验证(改 dev_smoke 凭据触发 AuthFailedError)stage 保持 running
- [ ] 偏差但合理 → 抛给用户决策
- [ ] 偏差不合理 → 打回实现者修改

---

## 完成记录

```text
完成日期: 2026-06-08
完成 commit: (待用户确认后 commit, 本任务单 + ADR-0005 + 代码 + spec + TC 全部一个 commit)
任务状态: doing -> done

改动摘要:
- 新建 docs/adr/0005-vps-stage-as-resource-lock.md (supersede ADR-0001 §决策 §4 单层锁)
- workers/xray_worker.py:
  · 新增 _lock_vps_resource(vps_id) staticmethod, 抢到 task 后 SSH 之前调一次, 写 vps.stage=RUNNING
  · _mark_done 把 vps.stage 改 CONNECTABLE (释放资源锁)
  · _mark_failed / _handle_retriable 不改 (失败保持 running, ADR-0005 §3)
- db/models.py VPSStage docstring 末尾加 ADR-0005 引用
- test/xray_worker/spec.md v5.1 → v5.2:
  · 顶部 ADR 列表加 ADR-0005, 标 ADR-0001 §4 被 supersede
  · §1 工人定位重写
  · §2 入口契约加"抢到 task 后占资源锁"段, 改输出描述
  · §4 成功出口删过时 "stage": "running" 描述, 改 _mark_done 行为说明
  · §9 不变量拆"锁状态机不变量"(task vs stage 4 个对照) + "业务不变量"
  · §三 修订历史加 v5.2 条目
- TC-11_no_default_port: failure 断言 vps.stage CONNECTABLE → RUNNING (ADR-0005 §3)
- TC-14_real_server: docstring + 测试方法 docstring 端到端预期 running → connectable
- TC-15_lock_semantics(新建): 3 个 testcase 覆盖锁状态机
  · TC-15a 抢锁后 stage=running
  · TC-15b 完工后 stage=connectable
  · TC-15c 失败后 stage=running 保持
- dev_smoke_xray_worker.py 顶部 docstring + 末尾验收清单字符串改 stage=connectable

测试命令:
- 单元测试 (项目根目录):
    VPS_SERVER_TESTING=1 uv run python -m unittest \
      test.xray_worker.TC-01_branch_classify ... test.xray_worker.TC-15_lock_semantics
- 端到端 smoke:
    rm db/vps_server.db  (用户已删)
    uv run python -c "from db.base import Base; from db import models; from db.engine import engine; Base.metadata.create_all(engine)"
    uv run python dev_smoke_ssh_worker.py
    uv run python dev_smoke_xray_worker.py

测试结果:
- 单元测试: Ran 34 tests in 0.065s, OK (skipped=1)
  · TC-14 skip 算通过 (真机测, 由 dev_smoke 替代)
  · 其余 33 testcase 全 PASS
- 新增 TC-15 三个 testcase 全 PASS

端到端 smoke 结果 (用户 dev DB, 真 VPS 203.0.113.10):
  SSHWorker 入库后:
    vps_record id=1 stage='connectable' xray_version=''
    vps_task id=1 status='pending'
  XrayWorker 完工后:
    vps_record id=1 stage='connectable' ⭐ (新语义生效, 完工释放资源锁)
                    xray_version='Xray 26.3.27 ...' used_port_count=1
    vps_task id=1 status='done' last_error_code=''
    proxy_record id=1 vps_port=11080 status='using' egress_ip=198.51.100.10 egress_country=SG
    ip_record id=1 egress_ip=198.51.100.10 country=SG entry_host=proxy.miluproxy.com
                   expire_date=NULL is_active=1
  验收完全匹配 ADR-0005 + spec v5.2 §9 不变量

未覆盖风险:
- 失败 stage='running' 后没"维修工人"自动处理, 需人工介入或等后续封存工人启用
  (ADR-0005 §决策 §3 已接受此风险)
- TC-15c 只测了 AuthFailedError 一条失败路径; 其他失败路径
  (ssh_timeout / no_default_port / 临时错)共享 _mark_failed / _handle_retriable 逻辑,
  已在 TC-11 / TC-12 / TC-13 间接覆盖
  (TC-11 显式断言 NoDefaultPortError 路径下 vps.stage=RUNNING)

后续任务:
- ProxyDeployWorker 实现 (挑机查询按 ADR-0005 §决策 §4: stage=connectable
  + xray_version!='' + is_active=1)
- 维修工人 (workers/_shelved/ 启用时单独 ADR 决策, 处理失败挂 running 的 VPS)
- legacy 大手术 (沿用 done_07 已记: xray/manager.py 薄包装清理 / services/ 删除)
```
