# T-10 测试 VPS 凭据清单 (probe_vps.py)

**ID**: T-10
**状态**: done
**前置依赖**: 无
**后续依赖**: T-11(IPProbeWorker 实现,未来任务,会 import 本任务产出的 `PROBE_VPS_POOL`)
**关联 ADR**: [[0001-workers-replace-services]] §决策 §6(IPProbeWorker 触发场景)
**关联 spec**: 暂无(IPProbeWorker spec 后续单独建,本任务纯基础设施)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名从 `waiting_10_probe_vps_config.md` 改为
      `doing_10_probe_vps_config.md`。

如果目标任务已经是 `doing`, 说明已有窗口领取。不要抢同一任务,
先问用户或换任务。

### 必读清单

领取后、写代码前, 必须显式读取:

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`(尤其 §8 测试 VPS 配置化)
- [ ] `docs/adr/README.md`
- [ ] `docs/adr/0001-workers-replace-services.md`
- [ ] `config.py`(根目录, 项目通用配置, 看现状 + 风格)
- [ ] `ssh/session.py`(确认 `VPSSession.__init__` 形参顺序与键名)

未读完上面文件前, 禁止写代码 / 写测试 / 把任务改成 done。

---

## 1. 用户原话 / 业务目标

### 用户原话

> "在config加入常量给我可以登记1-3台的测试服务器 账号密码,入口,端口 这些信息够了,如果测试服务器连不上就挑下一台测"

> "单独一个config文件吧,不跟旧的config混一起"

> "那不包一层了麻烦 ProbeVPS 拿到根吧"
> (确认: 不建 `config/` 子包, 直接放仓库根 `probe_vps.py`)

> "工人干的活那是它那边的 rgvps 干活的"
> (确认: SSHWorker 跟本任务无关; 复用层在 VPSSession + XrayManager, 不在 SSHWorker)

> "你把写成常量配置 代码实现里用常量来读 密码也放在配置文件里 方便我后面修改 代码在我手上 安全"
> (2026-06-09 拍板: 密码走配置文件常量, 不走 .env; `probe_vps.py` 进 .gitignore,
>  另建 `probe_vps.example.py` 进 git 给模板)

> "如果没有可用测试服务器要及时抛信息说没有服务器,请往哪个文件添加给指引"
> (引出 `get_probe_vps_pool()` helper: 空 pool 抛 RuntimeError 带指引)

### Claude 整理后的业务理解

- **外部输入**: 无(纯静态配置)
- **第一件事**: 在仓库根新建 `probe_vps.py`, 装一份测试 VPS 凭据清单
- **主要流程**:
  1. 新建 `probe_vps.py`(进 .gitignore, 凭据直写常量)
  2. 定义 `PROBE_VPS_POOL: tuple[dict, ...]`(1-3 项)
  3. 提供 `get_probe_vps_pool()` helper, 空 pool 抛 RuntimeError 带指引
  4. 另建 `probe_vps.example.py`(进 git) 给模板
- **判断分支**: 仅 `get_probe_vps_pool()` 内空 / 非空二分
- **数据流**:
  - 读取: 无外部读取(凭据直写常量)
  - 写入: 无
- **同步 / 异步边界**: 同步(import 时一次性读取)
- **成功返回 / 失败返回**: N/A(纯配置)

### 本任务要解决什么

为未来的 **IPProbeWorker** 提供一份"测试 VPS 凭据清单"。未来工人 import 这份
清单, 按顺序挑一台测试 VPS:

- `VPSSession(**pool[0])` 展开即用(键名跟 `VPSSession.__init__` 形参对齐)
- 在测试 VPS 上临时挂用户提交的上游 IP 凭据(当 xray outbound)+ 内 ping
- 连不上挑下一台(fallback 循环逻辑住工人, 不住本任务)

### 本任务不解决什么

- ✗ 不实现 IPProbeWorker 本身
- ✗ 不实现"连不上挑下一台"的 fallback 循环(住未来工人代码)
- ✗ 不加 `test_port_range`(测试用端口段, 等聊 rgip 业务时再决定要不要加)
- ✗ 不连真服务器测试(连真服务器是 IPProbeWorker 任务的事)
- ✗ 不引入 `ProbeVPS` dataclass(已确认用 dict 元组即可, 字段键名直接对齐
   `VPSSession.__init__` 形参)
- ✗ 不建 `config/` 子包(已确认拿到根, 跟 `config.py` 平级)
- ✗ 不动旧 `config.py`

---

## 2. 实现参考

### 验收锚点

- `CLAUDE.local.md` §8 测试 VPS 配置化(原有约束, 本任务落地)
- `docs/adr/0001-workers-replace-services.md` §决策 §6(IPProbeWorker 触发)
- `ssh/session.py::VPSSession.__init__` 形参签名(`ip / username / password / port`)

### 改动文件清单

#### 新建 `probe_vps.py`(仓库根, 跟 `config.py` 平级; **进 .gitignore**)

```text
职责: IPProbeWorker 用的测试 VPS 凭据清单。

包含:
- PROBE_VPS_POOL: tuple[dict, ...]
  - 长度 1-3
  - 每项 dict 4 个字段: ip / port / username / password
  - 字段键名与 VPSSession.__init__ 形参对齐, 工人 VPSSession(**dict) 展开即用
  - 全部字段(含 password)直写常量字面量, 不读 env
- NO_PROBE_VPS_MESSAGE: str
  - 空 pool 时的提示文案, 指向 probe_vps.py 让用户加凭据
- get_probe_vps_pool() -> tuple[dict, ...]
  - 非空 → 返回 PROBE_VPS_POOL
  - 空 → 抛 RuntimeError(NO_PROBE_VPS_MESSAGE), 让上层把指引透传给 agent / 用户

谁用:
- 未来 workers/ip_probe_worker.py (T-13) 通过 get_probe_vps_pool() 取
```

#### 新建 `probe_vps.example.py`(仓库根, **进 git** 作为模板)

```text
跟 probe_vps.py 结构一致, 凭据字段填占位 (PLACEHOLDER_*)。
docstring 顶部说明: cp probe_vps.example.py probe_vps.py 后填真实凭据。
```

#### 改 `.gitignore`

```text
加一行: probe_vps.py
注释说明: 测试 VPS 凭据(密码直写常量), 占位见 probe_vps.example.py
```

#### 新建 `test/probe_vps/__init__.py`

```text
空文件, 让 test/probe_vps/ 成 pytest 收集目录。
```

#### 新建 `test/probe_vps/TC-01_probe_vps_pool.py`

```text
轻量结构断言, 不连真服务器:
- PROBE_VPS_POOL 是 tuple
- 1 <= len(PROBE_VPS_POOL) <= 3
- 每项 dict 含 ip / port / username / password 4 键
- ip / username / password 是 str 且非空(密码直写常量, 不允许空)
- port 是 int 且 > 0
- VPSSession(**pool[0]) 能实例化不抛错 (不调 connect)
- get_probe_vps_pool() 非空返回 pool
- monkeypatch 把 PROBE_VPS_POOL 置空时, get_probe_vps_pool() 抛 RuntimeError,
  消息等于 NO_PROBE_VPS_MESSAGE 且包含 "probe_vps.py"
```

#### 不动

```text
- config.py (旧, 项目通用配置)
- ssh/* / workers/* / xray/* / db/* / tools/* / toolbox/*
- 任何已有 ADR / spec
- .env / .env.example (本任务凭据不走 env)
```

### 实现轮廓

```python
# probe_vps.py(进 .gitignore, 不进 git)
"""IPProbeWorker 用的测试 VPS 凭据清单。

凭据(含密码)直接写本文件作为常量, 不走 .env。本文件已加入 .gitignore,
代码归本地所有, 不会进入 git。模板见 probe_vps.example.py。
"""


PROBE_VPS_POOL: tuple[dict, ...] = (
    {
        "ip": "<真实 IP>",
        "port": 22,
        "username": "root",
        "password": "<真实密码>",
    },
)


NO_PROBE_VPS_MESSAGE = (
    "没有可用测试 VPS。请往 probe_vps.py 的 PROBE_VPS_POOL 加一条凭据 "
    "(字段: ip / port / username / password, 跟 VPSSession.__init__ 形参对齐)。"
)


def get_probe_vps_pool() -> tuple[dict, ...]:
    if not PROBE_VPS_POOL:
        raise RuntimeError(NO_PROBE_VPS_MESSAGE)
    return PROBE_VPS_POOL
```

```python
# probe_vps.example.py(进 git, 作为模板)
"""结构跟 probe_vps.py 一致, 字段填占位 (PLACEHOLDER_*)。"""
# ...略
```

```python
# test/probe_vps/TC-01_probe_vps_pool.py
import pytest

import probe_vps
from probe_vps import NO_PROBE_VPS_MESSAGE, PROBE_VPS_POOL, get_probe_vps_pool
from ssh.session import VPSSession


def test_pool_is_tuple_with_valid_size():
    assert isinstance(PROBE_VPS_POOL, tuple)
    assert 1 <= len(PROBE_VPS_POOL) <= 3


@pytest.mark.parametrize("entry", PROBE_VPS_POOL)
def test_entry_has_required_keys(entry):
    assert set(entry.keys()) >= {"ip", "port", "username", "password"}
    assert isinstance(entry["ip"], str) and entry["ip"]
    assert isinstance(entry["port"], int) and entry["port"] > 0
    assert isinstance(entry["username"], str) and entry["username"]
    assert isinstance(entry["password"], str) and entry["password"]


def test_first_entry_can_construct_vps_session():
    sess = VPSSession(**PROBE_VPS_POOL[0])
    assert sess.ip == PROBE_VPS_POOL[0]["ip"]
    assert sess.port == PROBE_VPS_POOL[0]["port"]
    assert not sess.is_connected


def test_get_probe_vps_pool_returns_pool_when_non_empty():
    assert get_probe_vps_pool() is PROBE_VPS_POOL


def test_get_probe_vps_pool_raises_with_guidance_when_empty(monkeypatch):
    monkeypatch.setattr(probe_vps, "PROBE_VPS_POOL", ())
    with pytest.raises(RuntimeError) as exc_info:
        probe_vps.get_probe_vps_pool()
    assert str(exc_info.value) == NO_PROBE_VPS_MESSAGE
    assert "probe_vps.py" in str(exc_info.value)
```

### 数据结构

| 字段 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `PROBE_VPS_POOL[i]["ip"]` | 测试 VPS 入口 (IP/域名) | 未来 IPProbeWorker | 配置作者 (字面量直写 probe_vps.py) |
| `PROBE_VPS_POOL[i]["port"]` | SSH 端口 | 同上 | 同上 |
| `PROBE_VPS_POOL[i]["username"]` | SSH 用户名 | 同上 | 同上 |
| `PROBE_VPS_POOL[i]["password"]` | SSH 密码 | 同上 | 同上(本地文件, probe_vps.py 不进 git) |

### 缺工具 / 缺信息先报告

实现者遇到以下情况必须停下来报告, 不要自己拍板:

- 用户没提供任何真实测试 VPS 凭据(至少需要 1 台才有意义)
- 发现 `VPSSession.__init__` 形参跟本任务字段键名对不上(应为
  `ip / username / password / port`, 若已变更需先对齐)
- 用户希望加 `test_port_range` 等额外字段(本任务范围外, 需新开任务)

---

## 3. 验收交付

### 测试用例

#### TC-10-a `test/probe_vps/TC-01_probe_vps_pool.py`

业务故事:

```text
我作为未来的 IPProbeWorker, 调 get_probe_vps_pool() 拿凭据清单:
- 有配 → 拿到 1-3 项, 每项可 VPSSession(**entry) 展开实例化
- 没配 (PROBE_VPS_POOL 是空 tuple) → 抛 RuntimeError 带指引,
  告诉调用方"去 probe_vps.py 加一条"
```

输入:
- `from probe_vps import PROBE_VPS_POOL, get_probe_vps_pool, NO_PROBE_VPS_MESSAGE`

预期:
- pool 是 tuple, 长度 1-3
- 每项 dict 含 ip / port / username / password
- ip / username / password 非空 str(密码也直写常量), port 正整数
- `VPSSession(**pool[0])` 实例化不抛错, 实例属性跟字典对齐, `is_connected=False`
- 非空时 `get_probe_vps_pool() is PROBE_VPS_POOL`
- monkeypatch 置空时 `get_probe_vps_pool()` 抛 `RuntimeError`,
  消息等于 `NO_PROBE_VPS_MESSAGE` 且包含 `probe_vps.py`

不应发生:
- pool 是空 tuple(常态: 进 commit 时 pool 已有真实凭据)
- 字段键名跟 VPSSession 形参不对应
- 实例化时抛错

### 必跑测试命令

```bash
VPS_SERVER_TESTING=1 pytest test/probe_vps/TC-01_probe_vps_pool.py -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。

- [x] 开工前已将任务文件从 `waiting` 改为 `doing`。
- [x] 已新建 `probe_vps.py`(仓库根, 含 `PROBE_VPS_POOL` + `NO_PROBE_VPS_MESSAGE` +
      `get_probe_vps_pool()`)。
- [x] 已新建 `probe_vps.example.py`(仓库根, 模板进 git)。
- [x] 已在 `.gitignore` 加一行 `probe_vps.py`。
- [x] 已新建 `test/probe_vps/__init__.py` + `test/probe_vps/TC-01_probe_vps_pool.py`。
- [x] 必跑测试命令 PASS。
- [x] 真实测试 VPS 凭据已由用户提供并填入 `PROBE_VPS_POOL`(占位 `PLACEHOLDER_*`
      只许出现在 `probe_vps.example.py`, 不允许在 `probe_vps.py`)。
- [x] 没有改动"不动"清单里的文件。
- [x] 没有引入新的 dataclass / 类(纯 dict 元组 + helper 函数)。
- [x] 没有建 `config/` 子包(直接根目录)。
- [x] 完成记录段已填(测试结果原样贴出)。

### 实现过程记录(实现者完工时填)

```text
改动文件:
- <path>

测试结果:
- VPS_SERVER_TESTING=1 pytest test/probe_vps/TC-01_probe_vps_pool.py -v -> <result>

偏差 / 风险:
- <none | details>
```

### Claude 验收检查清单

□ 对照用户原话检查: 文件位置(根)、不包一层、字段对齐 VPSSession、密码走 env
□ 对照 CLAUDE.local.md §8 检查: 测试 VPS 配置化原则一致
□ 跑必跑测试命令并记录结果
□ 检查实现者完工标准全部满足
□ 检查 PLACEHOLDER 已被真实凭据替换
□ 偏差但合理 -> 抛给用户决策
□ 偏差不合理 -> 打回实现者修改

---

## 完成记录(done 时追加)

```text
完成日期: 2026-06-09
完成 commit: 见本 commit hash
任务状态: doing -> done

改动摘要:
- 新建 probe_vps.py(仓库根, 进 .gitignore): 装常量 PROBE_VPS_POOL + 错误指引常量
  NO_PROBE_VPS_MESSAGE + helper get_probe_vps_pool()(空 pool 抛 RuntimeError 带指引)。
  当前 pool 含 1 条真实测试 VPS 凭据(用户 2026-06-09 提供)。
- 新建 probe_vps.example.py(仓库根, 进 git): 同结构占位模板, 字段为 PLACEHOLDER_*,
  供未来新环境 cp 后填。
- 改 .gitignore: 加 `probe_vps.py` 一行(密码直写常量, 不进 git)。
- 新建 test/probe_vps/__init__.py: 让 pytest 收集目录。
- 新建 test/probe_vps/TC-01_probe_vps_pool.py: 5 个用例覆盖
  (结构/字段/VPSSession 实例化/helper 返回/空 pool 抛错带指引)。

测试命令:
- VPS_SERVER_TESTING=1 pytest test/probe_vps/TC-01_probe_vps_pool.py -v

测试结果:
- 5 passed in 0.08s
  - test_pool_is_tuple_with_valid_size PASSED
  - test_entry_has_required_keys[entry0] PASSED
  - test_first_entry_can_construct_vps_session PASSED
  - test_get_probe_vps_pool_returns_pool_when_non_empty PASSED
  - test_get_probe_vps_pool_raises_with_guidance_when_empty PASSED

偏差(已跟用户对齐, 任务单上文已同步修订):
- 原任务单要求"密码走 .env" → 2026-06-09 用户拍板"密码直写常量,
  代码归本地所有, probe_vps.py 进 .gitignore, 另建 .example.py 进 git"。
  原话: "你把写成常量配置 代码实现里用常量来读 密码也放在配置文件里 方便我后面修改"
- 新增 get_probe_vps_pool() helper + NO_PROBE_VPS_MESSAGE 常量, 兜底"空 pool 时
  抛错带指引"。用户原话: "如果没有可用测试服务器要及时抛信息说没有服务器,请往哪个
  文件添加给指引"。

未覆盖风险:
- 未做真服务器 SSH 连通验证(本任务边界, 由未来 IPProbeWorker T-13 跑真机)。
- 用户本地 probe_vps.py 不进 git, 跨机器/换设备时需手动 cp .example.py 再填凭据。

后续任务:
- T-13 IPProbeWorker 实现: 通过 `get_probe_vps_pool()` 取清单, 按顺序挑测试 VPS,
  连不上挑下一台 (fallback 循环住工人, 见 waiting_13_ip_probe_worker_implementation.md)。
```
