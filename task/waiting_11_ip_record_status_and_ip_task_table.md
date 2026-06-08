# T-11 ip_record.status 字段 + IPTask 表

**ID**: T-11
**状态**: waiting
**前置依赖**: 无(纯 db 模型改造,不依赖其他任务)
**后续依赖**: T-13(IPProbeWorker 实现会 import `IPStatus` / `IPTask`)
**关联 ADR**: [[0001-workers-replace-services]] §决策 §6;[[0005-vps-stage-as-resource-lock]](两层锁分离,IPTask 也吃这套)
**关联 spec**: [[test/ip_probe_worker/spec.md]] v2 §G(新增字段/表代码片段)+ §6(IPStatus 状态语义)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名从 `waiting_11_...md` 改为 `doing_11_...md`。

### 必读清单

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`(尤其 §业务编排:worker / kit / task 体系 + §数据模型契约)
- [ ] `docs/adr/0001-workers-replace-services.md`
- [ ] `docs/adr/0005-vps-stage-as-resource-lock.md`
- [ ] `test/ip_probe_worker/spec.md` v2 全文,重点 §6 + §G
- [ ] `db/models.py` 当前实现(尤其 `VPSStage` / `VPSTask` / `IPRecord` / `TaskStatus`)
- [ ] `test/_data_structures/test_vps_task.py`(VPSTask 测试样板,IPTask 参照写)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "IP表我需要状态机 但只要两个字段,使用中和可使用 可使用表示IP工人已经校验过这条IP是可达的并派生任务"

> "IP不知道哪台VPS那就不用它写,谁配的谁写"

> "IP表的任务锁跟vpstask一样的定义就行"

### 整理后的业务理解

- **外部输入**: 无(纯模型层改造)
- **影响业务**:
  - IPProbeWorker 入库时写 `ip_record.status = USABLE` + 派 `ip_task(pending, vps_id=NULL)`
  - ProxyDeployWorker(未来)抢 `ip_task` 后回填 `vps_id`,配置成功改 `ip_record.status = USING`
- **数据流**:
  - 读: 无(本任务不读)
  - 写: 新加 `ip_record.status` 列;新建 `ip_task` 表
- **同步 / 异步边界**: N/A
- **成功 / 失败返回**: N/A

### 本任务要解决什么

- 让 `IPRecord` 有 `status` 状态机字段(USABLE / USING),配合 IPProbeWorker / ProxyDeployWorker 协同
- 新建 `IPTask` 表,作 IPProbeWorker → ProxyDeployWorker 的异步接力媒介
- `ip_task.vps_id` 字段 nullable(IPProbeWorker 留 NULL,ProxyDeployWorker 挑到 VPS 后回填)

### 本任务不解决什么

- ✗ 不实现 IPProbeWorker 工人(T-13)
- ✗ 不实现 ProxyDeployWorker 工人(后续任务)
- ✗ 不动 VPSRecord / VPSTask / ProxyRecord(只动 IPRecord + 加 IPTask)
- ✗ 不动现有 `ip_record` 表数据(用 ALTER + default 兜底,dev SQLite 也能跑)

---

## 2. 实现参考

### 验收锚点

- `test/ip_probe_worker/spec.md` v2 §6(IPStatus 语义)+ §G(代码片段)
- `db/models.py` `VPSTask` 完整结构(IPTask 1:1 对称参照)
- CLAUDE.local.md §数据模型契约 §生命周期 / §DB 增量写入

### 改动文件清单

#### 改 `db/models.py`

```text
1. 新增类 IPStatus(放在 IPRecord 之前, 跟 VPSStage / ProxyStatus 风格一致):
   - USABLE = "usable"
   - USING = "using"
   - 类 docstring 说明状态机语义 + 谁写

2. IPRecord 类内追加 status 字段(放在 is_active 附近, 跟其他业务状态字段聚合):
   status: Mapped[str] = mapped_column(
       String(16), default=IPStatus.USABLE, nullable=False
   )

3. IPRecord.from_form 工厂方法:
   - 入参不加 status(默认 USABLE)
   - 返回 cls(..., status=IPStatus.USABLE) 显式标注

4. 新增类 IPTask(放在 VPSTask 后面, 1:1 对称):
   - 表名 ip_task
   - 必填: ip_id (FK -> ip_record.id, ondelete=RESTRICT, index)
   - 谁配的谁写: vps_id (FK -> vps_record.id, ondelete=RESTRICT, nullable=True, index)
   - 跟 VPSTask 完全对称的字段:
       status / retry_count / next_run_at
       last_error_code / last_error_msg
       worker_id / locked_until
       created_at / updated_at / completed_at
   - 索引:
       Index("ix_ip_task_status_next_run", "status", "next_run_at")
       Index("ix_ip_task_ip_status", "ip_id", "status")
   - __repr__ 跟 VPSTask 同款(不打长字段)
   - TaskStatus 枚举直接复用, 不新加

5. 不动:
   - VPSStage / VPSRecord / VPSTask
   - ProxyStatus / ProxyRecord
   - IPProtocol
   - TaskStatus (复用)
   - 所有 from_form / from_extracted_binding / from_new_deployment 等其他方法
```

#### 新建 `test/_data_structures/test_ip_record_status.py`

```text
单测 IPRecord.status 字段 + IPStatus 枚举:
- 默认值为 USABLE
- 可设为 USING
- IPRecord.from_form 默认带 status=USABLE
- IPStatus 常量值正确 ("usable" / "using")
```

#### 新建 `test/_data_structures/test_ip_task.py`

```text
单测 IPTask 表(参照 test_vps_task.py 风格):
- 建表成功(metadata 含 ip_task)
- 必填字段验证: ip_id 必填
- vps_id 默认 NULL(IPProbeWorker 建任务时不写)
- vps_id 可被 update(ProxyDeployWorker 回填)
- 索引存在(扫表 + 按 ip_id 查活跃)
- TaskStatus 枚举值跟 VPSTask 共用
```

#### 不动

```text
- db/base.py / db/engine.py / db/session.py
- 任何 workers/ / xray/ / tools/ / services/ 现有代码
- VPSTask / VPSRecord / ProxyRecord 任何字段
- 现有迁移逻辑 (dev SQLite Base.metadata.create_all 自动建新表)
```

### 实现轮廓

```python
# db/models.py 关键片段:

class IPStatus:
    """ip_record.status 状态机(IPProbeWorker / ProxyDeployWorker 协同维护)。
    
    谁推进:
      IPProbeWorker 入库时    → 永远写 USABLE
      ProxyDeployWorker 配置成功 → 同事务写 USING
      ProxyDeployWorker 配置失败 → 不动 status (保持 USABLE, 下次任务重新挑 VPS)
    
    业务含义:
      USABLE = IPProbeWorker 校验通过, 等 ProxyDeployWorker 来挑
      USING  = 已被某台生产 VPS 挂上, 真正在用
    
    跟 is_active 是独立维度:
      is_active = 整体还有效 (过期标 0)
      status    = 当前在不在被用 (业务流转)
    """
    USABLE = "usable"
    USING = "using"


class IPRecord(Base):
    # ... 现有字段 ...
    
    # ---------- 业务流转状态机 (T-11 新增) ----------
    status: Mapped[str] = mapped_column(
        String(16), default=IPStatus.USABLE, nullable=False
    )
    
    # ... 现有方法 ...
    
    @classmethod
    def from_form(cls, ...) -> "IPRecord":
        # ... 现有逻辑不动, 只追加 status 默认值 ...
        return cls(
            ...,
            status=IPStatus.USABLE,  # T-11 新增
        )


class IPTask(Base):
    """IP 挂机部署任务。ProxyDeployWorker 消费。
    
    每条 = 把某条新登记的 IP 挂到某台生产 VPS 当 outbound 的活儿。
    IPProbeWorker 入库 IP 时建一条 pending, ProxyDeployWorker 扫表领。
    
    锁粒度 = task(跟 VPSTask 同样的软锁机制)。
    vps_id 谁配的谁写: IPProbeWorker 建任务时留 NULL, 
    ProxyDeployWorker 挑到 VPS 后回填(同事务里跟 vps.stage=running 联动)。
    """
    __tablename__ = "ip_task"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    ip_id: Mapped[int] = mapped_column(
        ForeignKey("ip_record.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    vps_id: Mapped[int | None] = mapped_column(
        ForeignKey("vps_record.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    
    status: Mapped[str] = mapped_column(
        String(16), default=TaskStatus.PENDING, nullable=False
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    
    last_error_code: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    last_error_msg: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    
    worker_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    __table_args__ = (
        Index("ix_ip_task_status_next_run", "status", "next_run_at"),
        Index("ix_ip_task_ip_status", "ip_id", "status"),
    )
    
    def __repr__(self) -> str:
        return (
            f"<IPTask id={self.id} ip={self.ip_id} vps={self.vps_id or '?'} "
            f"status={self.status} retry={self.retry_count}>"
        )
```

### 数据结构

| 字段 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `ip_record.status` | usable / using 业务状态 | ProxyDeployWorker 挑机查询 | IPProbeWorker 入库(usable) / ProxyDeployWorker 配置成功(using) |
| `ip_task.ip_id` | 指向的 IP 记录 | 工人 | IPProbeWorker 建任务时 |
| `ip_task.vps_id` | 指向的 VPS 记录(谁配的谁写) | 排障 / 续跑 | ProxyDeployWorker 挑到 VPS 后回填 |
| `ip_task.status` | TaskStatus 枚举 | worker 扫表 | worker 全过程 |
| `ip_task.locked_until / worker_id` | 软锁 | worker 扫表 | worker 抢任务时 |

### 缺工具 / 缺信息先报告

- dev SQLite 已有 `ip_record` 表(无 status 列)→ 是否手动加列?或允许 drop + create_all 重建?
  → 按 CLAUDE.local.md §Dev DB 迁移规则: `ALTER TABLE ip_record ADD COLUMN status VARCHAR(16) DEFAULT 'usable' NOT NULL;`
  实现者执行 `sqlite3 db/vps_server.db "ALTER TABLE ip_record ADD COLUMN status VARCHAR(16) DEFAULT 'usable' NOT NULL;"` 后再跑测试
- `ip_task` 表 dev SQLite 缺 → `Base.metadata.create_all(engine, tables=[IPTask.__table__])`

---

## 3. 验收交付

### 测试用例

#### TC-11-a `test/_data_structures/test_ip_record_status.py`

业务故事:

```text
我作为 IPProbeWorker, 入库时希望 status 默认 USABLE;
作为 ProxyDeployWorker, 配置成功后能把 status 改成 USING。
```

输入 / 预期:

- `IPRecord.from_form(..., 不传 status)` → 实例 `status == IPStatus.USABLE`
- `IPStatus.USABLE == "usable"`, `IPStatus.USING == "using"`
- 写入 DB 后 select 出来 status 仍是 usable
- update status 为 using → select 仍是 using

#### TC-11-b `test/_data_structures/test_ip_task.py`

业务故事(参照 `test_vps_task.py`):

```text
IPProbeWorker 入库 IP 后建一条 ip_task(pending, vps_id=NULL);
ProxyDeployWorker 抢到任务后回填 vps_id;
完工时 status='done', completed_at 写入。
```

输入 / 预期:

- 建 `IPTask(ip_id=1)`(不传 vps_id)→ DB 里 vps_id 为 NULL
- 默认 `status=pending, retry_count=0, worker_id="", last_error_code=""`
- 更新 `task.vps_id = 7` → DB 里 vps_id == 7
- 更新 `task.status = "in_progress"`, `task.locked_until = ...` → 都生效
- 索引 `ix_ip_task_status_next_run` 和 `ix_ip_task_ip_status` 存在
- `__repr__` 不打 last_error_msg

### 必跑测试命令

```bash
VPS_SERVER_TESTING=1 pytest test/_data_structures/test_ip_record_status.py test/_data_structures/test_ip_task.py -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。

- [ ] 开工前文件名 waiting → doing
- [ ] `db/models.py` 新增 `IPStatus` 类
- [ ] `IPRecord` 加 `status` 字段 + `from_form` 默认带 USABLE
- [ ] `db/models.py` 新增 `IPTask` 类(1:1 对称 VPSTask)
- [ ] 新增 `test/_data_structures/test_ip_record_status.py` + `test_ip_task.py`
- [ ] dev SQLite `ip_record` 已 ALTER 加 status 列
- [ ] dev SQLite `ip_task` 表已 create
- [ ] 必跑测试命令 PASS
- [ ] 不动 VPSTask / VPSRecord / ProxyRecord 任何字段
- [ ] 不动 services / workers / xray / tools 任何代码
- [ ] 完成记录段已填(测试结果原样贴)

### 实现过程记录(实现者完工时填)

```text
改动文件:
- db/models.py
- test/_data_structures/test_ip_record_status.py
- test/_data_structures/test_ip_task.py

测试结果:
- VPS_SERVER_TESTING=1 pytest ... -> <result>

dev DB 迁移:
- ALTER TABLE ip_record ADD COLUMN status ... -> ok
- create_all(IPTask) -> ok

偏差 / 风险:
- <none | details>
```

### Claude 验收检查清单

□ 对照 spec v2 §6 / §G 检查字段名 / 默认值 / 索引
□ 对照 VPSTask 检查 IPTask 字段一一对称(除 vps_id nullable)
□ 跑必跑测试命令并记录结果
□ 检查实现者完工标准全部满足
□ 检查 dev DB 迁移已完成
□ 偏差但合理 -> 抛给用户决策
□ 偏差不合理 -> 打回实现者修改

---

## 完成记录(done 时追加)

```text
完成日期:
完成 commit:
任务状态: doing -> done
改动摘要:
测试命令:
测试结果:
未覆盖风险:
后续任务:
```
