# T-15 ProxyDeploy 前置 — DB schema + config 常量

**ID**: T-15
**状态**: waiting
**前置依赖**: 无(纯 schema + config 改动, 不依赖其他 task)
**后续依赖**: T-16 (ProxyDeployWorker 实现) —— 需要 ProxyStatus 3 档枚举 + MAX_PORTS_PER_VPS 常量
**关联 ADR**: `docs/adr/0006-proxy-deploy-worker.md` §决策 §3 §6 §7
**关联 spec**: `test/proxy_deploy_worker/spec.md` §5 端口算法 + §6 收尾 DB 写入

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`
- [ ] 写代码前已将文件名改为 `task/doing_15_proxy_deploy_prereq.md`

### 必读清单

- [ ] `CLAUDE.md` / `CLAUDE.local.md`
- [ ] `docs/adr/0006-proxy-deploy-worker.md` §决策 §3 §6 §7(本任务直接落地)
- [ ] `db/models.py::ProxyStatus`(L137-142)/ `ProxyRecord`(L145-286): 当前 2 档枚举 + stale 注释
- [ ] `config.py`(L88 附近): 现有 `XRAY_DEFAULT_PORT`
- [ ] `toolbox/ports.py::COMMON_RESERVED_PORTS`: 已有的排除清单(核查它跟 ADR-0006 §6 说的 `EXCLUDED_PORTS` 是否一致)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "可以用 B 方案, 你给我的状态机来写, 你第三个字段我后续工人会用得到 ... 任务完成配置上去 内ping通, 防火墙放行剩外部问题 vps的已用+1" (cd5a5cba 2026-06-09)
> "status: str 三档枚举(B 方案): using / pending_fw / inactive"

### 业务目标

ADR-0006 决策落地的最小前置:

1. `proxy_record.status` 表达 "完全可用 / 等用户放行 / 已停用" 三态
2. 业务参数 `MAX_PORTS_PER_VPS` 集中到 config(后续 ProxyDeployWorker 调)
3. `EXCLUDED_PORTS` 概念在代码层落实(复用现有 `COMMON_RESERVED_PORTS` 或补全)

### 本任务要解决什么

- proxy_record 表能存 3 档状态(为 ProxyDeployWorker 收尾步骤铺路)
- ProxyDeployWorker 跑起来时一拿 config 就有 `MAX_PORTS_PER_VPS` / 端口排除清单可用

### 本任务不解决什么

- ❌ 不实现 ProxyDeployWorker 业务逻辑(T-16)
- ❌ 不动 MCP 工具(T-17)
- ❌ 不补历史 `proxy_record` 数据(无数据, 不需要 migration)

---

## 2. 实现参考

### 验收锚点

- `docs/adr/0006-proxy-deploy-worker.md` §决策 §3 (`MAX_PORTS_PER_VPS = 3`)
- `docs/adr/0006-proxy-deploy-worker.md` §决策 §6 (`EXCLUDED_PORTS` 排除清单)
- `docs/adr/0006-proxy-deploy-worker.md` §决策 §7 (proxy_record.status 3 档)
- `docs/adr/0006-proxy-deploy-worker.md` §影响清单(逐文件标了改动)

### 改动文件清单

#### 改 `db/models.py::ProxyStatus`(L137-142)

```python
class ProxyStatus:
    """proxy_record.status (3 档, ADR-0006 §决策 §7)。

    谁推进:
      ProxyDeployWorker 收尾 → 写 USING 或 PENDING_FW (内/外 ping 结果决定)
      封存的 ExpiryWorker / CleanupWorker (未来) → 写 INACTIVE
    """
    USING       = "using"        # 内通 + 外通, 完全可用
    PENDING_FW  = "pending_fw"   # 内通但外不通, 等用户去 VPS 厂商面板放行端口/安全组
    INACTIVE    = "inactive"     # 上游过期 / 主动停用 (吸收旧 EXPIRED 语义)
```

⚠️ 注意: 旧 `EXPIRED = "expired"` 直接改名 `INACTIVE = "inactive"`(语义吸收), 不留兼容别名。

#### 改 `db/models.py::ProxyRecord` 类注释(L145-158)

旧注释 stale, 改:

```python
class ProxyRecord(Base):
    """VPS 端口绑定记录(事实表)。

    每行 = 一台 VPS 上一个出口端口当前挂着哪条上游代理。
    一台 VPS 上限 `MAX_PORTS_PER_VPS` 条 (config.py, 默认 3, ADR-0006 §3)。
    端口策略: 高位随机, 避开 EXCLUDED_PORTS + 默认入口 + 该 VPS 已用端口
            (ADR-0002 §3 + ADR-0006 §6); 纳管端口原样接管不动 (ADR-0002 §2)。

    数据来源:
      - 纳管(XrayWorker 统一收尾): 从 xray config 抠出已部署的客户端 inbound
      - 新部署(ProxyDeployWorker): 把 ip_record 挂到 vps 时新建一行

    密码字段 inbound_pwd_encrypted 同 VPSRecord 约定: 拿到密文 bytes,
    明文走 get_inbound_pwd()。
    """
```

#### 改 `config.py`

L88 (`XRAY_DEFAULT_PORT = 18440`) 附近加:

```python
# 一台 VPS 最多挂几条业务代理 (ProxyDeployWorker 容量阈值, ADR-0006 §决策 §3)
# 业务参数, 真实负载下不够就调; 改这一个常量即可, worker 代码不动
MAX_PORTS_PER_VPS = 3
```

#### 核查 `EXCLUDED_PORTS` —— 不另立, 复用现有

`toolbox/ports.py::COMMON_RESERVED_PORTS` 已经存在(`compute_available_ports` 默认参数), **概念上就是 ADR-0006 §6 的 EXCLUDED_PORTS**。

实现者动作:

1. Read `toolbox/ports.py` 确认 `COMMON_RESERVED_PORTS` 内容
2. 对照 ADR-0006 §6 / ADR-0002 §3 要求("0-1023 well-known + 常用应用端口"), 看是否完整
3. 如果**完整** → 不另立常量, 在 `toolbox/ports.py` 文件顶部 docstring 加一行"⭐ 即 ADR-0006 §6 的 EXCLUDED_PORTS" + 不改 `config.py`
4. 如果**不完整** → 在 `toolbox/ports.py::COMMON_RESERVED_PORTS` 补全(加 SOCKS5 1080 / MySQL 3306 / Redis 6379 / 等常见应用端口); 不在 `config.py` 另起 `EXCLUDED_PORTS`

⚠️ 不允许另起 `config.py::EXCLUDED_PORTS` 跟 `toolbox/ports.py::COMMON_RESERVED_PORTS` 双轨(§反模式 "冗余常量")。

#### 不动

- `workers/proxy_deploy_worker.py`(不存在, T-16 新建)
- `tools/*`(T-17 处理)
- `db/models.py::ProxyRecord` 字段(只改注释, 不动 schema, 因为 `status` 字段已经存在, 默认值 `USING` 也对)

### 数据迁移

dev SQLite 现状: `proxy_record` 应无数据(ProxyDeployWorker 还没跑过)。

```bash
# 实现者先确认无数据
sqlite3 dev.db "SELECT COUNT(*) FROM proxy_record;"
# 若 = 0: 直接改 ORM 类, status 字段值类型不变, 无需 schema migration
# 若 > 0: 停下来报告, 等用户拍清库 / patch
```

---

## 3. 验收交付

### 测试用例

#### TC-01 `test/proxy_deploy_worker/TC-01_proxy_status_enum.py`

```python
def test_proxy_status_3_values():
    from db.models import ProxyStatus
    assert ProxyStatus.USING == "using"
    assert ProxyStatus.PENDING_FW == "pending_fw"
    assert ProxyStatus.INACTIVE == "inactive"
    # 旧 EXPIRED 不再存在
    assert not hasattr(ProxyStatus, "EXPIRED")

def test_max_ports_per_vps_in_config():
    from config import MAX_PORTS_PER_VPS
    assert MAX_PORTS_PER_VPS == 3
    assert isinstance(MAX_PORTS_PER_VPS, int)

def test_excluded_ports_covers_well_known():
    """核查复用现有 COMMON_RESERVED_PORTS, 而非 config.EXCLUDED_PORTS 另立。"""
    from toolbox.ports import COMMON_RESERVED_PORTS
    # well-known 段必须在里面
    assert 22 in COMMON_RESERVED_PORTS    # SSH
    assert 80 in COMMON_RESERVED_PORTS    # HTTP
    assert 443 in COMMON_RESERVED_PORTS   # HTTPS
    # 常用应用端口
    assert 3306 in COMMON_RESERVED_PORTS  # MySQL (若实现者补全)
    # config.py 不应另起 EXCLUDED_PORTS
    import config
    assert not hasattr(config, "EXCLUDED_PORTS"), "不允许跟 toolbox.ports.COMMON_RESERVED_PORTS 双轨"
```

⚠️ 如果实现者发现 `COMMON_RESERVED_PORTS` 不全且补全, TC 里的具体端口断言可以加; 不全且未补, 报告。

### 必跑测试命令

```bash
PYTHONPATH=. pytest test/proxy_deploy_worker/TC-01_proxy_status_enum.py -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`

- [ ] 任务文件改为 `doing`
- [ ] `db/models.py::ProxyStatus` 改为 3 档(USING/PENDING_FW/INACTIVE), 旧 EXPIRED 移除
- [ ] `db/models.py::ProxyRecord` 类注释更新(对齐 ADR-0006 §3 §6)
- [ ] `config.py` 加 `MAX_PORTS_PER_VPS = 3`
- [ ] 核查 `toolbox/ports.py::COMMON_RESERVED_PORTS` 是否覆盖 EXCLUDED_PORTS 全集; 补全或不补都在实现过程记录里说明
- [ ] **没有**另起 `config.py::EXCLUDED_PORTS`(防双轨)
- [ ] TC-01 全过
- [ ] 完成记录段已填

---

## 完成记录(done 时追加)

```text
完成日期: 2026-06-09
完成 commit: (跟本任务单 waiting→done 同 commit, 见 git log)
任务状态: waiting -> doing -> done

实现者完工标准 (全 ✅):
- [x] 任务文件改为 doing → done
- [x] db/models.py::ProxyStatus 改为 3 档 (USING / PENDING_FW / INACTIVE)
- [x] db/models.py::ProxyRecord 类注释更新 (对齐 ADR-0006 §3 §6)
- [x] config.py 加 MAX_PORTS_PER_VPS = 3
- [x] 核查 toolbox/ports.COMMON_RESERVED_PORTS 是否覆盖 EXCLUDED_PORTS 全集 → 不全, 补 7 个
- [x] 没有另起 config.py::EXCLUDED_PORTS (防双轨)
- [x] TC-01 全过 (6/6)
- [x] 完成记录段已填

改动摘要:
- db/models.py::ProxyStatus 2 档 → 3 档 (using / pending_fw / inactive, 旧 EXPIRED→INACTIVE 吸收语义)
- db/models.py::ProxyRecord 类注释更新 (对齐 ADR-0006 §3 §6: 上限 MAX_PORTS_PER_VPS 条 + 高位随机避开 EXCLUDED_PORTS, 删 stale 的 "18441-18450 / 最多 10 条")
- config.py 新增 MAX_PORTS_PER_VPS = 3 + 注释引用 EXCLUDED_PORTS 复用 toolbox/ports 不双轨
- toolbox/ports.py::COMMON_RESERVED_PORTS 补 7 个常见应用端口 + docstring 标 "⭐ 即 ADR-0006 §6 / ADR-0002 §3 EXCLUDED_PORTS"
- 新建 test/proxy_deploy_worker/TC-01_proxy_status_enum.py (3 个 TestCase, 6 个子测试)

COMMON_RESERVED_PORTS 核查结论:
- 原 10 个: 22 / 25 / 53 / 80 / 443 / 1082 / 3306 / 8080 / 18789 / 54321
- 补 7 个: 1080 SOCKS5 / 5432 PostgreSQL / 6379 Redis / 9090 Prometheus / 9100 NodeExporter / 11211 Memcached / 27017 MongoDB
- 补后共 17 个 (保守清单, 不可能穷尽; 业务跑出来撞到再补)

测试命令:
- PYTHONPATH=. uv run pytest test/proxy_deploy_worker/TC-01_proxy_status_enum.py -v
- PYTHONPATH=. uv run pytest test/xray_worker/TC-07_tail_takeover_ok.py -v  (回归)

测试结果:
- TC-01: 6 passed in 0.24s ✅
- TC-07 (回归): 1 passed in 0.28s ✅ (ProxyStatus.USING 改名零影响)

未覆盖风险:
- proxy_record 表 dev SQLite 有 1 行历史 status='using' 数据 (XrayWorker 2026-06-08 纳管真实记录 vps_id=1 port=11080 egress=198.51.100.10 SG), 不受 EXPIRED→INACTIVE 改名影响, 用户已确认保留 (会话决策 ①a)
- MAX_PORTS_PER_VPS=3 是经验值, T-16 真机时如不够再调一处常量
- COMMON_RESERVED_PORTS 17 个端口是保守清单, 不可能穷尽

后续任务: T-16 (ProxyDeployWorker 实现 + 12-15 个 TC)
```
