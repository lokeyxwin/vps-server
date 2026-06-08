# T-10 测试 VPS 凭据清单 (probe_vps.py)

**ID**: T-10
**状态**: waiting
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

### Claude 整理后的业务理解

- **外部输入**: 无(纯静态配置)
- **第一件事**: 在仓库根新建 `probe_vps.py`, 装一份测试 VPS 凭据清单
- **主要流程**:
  1. 新建 `probe_vps.py`
  2. 定义 `PROBE_VPS_POOL: tuple[dict, ...]`(1-3 项)
  3. 密码字段走 `.env` 环境变量(跟项目现有风格一致: `ENCRYPTION_KEY` /
     `MYSQL_PASSWORD` / `IPINFO_TOKEN` 都走 env)
- **判断分支**: 无(纯数据)
- **数据流**:
  - 读取: `.env`(密码字段)
  - 写入: 无
- **同步 / 异步边界**: 同步(import 时一次性读取)
- **成功返回 / 失败返回**: N/A(纯配置, 没有调用语义)

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

#### 新建 `probe_vps.py`(仓库根, 跟 `config.py` 平级)

```text
职责: IPProbeWorker 用的测试 VPS 凭据清单。

包含:
- PROBE_VPS_POOL: tuple[dict, ...]
  - 长度 1-3
  - 每项 dict 4 个字段: ip / port / username / password
  - 字段键名与 VPSSession.__init__ 形参对齐, 工人 VPSSession(**dict) 展开即用
  - ip / port / username 硬编码到文件里(便于查看)
  - password 走 os.environ.get(f"PROBE_VPS_{N}_PASSWORD", "") (N=1..3)
  - 真实 IP / 账号待用户提供后填入(本任务先填占位 PLACEHOLDER, commit 前由
    用户提供真实值再替换)

谁用:
- 未来 workers/ip_probe_worker.py (T-11)
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
- ip / username 是 str 且非空
- port 是 int 且 > 0
- password 字段存在 (允许为空字符串, 表示 .env 未配)
- VPSSession(**pool[0]) 能实例化不抛错 (不调 connect)
```

#### 不动

```text
- config.py (旧, 项目通用配置)
- ssh/* / workers/* / xray/* / db/* / tools/* / toolbox/*
- 任何已有 ADR / spec
```

### 实现轮廓

```python
# probe_vps.py
"""IPProbeWorker 用的测试 VPS 凭据清单。

IPProbeWorker (rgip 入口同步段) 触发时, 按顺序从这份清单挑一台测试 VPS,
SSH 上去临时挂用户提交的上游 IP 凭据 (作为 xray outbound) + 内 ping 验证。
连不上挑下一台 (fallback 循环住工人, 不住本文件)。

字段键名与 VPSSession.__init__ 形参对齐, 工人 VPSSession(**dict) 展开即用。

密码走 .env 避免源码进 git 时明文外泄:
    PROBE_VPS_1_PASSWORD=...
    PROBE_VPS_2_PASSWORD=...
    PROBE_VPS_3_PASSWORD=...

谁用:
- 未来 workers/ip_probe_worker.py
"""

import os


PROBE_VPS_POOL: tuple[dict, ...] = (
    {
        "ip": "PLACEHOLDER_HOST_1",
        "port": 22,
        "username": "root",
        "password": os.environ.get("PROBE_VPS_1_PASSWORD", ""),
    },
    # 1-3 台, 实现时按用户提供的真实凭据补
)
```

```python
# test/probe_vps/TC-01_probe_vps_pool.py
"""TC-10-a 测试 VPS 凭据清单结构断言。

只测静态结构, 不连真服务器。
"""

import pytest

from probe_vps import PROBE_VPS_POOL
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
    assert isinstance(entry["password"], str)  # 允许为空: .env 未配


def test_first_entry_can_construct_vps_session():
    """字段键名跟 VPSSession.__init__ 形参对齐, 展开实例化不抛错。"""
    sess = VPSSession(**PROBE_VPS_POOL[0])
    assert sess.ip == PROBE_VPS_POOL[0]["ip"]
    assert sess.port == PROBE_VPS_POOL[0]["port"]
    assert not sess.is_connected  # 只构造不连接
```

### 数据结构

| 字段 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `PROBE_VPS_POOL[i]["ip"]` | 测试 VPS 入口 (IP/域名) | 未来 IPProbeWorker | 配置作者 (硬编码) |
| `PROBE_VPS_POOL[i]["port"]` | SSH 端口 | 同上 | 同上 |
| `PROBE_VPS_POOL[i]["username"]` | SSH 用户名 | 同上 | 同上 |
| `PROBE_VPS_POOL[i]["password"]` | SSH 密码 | 同上 | `.env` 文件: `PROBE_VPS_<N>_PASSWORD` |

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
我作为未来的 IPProbeWorker, import PROBE_VPS_POOL 后:
- 至少能拿到 1 项, 最多 3 项
- 每一项字段键名都跟 VPSSession.__init__ 形参对得上
- VPSSession(**pool[0]) 能正常实例化 (不实际连接)
- 密码字段允许为空 (.env 没配的开发环境也能 import)
```

输入:
- `from probe_vps import PROBE_VPS_POOL`

预期:
- pool 是 tuple, 长度 1-3
- 每项 dict 含 ip / port / username / password
- ip / username 非空 str, port 正整数, password 是 str (可空)
- `VPSSession(**pool[0])` 实例化不抛错, 实例属性跟字典对齐, `is_connected=False`

不应发生:
- pool 是空 tuple
- 字段键名跟 VPSSession 形参不对应
- 实例化时抛错

### 必跑测试命令

```bash
VPS_SERVER_TESTING=1 pytest test/probe_vps/TC-01_probe_vps_pool.py -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。

- [ ] 开工前已将任务文件从 `waiting` 改为 `doing`。
- [ ] 已新建 `probe_vps.py`(仓库根)。
- [ ] 已新建 `test/probe_vps/__init__.py` + `test/probe_vps/TC-01_probe_vps_pool.py`。
- [ ] 必跑测试命令 PASS。
- [ ] 真实测试 VPS 凭据已由用户提供并填入 `PROBE_VPS_POOL`(占位 `PLACEHOLDER_*`
      不允许进入完工 commit)。
- [ ] `.env` 已配 `PROBE_VPS_<N>_PASSWORD` 对应每条记录。
- [ ] 没有改动"不动"清单里的文件。
- [ ] 没有引入新的 dataclass / 类(纯 dict 元组)。
- [ ] 没有建 `config/` 子包(直接根目录)。
- [ ] 完成记录段已填(测试结果原样贴出)。

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

> 任务完成后再填。waiting 阶段不要预填。

```text
完成日期:
完成 commit:
任务状态: doing -> done
改动摘要:
测试命令:
测试结果:
未覆盖风险:
后续任务:
```
