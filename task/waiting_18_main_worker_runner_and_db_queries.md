# T-18 main.py = worker runner + services 查询函数搬到 db/queries.py + CLAUDE.local.md 心智模型 + MCP 工具评估规则

**ID**: T-18
**状态**: waiting
**前置依赖**:
  - T-06 ✅ done (register_vps MCP entry)
  - T-15 ✅ done (ProxyStatus 3 档 + MAX_PORTS_PER_VPS + EXCLUDED_PORTS)
  - T-16 ✅ done (ProxyDeployWorker 实现)
  - T-17 ✅ done (MCP 5 件套 — register_ip 改名 + 2 状态查询 + services/registration_query.py 新建)
**后续依赖**: 无 (本任务完工 = 后端架构二进程模型完整就位)
**关联 ADR**: `docs/adr/0008-main-as-worker-runner-and-db-queries-home.md` (**本任务主依据**)
**关联 spec**: 无独立 spec (本任务是基础设施 + 规则文件改, 行为规约住 CLAUDE.local.md + ADR)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] T-06 / T-15 / T-16 / T-17 已 done (上面打勾确认)
- [ ] 本任务仍是 `waiting`
- [ ] 写代码前已将文件名改为 `task/doing_18_main_worker_runner_and_db_queries.md`

### 必读清单

领取后、写代码前必须显式读取:

- [ ] `CLAUDE.md` / `CLAUDE.local.md` (尤其 §11 旧 services / §10 MCP 工具暴露三类)
- [ ] `docs/adr/0008-main-as-worker-runner-and-db-queries-home.md` (**全文**, 本任务主依据)
- [ ] `docs/adr/0001-workers-replace-services.md` (worker 架构基础)
- [ ] `docs/adr/0007-mcp-tools-naming-and-conventions.md` (MCP 工具规约, 本任务消化 §影响清单"暂保留")
- [ ] `main.py` (要重写)
- [ ] `mcp_server.py` (确认 worker 调度入口不在它身上)
- [ ] `workers/xray_worker.py` + `workers/proxy_deploy_worker.py` (确认 run_once 接口 = `-> int` 返 0/1)
- [ ] `services/registration_query.py` (要搬)
- [ ] `services/proxy_query.py` (要搬)
- [ ] `tools/get_vps_registration_status.py` + `tools/get_ip_registration_status.py` + `tools/get_available_proxy_nodes.py` (要改 import)
- [ ] `config.py` (要加 `POLL_INTERVAL_SECONDS`)

未读完上面文件前, 禁止写代码 / 写测试 / 把任务改 done / 给"我已理解"结论。

---

## 1. 用户原话 / 业务目标

### 用户原话

> "main 能不能当做拉起服务的脚本, 我不想那么多心智负担, MCP 就是启动前台, main 就是启动常驻服务"
> "services 我记得是旧业务逻辑也在里面的, 为啥任务还让你放进去"
> "17 任务封装的查询工具放到 db/ 查询 / MCP 查询工具都在 db 这里拿就完事了吧"
> "查询工具都在 db, 更新工具也在 db, mcp 那边我也分了两套服务 一个 admin 一个 user 工具权限可以隔离 ... 任务表不允许外部修改, 那个是历史日志事实, 能改的就只有 IP 表和 VPS 表 字段还是有限制的都有限制的没事"
> "噢不止字段受限, 还要求不允许覆盖更新只能白名单更新就是我要改日期要精准的找到那个 VPS 或 IP 去改"

### Claude 整理后的业务理解

- 外部输入: 部署 / 运维拉起两个进程
- 主要流程:
  1. `mcp_server.py` 当前台 (stdio MCP, 分发 5 工具 handler)
  2. `main.py worker-loop` 当后端常驻 (扫 task 表, 推 worker)
- 数据流:
  - 读取: `vps_task` / `ip_task` (worker.run_once 内部扫)
  - 写入: `db/queries.py` 写入函数 (本任务范围内**无**, 未来按 ABCD 规则加)
- 同步 / 异步边界:
  - MCP 工具 handler 同步调 `db/queries` 或 worker.process 立刻返
  - worker-loop 异步轮询消费 task 表
- 成功返回: worker-loop 进程长期常驻, 收 SIGTERM/SIGINT 后优雅退出 (exit 0)
- 失败返回: 进程启动期异常(import 错 / config 错)直接抛栈, 退码非 0

### 本任务要解决什么

- `mcp_server.py` + `main.py worker-loop` 二进程模型就位, **MCP 启动后真正能完成端到端业务** (装机 + 部署代理都跑得起来)
- `services/` 目录退出活跃路径, MCP 工具一律 `from db.queries import` (查询函数集中, 心智干净)
- CLAUDE.local.md 把"心智模型"+"MCP 工具上线评估清单 / 写入工具白名单 patch 4 条规则"写进规则, 长期约束未来代码

### 本任务不解决什么

- ❌ **不实现任何 `update_*` MCP 工具** (ABCD 4 条规则是未来约束, 本任务范围内不建写入工具)
- ❌ **不真正拆 admin/user 两套 MCP server** (ADR-0007 §8 留下波)
- ❌ **不动 mcp_server.py 实际代码** (server name / instructions 仍是 stale, 留下波)
- ❌ **不动 worker 内部逻辑** (run_once / process_task 跟 T-09 / T-16 落地版本一致)
- ❌ **不动 ORM 模型** (db/models.py 不动)
- ❌ **不写部署文档 (systemd / supervisor / docker)**, ADR-0008 §决策 §1 明确说"部署关注点跟架构决策无关"; 顶部 docstring 提示双进程拉起即可
- ❌ **不优化 worker loop 并发** (当前串行 XrayWorker → ProxyDeployWorker, 留后续)

---

## 2. 实现参考

### 验收锚点

- `docs/adr/0008-*.md` §决策 §1 main.py / §决策 §2 db/queries.py / §决策 §3 MCP 工具评估 / §决策 §4 心智模型
- `docs/adr/0008-*.md` §影响清单 (逐项落地, 一项不漏)
- `CLAUDE.local.md` §11 旧 services 边界 (本任务后 services/ 不再被任何活跃路径 import)

### 改动文件清单

#### 改 `main.py`

```text
现状: 老 services CLI, 3 子命令 (rgvps / xrayinit / rgip) 直接 from services.xxx import.
目标: 重写为 worker 常驻调度入口.

要做的:
1. 删 _build_parser 里 rgvps / xrayinit / rgip 3 个 subparsers + 对应 main() 分支
2. 删顶部 docstring 老 CLI 用法说明
3. 加 worker-loop 子命令 (argparse 保留 subparsers 便于未来扩展):
     uv run python main.py worker-loop
4. 实现轮廓 (见下):
   - signal handler 设 _stop=True
   - while not _stop: 串行 XrayWorker().run_once() + ProxyDeployWorker().run_once()
     - busy 累加, 没活则 time.sleep(POLL_INTERVAL_SECONDS)
   - 优雅退出 log + exit 0
5. 顶部 docstring 重写:
     - 用法: uv run python main.py worker-loop
     - 双进程心智: mcp_server.py 前台 + main.py worker-loop 后端常驻
     - 引用 ADR-0008
```

#### 改 `config.py`

```text
加常量: POLL_INTERVAL_SECONDS = 2
放位置: 跟 SSH_CONNECT_TIMEOUT 等基础设施常量同区段, 加注释 "worker-loop idle 时 sleep 秒数".
```

#### 新建 `db/queries.py`

```text
装 3 个 read-only 函数 (从 services 搬, 签名不变):
  - query_vps_status(vps_id, task_id) -> dict
  - query_ip_status(ip_id, task_id)   -> dict
  - list_available_proxies(country_code="") -> list[dict]

顶部 docstring:
  - 装啥: "MCP 工具调的所有业务函数集合"
  - 读写都在 (本任务范围内只搬读函数, 写函数未来按 ADR-0008 §3.3 ABCD 4 条规则加)
  - 引用 ADR-0008
  - 引用 CLAUDE.local.md §14 (新加的 MCP 工具评估清单)

import 路径调整:
  - 原 services.registration_query 从 db.models / db.session import → 同样, 不改
  - 原 services.proxy_query 从 db import (IPRecord 等聚合) → 改成 from db.models import (避免依赖 services 风格的聚合)
```

#### 改 3 个 tools 文件 import

```text
tools/get_vps_registration_status.py:
  from services.registration_query import query_vps_status
  → from db.queries import query_vps_status

tools/get_ip_registration_status.py:
  from services.registration_query import query_ip_status
  → from db.queries import query_ip_status

tools/get_available_proxy_nodes.py:
  from services.proxy_query import list_available_proxies
  → from db.queries import list_available_proxies

其他改动: 无 (description / inputSchema / handler 行为完全不变, 只改一行 import).
```

#### 删 `services/registration_query.py` + `services/proxy_query.py`

```text
git rm services/registration_query.py
git rm services/proxy_query.py

确认: grep -rn 'from services' --include='*.py' . 应只剩 main.py 老 CLI 引用
(本任务一并删).

如果还有别处 import services/, 报告.
```

#### 改 `CLAUDE.local.md`

```text
加 2 个新节 (在 §12 纳管模式后面, 修订历史前面):

§13 心智模型 — 二进程 + 4 层 (本节内容见 ADR-0008 §决策 §4)
  - mcp_server.py: 前台收单
  - main.py worker-loop: 后端常驻调度
  - workers/: 异步业务编排
  - tools/: 协议适配, handler 一律 调 db/queries 或 workers/
  - db/queries.py: MCP 工具调的所有业务函数 (读写都在)
  - db/models.py: ORM 表结构
  - services/: 旧业务编排, 不删不增不导入

§14 MCP 工具上线评估清单 + 写入工具白名单 patch 4 条硬规则
  (本节内容见 ADR-0008 §决策 §3, 这里是规则落地)
  - 14.1 admin/user 分层 (新增工具上线前必须跟需求窗口确认影响面 + 暴露面)
  - 14.2 任务表不暴露写入工具 (vps_task / ip_task 永不暴露 update_*)
  - 14.3 业务表写入工具 4 条硬规则 (ABCD: 主键精准 / 白名单 / 不覆盖 / 命名反映约束)
  - 14.4 现有工具回顾 (5 工具均不在 update 范畴, 4 条规则约束未来代码)

修订历史加 v5:
- v5 2026-06-09 追加 ADR-0008 落地:
    - 新增 §13 心智模型 (二进程 + 4 层 + db/queries.py 边界)
    - 新增 §14 MCP 工具上线评估清单 + 写入工具白名单 patch 4 条硬规则
    - §11 旧 services/ 处理 status 加注 "本 ADR 起 services/ 退出活跃路径"
```

#### 不动

```text
- mcp_server.py (server name / instructions stale 留下波, ADR-0007 §8)
- workers/xray_worker.py
- workers/proxy_deploy_worker.py
- workers/ssh_worker.py
- workers/ip_probe_worker.py
- db/models.py
- tools/register_vps.py
- tools/register_ip.py
- test/* 里所有 worker spec.md (本任务不动 worker 行为)
- ADR-0001 ~ ADR-0007 (永不改原则)
```

### 实现轮廓

#### main.py 核心 worker-loop 伪代码

```python
"""项目统一入口.

二进程心智模型 (ADR-0008):
  mcp_server.py      前台收单     接 stdio MCP, 分发工具 handler
  main.py worker-loop 后端常驻    扫 task 表 + 推 worker

用法:
  uv run python main.py worker-loop      # 启动后端 worker 调度循环
"""

from __future__ import annotations

import argparse
import signal
import sys
import time

import config
from log import get_logger


logger = get_logger("main.worker_loop")

_stop = False


def _install_signal_handlers() -> None:
    """SIGTERM / SIGINT 都置 _stop=True, 让 worker loop 下一轮检测后退出."""
    def _handler(signum, _frame):
        global _stop
        _stop = True
        logger.info("main.worker-loop 收到信号 %s, 准备优雅退出", signum)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _run_worker_loop() -> int:
    """串行调度异步段 worker (XrayWorker → ProxyDeployWorker), idle 时 sleep.

    注: worker-loop 只调度异步段 worker。SSHWorker / IPProbeWorker 是 MCP 入口
    工具的同步段, 由 register_vps / register_ip handler 直接调 process(),
    不进 loop。
    """
    from workers.proxy_deploy_worker import ProxyDeployWorker
    from workers.xray_worker import XrayWorker

    xray_worker = XrayWorker()
    proxy_worker = ProxyDeployWorker()

    logger.info(
        "main.worker-loop 启动: poll_interval=%ds, workers=[XrayWorker, ProxyDeployWorker]",
        config.POLL_INTERVAL_SECONDS,
    )

    while not _stop:
        busy = 0
        try:
            busy += xray_worker.run_once()
        except Exception as exc:  # noqa: BLE001 — worker 自身异常不杀死循环
            logger.warning("XrayWorker.run_once 抛错: %s: %s", type(exc).__name__, exc)
        try:
            busy += proxy_worker.run_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ProxyDeployWorker.run_once 抛错: %s: %s", type(exc).__name__, exc)

        if not busy and not _stop:
            time.sleep(config.POLL_INTERVAL_SECONDS)

    logger.info("main.worker-loop 已退出")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vps-server",
        description="VPS / IP / Proxy 资产管理 — 后端 worker 调度入口",
    )
    subparsers = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    subparsers.add_parser(
        "worker-loop",
        help="启动 worker 调度循环 (常驻进程)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.action == "worker-loop":
        _install_signal_handlers()
        return _run_worker_loop()
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

### 数据结构 / 状态迁移

| 字段 / 状态 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `_stop` (main 模块全局) | worker-loop 是否该退出 | worker-loop while 判断 | signal handler 收 SIGTERM/SIGINT 置 True |
| `config.POLL_INTERVAL_SECONDS` | worker-loop idle 时 sleep 秒数 | `_run_worker_loop` | 静态配置 (本任务设 2) |

### 缺工具 / 缺信息先报告

- 如发现 `workers.xray_worker.run_once` 不是 `() -> int` 接口 → 报告 (跟 task 单实现轮廓不符)
- 如发现 `db/queries.py` 中某函数依赖 `services/` 模块或别处 → 报告
- 如发现某 tools 文件 import 路径除 `from services.xxx` 之外还有别的 services/ 依赖 → 报告
- 如发现 `mcp_server.py` 有任何对 `services/` 的引用 (理论上不该有) → 报告

---

## 3. 验收交付

### 测试用例

#### TC-18-01 main.py worker-loop 子命令存在 + argparse 行为

`test/main/TC-01_argparse.py` (新建 test/main/__init__.py + 本文件)

业务故事:
```
跑 main.py 无参数 → 退码非 0 + stderr 提示 worker-loop 子命令
跑 main.py worker-loop --help → 退码 0
```

输入:
- argv = []
- argv = ["worker-loop", "--help"]

预期:
- 第一种: argparse 报错 (subparsers required=True), exit code 非 0
- 第二种: argparse 打印 help, exit code 0

#### TC-18-02 _install_signal_handlers 设置成功

`test/main/TC-02_signal_handlers.py`

业务故事:
```
调 _install_signal_handlers 后, signal.getsignal(SIGTERM) 不再是默认值
(改成本模块内部 handler), SIGINT 同理.
```

输入: 调 `main._install_signal_handlers()`
预期:
- `signal.getsignal(signal.SIGTERM)` != SIG_DFL
- `signal.getsignal(signal.SIGINT)` != SIG_DFL

#### TC-18-03 _run_worker_loop 收到 _stop=True 后 1 个循环内退出

`test/main/TC-03_loop_exits_on_stop.py`

业务故事:
```
mock XrayWorker / ProxyDeployWorker run_once 返 0 (没活).
设 _stop=True (在循环之前), 进入 _run_worker_loop, 应立刻返回 0,
不调 sleep, 不调 worker.run_once 超过 1 次.
```

输入:
- mock workers, 设 main._stop=True (用 monkeypatch)
- 调 `main._run_worker_loop()`

预期:
- 返回 0
- XrayWorker.run_once / ProxyDeployWorker.run_once 都没被调 (或最多调一次, 看实现)
- time.sleep 没被调

#### TC-18-04 _run_worker_loop busy=1 时不 sleep, idle=0 时 sleep

`test/main/TC-04_loop_busy_no_sleep.py`

业务故事:
```
mock XrayWorker.run_once 返 1 (有活), ProxyDeployWorker 返 0
→ 应立刻进入下一轮, 不 sleep.
模拟一轮后置 _stop=True 退出.
```

预期:
- time.sleep 在 busy>=1 的那轮没被调

#### TC-18-05 worker run_once 抛异常不杀死 loop

`test/main/TC-05_loop_resilient_to_worker_error.py`

业务故事:
```
mock XrayWorker.run_once side_effect=Exception, ProxyDeployWorker 正常.
循环应继续, 不退出, 第二轮置 _stop=True 退出.
日志应记 warning.
```

预期:
- 循环没崩
- 异常被 catch + log warning

#### TC-18-06 db/queries.py 3 函数签名 + 行为透传

`test/db/TC-01_queries.py` (新建 test/db/__init__.py + 本文件)

业务故事:
```
db/queries.py 装 3 个函数, 函数签名跟原 services 一致.
跑 query_vps_status / query_ip_status / list_available_proxies 各一次,
返回 dict / list 形状跟原 services 一致 (因为内容就是搬过来的).
```

子测:
- TC-06-a `query_vps_status` 签名: `(vps_id=None, task_id=None) -> dict`
- TC-06-b `query_ip_status` 签名: `(ip_id=None, task_id=None) -> dict`
- TC-06-c `list_available_proxies` 签名: `(country_code="") -> list[dict]`
- TC-06-d 三函数都返回正确"空结果"形状 (空 DB 时 not_found / [])

#### TC-18-07 ⭐ 防回退 — services/ 目录不再有 registration_query.py / proxy_query.py

`test/db/TC-02_no_services_residue.py`

子测:
- TC-07-a `services/registration_query.py` 不存在
- TC-07-b `services/proxy_query.py` 不存在

#### TC-18-08 ⭐ 防回退 — 没有任何活跃路径 import services/

`test/db/TC-03_no_services_imports.py`

业务故事:
```
grep 整个项目 (排除 services/ 自身 + 排除 services/__pycache__ + 排除测试)
不应再有 `from services` import 出现.
排除 main.py 老 CLI 的 3 处 import (本任务一并删).
```

实现:
```python
import pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXCLUDE_DIRS = {"services", "__pycache__", ".venv", ".git", "test"}

bad = []
for p in ROOT.rglob("*.py"):
    if any(part in EXCLUDE_DIRS for part in p.parts):
        continue
    text = p.read_text(encoding="utf-8")
    if re.search(r"^\s*from\s+services\b|\bimport\s+services\b", text, re.MULTILINE):
        bad.append(str(p.relative_to(ROOT)))

assert not bad, f"以下文件还在 import services/: {bad}"
```

#### TC-18-09 ⭐ 防回退 — 3 个 tools 文件 import 路径已改

`test/db/TC-04_tools_use_db_queries.py`

子测:
- TC-09-a `tools/get_vps_registration_status.py` 文件文本含 `from db.queries import query_vps_status`
- TC-09-b `tools/get_ip_registration_status.py` 文件文本含 `from db.queries import query_ip_status`
- TC-09-c `tools/get_available_proxy_nodes.py` 文件文本含 `from db.queries import list_available_proxies`
- TC-09-d 3 个文件都不含 `from services` import

#### TC-18-10 上游测试全过 (回归)

```bash
# 跑 T-06 / T-15 / T-16 / T-17 + 上游 worker spec 全部 TC, 都应 PASS
PYTHONPATH=. uv run pytest test/mcp_tools/TC-*.py test/ssh_worker/TC-*.py test/proxy_deploy_worker/TC-*.py test/xray_worker/TC-*.py test/ip_probe_worker/TC-*.py -q
# 必须全部 PASS (除原本就 skip 的真机 TC), 因为本任务只搬位置不动行为
```

### 必跑测试命令

```bash
PYTHONPATH=. VPS_SERVER_TESTING=1 uv run pytest \
  test/main/TC-*.py \
  test/db/TC-*.py \
  test/mcp_tools/TC-*.py \
  test/ssh_worker/TC-*.py \
  test/proxy_deploy_worker/TC-*.py \
  test/xray_worker/TC-*.py \
  test/ip_probe_worker/TC-*.py \
  -v
```

启动验证 (手动跑一次):

```bash
PYTHONPATH=. uv run python main.py --help
# 应看到 worker-loop 子命令
PYTHONPATH=. uv run python main.py worker-loop &
PID=$!
sleep 2
kill -TERM $PID
wait $PID
# 应看到优雅退出日志, 退码 0
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。一项不打勾都不算完工。

- [ ] T-06 / T-15 / T-16 / T-17 已 done (前置)
- [ ] 任务文件改为 `doing`
- [ ] `main.py` 重写完: 删旧 3 子命令 + 加 worker-loop + signal handler + 顶部 docstring 重写
- [ ] `config.py` 加 `POLL_INTERVAL_SECONDS = 2`
- [ ] `db/queries.py` 新建, 含 3 函数 (内容从 services 搬, 签名不变)
- [ ] `tools/get_vps_registration_status.py` / `tools/get_ip_registration_status.py` / `tools/get_available_proxy_nodes.py` import 改 `from db.queries`
- [ ] `services/registration_query.py` 已 `git rm`
- [ ] `services/proxy_query.py` 已 `git rm`
- [ ] `CLAUDE.local.md` 加 §13 心智模型 + §14 MCP 工具评估清单 + 修订历史 v5 条目
- [ ] TC-18-01 ~ TC-18-09 全过
- [ ] TC-18-10 全套回归 PASS (除 TC-14 真机原本 skip)
- [ ] 手动启动验证: `main.py worker-loop` 能跑起来 + 收 SIGTERM 优雅退出
- [ ] 完成记录段已填(测试结果原样贴出, 不剪裁)

### 实现过程记录(实现者完工时填)

```text
改动文件:
- main.py (重写: 删 3 子命令 + worker-loop)
- config.py (加 POLL_INTERVAL_SECONDS)
- db/queries.py (新建)
- tools/get_vps_registration_status.py (改 import)
- tools/get_ip_registration_status.py (改 import)
- tools/get_available_proxy_nodes.py (改 import)
- CLAUDE.local.md (加 §13 §14 + v5 修订历史)

删除文件:
- services/registration_query.py
- services/proxy_query.py

新增测试:
- test/main/__init__.py (空)
- test/main/TC-01_argparse.py
- test/main/TC-02_signal_handlers.py
- test/main/TC-03_loop_exits_on_stop.py
- test/main/TC-04_loop_busy_no_sleep.py
- test/main/TC-05_loop_resilient_to_worker_error.py
- test/db/__init__.py (空)
- test/db/TC-01_queries.py
- test/db/TC-02_no_services_residue.py
- test/db/TC-03_no_services_imports.py
- test/db/TC-04_tools_use_db_queries.py

测试结果:
- <跑测命令> -> <结果原样贴>

偏差 / 风险:
- <none | details>

启动验证:
- main.py worker-loop 启动: <日志原样贴一段>
- SIGTERM 退出: <日志原样贴>
```

### Claude 验收检查清单

□ 对照用户原话检查实现没有跑偏 (二进程模型 / services 退路 / db/queries 读写都在 / 写入工具 4 条规则)
□ 对照 ADR-0008 §影响清单逐项核对改动
□ 对照"不动"清单确认没碰
□ 跑必跑测试命令并贴结果
□ 检查实现者完工标准全部满足
□ 手动验证 `main.py worker-loop` 能拉起 + SIGTERM 退出
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回实现者修改

---

## 完成记录(done 时追加)

> 任务完成后再填。waiting 阶段不要预填。

```text
完成日期:
完成 commit:
任务状态: doing -> done
改动摘要:
测试命令:
测试结果:
未覆盖风险:
后续任务: admin/user 真正拆 MCP server (ADR-0007 §8 + ADR-0008 §3.1 留下波, 后续单独 ADR)
```
