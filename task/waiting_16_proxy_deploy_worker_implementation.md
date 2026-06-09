# T-16 ProxyDeployWorker 实现 + 全套 TC

**ID**: T-16
**状态**: waiting
**前置依赖**: T-15 (ProxyStatus 3 档 + MAX_PORTS_PER_VPS) **必须先 done**
**后续依赖**: 无(本任务完工 = ProxyDeployWorker 端到端链路通)
**关联 ADR**: `docs/adr/0006-proxy-deploy-worker.md`
**关联 spec**: `test/proxy_deploy_worker/spec.md` v1(**主依据**, 6 步业务流 + 工具清单 + 6 条不变量)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认 T-15 是 `done`(否则本任务依赖未到位, 不能开工)
- [ ] 本任务仍是 `waiting`
- [ ] 写代码前改名为 `task/doing_16_proxy_deploy_worker_implementation.md`

### 必读清单

- [ ] `CLAUDE.md` / `CLAUDE.local.md`(尤其 §业务编排 + §4.3 工具实现顺序)
- [ ] `docs/adr/0001` / `0002` / `0005` / `0006`(本任务主决策依据 + 资源锁两层语义)
- [ ] `test/proxy_deploy_worker/spec.md` v1 全文
- [ ] `workers/xray_worker.py`(同类异步 worker, 学结构 / 抢锁 / process_task 模式)
- [ ] `workers/ip_probe_worker.py`(同类异步 worker, 学 session_scope 用法)
- [ ] `xray/manager.py::XrayManager.apply_proxy_binding` / `rollback_proxy_binding`(spec §工具清单 §A)
- [ ] `toolbox/ports.py` / `toolbox/firewall.py` / `toolbox/proxy_check.py`(spec §工具清单 §A)
- [ ] `db/models.py::ProxyStatus` / `ProxyRecord` / `IPStatus` / `IPRecord` / `IPTask` / `VPSStage` / `VPSRecord`

---

## 1. 用户原话 / 业务目标

### 用户原话

> "可以用 B 方案 ... 任务完成配置上去 内 ping 通, 防火墙放行剩外部问题 vps 的已用+1"
> "逻辑对了 你先给 ADR 和 spec 吧"(2026-06-09 业务故事 6 步对齐时)

### 业务目标

让 `ip_task` 真能被消费 —— IPProbeWorker 派出来的待挂 IP 被 ProxyDeployWorker 接力, 完成 "挑机 → 挑端口 → 配上线 → 内/外 ping → 收尾" 6 步。

### 本任务要解决什么

- ProxyDeployWorker 进程跑起来时能扫 `ip_task pending` 任务并消费
- 一条 IP 完整挂到一台生产 VPS, `proxy_record` 入一行, `ip_task` 走完到 done

### 本任务不解决什么

- ❌ 不写 MCP 工具(T-17)
- ❌ 不动 schema(T-15 已做)
- ❌ 不引入定时巡检 / 维修工人(封存的 ExpiryWorker / CleanupWorker)
- ❌ 不实现"配好后自动重新外 ping"(pending_fw 状态由查询工具自然暴露, 用户去面板放行后下次查就 using)

---

## 2. 实现参考

### 验收锚点

- `test/proxy_deploy_worker/spec.md` §3 业务主流程 6 步
- `test/proxy_deploy_worker/spec.md` §4 挑机算法(SQL + 抢机两写同事务)
- `test/proxy_deploy_worker/spec.md` §5 挑端口算法
- `test/proxy_deploy_worker/spec.md` §6 收尾 DB 写入
- `test/proxy_deploy_worker/spec.md` §7 失败分支汇总(6 种 last_error_code)
- `test/proxy_deploy_worker/spec.md` §9 不变量 6 条
- `docs/adr/0005-vps-stage-as-resource-lock.md` §决策(资源锁释放/保留语义)

### 改动文件清单

#### 新建 `workers/proxy_deploy_worker.py`

类风格(参考 `workers/xray_worker.py`):

```python
class ProxyDeployWorker:
    """挂上游 IP 到生产 VPS 当 socks5 outbound。消费 ip_task。"""

    def __init__(self, worker_id: str = "proxy_deploy"):
        self.worker_id = worker_id

    def process_task(self, task_id: int) -> dict:
        """主入口: 抢锁 → 挑机 → 挑端口 → 配上线 → 验证 → 收尾。"""
        ...

    # ----- 私有编排方法(spec §工具清单 §B)-----

    def _pick_vps(self, session, task) -> VPSRecord | None:
        """走 spec §4 挑机 SQL, 同事务抢资源锁 + 回填 task.vps_id。"""
        ...

    def _pick_port(self, client, vps) -> int | None:
        """走 spec §5 端口算法, 返候选端口, 候选池空时返 None。"""
        ...

    def _deploy_one_binding(self, client, vps, vps_port, ip) -> dict:
        """步骤 4-5: 配上线 + 防火墙 + 内 ping + 外 ping。

        返回:
          {"status": "success", "outer_ping_ok": True/False, ...}
          {"status": "failed", "last_error_code": "...", ...}
        """
        ...

    def _mark_done(self, session, task, vps, vps_port, ip, outer_ping_ok):
        """成功收尾: INSERT proxy_record + UPDATE ip/vps/task 同事务。"""
        ...

    def _mark_failed(self, session, task, last_error_code, last_error_msg, *, release_stage=False):
        """失败收尾: UPDATE task.status=failed; 默认 vps.stage 不释放(ADR-0005 §3)。"""
        ...
```

#### 不动

- `db/models.py`(T-15 已动, 本任务不动)
- `xray/manager.py`(用现有 `apply_proxy_binding` / `rollback_proxy_binding`)
- `toolbox/*`(用现有 `ports` / `firewall` / `proxy_check`)
- `tools/*`(T-17)
- `mcp_server.py`(本任务不暴露 MCP 入口, ProxyDeployWorker 是纯异步 worker)

### 实现轮廓 — 主流程

按 spec §3 直译:

```python
def process_task(self, task_id: int) -> dict:
    # ① 抢锁(同 XrayWorker 模式)
    with session_scope() as s:
        task = self._claim_task(s, task_id)
        if task is None:
            return {"status": "skipped", "reason": "task_already_taken"}

    # ② 挑 VPS(同事务抢资源锁 + 回填)
    with session_scope() as s:
        task = s.get(IPTask, task_id)
        vps = self._pick_vps(s, task)
        if vps is None:
            self._mark_failed(s, task, "no_vps_capacity", "VPS 池子满了", release_stage=False)
            return {"status": "failed", "last_error_code": "no_vps_capacity"}
        # 同事务: vps.stage='running' + task.vps_id=vps.id

    # ③④⑤ 进 VPS, 挑端口, 配上线, 两次 ping
    try:
        with SSHSession(vps.ip, vps.username, vps.password, vps.port) as session:
            client = session.client
            vps_port = self._pick_port(client, vps)
            if vps_port is None:
                with session_scope() as s:
                    task = s.get(IPTask, task_id)
                    self._mark_failed(s, task, "no_port_available", "端口候选池空")
                return ...

            ip = ...  # 从 task.ip_id 取
            deploy_result = self._deploy_one_binding(client, vps, vps_port, ip)
            if deploy_result["status"] == "failed":
                with session_scope() as s:
                    task = s.get(IPTask, task_id)
                    self._mark_failed(s, task, deploy_result["last_error_code"], deploy_result["last_error_msg"])
                return ...

            # ⑥ 收尾
            with session_scope() as s:
                task = s.get(IPTask, task_id)
                self._mark_done(s, task, vps, vps_port, ip, deploy_result["outer_ping_ok"])
    except SSH 相关异常:
        ...处理 ssh_disconnected 退避重试...

    return {"status": "done", ...}
```

### 失败码全集(对照 spec §7)

| 失败码 | 触发 | release stage? | xray 配置 | 重试? |
|--------|------|---------------|----------|------|
| `no_vps_capacity` | 挑机 SQL 0 行 | n/a(没抢机)| 没碰过 | ❌ 终态 |
| `no_port_available` | 端口候选池空 | ❌ 保持 running | 没碰过 | ❌ 终态 |
| `apply_binding_failed` | xray apply 报错 | ❌ 保持 running | 已 rollback | ❌ 终态 |
| `firewall_open_failed` | 防火墙放行报错 | ❌ 保持 running | 已 rollback | ❌ 终态 |
| `inner_ping_failed` | 内 ping 不通 | ❌ 保持 running | **已 rollback 三件套** | ❌ 终态 |
| `ssh_disconnected` | SSH 中途断 | ❌ 保持 running | 状态不可知 | ✅ 退避重试 N 次 |

### 缺工具 / 缺信息先报告

- 如发现 `_classify_xray_error` 等帮手方法在 SSHSession / XrayManager 没有, 但需要 → 报告
- 如发现 spec §4 挑机 SQL 在 SQLAlchemy 实现上有歧义(比如 `FOR UPDATE` 在 SQLite 上不 work) → 报告
- 如发现 `apply_proxy_binding` 接口不接受某入参 → 报告

---

## 3. 验收交付

### 测试用例(估 12-15 个 TC, 按 spec §3 §4 §5 §6 §7 §9 全覆盖)

#### `test/proxy_deploy_worker/TC-*.py`(主流程)

- **TC-02 挑机 SQL**: mock VPS 池, 验 4 个条件命中 + 最闲优先 + 同档 RANDOM()
- **TC-03 抢机两写同事务**: mock session, 验 `vps.stage='running'` 和 `task.vps_id` 在同一 commit
- **TC-04 没机 → failed**: VPS 池空, 验 `task.status='failed'` + `last_error_code='no_vps_capacity'`, **不退避重试**, 不动 vps
- **TC-05 挑端口算法**: mock `get_used_ports` / `COMMON_RESERVED_PORTS` / `proxy_record`, 验候选池 = 1024-65535 - 三集并
- **TC-06 端口池空 → failed**: 端口集为空, 验 `last_error_code='no_port_available'`, vps.stage 保持 running
- **TC-07 配上线成功 + 内通 + 外通 → done + using**: 全链路 happy path, 验 proxy_record/ip/vps/task 4 表写入
- **TC-08 配上线成功 + 内通 + 外不通 → done + pending_fw**: 半成功, 验 status 字段
- **TC-09 内通失败 → rollback 三件套 + failed**: 内 ping 不通, 验调 `rollback_proxy_binding` + 不入库 + `last_error_code='inner_ping_failed'`
- **TC-10 apply_binding 报错 → rollback + failed**: 验 `apply_binding_failed`
- **TC-11 firewall 报错 → rollback + failed**: 验 `firewall_open_failed`
- **TC-12 锁状态机不变量**: 成功 → stage 释放; 失败 → stage 保持; 测两条路径
- **TC-13 used_port_count +1 只在 done 时**: 验失败路径不 +1
- **TC-14 真服务器 e2e**(可选, `@pytest.mark.skipif(无 VPS 环境)`): 准备一台测试 VPS, 跑完整链路

### 必跑测试命令

```bash
PYTHONPATH=. pytest test/proxy_deploy_worker/TC-*.py -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`

- [ ] T-15 已 done(前置)
- [ ] 任务文件改为 `doing`
- [ ] `workers/proxy_deploy_worker.py` 实现完, 主流程 + 6 个私有方法
- [ ] 失败 6 码全实现, 对照 spec §7 / 本任务"失败码全集"表
- [ ] 不变量 6 条全实现(spec §9), TC-12 / TC-13 覆盖
- [ ] TC-02 ~ TC-13 全过(TC-14 真服务器可选)
- [ ] 完成记录段已填(测试结果原样贴)
- [ ] **没有**改动 db/models.py / tools/* / mcp_server.py(对照"不动"清单)

---

## 完成记录(done 时追加)

```text
完成日期:
完成 commit:
任务状态: doing -> done
改动摘要:
TC 通过数: N / 总数
未覆盖风险:
后续任务: T-17 (MCP 剩余 4 件套, 含 get_ip_registration_status 暴露 proxy_record 给 agent)
```
