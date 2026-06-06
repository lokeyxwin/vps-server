# T-01 VPSRecord schema v4：stage 改 2 值 + 删冗余字段 + port 去默认

**ID**: T-01
**前置依赖**: 无（可与 T-02 vps_task 并行）
**后续依赖**: T-04 SSHWorker 实现需要本任务完成

---

## 验收锚点（实现者按这些标准做）

- `tests_behavior/ssh_worker/spec.md` v4 §3 路线 B ④（stage='connectable' 入库 + xray_version=""）
- `tests_behavior/ssh_worker/spec.md` v4 §5 不变量：
  - SSHWorker 写入的 stage 只能是 `connectable`
  - SSHWorker 写入的 xray_version 永远为空字符串
  - **错误信息只住 vps_task 表，不住 vps_record**
  - SSHWorker 只 touch vps_record + vps_task 两张表
- `tests_behavior/ssh_worker/spec.md` v4 §0 实现者硬约束（旧代码姿势 + 缺工具先报告）
- `docs/adr/0002-takeover-mode-handled-by-xray-worker.md`（字段语义改变 + 端口策略）
- `CLAUDE.local.md` §0 legacy 代码三档姿势表（services / test / xray 旧函数禁直接 import）

---

## 改动文件清单

### 改 `db/models.py`

```
① 旧 class XrayStatus → 改名 class VPSStage
   只保留 2 个常量:
     CONNECTABLE  = "connectable"   # 此刻没工人在用 + 验证过能连(=空闲可拿)
     RUNNING      = "running"       # 有任意工人正在用这台服务器(=被占用中)

   删掉旧值(全部):
     NOT_INSTALLED / INSTALLING / INSTALL_FAILED /
     STOPPED / UNINSTALLED

   注意: v3 spec 曾计划留 UNREACHABLE 第 3 值, v4 删除（连不上一律抛回不入库）

② VPSRecord 字段改名:
     xray_status      → stage         (默认值 "connectable", 由 SSHWorker 入库时写)
     idle_port_count  → used_port_count (默认值 0, XrayWorker 纳管时写)

③ VPSRecord 字段**整删**:
     xray_status_message   (原 v2 计划改名 stage_message, v4 整删)
                            理由: 错误信息只住 vps_task 表 (last_error_code / last_error_msg)
                            vps_record 保持纯净, 不被错误状态污染

④ VPSRecord 字段调整:
     port 去掉 default=22  (业务层必填强制, ORM 不兜底)

⑤ VPSRecord 字段保留不动(明确写出, 避免误删):
     xray_version          (SSHWorker 永远不写; XrayWorker 第一次干完写)
     xray_installed_at     (XrayWorker 第一次装完写; 巡检参考)
     xray_last_checked_at  (巡检维护; SSHWorker 不写)
     is_active             (1=可用 0=过期, 巡检维护; SSHWorker 不写)
     provider_domain / ip / username / password_encrypted / os_name / os_version /
     expire_date / created_at / updated_at  (全部不动)

⑥ stage 字段语义注释 (新):
     connectable -> "此刻没工人在用 + 验证过能连接, 可被任何工人抢去用"
     running     -> "有任意工人正在用这台服务器, 别的工人挑机时跳过此机"
     谁推进     -> SSHWorker 入库写 connectable
                  抢到这台机的工人 (XrayWorker / 未来巡检等) 写 running
                  干完释放 -> 改回 connectable (XrayWorker 自管, 失败时保持 running 锁住等人介入)
```

### 改 `db/__init__.py`

```
__all__ 把 XrayStatus 移除, 加 VPSStage
```

### 新建 `tests_behavior/_data_structures/test_vps_record_v4.py`

8 个 schema 测试（详见下面）。

> ⚠️ 文件名用 v4 而不是 v2，对齐 spec.md v4。如旧 v2 测试文件已存在，删除或重命名。

### 不动（本任务接受这些副作用）

```
⚠️ services/* 旧业务函数会跑不通 (引用了旧 XrayStatus 等)
  - services/vps_register.py
  - services/vps_init.py
  - services/ip_register.py
  实现者**不修复**, 留作 cp 思路参照（CLAUDE.local §0 legacy 姿势表）

⚠️ test/test_vps*.py / test/test_ip_register.py 等旧单测会失败
  实现者**不修复** (它们测的是旧 services)
  仅需保证新 tests_behavior/_data_structures/test_vps_record_v4.py 全过

⚠️ xray/manager.py 旧 XrayManager 类可能引用旧字段名
  实现者**不修复** (XrayWorker 那条链由 task/03 + task/07 处理)
```

---

## 实现轮廓（给实现者参考，不强制 1:1）

### VPSStage 类风格（沿用现有项目状态常量类风格）

```python
class VPSStage:
    """VPS 占用状态机（2 值，spec v4 拍板）。

    谁推进:
      SSHWorker 入库时    → 永远写 CONNECTABLE（spec v4 §5 不变量）
      抢到这台机的工人     → 写 RUNNING 锁住（XrayWorker / 未来巡检等）
      工人干完释放         → 改回 CONNECTABLE
      工人失败            → 保持 RUNNING 锁住等人介入（spec v4 Q2 拍板）

    业务含义:
      CONNECTABLE = 此刻没工人在用 + 验证过能连，可被任意工人抢
      RUNNING     = 有任意工人正在用，别的工人挑机时跳过
    """

    CONNECTABLE = "connectable"
    RUNNING     = "running"
```

### VPSRecord 字段改动示例

```python
class VPSRecord(Base):
    # ----- 不动的字段（保留语义）-----
    id: Mapped[int] = ...
    provider_domain: Mapped[str] = ...
    ip: Mapped[str] = ...  # unique
    username: Mapped[str] = ...
    password_encrypted: Mapped[bytes] = ...
    os_name: Mapped[str] = ...
    os_version: Mapped[str] = ...
    expire_date: Mapped[date | None] = ...
    is_active: Mapped[int] = ...
    xray_version: Mapped[str] = ...        # SSHWorker 永远不写
    xray_installed_at: Mapped[datetime | None] = ...
    xray_last_checked_at: Mapped[datetime | None] = ...
    created_at / updated_at = ...

    # ----- port: 去 default -----
    port: Mapped[int] = mapped_column(Integer, nullable=False)  # ← 不再有 default=22

    # ----- 旧 xray_status → 改名 stage -----
    stage: Mapped[str] = mapped_column(
        String(32), default=VPSStage.CONNECTABLE, nullable=False
    )

    # ----- 旧 idle_port_count → 改名 used_port_count -----
    used_port_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ----- 删除 xray_status_message: 错误住 vps_task -----
    # (无字段, 整删)
```

### `from_form()` 工厂方法调整

旧的 `from_form()` 的签名中 `port: int = 22` 也要去掉默认值（业务层必填强制）。
但要保留：`os_name=""` 等可选默认（spec v4 §6 OS 读不到留空入库）。

### dev SQLite 迁移说明（写在任务结尾给用户）

```
dev DB 因字段改名 + 删除无法增量迁移, 实现者完工后请用户手动:

  sqlite3 db/vps_server.db
    DROP TABLE vps_record;
    DROP TABLE proxy_record;     # 如果存在依赖
    DROP TABLE ip_record;        # 如果存在依赖
  uv run python -c "from db import engine; from db.base import Base; \
                    from db.models import VPSRecord; \
                    Base.metadata.create_all(engine)"

旧 dev 数据全部清空（本来就是测试用，可接受）。
```

---

## 测试用例（实现者按这些写 .py）

测试文件: `tests_behavior/_data_structures/test_vps_record_v4.py`

```
TC-01  新建 VPSRecord, stage 默认值应为 "connectable"
       验证: VPSRecord(...).stage == VPSStage.CONNECTABLE

TC-02  新建 VPSRecord, used_port_count 默认值应为 0

TC-03  旧字段不存在（应 AttributeError 或 ORM 字段映射不存在）:
         - xray_status
         - xray_status_message
         - stage_message      # v2 曾计划改名, v4 整删
         - idle_port_count

TC-04  VPSStage 类只有 2 个常量:
         CONNECTABLE = "connectable"
         RUNNING     = "running"
       不存在 UNREACHABLE / NOT_INSTALLED / INSTALLING / STOPPED 等

TC-05  stage 字段能存这 2 个值, 存进 DB 后查回正确
       VPSStage.CONNECTABLE / RUNNING 都能 round-trip

TC-06  port 字段无 default value（构造时不传 port 应报错或 None）
       验证: VPSRecord 构造时 port 必填
       验证方式: 用 inspect/sqlalchemy schema 检查 column.default is None
                或者 try cls(ip=..., username=..., password=...) 不传 port 应 fail

TC-07  update used_port_count 从 0 → 5 → 3, 每次查回来值反映正确

TC-08  __repr__ 输出含 stage 字段,
       **不**输出 password_encrypted / username 等敏感字段
       (沿用现有 __repr__ 风格)
```

测试通用约束:
- 用 SQLite 内存 DB (`get_engine("sqlite")` 或 in-memory), 避免污染 dev DB
- 沿用 test/test_vps_model.py 现有测试风格（**只参考思路，不 import 旧测试**）
- 测试前后清表干净

---

## 实现者完工标准（自检清单）

```
- [ ] db/models.py 改完, grep 'xray_status' 在 db/models.py 内无残留
- [ ] db/models.py 改完, grep 'xray_status_message' / 'stage_message' 在 db/models.py 内无残留
- [ ] db/models.py 改完, grep 'idle_port_count' 在 db/models.py 内无残留
- [ ] db/models.py 改完, grep 'class XrayStatus' 在 db/models.py 内无残留
- [ ] db/models.py 改完, VPSStage 类只有 2 个常量（grep 'UNREACHABLE' 也应该无）
- [ ] db/models.py 改完, port 字段无 default=22
- [ ] db/models.py 改完, from_form() 签名 port 无默认值
- [ ] db/__init__.py 已把 XrayStatus 移除, 加 VPSStage
- [ ] 8 个测试全过(uv run python -m unittest tests_behavior._data_structures.test_vps_record_v4)
- [ ] 实现者**不动** services/* / xray/manager.py / xray/service.py / xray/config.py
- [ ] 任务结尾留 dev DB 迁移说明给用户（上面那段）
- [ ] git add 只 add db/models.py / db/__init__.py / 新测试文件
- [ ] commit 标题: feat(db): VPSRecord schema v4 - stage 2 值 + 删冗余字段 + port 必填
- [ ] 如遇缺工具/不清楚的设计点: 不要自己拍, **停下来报告给 Claude / 用户**
       (spec v4 §0 第 2 条规则)
```

---

## 实现过程记录（实现者完工时填）

> 实现期间如果造了新工具/方法/常量，按这个格式记录：

```
- 造了 <工具名>（类 / 函数 / 方法）
  住 <文件路径>
  干啥 <一句话功能>
  测试 <对应 TC 编号>
  审批 用户在 <对话/issue> 批准
```

如果只是按本任务单清单改了字段，没造新工具，写"无新增工具"即可。

---

## Claude 验收检查清单

```
□ 跑 tests_behavior/_data_structures/test_vps_record_v4.py 全过
□ git diff db/models.py:
    - 确认 class XrayStatus → VPSStage 改名 + 只 2 常量
    - 确认 xray_status → stage 改名, default=VPSStage.CONNECTABLE
    - 确认 xray_status_message 整删（不是改名）
    - 确认 idle_port_count → used_port_count 改名
    - 确认 port 字段无 default=22
□ git diff db/__init__.py: 确认 __all__ 更新（去 XrayStatus 加 VPSStage）
□ 对照 spec v4 §5 不变量 检查字段语义一致
□ 实现者是否乱改了 services/* / xray/* (应该没改)
□ 实现过程记录段是否填了（造了啥/无新增工具）
□ 偏差但合理（例如实现者建议字段叫别的名字, 比如 `stage` → `占用状态` 中文等）
  → 抛给用户决策
□ 偏差不合理 → 打回让实现者改
```

---

## 备注

本任务完成后会**临时打破** services/* 旧业务（MCP rgvps / rgip 工具暂时不可用），
直到 T-04 (SSHWorker) + T-06 (rgvps 入口) 完成才能恢复。这是 ADR-0001 架构升级
的预期代价。

新链路完工前如果需要紧急用旧业务，从 git 历史回滚本 commit 即可恢复。

---

## v4 vs v2 修订总结

| 项 | v2 计划 | v4 实际 |
|---|--------|--------|
| VPSStage 常量数 | 3 (CONNECTABLE/RUNNING/UNREACHABLE) | **2 (CONNECTABLE/RUNNING)** |
| stage_message 字段 | 由 xray_status_message 改名保留 | **整删**（错误住 vps_task） |
| port 字段 | 保留 default=22 | **去 default**（必填强制） |
| stage 语义 | connectable=入库后 / running=可投产 | **connectable=空闲可拿 / running=被占用中**（语义全变） |
| 测试 TC 数 | 8 | 8（内容改） |
