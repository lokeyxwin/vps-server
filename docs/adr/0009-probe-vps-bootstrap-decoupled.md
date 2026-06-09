# 0009. 测试 VPS 自举模块 (probe_vps.bootstrap) 跟 worker 体系完全解耦

**日期**: 2026-06-09
**状态**: Accepted

---

## Supersedes / 补充

- 补充(非推翻) [[0001-workers-replace-services]] §决策 §1 工人清单
  → 本 ADR 引入"测试 VPS 自举模块" 作为 IPProbeWorker 前置, 但**不进 worker 体系**
- 沉档 `issue/2026-06-09-probe-vps-bootstrap.md` 的拟方案 (姿态 B), 该 issue 标"已沉 ADR-0009"

> 注: 被本 ADR 补充的旧 ADR 本身不动(永不改原则); 当下真相以本 ADR + 后续 ADR + spec.md 为准。

---

## 背景

T-18 完工后跑端到端首次 `register_ip` (vps 端正常装好 xray + 纳管, 然后试 IP), 触发了一个明确的设计缺口:

```
19:11:36 ▶ services.ip_probe_worker: _pick_probe_vps: 测试 VPS ip=203.0.113.20 通 → 选定
19:11:36 [INFO] xray.manager: XrayManager.replace_proxy_binding: vps_port=19000 → replacing
19:11:37 ▶ services.ip_probe_worker: process: 未分类异常
  (ConfigWriteError: ... bash: /usr/local/etc/xray/config.json: No such file or directory)
  → proxy_failed
```

**根因**: `IPProbeWorker._apply_test_outbound` 直接调 `XrayManager.replace_proxy_binding(...)` → 读 `/usr/local/etc/xray/config.json` → **文件不存在因为测试 VPS 没装 xray** → `ConfigWriteError` 兜底转 `proxy_failed`.

更深一层: **IPProbeWorker 完全假定测试 VPS 已 ready, 没有任何探测/装机分支**, 跟 XrayWorker (有 A/B/C 3 分支 + 统一收尾) 形成鲜明对比.

跟 XrayWorker 对比:

| 工人 | 装机自举 |
|---|---|
| **XrayWorker** | ✅ 3 分支 + 统一收尾 (A 全新装 / B 启动 / C 已装跑着) |
| **IPProbeWorker** | ❌ 完全假定测试 VPS 已 ready, 没有任何探测/装机分支 |

讨论里曾考虑两种姿态:

- **姿态 A**: 塞进 XrayWorker 复用 3 分支 + 统一收尾 + 让步算法 + 纳管 (ADR-0003/0004 那套)
- **姿态 B**: 独立小模块, 跟现有 worker 体系完全解耦

---

## 决策

### 1. 拍板姿态 B — `probe_vps/bootstrap.ensure_ready()` 独立模块, 完全解耦

新建 `probe_vps/` 包(把现有单文件 `probe_vps.py` 改组成包目录), 含 `bootstrap.py::ensure_ready()` 自举主入口.

**关键边界**:
- **不入** vps_record / vps_task / stage 锁 — 测试机不是业务资产
- **不走** XrayWorker / 3 分支 / 让步算法 / 纳管 / 自启检查 — 这些都是业务装机的语义
- **不写** 任何业务表 — bootstrap 只动测试机自身, 不动 DB

**理由**:
- 测试机不是业务资产, 不入 vps_record / vps_task / stage 锁
- 端口可控 (我们自己的机), 不需要让步算法
- 不会有别人挂出口, 不需要纳管
- 不需要 retry/熔断/异步状态机, 同步段做完即可
- 复用 XrayWorker 反而把 "业务装机" 和 "工具机自举" 两件事撕到一起, 增加维护成本

### 2. `probe_vps/` 包结构

```
probe_vps/
├── __init__.py        ← 暴露 ensure_ready / config 常量 / 异常类
├── config.py          ← 4 字段 os.getenv + 占位 + INBOUND_PORT 常量 (沉单文件 probe_vps.py 现有内容)
└── bootstrap.py       ← ensure_ready() 主入口 (幂等) + ProbeVPSHandle + 异常类
```

**单文件 `probe_vps.py` 改组成包目录**, 现有 `PROBE_VPS_POOL` / `PROBE_TEST_PORT` / `NO_PROBE_VPS_MESSAGE` / `get_probe_vps_pool()` 整体搬到 `probe_vps/config.py`, 通过 `probe_vps/__init__.py` 顶部 re-export 保持现有 import 路径不变 (`from probe_vps import get_probe_vps_pool` 等).

### 3. `bootstrap.ensure_ready()` 6 步幂等

```
① 拿 pool 第 N 条 (调用方指定 index, 默认 0) → config.get_probe_vps_pool()
② SSH 连 → 失败 → 抛 ProbeVPSUnreachable
③ xray version → 没装就跑 install (XrayManager.install, 复用 xray 官方一键脚本)
                → install 失败抛 ProbeVPSSetupFailed
④ systemctl is-active xray → 没跑就 start
                → start 失败抛 ProbeVPSSetupFailed
⑤ 读 xray config → 没 socks5(noauth)→freedom 在 INBOUND_PORT (19000) 就 add + reload
                → 失败抛 ProbeVPSSetupFailed
⑥ 返回 ProbeVPSHandle(host, ssh_client, inbound_port=INBOUND_PORT)
```

**幂等保证**: 步骤 ③④⑤ 都先检查"是否已就绪", 已就绪则跳过实际动作. 跑 N 次结果一致.

**不走自启 (enable)**: 跟 XrayWorker 不同, 测试机不需要长期常驻自启, 单次拉起即可.
本机重启后再跑一次 ensure_ready 重新拉起, 不依赖 systemd 持久化.

### 4. 异常类设计

```python
class ProbeVPSError(Exception):
    """probe_vps 基类异常."""

class ProbeVPSUnreachable(ProbeVPSError):
    """SSH 连不上, 跟现有 IPProbeWorker probe_vps_unreachable 一致."""

class ProbeVPSSetupFailed(ProbeVPSError):
    """SSH 通但 xray 装/起/配 失败, 新增状态码 probe_vps_not_ready."""
```

### 5. IPProbeWorker 入口加调用

`IPProbeWorker.process` 在 `_pick_probe_vps` 之后, `_apply_test_outbound` 之前, 调一次 `bootstrap.ensure_ready()`:

```python
# 现状
probe_entry = self._pick_probe_vps()
# ... 直接 _apply_test_outbound

# 改后
probe_entry = self._pick_probe_vps()
try:
    handle = bootstrap.ensure_ready(probe_entry)
except ProbeVPSUnreachable as exc:
    return {"status": "probe_vps_unreachable", "message": str(exc)}
except ProbeVPSSetupFailed as exc:
    return {"status": "probe_vps_not_ready", "message": str(exc)}
# 然后用 handle 跑后续步骤
```

**新增状态码 `probe_vps_not_ready`** 跟现有 `probe_vps_unreachable` 区分:
- `probe_vps_unreachable` = SSH 都连不上 (测试机宕机 / 凭据错)
- `probe_vps_not_ready` = SSH 通但 xray 没准备好 (装失败 / 起失败 / 配失败)

两种都是测试机基础设施问题, 跟 `proxy_failed` (上游 IP 问题) 严格区分.

### 6. 出三个新入口暴露给运维/agent

#### 6.1 `main.py init-probe-vps` 子命令 (CLI)

```bash
PYTHONPATH=. uv run python main.py init-probe-vps
PYTHONPATH=. uv run python main.py init-probe-vps --slot 2     # 选 pool 第 2 条
```

跟 `init-db` 同款"运维子命令"姿态, 人工/部署调一次. 出错直接抛 stack.

#### 6.2 MCP 工具 `init_db` (admin)

把 `main.py init-db` 同款逻辑包成 MCP 工具.

#### 6.3 MCP 工具 `init_probe_vps` (admin)

把 `main.py init-probe-vps` 同款逻辑包成 MCP 工具.

**两个 MCP 工具都暴露给 admin**, 跟 [[0008-main-as-worker-runner-and-db-queries-home]] §3.1 admin/user 分层评估对齐:
- `init_db` 改 DB schema, 极危险, admin only
- `init_probe_vps` 装测试机 xray, 改基础设施, admin only
- 都不在 ABCD 写入工具 4 条规则约束内 (不动业务表, 不是 update_*)
- admin/user 真正拆 server 仍留下波 (ADR-0007 §8 + ADR-0008 §3.1)

---

## 备选方案

### 方案 A (被否决): 塞进 XrayWorker 复用 3 分支 + 统一收尾

让 XrayWorker.process_task 看到 vps_id 对应是测试机 (特殊标记) 就走简化路径.

**否决理由**:
- 把"业务装机" 和 "工具机自举" 撕到一起, 心智负担大
- 测试机不入 vps_record, 没法走 task 表协调, 跟 XrayWorker 的 task-driven 模型不兼容
- 让步算法 / 纳管 / 自启检查全是业务装机的语义, 测试机不需要
- 维护成本: 加新 worker 类型时, 都要思考"它要不要支持工具机分支"

### 方案 C (被否决): 测试机也走 vps_record + vps_task

测试机也入业务表, 在 vps_record 加个 is_probe_vps 标记, vps_task 也派给 XrayWorker.

**否决理由**:
- 污染业务表 (vps_record 应该只装"客户挂代理的生产机")
- ProxyDeployWorker / 巡检等业务工人挑机时要加 "WHERE is_probe_vps=0" 过滤, 易漏
- 测试机生命周期跟生产 VPS 完全不同 (不到期 / 不释放 / 不挂代理), 强塞同一张表语义混乱

### 方案 D (被否决): IPProbeWorker 自己内嵌 ensure 逻辑

不抽 probe_vps/bootstrap, 直接在 IPProbeWorker 入口写 `if not is_installed: install; if not running: start; ...`.

**否决理由**:
- 违反 CLAUDE.md §2.7 (复用 + 外部 + 多步 = 抽工具)
- ensure_ready 逻辑跟 IPProbeWorker 主业务 (账密校验) 是两件不同的事, 内嵌会让 IPProbeWorker 失焦
- 未来如果有其他工人也要用测试机 (例如 IP 续期巡检), 没法复用

---

## 后果

### 好处

- 测试机基础设施跟业务装机解耦, 心智清晰
- IPProbeWorker 主流程聚焦 "账密校验", 不掺杂"工具机自举"
- `init-probe-vps` 子命令 + MCP 工具暴露, 运维 / agent 主动触发自举
- 新增 `probe_vps_not_ready` 状态码, agent 能准确告诉用户 "测试机挂了, 不是你的 IP 问题"
- ensure_ready 幂等, 跑 N 次结果一致, 失败可重试

### 引入的新约束

- 单文件 `probe_vps.py` 改组成 `probe_vps/` 包目录 (无破坏性变化, re-export 保持兼容)
- `probe_vps.example.py` 同步改名 (保持参考模板一致)
- IPProbeWorker.process 入口加 `ensure_ready()` 调用
- IPProbeWorker spec.md 加新 status `probe_vps_not_ready` (但本 ADR 范围不动 spec, 由 task 单同步)
- tools/register_ip.py description 加 status 转告规则 `probe_vps_not_ready`
- main.py 加 `init-probe-vps` 子命令
- tools/init_db.py + tools/init_probe_vps.py 新建 + tools/__init__.py 注册
- deploy/README.md §5 加一节 "首次部署 / 换测试机时跑 init-probe-vps"
- issue/2026-06-09-probe-vps-bootstrap.md 标"已沉 ADR-0009"

### 风险

- **xray 官方一键脚本失败**: 网络抖动 / GitHub 拉不下来 / curl 不在 → `ProbeVPSSetupFailed`. 用户重新跑 init-probe-vps 自愈
- **测试机 xray 已存在但配置坏了** (例如有 inbound 但端口冲突): ensure_ready ⑤ 步会跳过 add (因为 19000 已存在), 但 19000 是别的 outbound 而不是 freedom → 验证会失败. 缓解: 占位检查时要看协议是 socks5 + outbound 是 freedom, 不是端口存在就跳
- **pool 多条时 ensure_ready 只搞 index=0**: 调用方可选 --slot, 但 IPProbeWorker.process 默认拿 0; 若 0 装失败要不要 fallback 到 1? 当前**不做 fallback**, 保持简单, 单条失败即返 not_ready; 后续真有多机需求再讨论

---

## 用户口述原话 (关键节选)

> "这次主要看IP用的测试服务器能不能自己会不会安装xray拉起后再配代理验证"
> — 引出本 ADR (期望 IPProbeWorker 能自举测试机, 但实际没有此逻辑)

> "那就再出一个子命令 给测试用的服务器安装xray并跑起来，但不入库 到时候那两个init初始化也当做MCP工具暴露出去"
> — 引出 §6 三个新入口 (CLI 子命令 + 2 个 MCP 工具)

(沉档 issue/2026-06-09-probe-vps-bootstrap.md 里的早期讨论 — 当时已经详细拍板姿态 B, 本 ADR 沉为正式决策档案)

---

## 影响清单 (已锁定, 在下游 task 单里逐项落)

| 文件 | 现状 | 改动 | 落地任务单 |
|------|------|------|----------|
| `probe_vps.py` (单文件) | 装 PROBE_VPS_POOL / PROBE_TEST_PORT / NO_PROBE_VPS_MESSAGE / get_probe_vps_pool() | 改组成 `probe_vps/` 包: 内容搬到 `probe_vps/config.py`, 通过 `probe_vps/__init__.py` re-export 保持现有 import 路径不变 | T-19 |
| `probe_vps.example.py` (单文件) | 模板参考 | 同步改组成 `probe_vps_example/` 包或保持单文件, 待 task 决定 | T-19 |
| `probe_vps/__init__.py` | 不存在 | 新建, re-export config + bootstrap 公开符号 | T-19 |
| `probe_vps/bootstrap.py` | 不存在 | **新建**, ensure_ready() 6 步幂等 + ProbeVPSHandle + 2 个异常类 | T-19 |
| `workers/ip_probe_worker.py` | _pick_probe_vps 后直接 _apply_test_outbound | 加 ensure_ready() 调用 + 处理 ProbeVPSUnreachable / ProbeVPSSetupFailed → probe_vps_unreachable / probe_vps_not_ready | T-19 |
| `tools/register_ip.py` description | 7 个 status 含义 | 加 probe_vps_not_ready 转告规则 | T-19 |
| `main.py` | 2 子命令 (init-db / worker-loop) | 加 init-probe-vps 子命令 | T-19 |
| `tools/init_db.py` | 不存在 | **新建** MCP 工具 (admin) | T-19 |
| `tools/init_probe_vps.py` | 不存在 | **新建** MCP 工具 (admin) | T-19 |
| `tools/__init__.py` | 5 工具 ALL_TOOLS | 加 2 个工具到 ALL_TOOLS + 注释分类加"运维工具" 一档 | T-19 |
| `test/probe_vps/` | 已有 (probe_vps 单文件 TC) | 加 bootstrap_ensure_ready_*.py TC | T-19 |
| `test/main/TC-07_init_probe_vps.py` | 不存在 | 新建 | T-19 |
| `test/mcp_tools/TC-*.py` | 现有 | 补 init_db / init_probe_vps 注册 TC | T-19 |
| `test/ip_probe_worker/` | 现有 | 补 not_ready 路径 TC | T-19 |
| `deploy/README.md` | §5 init-db | §5 加 "5.2 初始化测试机" | T-19 |
| `issue/2026-06-09-probe-vps-bootstrap.md` | 状态"讨论中" | 标 "已沉 ADR-0009" | T-19 |
