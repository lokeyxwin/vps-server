# IPProbeWorker 行为规约(spec.md)

**版本**: v3(2026-06-10)
**模块**: `workers/ip_probe_worker.py`(待实现)
**类型**: 同步业务工人(rgip MCP 工具的同步段)
**对应 ADR**:
- `docs/adr/0001-workers-replace-services.md`(worker 架构;IPProbeWorker 是 §决策 §6 提到的同步入口工人之一)
- `docs/adr/0005-vps-stage-as-resource-lock.md`(两层锁分离;ProxyDeployWorker 后续挑机时会用到 `vps.stage='connectable'`)
- `CLAUDE.local.md` §8 测试 VPS 配置化、§9 工人阵容 IPProbeWorker 一行、§ 业务编排:worker / kit / task 体系

---

## 一、整理后的要点

### 1. 工人定位

IPProbeWorker 是 rgip MCP 工具入口的**同步段工人**:

- 接收用户提交的上游 IP 凭据(代理主机/入口端口/账号/密码/协议/声明出口 IP/...)
- 用 `probe_vps.PROBE_VPS_POOL` 里的测试 VPS SSH 上去
- 在测试 VPS 上**临时挂这条凭据当 xray outbound**,**内 ping** 验证它能不能用
- 通过 → 入 `ip_record` + 派 `ip_task`(pending)给 ProxyDeployWorker
- 不通过 → 不入库,清测试 VPS 残留,抛回错误

**MCP 边界外部串行**:rgip 工具的 `Tool.description` 会写明"多条 IP 请一条一条提交,等上一条返回再提交下一条"。工人内部**不加测试 VPS 并发锁**(YAGNI)。

**接力**:
```
rgip MCP → IPProbeWorker(同步)
   ↓ 派 ip_task(pending, vps_id=NULL)
ProxyDeployWorker(异步,见后续 spec)
```

### 2. 入口契约

**触发**: `tools/rgip.py` handler 调 `IPProbeWorker().process(...)`。

**入参**:

| 形参 | 类型 | 含义 |
|---|---|---|
| `entry_host` | str | 上游入口主机(IP 或域名,paramiko/xray 一视同仁) |
| `entry_port` | int | 上游入口端口 |
| `username` | str | 上游用户名 |
| `password` | str | 上游密码 |
| `protocol` | str | `socks5` / `http`(`IPProtocol` 枚举) |
| `declared_egress_ip` | str | 用户提交的声明出口 IP(只用作早期查重弹药,不入库) |
| `provider_domain` | str | 服务商域名(可空) |
| `expire_date` | date \| None | 有效期日期(用户提交"3天"由 MCP 层换算成日期再传入) |

**输出**(同步返回 dict):

成功:
```python
{"status": "queued", "ip_id": int, "task_id": int, "message": "已入库,后台 worker 会接手挂到生产 VPS"}
```

失败(7 种 status):
```python
{"status": "duplicate", "message": "...", "egress_ip": "..."}
{"status": "probe_vps_unreachable", "message": "..."}
{"status": "proxy_auth_failed", "message": "..."}
{"status": "proxy_timeout", "message": "..."}
{"status": "proxy_refused", "message": "..."}
{"status": "proxy_failed", "message": "..."}
```

### 3. 主流程(同步 8 步)

```
外部:rgip 收到一条上游 IP 凭据
  │
  ① 用 declared_egress_ip 查 ip_record → 命中 duplicate 抛回(不 SSH, 早期短路)
  │
  ② 从 PROBE_VPS_POOL 顺序挑测试 VPS, SSH 上去
        - 连不上 → 挑下一台
        - 全部都连不上 → probe_vps_unreachable 抛回(不入库)
  │
  ③ 看测试 VPS 上 19000 端口当前 inbound 配置
        - 有残留(上次测试没拆干净)→ 精准 remove
        - 干净了才进 ④
  │
  ④ 把上游凭据当 outbound 挂到测试 VPS 19000 端口
        (=校验"账密 + 入口端口"是否可用)
        ├─ proxy_auth_failed → 清残留 + 抛回(温馨文案,不重试)
        ├─ proxy_timeout(已自动重试 3 次仍超时)→ 清残留 + 抛回
        ├─ proxy_refused → 清残留 + 抛回(HK 场景罕见, 兜底)
        └─ proxy_failed(兜底)→ 清残留 + 抛回
  │
  ⑤ 校验通过 → 内 ping(走测试 VPS:19000)出去, 拿"实测出口 IP + 实测国家"
        - 走 geoip(`toolbox.geoip.lookup_egress`)拿 country_code/name/city/region_name
  │
  ⑥ 用"实测出口 IP" 二次查 ip_record
        - 命中 duplicate → 清残留 + 抛回(兜住声明 ≠ 实测的二货场景)
  │
  ⑦ 同步段收尾(都在同一会话里完成,不出 SSH):
        - 写 ip_record(is_active=1, 只入实测值)
        - 派 ip_task(status=pending, ip_id=新 id, vps_id=NULL)
        - 拆 19000 测试配置(remove outbound + inbound + 路由三件套)
        - close SSH 连接
  │
  ⑧ 返回 {status: queued, ip_id, task_id, message: "..."}
```

### 4. 测试 VPS 资源使用规则

- 测试 VPS 凭据清单住 **`probe_vps.py`(仓库根)** 的 `PROBE_VPS_POOL`(T-10 任务产出)
- IPProbeWorker 顺序遍历挑(挑第一个 SSH 通的)
- **不持锁**:MCP 边界外部串行已保证一次只有一条 process 在跑
- **测试用端口固定 19000**:跟测试 VPS 自身默认入口 18440 隔离(不动 18440 的 freedom 直进直出),跟生产 VPS 高位段也隔离
- 测试完**必须 remove 三件套**(outbound + inbound + 路由),测试 VPS 复原

### 5. 端口 / 资源规则

| 端口 | 用途 | 谁管 |
|---|---|---|
| `18440`(测试 VPS 上) | 测试 VPS 自身默认入口 socks5→freedom 直进直出(ADR-0004) | 不动 |
| `19000`(测试 VPS 上) | IPProbeWorker 临时挂上游测试的端口 | 每次测完拆干净 |

### 6. 状态字段语义

> v3(ADR-0010): `ip_record.status` 字段已删除。"这条 IP 在不在用"
> 的真相源 = `proxy_record` 是否有 `ip_id` 指向它(且 `status<>'inactive'`),
> 不再用 IP 表里的 derived 字段。下面 ProxyDeployWorker 挑机查询同步改成
> `LEFT JOIN proxy_record ... WHERE proxy_record.id IS NULL`。

#### `ip_record.is_active`(已有字段,不动)

- `is_active=1` 默认;巡检模块(未来)看 `expire_date` 过期标 0
- 表达"整体还有效"(过期/手动停用判定);"当前在不在用"由 proxy_record 存在性表达
- ProxyDeployWorker 挑 IP 时:`ip.is_active=1 AND 没 proxy_record 指向它`

#### `ip_task.vps_id`(新增字段, nullable, 谁配的谁写)

- IPProbeWorker 建 task 时 = NULL(此刻还不知道挂哪台 VPS)
- ProxyDeployWorker 挑到 VPS 后**自己回填**

### 7. 失败处理

| 场景 | 触发 | 重试 | 入库 | 错误码 | 文案要点 |
|---|---|---|---|---|---|
| 声明出口 IP 已存在 | ① 查重命中 | 否 | 否 | `duplicate` | "这条出口 IP `{egress_ip}` 已经在库,无需重复登记。" |
| 测试 VPS 全连不上 | ② 全挂 | 否 | 否 | `probe_vps_unreachable` | "所有测试 VPS 都连不上,无法校验。请联系管理员检查测试 VPS 状态。" |
| 上游账密错 | ④ socks5 认证拒绝 | 否 | 否 | `proxy_auth_failed` | "上游代理 `{host}:{port}` 密码校验失败。OCR 容易看错 `0/O`、`1/l/I`、`K/k`、`c/C` 这类字符,请逐位核对;另外注意:服务商面板登录密码 ≠ 代理认证密码,请回服务商面板复制最新的代理凭据。" |
| 上游连接超时 | ④ 连接超时 | **是,3 次** | 否 | `proxy_timeout` | "上游代理 `{host}:{port}` 连接超时(已重试 3 次)。可能代理服务暂时挂了 / 入口端口填错 / 测试 VPS 到上游的网络抖动。建议:服务商面板核对代理状态后稍后重试。" |
| 上游拒接 | ④ TCP 拒接 | 否 | 否 | `proxy_refused` | "上游代理 `{host}:{port}` 被拒绝。罕见场景:上游服务可能已停用 / 端口未监听 / 测试 VPS 到该网络的链路被封锁。建议:服务商面板核对代理状态。" |
| 上游其他错 | ④ 兜底 | 否 | 否 | `proxy_failed` | "上游代理 `{host}:{port}` 校验失败:`{原因}`" |
| 实测出口 IP 已存在 | ⑥ 二次查重命中 | 否 | 否 | `duplicate` | "这条出口 IP(实测)`{actual_egress_ip}` 已经在库,无需重复登记。" |

**所有失败都必须清 19000 测试残留**(包括异常路径)——通过 `try/finally` 兜底。

### 8. 不做的事

- ✗ 不挂出口到生产 VPS(那是 ProxyDeployWorker 的事)
- ✗ 不写 `proxy_record`(那是 ProxyDeployWorker 的事)
- ✗ 不改 `vps_record`(测试 VPS 不在 `vps_record` 里,它住 `probe_vps.py`)
- ✗ 不抢测试 VPS 锁(MCP 边界外部串行已保证)
- ✗ 不持久化"声明出口 IP / 声明国家 / 带宽 / 时间戳"(只用作早期查重,不入库)
- ✗ 不调 SSHWorker(SSHWorker 是 rgvps 的工人,跟本工人无关——复用层在 `VPSSession + XrayManager`)
- ✗ 不处理"已有 ip_record 但 status 不一致"的复杂场景(本工人只新登记,不更新)

### 9. 不变量

- 入库时 `ip_record.is_active` **永远写 `1`**
- 入库时只写**实测值**(`egress_ip = actual_egress_ip`, `country_* = geoip 返回值`)
- 派 `ip_task` 时 `vps_id` **永远写 NULL**(谁配的谁写)
- 同步段返回前 `19000` 端口**必须干净**(无论成功失败都得拆)
- 同步段返回前 SSH **必须 close**
- 测试 VPS **不进任何业务表**(不入 `vps_record` / `proxy_record`)
- 失败任何 status **绝不入库**(参考 SSHWorker 路线 C 设计)
- 一次 `process` 只处理一条 IP 凭据(MCP 边界保证)

### 10. 边界情况

| 情况 | 期望行为 |
|---|---|
| `declared_egress_ip` 跟 `actual_egress_ip` 不一致 | 用实测的入库,声明值丢弃;不抛错 |
| `declared_egress_ip` 用户没传或传空 | 跳过 ① 早期查重,直接走 ②~⑤,⑥ 二次查重兜底 |
| 入口是域名(`entry_host="proxy.x.com"`) | 流程一样,xray 自己解析域名;实测出口 IP 跟入口主机不可能相等(正常) |
| 入口是 IP 且 = 实测出口 | 也走 ① / ⑥ 查重,跟域名入口流程一样 |
| `expire_date` 用户没传 | 入库时 `expire_date=NULL`(同 ADR-0002 §5 纳管 IP 的处理) |
| 测试 VPS 上 19000 端口残留无法 remove | 抛 `proxy_failed`(残留清不掉就别冒险挂新的) |
| 测试 VPS SSH 通了但 19000 操作 xray 失败 | 抛对应 xray 错误,跳到下一台测试 VPS 重试 |
| `geoip.lookup_egress` 失败 | `country_*` 全落空串(参考 IPRecord.from_form 的 geo=None 兜底) |

---

## 二、工具清单

读代码现状后确认: **工具几乎全在,只一处小改**(`xray/service.py::test_internal_socks` 返回 dict 加 `exit_code` + `stderr` 字段)。

### A. `ssh.session.VPSSession` 方法(`ssh/session.py`)

| 方法 | IPProbe 哪步用 | 状态 |
|---|---|---|
| `VPSSession(**probe_dict)` | ② 用 `PROBE_VPS_POOL[i]` 展开实例化 | ✅ 已有(键名对齐) |
| `with` 上下文管理 | ②~⑦ 整段会话生命周期 | ✅ 已有(`session.py:146`) |
| `.client` 属性 | 给 XrayManager 用 | ✅ 已有(`session.py:87`) |
| `.close()` | 同步段末尾 | ✅ 已有(自动 by `with`) |

### B. `xray.manager.XrayManager` 方法(`xray/manager.py`)

| 方法 | IPProbe 哪步用 | 状态 |
|---|---|---|
| `XrayManager(client)` 构造 | ② SSH 上去后绑 client | ✅ 已有(`manager.py:113`) |
| `replace_proxy_binding(vps_port, outbound, user, pwd)` | **③+④ 一行搞定** "清残留 + 挂新的"(`remove` 幂等,没旧的也 OK) | ✅ 已有(`manager.py:264`) |
| `rollback_proxy_binding(vps_port, last_config)` | ⑦ / 失败兜底 拆 19000 三件套 | ✅ 已有(`manager.py:305`) |
| `test_internal_socks(port, user, pwd)` | ④+⑤ 内 ping(透传 `service.test_internal_socks`) | ✅ 已有(`manager.py:212`) |
| `read_config()`(必要时) | 调试 / 巡检 | ✅ 已有(`manager.py:161` 间接) |

**关键**:`replace_proxy_binding` 内部已经是 "先 remove 旧的 + 再 add 新的"(`manager.py:271` 注释),完美吻合 ③+④ 需求,IPProbe 调这一个即可。

### C. `xray.config` 纯函数 + 异常类(IPProbe 直接 import)

| 元素 | 用途 | 状态 |
|---|---|---|
| `build_proxy_outbound(host, port, user, pwd, protocol, tag)` | 把用户提交的上游凭据翻成 outbound dict | ✅ 已有(`config.py:195`) |
| `generate_random_auth(length=16)` | 给测试用 inbound 生成随机账密(测完拆,不入库) | ✅ 已有(`config.py:175`) |
| `PROTOCOL_SOCKS5 / PROTOCOL_HTTP` 常量 + `SUPPORTED_PROTOCOLS` | 协议校验 | ✅ 已有(`config.py:44`) |
| `UnsupportedProtocolError` / `PortConflictError` / `PortAlreadyBoundError` / `OutboundTagConflictError` / `ConfigWriteError` / `ConfigValidationError` / `ConfigReadError` | 异常类(透传给 IPProbe 转 status) | ✅ 已有(`config.py:118-143`) |

### D. `toolbox/` 通用工具

| 工具 | 状态 |
|---|---|
| `toolbox/geoip.lookup_egress(ip)` 返回 country_code/name/city/region_name + 兜底空串不抛 | ✅ 已有(`geoip.py:61`)—— ⑤ 步直接用 |
| `toolbox/security.encrypt_password` | ✅ 已有(`IPRecord.from_form` 内部已调,IPProbe 不直接碰) |
| `toolbox/proxy_check.test_internal(client, port, user, pwd)` → `(ok, egress_ip)` | ⚠️ 已有(`proxy_check.py:83`)但**返回值粒度太粗** —— IPProbe 直接调 `xray.service.test_internal_socks` 拿完整 dict(含新追加的 `exit_code` + `stderr`),不走这层 |

### ⚠️ E. 唯一小改:`xray.service.test_internal_socks` 返回 dict 加 2 字段

**问题**:`test_internal_socks` 当前只返回 `{ok, http_code, body, error}`(`service.py:307`),**丢失了 curl exit code 和 stderr**,IPProbeWorker 无法区分 auth / timeout / refused 三类失败。

**改动**(向后兼容,**不动现有键**):

```python
{
    "ok": bool,
    "http_code": int | None,
    "body": str,
    "error": str | None,
    "exit_code": int,         # ⭐ 新增:curl 命令的 shell exit code
    "stderr": str,            # ⭐ 新增:curl 的 stderr 输出(便于关键字匹配)
}
```

**向后兼容性**:`XrayWorker` / `toolbox.proxy_check.test_internal` 当前只看 `ok / body`,完全不受影响。

**错误分类逻辑不抽工具层**,住 `IPProbeWorker._classify_proxy_error(exit_code, stderr)` 私有方法。curl exit code 标准:
- `7` (CURLE_COULDNT_CONNECT) → `proxy_refused`
- `28` (CURLE_OPERATION_TIMEDOUT) → `proxy_timeout`
- `97` (CURLE_PROXY) / stderr 含 "SOCKS5" 认证关键字 → `proxy_auth_failed`
- 其他 → `proxy_failed`(兜底)

### F. 工人内部私有编排(住 `workers/ip_probe_worker.py`)

按 [[feedback-工具编排发现式抽取]] 原则,私有编排住工人 `.py` 内部(下划线开头),命名按实现者方便,spec 不强制锁死方法名,但行为锚点必须满足:

| 私有方法 | 步骤 | 干啥 |
|---|---|---|
| `_lookup_by_declared(declared_egress_ip)` | ① | DB 查 `ip_record.egress_ip = declared` → 命中返回现有记录,没命中返回 None |
| `_lookup_by_actual(actual_egress_ip)` | ⑥ | DB 查 `ip_record.egress_ip = actual`(二次查重) |
| `_pick_probe_vps()` | ② | 顺序遍历 `PROBE_VPS_POOL`,SSH 连通的返回 VPSSession;全挂抛 `probe_vps_unreachable` |
| `_apply_test_outbound(xm, entry_host, entry_port, user, pwd, protocol)` | ③+④ | 用 `build_proxy_outbound` + `generate_random_auth` 造 outbound + inbound 账密,调 `xm.replace_proxy_binding(19000, ...)`,返回 `(last_config, test_inbound_user, test_inbound_pwd)` |
| `_classify_proxy_error(exit_code, stderr)` | ④ | curl exit code / stderr → status(auth_failed / timeout / refused / failed) |
| `_probe_and_resolve(xm, test_inbound_user, test_inbound_pwd)` | ④+⑤ | 调 `test_internal_socks(19000, test_inbound_user, test_inbound_pwd)`;通 → geoip 拿 country;不通 → 调 `_classify_proxy_error` |
| `_persist_and_dispatch(actual_egress_ip, geo, entry_*, ...)` | ⑦ | **同事务** 写 `ip_record(is_active=1)` + `ip_task(pending, vps_id=NULL)` |
| `_cleanup_probe(xm, last_config)` | try/finally 兜底 | 调 `xm.rollback_proxy_binding(19000, last_config)` 拆三件套(失败也走) |

### G. 新增 / 改的字段和表(走 db 改造任务单)

> v3(ADR-0010): 原 `IPStatus` 常量类 + `IPRecord.status` 字段 + 工厂 status 入参
> 已全删,本节相应条目去除。`IPTask` 表 / `probe_vps.py` 等保留有效。

| 改动 | 文件 | 状态 |
|---|---|---|
| `IPTask` 表(1:1 对称 VPSTask,`vps_id` nullable,谁配的谁写) | `db/models.py` 改 | 新建表 |
| `PROBE_VPS_POOL` 清单 + `PROBE_TEST_PORT=19000` 常量 | `probe_vps.py` 新建 | T-10 已落任务单 |

```python
# db/models.py 追加片段:
class IPTask(Base):
    """IP 挂机部署任务。ProxyDeployWorker 消费。
    
    每条 = 把某条新登记的 IP 挂到某台生产 VPS 当 outbound 的活儿。
    IPProbeWorker 入库 IP 时建一条 pending, ProxyDeployWorker 扫表领。
    
    锁粒度 = task(跟 VPSTask 同样的软锁机制)。
    vps_id 谁配的谁写: IPProbeWorker 建任务时留 NULL, ProxyDeployWorker 挑到 VPS 后回填。
    """
    __tablename__ = "ip_task"

    id: PK (Integer, autoincrement)
    ip_id: FK -> ip_record.id, nullable=False, index=True
    vps_id: FK -> vps_record.id, nullable=True   # ⭐ 谁配的谁写

    # 跟 VPSTask 完全对称:
    status / retry_count / next_run_at
    last_error_code / last_error_msg
    worker_id / locked_until
    created_at / updated_at / completed_at

    __table_args__ = (
        Index("ix_ip_task_status_next_run", "status", "next_run_at"),
        Index("ix_ip_task_ip_status", "ip_id", "status"),
    )
```

`TaskStatus` 枚举直接复用(`PENDING / IN_PROGRESS / DONE / FAILED`)。

---

## 三、下游 ProxyDeployWorker 待办占位

> 等聊到 ProxyDeployWorker 时,这段搬到 `test/proxy_deploy_worker/spec.md`。
> 这里先标记,免得遗失。

- ProxyDeployWorker 挑机查询:`SELECT * FROM vps_record WHERE stage='connectable' AND xray_version != '' AND is_active=1 ORDER BY used_port_count ASC`
  —— **按 `used_port_count` 升序挑(已用最少的优先)**(2026-06-08 用户拍)
- 配置成功**同一事务**写:`ip_task.status='done'` + `vps.stage='connectable'`(释放资源锁) + INSERT `proxy_record`(`ip_id` 指向本 IP)
- 配置失败:`ip_task` 重试 / 终态 failed,**不写** `proxy_record`(代替旧的 `ip_record.status` 不动 ——
  v3 ADR-0010 删字段后,"在不在用" 直接看 proxy_record 是否存在)

---

## 四、修订历史

- v3 2026-06-10: 删 `ip_record.status` 字段同步 (ADR-0010)。
  - §1 工人定位 去掉"入库 status=usable"措辞
  - §3 主流程 ⑦ 步去掉"status=usable"措辞
  - §6 整段重写: 删 `ip_record.status` / IPStatus 子节, 用 proxy_record 存在性表达"在不在用",
    is_active 子节同步去掉"跟 status 独立维度"措辞
  - §9 不变量 去掉"入库 status 永远写 usable"一条
  - §F `_persist_and_dispatch` 行为说明去掉 status
  - §G IPStatus / IPRecord.status / from_form status 入参三条同步去掉,
    代码片段 class IPStatus 整段删
  - 用户口述原话节录在 ADR-0010

- v2 2026-06-08: §二 工具清单从"大类占位"升级为基于代码现状的完整真假判定。
  - §A VPSSession / §B XrayManager / §C xray.config 全部标"✅ 已有"+ 当前路径行号
  - §D toolbox 标"✅ 已有",其中 `test_internal` 粒度太粗,IPProbe 直接调 `service.test_internal_socks`
  - §E 唯一小改:`xray.service.test_internal_socks` 返回 dict 追加 `exit_code` + `stderr`(向后兼容,XrayWorker 不受影响)
  - §F 私有编排 8 个方法明细(`_lookup_by_*` / `_pick_probe_vps` / `_apply_test_outbound` / `_classify_proxy_error` / `_probe_and_resolve` / `_persist_and_dispatch` / `_cleanup_probe`)
  - §G 新增字段/表跟 db 模型代码片段对齐
  - 关键发现:`replace_proxy_binding` 内部已是 "remove + add",一行搞定 ③+④
  - 错误分类住 IPProbeWorker 内部私有方法,不抽工具层

- v1 2026-06-08: 初版。
  - 跟用户对齐 8 步同步主流程
  - 状态机:`ip_record.status` 新增 usable/using 二值
  - 新建表:`ip_task` 1:1 对称 `VPSTask`,`vps_id` nullable
  - 测试端口固定 19000(避开 18440 默认入口 + well-known)
  - 失败 7 分类 + 温馨文案
  - 工具清单大类占位,细节拆解阶段再展开
