# T-02 建 vps_task 异步任务表 + 通用 TaskStatus 类

**ID**: T-02
**前置依赖**: 无(可与 T-01 并行)
**后续依赖**: T-04 SSHWorker._入库派任务 需要本任务完成

---

## 验收锚点

- `CLAUDE.local.md` §4 task 表 + worker 接力规则
- `docs/adr/0001-workers-replace-services.md` §决策 §3(task 表是异步协调媒介)
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md` §决策(stage / used_port_count 由谁写)
- `tests_behavior/xray_worker/spec.md` §2 入口契约 + §7 失败处理(状态机)
- `tests_behavior/ssh_worker/spec.md` §3 路线 B ④(入库时建一条 vps_task)

## 改动文件清单

### 改 `db/models.py`

```
① 加 class TaskStatus 常量类(通用,给所有 task 表共用):
     PENDING         = "pending"
     IN_PROGRESS     = "in_progress"
     PENDING_RETRY   = "pending_retry"
     DONE            = "done"
     FAILED          = "failed"
     CIRCUIT_BROKEN  = "circuit_broken"

② 加 class VPSTask(Base) ORM 模型, 字段见下面

③ 注意:本任务不建 ip_task(留给 T-XX 单独任务,共用 TaskStatus)
```

### 改 `db/__init__.py`

```
__all__ 加: VPSTask, TaskStatus
```

### 新建 `tests_behavior/_data_structures/test_vps_task.py`

11 个 schema 测试(详见下面)

### 不动

```
不动 services/* / xray/* / workers/* / tools/* 等
仅触碰 db/models.py + db/__init__.py + 新测试文件
```

---

## 字段轮廓(给实现者参考)

| 字段名 | 类型 | 默认值 | 含义(大白话) |
|--------|------|--------|------------|
| id | int PK autoincrement | — | 任务编号 |
| vps_id | int FK→vps_record.id | 必填,nullable=False | 哪台 VPS |
| status | str(16) NOT NULL | "pending" | 现在到哪一步了 |
| retry_count | int NOT NULL | 0 | 失败了几次 |
| next_run_at | datetime NOT NULL | now() | 下次什么时候再试 |
| last_error_code | str(32) | "" | 最近一次失败的错误代号 |
| last_error_msg | str(255) | "" | 最近一次失败的人话原因 |
| worker_id | str(64) | "" | 谁在锁着这条 |
| locked_until | datetime / nullable | NULL | 锁过期时间(软锁) |
| created_at | datetime NOT NULL | server_default now() | 啥时候建的 |
| updated_at | datetime NOT NULL | server_default now() + onupdate now() | 啥时候改的 |
| completed_at | datetime / nullable | NULL | 啥时候完工的(仅 status=done 时填) |

## 索引

```
ix_vps_task_status_next_run  on (status, next_run_at)
   给谁用: worker 扫表领活儿(每秒发生),按 pending+next_run_at 查
   原因  : 这是最高频查询,必须索引

ix_vps_task_vps_status       on (vps_id, status)
   给谁用: 查"VPS#X 当前有没有任务在跑"(比如挑可投产 VPS)
   原因  : 用于"在 VPS 池里找空闲机器"

注:不用复合主键,id 单列 PK 即可
注:不加 worker_id 索引(查询不频繁)
```

---

## 实现轮廓(给实现者参考,不强制 1:1)

### TaskStatus 类风格(沿用现有项目状态常量类风格)

```python
class TaskStatus:
    """task 表通用状态机(vps_task / 未来 ip_task 共用)。

    谁推进:
      pending → in_progress    : worker 抢到锁
      in_progress → done       : 干完
      in_progress → pending_retry: 临时错(网络抖/超时),退避后重试
      in_progress → failed     : 永久错(密码改了),需人介入
      in_progress → circuit_broken: 连续 N 次同 code,熔断
    """

    PENDING         = "pending"
    IN_PROGRESS     = "in_progress"
    PENDING_RETRY   = "pending_retry"
    DONE            = "done"
    FAILED          = "failed"
    CIRCUIT_BROKEN  = "circuit_broken"
```

### VPSTask 模型示例(沿用现有 ORM 风格)

```python
class VPSTask(Base):
    """VPS 装机/维护类任务。XrayWorker 消费。

    每条 = 一个"装 xray / 启停 xray / 纳管"活儿。
    SSHWorker 入库 VPS 时建一条 pending,
    XrayWorker 扫表领活儿。
    """

    __tablename__ = "vps_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    vps_id: Mapped[int] = mapped_column(
        ForeignKey("vps_record.id", ondelete="RESTRICT"),
        nullable=False, index=True,
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
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_vps_task_status_next_run", "status", "next_run_at"),
        Index("ix_vps_task_vps_status", "vps_id", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<VPSTask id={self.id} vps={self.vps_id} "
            f"status={self.status} retry={self.retry_count}>"
        )
```

### dev SQLite 迁移说明(给用户)

```
本任务只加新表, 不动旧表, 因此:

  uv run python -c "from db import engine; from db.base import Base; \
                    from db.models import VPSTask; \
                    Base.metadata.create_all(engine, tables=[VPSTask.__table__])"

旧 dev 数据保留(本任务不影响)。
```

---

## 测试用例(实现者按这些写 .py)

测试文件: `tests_behavior/_data_structures/test_vps_task.py`

```
TC-01  建新 VPSTask, status 默认 "pending"
TC-02  retry_count 默认 0
TC-03  next_run_at 默认 = now() 自动(server_default)
TC-04  worker_id / locked_until / completed_at 默认可空/空串
TC-05  TaskStatus 类只有 6 个常量(PENDING/IN_PROGRESS/PENDING_RETRY/
       DONE/FAILED/CIRCUIT_BROKEN), 无其他
TC-06  status 字段能存这 6 个值, 存进 DB 后查回正确
TC-07  vps_id FK 引用不存在的 vps_record 报错(SQLite FK 开启时)
TC-08  update task 后 updated_at 自动变(onupdate=now)
TC-09  update completed_at 业务约束:仅 status='done' 时填(DB 不强制,
       但测试验证业务层调用模式)
TC-10  两个索引建好:查 sqlite_master 含 ix_vps_task_status_next_run +
       ix_vps_task_vps_status
TC-11  ⚠️ 抢锁原子性(标记 @skip,等真机环境再测)
       预期场景:两个 worker 同时跑
         UPDATE vps_task SET status='in_progress', worker_id=?
         WHERE id=? AND status='pending'
       只能 1 个 affected_rows=1, 另一个 affected_rows=0
       SQLite 单连接难测,加 pytest.mark.skip 或 unittest skip
       注释:"等真机 PostgreSQL/MySQL 多连接环境验证"
TC-12  __repr__ 输出 id/vps_id/status/retry_count,
       不输出 last_error_msg(长字段)
```

测试通用约束:
- 用 SQLite 内存 DB, 不污染 dev
- 测试前后清表干净

---

## 实现者完工标准(自检清单)

```
- [ ] 11 个测试全过(TC-11 通过 skip 计入"全过")
- [ ] db/models.py 内 grep 'class VPSTask' 唯一一处
- [ ] db/models.py 内 grep 'class TaskStatus' 唯一一处
- [ ] db/__init__.py __all__ 含 VPSTask + TaskStatus
- [ ] 不动 services/* / xray/* / workers/* / tools/* / proxy/*
- [ ] git add 只 add db/models.py / db/__init__.py / 新测试
- [ ] commit 标题: feat(db): 加 VPSTask 异步任务表 + TaskStatus 常量
- [ ] 任务结尾留 dev DB 建表说明给用户(上面那段)
```

---

## Claude 验收检查清单

```
□ 跑 tests_behavior/_data_structures/test_vps_task.py 全过(含 skip TC-11)
□ git diff db/models.py:
    - 确认加了 class TaskStatus(6 常量没多没少)
    - 确认加了 class VPSTask(字段没多没少, 类型对)
    - 确认 2 个索引名对
    - 确认 vps_id FK 指向 vps_record.id
□ git diff db/__init__.py: 确认 __all__ 更新
□ 对照 ADR-0001/0002 检查"task 表是协调媒介"的语义
□ 字段命名风格: 沿用现有简洁风格(last_error_code / next_run_at)
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回让实现者改
```

---

## 备注

本任务与 T-01 (VPSRecord schema v2) **完全独立**, 可并行实施:
- 都改 db/models.py 但改不同位置(T-01 改老类, T-02 加新类), git 合并不冲突
- 各自的测试文件独立

完工后 + T-01 完工后, T-04 (SSHWorker 实现) 才能开始(数据层依赖齐了)。
