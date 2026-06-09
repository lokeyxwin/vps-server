# T-13 IPProbeWorker 实现(rgip 入口同步段主工人)

**ID**: T-13
**状态**: done
**前置依赖**:
- T-10(probe_vps.PROBE_VPS_POOL 测试 VPS 清单)
- T-11(IPStatus / IPRecord.status 字段 / IPTask 表)
- T-12(test_internal_socks 暴露 exit_code + stderr)
**后续依赖**: T-14(tools/rgip.py MCP 入口调本工人)
**关联 ADR**: [[0001-workers-replace-services]] §决策 §6;[[0004-xray-worker-flow-refinements]] §决策 §5 共享 outbound 兜底
**关联 spec**: [[test/ip_probe_worker/spec.md]] v2(全文)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 已确认 T-10 / T-11 / T-12 都已 done(本任务依赖它们)。
- [ ] 开始写代码前, 已将文件名从 `waiting_13_...md` 改为 `doing_13_...md`。

### 必读清单

- [ ] `CLAUDE.md`(尤其 §2.4 / §2.5 / §4.3 / §7)
- [ ] `CLAUDE.local.md`(尤其 §业务编排:worker / kit / task 体系 + 工人阵容 + 错误细分契约)
- [ ] `docs/adr/0001-workers-replace-services.md`
- [ ] `docs/adr/0005-vps-stage-as-resource-lock.md`
- [ ] `test/ip_probe_worker/spec.md` v2 **全文**(8 步主流程 + §二 工具清单)
- [ ] `workers/ssh_worker.py`(SSHWorker 类风格 / 私有方法切片 / 错误文案模板)
- [ ] `workers/xray_worker.py`(XrayWorker 完整实现 / try/finally cleanup 兜底)
- [ ] `ssh/session.py`(VPSSession 接口)
- [ ] `xray/manager.py`(replace_proxy_binding / rollback_proxy_binding / test_internal_socks)
- [ ] `xray/config.py`(build_proxy_outbound / generate_random_auth / 各异常类)
- [ ] `xray/service.py::test_internal_socks`(T-12 改后版本)
- [ ] `toolbox/geoip.py::lookup_egress`
- [ ] `toolbox/proxy_check.py`(对照,不直接调)
- [ ] `db/models.py`(IPRecord / IPTask / IPStatus / TaskStatus,T-11 改后)
- [ ] `probe_vps.py`(PROBE_VPS_POOL,T-10 改后)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "外部传进来一条上游 IP 凭据 ... 先查库就看出口 IP 在数据库有没有重复 重复抛回 没重复走下一步, 启动 VPS 类这个类的服务器从 config 里拿(前面说了) 然后登录上去服务器后拿 xray 操作类, 直接拿这条代理配一次 xray 的配置 ... 测试入口账号密码端口可以用这里是校验账号密码端口能不能用; 连不上就抛错误回去; 这里还是同步函数同时把这个配置 remove 了收尾 同步结束"

> "Q1 出口 IP 服务商一般都会直接给的, 不浪费资源直接查 ... Q3 对, AUTH 精准说密码说请校验, 超时的话重试 3 次; 拒接我想不到什么原因 ... Q4 兜个底把用实测的, 拿到后 IP 再再数据库里查一次重复"

> "算了就统一同步吧, 反正对外 mcp 说明如果有多条等待第一条搞完再扔第二条"

> "IPP 是入口登记签派任务, proxy 就抢任务抢到任务就 running, 然后就去看 VPS 表有哪台服务器空闲"

### 整理后的业务理解

- **外部输入**: 用户提交的上游 IP 凭据(entry_host / entry_port / username / password / protocol / declared_egress_ip / provider_domain / expire_date)
- **第一件事**: 用 declared_egress_ip 查 ip_record 早期短路
- **主要流程**: 见 spec §3 八步
- **判断分支**: 见 spec §7 失败分类
- **数据流**:
  - 读: ip_record(2 次查重)
  - 写: ip_record(成功入库)+ ip_task(派任务,vps_id=NULL)
  - 临时操作远端测试 VPS xray 配置(挂 + 拆)
- **同步 / 异步边界**: 全同步(8 步在一个 process 调用里跑完)
- **成功 / 失败返回**: 见 spec §2 入口契约 + §7 失败分类

### 本任务要解决什么

实现 `workers/ip_probe_worker.py::IPProbeWorker` 类 + 全套 TC, 让 rgip MCP 工具的同步段能跑通:
- 接 1 条上游 IP 凭据
- 校验通过 → 入 ip_record(usable) + 派 ip_task(pending, vps_id=NULL)
- 校验失败 → 不入库 + 清测试 VPS 残留 + 抛 7 种 status 之一

### 本任务不解决什么

- ✗ 不实现 ProxyDeployWorker(下游异步, 单独任务)
- ✗ 不实现 tools/rgip.py MCP 入口(T-14)
- ✗ 不实现"测试 VPS 同时并发"锁(MCP 边界外部串行已保证)
- ✗ 不入库声明值(declared_egress_ip / 声明国家)只用作早期查重
- ✗ 不动 SSHWorker / XrayWorker / 旧 services/
- ✗ 不抽错误分类到工具层(住工人内部私有方法)

---

## 2. 实现参考

### 验收锚点

- `test/ip_probe_worker/spec.md` v2 全文
- spec §3 主流程 / §6 状态机 / §7 失败分类 / §8 不做的事 / §9 不变量 / §10 边界
- spec §二 工具清单 §A~§G(实现完全照单调用)

### 改动文件清单

#### 新建 `workers/ip_probe_worker.py`

```text
职责: 实现 IPProbeWorker 类(rgip MCP 工具同步段主工人)。

类结构(参照 SSHWorker / XrayWorker 风格):
- 顶部:错误文案常量 + curl exit code → status 映射
- IPProbeWorker 类:
  - __init__: 不绑状态,每次 process 来一条新的
  - process(...): 主入口,返回 dict 含 status + 业务字段
  - 8 个私有方法见 spec §F

不调:
- workers/ssh_worker.py / workers/xray_worker.py(平行工人, 不互调)
- services/(旧, 不 import)

依赖 import:
- ssh.session.VPSSession
- xray.manager.XrayManager
- xray.config: build_proxy_outbound / generate_random_auth / PROTOCOL_SOCKS5 / 异常类
- xray.service: test_internal_socks(T-12 改后)
- toolbox.geoip: lookup_egress
- db.models: IPRecord / IPTask / IPStatus / TaskStatus
- db.session: session_scope
- probe_vps: PROBE_VPS_POOL, PROBE_TEST_PORT
- log: get_logger
```

#### 新建 `test/ip_probe_worker/TC-01_lookup_declared.py`

```text
TC: ① 早期查重
- 库里已有 declared_egress_ip → 返回 status=duplicate,不 SSH
- 库里无 → 不短路,继续走 ②(本 TC 只测短路, mock 后续)
```

#### 新建 `test/ip_probe_worker/TC-02_pick_probe_vps.py`

```text
TC: ② 测试 VPS 顺序挑
- PROBE_VPS_POOL 第 1 台连通 → 返回该 session
- 第 1 台 SSH 失败,第 2 台连通 → 返回第 2 台 session
- 全部失败 → 抛/返回 probe_vps_unreachable
```

#### 新建 `test/ip_probe_worker/TC-03_apply_test_outbound.py`

```text
TC: ③+④ 挂上游
- XrayManager.replace_proxy_binding 被调时参数正确 (19000, build_proxy_outbound 产物)
- 返回 last_config 给 cleanup 用
```

#### 新建 `test/ip_probe_worker/TC-04_classify_proxy_error.py`

```text
TC: _classify_proxy_error 映射
- exit_code=7  → proxy_refused
- exit_code=28 → proxy_timeout
- exit_code=97 → proxy_auth_failed
- exit_code=97 但 stderr 含 "auth" 关键字 → proxy_auth_failed(双重确认)
- 其他 exit_code → proxy_failed
- exit_code=0 + ok=True 不调本方法(不走错误分支)
```

#### 新建 `test/ip_probe_worker/TC-05_proxy_auth_failed.py`

```text
TC: ④ auth_failed 路径
- mock test_internal_socks 返回 {ok:False, exit_code:97, stderr:"...auth..."}
- 预期: status=proxy_auth_failed
- 预期: rollback_proxy_binding 被调(清残留)
- 预期: 不入库 (ip_record / ip_task 都没写)
- 预期: 错误文案含 OCR 提示 0/O / 1/l/I / K/k 关键字
```

#### 新建 `test/ip_probe_worker/TC-06_proxy_timeout.py`

```text
TC: ④ timeout 路径
- 重试 3 次都超时 → status=proxy_timeout
- 文案含 "已重试 3 次"
- 清残留 + 不入库
- 重试逻辑住 IPProbeWorker 内部(参考 SSH_CONNECT_RETRY_BACKOFF)
```

#### 新建 `test/ip_probe_worker/TC-07_proxy_refused.py`

```text
TC: ④ refused 路径
- exit_code=7 → status=proxy_refused
- 不重试
- 清残留 + 不入库
```

#### 新建 `test/ip_probe_worker/TC-08_queued_success.py`

```text
TC: ⑦ 入库 + 派任务成功路径
- mock test_internal_socks ok=True + body=actual_egress_ip
- mock lookup_egress 返回 country dict
- 预期: ip_record 新建 (status=usable, is_active=1, egress_ip=actual)
- 预期: ip_task 新建 (status=pending, ip_id=新 id, vps_id=NULL)
- 预期: 测试残留已拆 (rollback_proxy_binding 被调)
- 预期: 返回 status=queued + ip_id + task_id
- 预期: 声明值 declared_egress_ip / 声明国家 不入库
```

#### 新建 `test/ip_probe_worker/TC-09_duplicate_by_actual.py`

```text
TC: ⑥ 二次查重
- 第一次查 declared_egress_ip 没命中
- 挂 outbound + 内 ping 通 → 拿到 actual_egress_ip
- 但库里实际已存在 actual_egress_ip 这条 → 返回 status=duplicate
- 预期: 清残留 + 不入库
```

#### 新建 `test/ip_probe_worker/TC-10_cleanup_on_exception.py`

```text
TC: try/finally 兜底
- mock test_internal_socks 抛非业务异常 (paramiko 连接断)
- 预期: rollback_proxy_binding 仍被调(finally 兜底)
- 预期: status=proxy_failed
- 预期: 不入库
```

#### 新建 `test/ip_probe_worker/TC-11_domain_entry.py`

```text
TC: 域名入口
- entry_host="proxy.miluproxy.com"
- 流程跟 IP 入口完全一样
- 实测出口 IP 跟入口主机不同 → 不当问题
```

#### 新建 `test/ip_probe_worker/__init__.py`

```text
空文件 + log 命名空间初始化, 让 pytest 收集本目录。
```

#### 不动

```text
- workers/ssh_worker.py / workers/xray_worker.py
- xray/manager.py / xray/service.py / xray/config.py(除非 T-12 内变更, T-13 不动)
- toolbox/ 任何文件
- db/models.py(T-11 改完, T-13 只 import)
- probe_vps.py(T-10 改完, T-13 只 import)
- services/ 旧代码
- 任何 ADR / 已有 spec
```

### 实现轮廓

```python
# workers/ip_probe_worker.py

"""IPProbeWorker —— rgip MCP 工具的同步段主工人。

干啥:
  接 1 条上游 IP 凭据, 用测试 VPS 校验账密+端口, 通过 → 入库 + 派 ip_task。
  跟 SSHWorker 平行(SSHWorker 是 rgvps 的同步段)。

谁会调我:
  tools/rgip.py (MCP 工具入口, T-14)

我会用到的工具(完全照 spec v2 §二 工具清单):
  - ssh.session.VPSSession
  - xray.manager.XrayManager.replace_proxy_binding / rollback_proxy_binding
  - xray.config.build_proxy_outbound / generate_random_auth
  - xray.service.test_internal_socks(T-12 改后, 含 exit_code/stderr)
  - toolbox.geoip.lookup_egress
  - db.models: IPRecord / IPTask / IPStatus / TaskStatus
  - probe_vps.PROBE_VPS_POOL / PROBE_TEST_PORT

我的私有方法(下划线开头):
  _lookup_by_declared / _lookup_by_actual / _pick_probe_vps /
  _apply_test_outbound / _classify_proxy_error /
  _probe_and_resolve / _persist_and_dispatch / _cleanup_probe

我返回的 status 集 (7 种):
  - duplicate / probe_vps_unreachable
  - proxy_auth_failed / proxy_timeout / proxy_refused / proxy_failed
  - queued (成功)

行为规约金标准: test/ip_probe_worker/spec.md v2
"""

from __future__ import annotations

# ... [imports]


# ============ 失败文案常量 (spec v2 §7) ============

_AUTH_FAILED_MESSAGE = (
    "上游代理 {host}:{port} 密码校验失败。"
    "OCR 容易看错 0/O、1/l/I、K/k、c/C 这类字符, 请逐位核对;"
    "另外注意: 服务商面板登录密码 ≠ 代理认证密码, "
    "请回服务商面板复制最新的代理凭据。"
)

_TIMEOUT_MESSAGE = (
    "上游代理 {host}:{port} 连接超时(已重试 3 次)。"
    "可能代理服务暂时挂了 / 入口端口填错 / 测试 VPS 到上游的网络抖动。"
    "建议: 服务商面板核对代理状态后稍后重试。"
)

_REFUSED_MESSAGE = (
    "上游代理 {host}:{port} 被拒绝。"
    "罕见场景: 上游服务可能已停用 / 端口未监听 / 测试 VPS 到该网络的链路被封锁。"
    "建议: 服务商面板核对代理状态。"
)

_FAILED_MESSAGE = "上游代理 {host}:{port} 校验失败: {detail}"

_PROBE_VPS_UNREACHABLE_MESSAGE = (
    "所有测试 VPS 都连不上, 无法校验。请联系管理员检查测试 VPS 状态。"
)

_DUPLICATE_MESSAGE = "这条出口 IP {egress_ip} 已经在库, 无需重复登记。"


# ============ curl exit code 分类映射 ============

# spec v2 §E 的标准 curl exit code 含义
_CURL_REFUSED = 7        # CURLE_COULDNT_CONNECT
_CURL_TIMEOUT = 28       # CURLE_OPERATION_TIMEDOUT
_CURL_PROXY_ERROR = 97   # CURLE_PROXY (常为 socks auth 错误)


# ============ 内部超时常量 (timeout 重试用) ============

_PROXY_PROBE_TIMEOUT = 15      # 单次内 ping 超时
_PROXY_PROBE_RETRY_ATTEMPTS = 3 # spec §7 timeout 重试 3 次


class IPProbeWorker:
    """rgip 入口同步段工人。调用方: tools/rgip.py 的 handler。"""

    def __init__(self) -> None:
        pass

    # ============ 主入口 ============

    def process(
        self,
        entry_host: str,
        entry_port: int,
        username: str,
        password: str,
        protocol: str,
        declared_egress_ip: str,
        provider_domain: str = "",
        expire_date=None,
    ) -> dict:
        """同步 8 步主流程, 返回 status + 业务字段 dict (spec §3)。"""
        
        # ① 早期查重 (declared)
        if declared_egress_ip:
            existing = self._lookup_by_declared(declared_egress_ip)
            if existing is not None:
                return {
                    "status": "duplicate",
                    "message": _DUPLICATE_MESSAGE.format(egress_ip=declared_egress_ip),
                    "egress_ip": declared_egress_ip,
                }

        # ② 挑测试 VPS
        try:
            session = self._pick_probe_vps()
        except _ProbeVPSAllDownError:
            return {
                "status": "probe_vps_unreachable",
                "message": _PROBE_VPS_UNREACHABLE_MESSAGE,
            }

        # ③ ~ ⑦ 在 try/finally 里跑, finally 兜底拆残留
        last_config = None
        try:
            with session:
                xm = XrayManager(session.client)
                
                # ③+④ 挂上游 + 拿测试 inbound 账密
                last_config, test_user, test_pwd = self._apply_test_outbound(
                    xm, entry_host, entry_port, username, password, protocol,
                )
                
                # ④+⑤ 内 ping 校验 + 拿实测出口
                probe_result = self._probe_and_resolve(
                    session.client, test_user, test_pwd,
                    upstream_host=entry_host, upstream_port=entry_port,
                )
                if not probe_result["ok"]:
                    return probe_result["error_response"]
                
                actual_egress_ip = probe_result["actual_egress_ip"]
                geo = probe_result["geo"]
                
                # ⑥ 二次查重 (actual)
                if self._lookup_by_actual(actual_egress_ip) is not None:
                    return {
                        "status": "duplicate",
                        "message": _DUPLICATE_MESSAGE.format(egress_ip=actual_egress_ip),
                        "egress_ip": actual_egress_ip,
                    }
                
                # ⑦ 入库 + 派任务
                result = self._persist_and_dispatch(
                    entry_host=entry_host,
                    entry_port=entry_port,
                    username=username,
                    password=password,
                    protocol=protocol,
                    actual_egress_ip=actual_egress_ip,
                    geo=geo,
                    provider_domain=provider_domain,
                    expire_date=expire_date,
                )
                
                # ⑧ 返回
                return {
                    "status": "queued",
                    "ip_id": result["ip_id"],
                    "task_id": result["task_id"],
                    "message": "已入库, 后台 worker 会接手挂到生产 VPS",
                }
        finally:
            # 兜底拆 19000 残留 (任何路径都走)
            if last_config is not None:
                try:
                    with VPSSession(...) as cleanup_sess:  # 或复用 session, 实现者拍
                        xm_cleanup = XrayManager(cleanup_sess.client)
                        self._cleanup_probe(xm_cleanup, last_config)
                except Exception as exc:  # noqa: BLE001 — cleanup 失败不影响业务返回
                    logger.warning("_cleanup_probe failed: %s", exc)

    # ============ 私有方法 (8 个,见 spec §F) ============
    # 完整实现略, 按 spec §F 表格 + 上面 process 调用规约填即可。
```

### 数据结构 / 状态迁移

| 字段 / 状态 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `ip_record.status` | usable / using | ProxyDeployWorker | IPProbeWorker 入库(usable) |
| `ip_record.egress_ip` | 实测出口 IP(查重键) | 业务全程 | IPProbeWorker 入库 |
| `ip_task.status` | pending / in_progress / done / failed | worker 扫表 | IPProbeWorker 建任务(pending) |
| `ip_task.vps_id` | nullable, 谁配的谁写 | 排障 | IPProbeWorker 写 NULL,ProxyDeployWorker 回填(本任务不实现) |

### 缺工具 / 缺信息先报告

- 发现 spec / ADR 没写清楚的业务判断 → 停下来报告
- 发现需要新增工具(本任务范围: spec 已盘 §二, 不应该有新增)→ 停下来报告
- 发现 curl exit code 实测跟 spec §E 不一致 → 停下来报告

---

## 3. 验收交付

### 测试用例

11 个 TC 见上面"改动文件清单"列表。每个 TC 业务故事 + 输入 + 预期 + 不应发生写齐。

### 必跑测试命令

```bash
VPS_SERVER_TESTING=1 pytest test/ip_probe_worker/ -v
```

### 实现者完工标准

- [x] 开工前文件名 waiting → doing
- [x] T-10 / T-11 / T-12 都已 done(本任务依赖)
- [x] `workers/ip_probe_worker.py` 实现完整(主入口 + 8 私有方法)
- [x] `test/ip_probe_worker/` 11 个 TC 都新增
- [x] 必跑测试命令全 PASS(用 glob 显式收集 TC-*.py, 见偏差段)
- [x] 文案符合 spec §7 7 种 status 表
- [x] 不变量 9 条(spec §9)全部满足
- [x] 没动 SSHWorker / XrayWorker / 旧 services/
- [x] 没新增工具(全部从 spec §二 §A~§G 调; B(a) 拍板小扩展 probe_vps.py)
- [x] 不入库声明值 / 带宽 / 时间戳
- [x] 完成记录段已填

### 实现过程记录

```text
改动文件:
- workers/ip_probe_worker.py (新建)
- test/ip_probe_worker/__init__.py (新建)
- test/ip_probe_worker/TC-01 ~ TC-11 (新建)

测试结果:
- VPS_SERVER_TESTING=1 pytest test/ip_probe_worker/ -v -> <result>

偏差 / 风险:
- <none | details>
```

### Claude 验收检查清单

□ 对照 spec §3 主流程 8 步逐条核对
□ 对照 spec §7 失败分类表核对 7 种 status + 文案
□ 对照 spec §9 不变量 9 条全满足
□ 对照 spec §10 边界情况逐项 TC 覆盖
□ 跑 pytest 验证全 PASS
□ 检查没新建任何工具(全部 spec §二 调)
□ 偏差但合理 -> 抛给用户决策
□ 偏差不合理 -> 打回实现者修改

---

## 完成记录(done 时追加)

```text
完成日期: 2026-06-09
完成 commit: 见本 commit hash
任务状态: doing -> done

改动摘要:
- 新建 workers/ip_probe_worker.py: IPProbeWorker 类 (主入口 process + 8 私有方法
  spec §F 全对齐, _classify_proxy_error 主靠 exit_code, stderr 关键字软兜底)。
  process 8 步流程在 try/finally 兜底 + 整段 except 兜底转 proxy_failed。
  失败文案 5 种 (auth_failed/timeout/refused/failed/duplicate-actual)。
- 新建 test/ip_probe_worker/__init__.py + _helpers.py + 11 个 TC 文件:
  TC-01 ① 早期查重 (3 用例)
  TC-02 ② 测试 VPS 顺序挑 (5 用例)
  TC-03 ③+④ 挂上游 (3 用例)
  TC-04 _classify_proxy_error 映射 (6 用例)
  TC-05 ④ proxy_auth_failed 路径 (3 用例)
  TC-06 ④ proxy_timeout 路径 + 重试 3 次 (3 用例)
  TC-07 ④ proxy_refused 路径 (3 用例)
  TC-08 ⑦ 入库成功 + 派任务 (6 用例)
  TC-09 ⑥ 二次查重 (3 用例)
  TC-10 try/finally 兜底 (3 用例)
  TC-11 域名入口 (2 用例)
  总 40 用例。
- 扩展 probe_vps.example.py + probe_vps.py 加 PROBE_TEST_PORT = 19000 常量
  (B(a) 拍板) + 注释说明端口选择理由 (跟 18440 默认入口 + 高位段隔离)。
- test/probe_vps/TC-01_probe_vps_pool.py 补一条断言: PROBE_TEST_PORT 是 1024-65535
  高位段且 != 18440。

测试命令:
- VPS_SERVER_TESTING=1 pytest test/ip_probe_worker/TC-*.py -v
- 全套回归: VPS_SERVER_TESTING=1 pytest \
      test/probe_vps/TC-*.py test/_data_structures/ \
      test/xray/test_test_internal_socks_structure.py \
      test/xray_worker/TC-*.py test/ssh_worker/TC-*.py \
      test/ip_probe_worker/TC-*.py -v

测试结果:
- T-13 本体: 40 collected, 40 passed in 0.32s, 0 failed
- 全套回归: 120 passed + 2 skipped (TC-14 真服务器 + VPSTask TC-11 抢锁原子性
  占位, 跟本任务无关) + 10 个 utcnow DeprecationWarning (存量)

冲突核对结果 (A-H 全部已对齐):
A. probe_vps API: 用 get_probe_vps_pool() helper, 不再用任务单原 PROBE_VPS_POOL
   直接 import (空 pool 时 helper 抛指引, 工人转 probe_vps_unreachable)。
B. PROBE_TEST_PORT 拍 (a): 扩 probe_vps.py + probe_vps.example.py 加常量, TC-01
   补一条断言。理由: 跟测试 VPS 资源 (PROBE_VPS_POOL / 18440 默认入口) 同住
   一个文件最内聚。
C. pytest TC 收集坑: 必跑命令用 glob `test/ip_probe_worker/TC-*.py`, 不是任务单
   原目录形式 (memory project_pytest_tc_collection_pitfall 已沉淀)。
D. stderr 多为空: _classify_proxy_error 主靠 exit_code (7/28/97 三档 + 兜底),
   stderr 关键字 ("auth" / "socks5" 大小写不敏感) 仅作软升级兜底; TC-04 测了
   两条软兜底路径但实测罕见。
E. dev DB: T-11 已迁移完成, T-13 不动 dev DB, 所有 TC 用 in-memory SQLite。
F. probe_vps.py 不在 git: TC 用 monkeypatch patch get_probe_vps_pool 注入测试
   数据, 不依赖本地文件存在。
G. IPRecord.from_form 签名: keyword-only 完全对齐, _persist_and_dispatch 按
   T-11 实际签名调 (geo 传 lookup_egress 返回 dict)。
H. IPTask 字段: vps_id nullable / TaskStatus.PENDING / 字段集跟 VPSTask 对称,
   建任务时 IPTask(ip_id=新 id) 即可, vps_id 不传 = NULL (spec §9 不变量)。

偏差 / 风险:
- 必跑测试命令偏差: `test/ip_probe_worker/` 改 `test/ip_probe_worker/TC-*.py`
  (项目级痛点, 已记 memory 笔记)。
- _classify_proxy_error stderr 关键字降级为软兜底: T-12 实施时 cmd 保留 2>&1,
  stderr 多为空; 任务单原写"双重确认"路径调整为"未知 exit_code + stderr 含
  auth/socks5 关键字 → 兜底升 auth_failed"。
- 真机端到端验证未跑: 需要用户提供真实上游代理凭据 + 测试 VPS 可达 + xray
  能正确装挂拆。计划在 T-14 (tools/rgip.py MCP 入口) 实施时一起做真机验证;
  或单独跑一次 dev_smoke_ip_probe_worker.py (本地脚本, 在 .gitignore)。
- 多个 TC setUp 有相似 patch 列表, 重复 boilerplate, 但跟项目其他 worker TC
  风格一致 (一文件一上下文)。如需提炼成 base class 可后续单独清理。

未覆盖风险:
- 真服务器端到端 (SSH 真连测试 VPS + xray 真装挂拆 + curl 真打上游) 未跑;
  spec §10 边界情况 "测试 VPS 19000 端口残留无法 remove" / "测试 VPS SSH 通了
  但 19000 操作 xray 失败" 等真机场景需要 T-14 / 真测试单独覆盖。
- 并发场景 (两个 rgip 同时调本工人) 不在范围, MCP 边界外部串行已保证
  (spec §1)。
- 测试 VPS 凭据轮换 / pool 动态变化场景未覆盖 (T-10 范畴, 凭据直写常量)。

后续任务:
- T-14 tools/rgip.py MCP 入口实施时:
  1. 真机跑一次 IPProbeWorker.process() 端到端验证
  2. Tool.description 写明"多条 IP 请一条一条提交, 等上一条返回再提交下一条"
  3. 状态查询工具配套
- ProxyDeployWorker (后续) 抢 ip_task 后:
  - 挑机查询用 vps_record.stage=connectable AND xray_version != '' AND
    is_active=1 ORDER BY used_port_count ASC
  - 配置成功同事务: ip_task.status=done + ip_record.status=using + 回填
    ip_task.vps_id + 写 proxy_record + vps.stage 流转
```
