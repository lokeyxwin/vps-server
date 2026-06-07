# test/ —— 行为规约与行为测试

这里保存当前项目的行为规约和行为测试。实现 agent 读这里来确认模块应该达到什么行为。

## 目录组织：按工人分

```
test/
├── ssh_worker/              ← SSHWorker 行为测试
├── xray_worker/             ← XrayWorker 行为测试
├── ip_probe_worker/         ← IPProbeWorker 行为测试
├── proxy_deploy_worker/     ← ProxyDeployWorker 行为测试
└── _data_structures/        ← 数据结构 / schema 测试（产品视角不用看）
```

## 测试文件三段约定

每个 `.py` 测试文件分三段：

### 第一段 顶部注释：用例描述（用户钦定，禁改）

人类可读的"行为故事"——产品视角的描述。
谁读了都能判断"这段代码做的事 = 不是我要的效果"。

```python
"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-SSH-01 SSHWorker 敲开新机器（账密对、xray 未装）

故事：
  用户第一次提交一台从未登记的服务器（IP/账号/密码/端口）。
  SSHWorker 应该敲门成功、看到 xray 没装、把机器登记入库，
  然后派一条 install_xray 任务给后续工人。

输入：
  - DB 里没有这个 IP
  - 服务器密码对、端口对、xray 未装

预期：
  - 返回 status=queued + task_id
  - DB 新增 vps_record(stage=connectable, xray_version='')
  - DB 新增 task(type=install_xray, status=pending, vps_id 指向这台)

不应发生：
  - SSH 第二次连接（应该一次连接顺手采集完所有信息）
  - 状态被标成 install_xxx（那是后续工人的事）
========================================================================
"""
```

### 第二段 中部代码：测试实现

实现者写测试代码（pytest / unittest 都行），按上面的"预期"写断言。

### 第三段 尾部注释：测试结论（跑完追加，禁先填）

```python
# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
# 跑通日期：
# 偏差：
# 待用户决策事项：
# ========================================================================
```

## 实现者约束

- bug 直接修，不写在结论里
- 跟用例**有偏差但也合理**的设计选择 → 抛回 Claude，由用户决策
- 用例描述（第一段）**不许改**
- 跑完一定要写结论（第三段）

---

## 测试隔离套路：`session_scope` patch（每个 worker TC 都用得到）

worker 用 `db.session.session_scope` 上下文管理器拿 SQLAlchemy session。
测试时需要把它指到独立 in-memory SQLite，避免:
- 污染本地 dev DB
- 测试之间互相串行依赖
- 真跑迁移 / 加密 key 等副作用

### 通用套路（TC-01/03/04/05 都用）

```python
from contextlib import contextmanager
from unittest.mock import patch
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from db.base import Base
from db.models import VPSRecord, VPSTask

def _make_in_memory_engine():
    engine = create_engine("sqlite:///:memory:")

    # 开外键, 跟生产对齐
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # 只建本测真用到的表(别全建, 跑得快, 错位早暴露)
    Base.metadata.create_all(
        engine, tables=[VPSRecord.__table__, VPSTask.__table__]
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


class TestXxx(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = _make_in_memory_engine()

        @contextmanager
        def _fake_scope():
            s = self.Session()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        # patch 的是 worker 模块里 import 进来的 session_scope, 不是 db.session 源头
        self._patcher = patch(
            "workers.ssh_worker.session_scope", _fake_scope
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.engine.dispose()
```

### 关键 3 点

1. **patch 路径要写"worker 那边 import 进来的名字"**, 即
   `workers.ssh_worker.session_scope`，**不是** `db.session.session_scope`。
   因为 worker 顶部 `from db.session import session_scope` 已经把符号绑到 worker
   模块里了，必须在那个绑点替换才生效。

2. **`_fake_scope` 必须模仿真 `session_scope` 的 commit/rollback 语义**——否则
   worker 里 `with session_scope() as s:` 跑完 s.flush() 拿 id 后没 commit, 测试断言
   读不到行。

3. **`Base.metadata.create_all(tables=[X, Y])` 只建本测用到的表**, 别 `create_all()`
   全建。跑得快, 而且如果 worker 不小心动了别的表, 测试会直接挂在 "no such table" 上
   早暴露错位。

### 给后续 worker TC 复用

`xray_worker / ip_probe_worker / proxy_deploy_worker` 各自 TC 都同套路, 只是:
- `patch` 路径换成 `workers.<那个>_worker.session_scope`
- `create_all(tables=[...])` 列本工人 touch 的表(IPTask / IPRecord / ProxyRecord 等)
- 工人自己的 4 个私有方法各自单测, 主入口 TC 再用真 SQLite + mock 私有方法跑防回退

> **不要**把 `_make_in_memory_engine` 抽成 `conftest.py` 共享 fixture——
> 工人各管各的表, 共享 fixture 会引入"无关字段被建"的歧义。每工人 TC 各自维护
> 自己版本, 看清楚自己 touch 什么表, 比共用清楚。
