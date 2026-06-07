# T-08 内 ping / 外 ping 工具搬 toolbox

**ID**: T-08
**前置依赖**: 无(可与 T-03 / T-07 并行,但建议 T-08 先做,T-07 实现时能直接 import)
**后续依赖**: T-07 XrayWorker 实现时调 `toolbox.proxy_check.test_internal` / `test_external`

---

## 验收锚点

- `docs/adr/0004-xray-worker-flow-refinements.md` §决策 §4 / §5(用 toolbox 工具做 ping)
- `test/xray_worker/spec.md` v5 §二 C "toolbox 通用工具"
- `CLAUDE.local.md` §0 legacy 代码姿势(旧 services 用的旧名字保留, 新代码用新名字)
- `CLAUDE.md` 反模式表"缺工具先造"(本任务就是造工具)

## 改动文件清单

### 改 `toolbox/proxy_check.py`(扩, 不 break 现有)

```
保留现有:
  - test_socks_proxy(proxy_ip, proxy_port, ..., user="", pwd="") -> dict
    旧 services 还在用, 不动

新增 2 个返回 bool 的简化函数:
  ① test_internal(client, port, user="", pwd="", timeout=10) -> bool
     ⭐ "内 ping" - 在 VPS 内部 SSH 跑 curl --socks5 测 inbound 通不通
     实现: 内部调 xray.service.test_internal_socks (现有), 然后只返回 result["ok"]
     不在这阶段把整段逻辑搬过来, 避免一次性改太多. 等 legacy services 整体删除时再彻底搬

  ② test_external(host, port, user="", pwd="", timeout=10) -> bool
     ⭐ "外 ping" - 从 worker 本机通过 socks5 代理发请求测 inbound 通不通
     实现: 内部调本文件现有 test_socks_proxy, 然后只返回 result["ok"]
```

### 不动

```
- toolbox/geoip.py::lookup_egress — 现有, 已是 v5 §4 "pingIP 查国家" 用的
- xray/service.py::test_internal_socks — legacy, 留着给旧 services 对照
- toolbox/proxy_check.py::test_socks_proxy — 旧 services 在用, 留着
- services/* — 一切 legacy 不动
- workers/* — XrayWorker 实现走 T-07, 不在本任务范围
```

### 新建测试

```
test/toolbox/ 不存在 → 不新建测试目录
test_internal 和 test_external 都是薄 wrapper, 行为由被包的旧函数验证
真测试在 T-07 XrayWorker 集成测里覆盖
```

---

## 实现轮廓

### 改 `toolbox/proxy_check.py`

```python
"""... 现有 docstring ..."""

from __future__ import annotations

import requests
import paramiko

import config
from log import get_logger

# 现有 import / 常量保留

logger = get_logger(__name__)


# === 现有的 test_socks_proxy 保留, 不动 ===

def test_socks_proxy(...) -> dict:
    # 现有实现, 不动
    ...


# === v5 新增 2 个简化 wrapper ===

def test_internal(
    client: paramiko.SSHClient,
    port: int,
    user: str = "",
    pwd: str = "",
    timeout: int = config.CONNECTIVITY_TEST_TIMEOUT,
) -> bool:
    """⭐ 内 ping - 在 VPS 内部 SSH 跑 curl 测 inbound 通不通.

    返回 True/False (通/不通). 由 XrayWorker 统一收尾用.

    内部委托给 xray.service.test_internal_socks (legacy 实现),
    只取 result["ok"]. 等 legacy services/ 整体删除时再把实现彻底搬过来.
    """
    from xray.service import test_internal_socks  # 局部 import 避免循环依赖
    result = test_internal_socks(
        client=client,
        port=port,
        user=user,
        pwd=pwd,
        timeout=timeout,
    )
    return result.get("ok", False)


def test_external(
    host: str,
    port: int,
    user: str = "",
    pwd: str = "",
    timeout: int = config.CONNECTIVITY_TEST_TIMEOUT,
) -> bool:
    """⭐ 外 ping - 从本机通过 socks5 代理发请求测远程 inbound 通不通.

    返回 True/False (通/不通). 由后续 ProxyDeployWorker 用 (验证客户端能从外部连上).

    内部委托给本文件 test_socks_proxy (现有), 只取 result["ok"].
    """
    result = test_socks_proxy(
        proxy_ip=host,
        proxy_port=port,
        user=user,
        pwd=pwd,
        timeout=timeout,
    )
    return result.get("ok", False)
```

---

## 实现者完工标准

```
- [ ] toolbox/proxy_check.py 新增 test_internal 和 test_external 两个函数
- [ ] 现有 test_socks_proxy 不动
- [ ] 不动 xray/service.py::test_internal_socks (legacy 留着)
- [ ] 不动 services/* / workers/* / tools/*
- [ ] uv run python -c "from toolbox.proxy_check import test_internal, test_external" 不报错
- [ ] commit 标题: feat(toolbox): proxy_check 加 test_internal / test_external 简化 wrapper
```

---

## Claude 验收检查清单

```
□ git diff toolbox/proxy_check.py:
    - 加了 test_internal 和 test_external 函数
    - 类型签名: 内 ping 接 paramiko.SSHClient + port, 外 ping 接 host + port
    - 都返回 bool
    - 都是 wrapper, 不是重写
    - test_socks_proxy 完全不动
□ uv run python -c "from toolbox.proxy_check import test_internal, test_external" 通过
□ uv run python -c "from xray.service import test_internal_socks" 还能跑(没破 legacy)
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 → 打回
```

---

## 备注: 为啥不直接把整段实现搬过来

按 CLAUDE.local.md §0, **legacy `services/`(以及 `xray/service.py` 里被 services 用的函数)留着不动作对照**。
现在搬整段实现会破坏对照价值, 而且增加本任务复杂度。

**完工里程碑(将来某个时间点)**:
- legacy `services/` 整体删除时
- 一并把 `xray/service.py::test_internal_socks` 实现真正搬到 `toolbox/proxy_check.py::test_internal` 里
- 删 `xray/service.py::test_internal_socks`
- 那是另一份 task,不在本任务范围
