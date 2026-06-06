# T-01 VPSRecord schema v2:加 stage 字段 + 端口字段改名

**ID**: T-01
**前置依赖**: 无(可与 T-02 vps_task 并行)
**后续依赖**: T-04 SSHWorker 实现需要本任务完成

---

## 验收锚点(实现者按这些标准做)

- `tests_behavior/ssh_worker/spec.md` §3 路线 B ④(stage='connectable' 入库)
- `tests_behavior/ssh_worker/spec.md` §5 不变量(stage 只能 connectable/unreachable)
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md` §4(字段语义改变)
- `CLAUDE.local.md` §9 工人阵容(stage 推进权属)
- `CLAUDE.md` §7 Python 风格指引(类常量风格)

## 改动文件清单

### 改 `db/models.py`

```
① 旧 class XrayStatus → 改名为 class VPSStage
   只保留 3 个常量:
     CONNECTABLE  = "connectable"   # SSHWorker 入库后
     RUNNING      = "running"       # XrayWorker 干完可投产
     UNREACHABLE  = "unreachable"   # 连不上(SSH 失败重试仍不行)

   删掉旧值:
     NOT_INSTALLED / INSTALLING / INSTALL_FAILED /
     STOPPED / UNINSTALLED

② VPSRecord 字段改名:
     xray_status          → stage         (默认值 "connectable")
     xray_status_message  → stage_message
     idle_port_count      → used_port_count

③ VPSRecord 字段保留不动:
     xray_version             (xray 软件属性,与 stage 解耦)
     xray_installed_at        (同上)
     xray_last_checked_at     (同上)
     is_active                (过期表达,不进状态机)
     其他所有字段

④ stage 字段语义:
     存什么 → 业务层用 VPSStage 常量约束(不强制 DB CHECK)
     默认值 → VPSStage.CONNECTABLE
     用谁的话来表达 → 见 CLAUDE.local.md §9

⑤ used_port_count 字段语义:
     已配置且内 ping 通的代理出口数量(整数,默认 0)
     由 XrayWorker 纳管时写;ProxyDeployWorker 增减
```

### 改 `db/__init__.py`

```
把 XrayStatus 从 __all__ 移除, 加 VPSStage
```

### 新建 `tests_behavior/_data_structures/test_vps_record_v2.py`

8 个 schema 测试(详见下面)

### 不动(本任务接受这些副作用)

```
⚠️ services/* 旧业务函数会跑不通(引用了旧 XrayStatus 等)
  - services/vps_register.py
  - services/vps_init.py
  - services/ip_register.py
  实现者不需要修复,留对照参考

⚠️ test/test_vps*.py / test/test_ip_register.py 等旧单测会失败
  实现者不需要修复(它们测的是旧 services)
  仅需保证 tests_behavior/_data_structures/test_vps_record_v2.py 全过

⚠️ xray/manager.py 旧 Manager 类可能引用旧字段名
  实现者不需要修复
```

---

## 实现轮廓(给实现者参考,不强制 1:1)

### VPSStage 类风格(沿用现有 XrayStatus 风格)

```python
class VPSStage:
    """VPS 生命周期阶段。

    谁来推进:
      SSHWorker 入库时    → CONNECTABLE 或 UNREACHABLE
      XrayWorker 干完时   → RUNNING
    """

    CONNECTABLE  = "connectable"
    RUNNING      = "running"
    UNREACHABLE  = "unreachable"
```

### VPSRecord 字段改动示例

```python
class VPSRecord(Base):
    # ... 其他字段不动 ...

    # 旧 xray_status → 改名 stage
    stage: Mapped[str] = mapped_column(
        String(32), default=VPSStage.CONNECTABLE, nullable=False
    )

    # 旧 xray_status_message → 改名 stage_message
    stage_message: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # 旧 idle_port_count → 改名 used_port_count
    used_port_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # xray_version / xray_installed_at / xray_last_checked_at / is_active 等保留不动
```

### dev SQLite 迁移说明(写在任务结尾给用户)

```
dev DB 因字段改名无法增量迁移,实现者完工后请用户手动:

  sqlite3 db/vps_server.db
    DROP TABLE vps_record;
    DROP TABLE proxy_record;
    DROP TABLE ip_record;
  uv run python -c "from db import engine; from db.base import Base; \
                    from db.models import VPSRecord, ProxyRecord, IPRecord; \
                    Base.metadata.create_all(engine)"

旧 dev 数据全部清空(本来就是测试用,可接受)。
```

---

## 测试用例(实现者按这些写 .py)

测试文件: `tests_behavior/_data_structures/test_vps_record_v2.py`

```
TC-01  新建 VPSRecord, stage 默认值应为 "connectable"
TC-02  新建 VPSRecord, used_port_count 默认值应为 0
TC-03  旧字段不存在: xray_status / xray_status_message / idle_port_count
       (尝试访问应 AttributeError 或 ORM 字段映射不存在)
TC-04  VPSStage 类只有 3 个常量:
       CONNECTABLE / RUNNING / UNREACHABLE
       不存在 NOT_INSTALLED / INSTALLING / STOPPED 等旧值
TC-05  stage 字段能存这 3 个值:
       VPSStage.CONNECTABLE / RUNNING / UNREACHABLE
       存进 DB 后查回来值正确
TC-06  stage_message 字段可空(默认 ""), 可设长字符串
TC-07  update used_port_count 从 0 → 5 → 3, 每次查回来值反映正确
TC-08  __repr__ 输出含 stage 字段,
       不输出 password_encrypted / username 等敏感字段
```

测试通用约束:
- 用 SQLite 内存 DB(`get_engine("sqlite")` 或类似), 避免污染 dev DB
- 沿用 test/test_vps_model.py 现有测试风格(参考但不必依赖)
- 测试前后清表干净

---

## 实现者完工标准(自检清单)

```
- [ ] db/models.py 改完, grep 'xray_status' 在 db/models.py 内无残留
- [ ] db/models.py 改完, grep 'idle_port_count' 在 db/models.py 内无残留
- [ ] db/models.py 改完, grep 'class XrayStatus' 在 db/models.py 内无残留
- [ ] db/__init__.py 已把 XrayStatus 移除, 加 VPSStage
- [ ] 8 个测试全过(uv run python -m unittest tests_behavior._data_structures.test_vps_record_v2)
- [ ] 实现者不动 services/* / xray/*(留对照,接受失效)
- [ ] 任务结尾留 dev DB 迁移说明给用户(上面那段)
- [ ] git add 只 add db/models.py / db/__init__.py / 新建测试文件,
       不 add 任何其他文件
- [ ] commit 标题: feat(db): VPSRecord schema v2 加 stage 字段
```

---

## Claude 验收检查清单

```
□ 跑 tests_behavior/_data_structures/test_vps_record_v2.py 全过
□ git diff db/models.py:
    - 确认 class XrayStatus → VPSStage 改名
    - 确认 3 个常量没多没少(CONNECTABLE/RUNNING/UNREACHABLE)
    - 确认 xray_status → stage 改名
    - 确认 idle_port_count → used_port_count 改名
□ git diff db/__init__.py: 确认 __all__ 更新
□ 对照 ADR-0002 §4 / spec §5 检查字段语义一致
□ 实现者是否乱改了 services/* / xray/*(应该没改)
□ 偏差但合理(例如实现者建议字段叫别的名字)→ 抛给用户决策
□ 偏差不合理 → 打回让实现者改
```

---

## 备注

本任务完成后会**临时打破** services/* 旧业务(MCP rgvps / rgip 工具暂时不可用),
直到 T-04 (SSHWorker) + T-06 (rgvps 入口) 完成才能恢复。这是 ADR-0001 架构升级
的预期代价。

新链路完工前如果需要紧急用旧业务,从 git 历史回滚本 commit 即可恢复。
