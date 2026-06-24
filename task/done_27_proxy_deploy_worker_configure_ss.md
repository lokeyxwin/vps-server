# T-27 ProxyDeployWorker 部署 Shadowsocks inbound(替代 socks5)

**ID**: T-27
**状态**: waiting
**前置依赖**: **T-26**(method 字段 + ProxyProtocol) + **T-28**(ShadowsocksProbe)
**后续依赖**: T-29(返回用部署出的 SS 数据)
**关联 ADR**: docs/adr/0011-* §决策 §1/§2/§5/§6
**关联 spec**: test/proxy_deploy_worker/spec.md(本任务要改)

---

## 0. 开工前必读 / 领取锁

- [ ] 确认仍 waiting + 确认 T-26 / T-28 已 done, 否则不能开工
- [ ] 改名 doing_27_*.md
- [ ] 读: CLAUDE.md / CLAUDE.local.md / docs/adr/README.md / ADR-0011 / ADR-0006 /
      test/proxy_deploy_worker/spec.md / `xray/config.py`(add_proxy_binding +
      build_proxy_relay_config) / `xray/manager.py`(apply/replace_proxy_binding) /
      `workers/proxy_deploy_worker.py` / `config.py`

---

## 1. 用户原话 / 业务目标

> "IP 成功入库后被消费时配置 socks5 的逻辑改成 ss 即可, 其他照旧"
> "我们自己配置的入口变成兼容性更好的"

### 业务理解

ProxyDeployWorker 把上游 IP 挂到生产 VPS 时, 对外那条 inbound 从 socks5(账密) 改成
Shadowsocks(method+password)。**上游 outbound 不动, 路由/端口/挑机/锁全不动**, 只换
对外 inbound 协议 + 跟着换验证方式 + 入库写 SS。

### 本任务要解决什么

新部署的对外节点直接是 SS, 内/外 ping 用 SS 方式验证, proxy_record 存 protocol=
shadowsocks + method。

### 不解决什么

- 不改连通测试类本身(T-28 提供 ShadowsocksProbe, 本任务只调用)
- 不改对外返回/share_link(T-29)
- 不动纳管(later)

---

## 2. 实现参考

### 改 `config.py`

```python
SS_METHOD = "aes-256-gcm"   # 对外 SS inbound 加密方式(ADR-0011 §2)
```

### 改 `xray/config.py` —— 方案 A（add_proxy_binding 加 protocol 参数）

> ⚠️ 方案 A 修正（2026-06-24 需求窗口拍板, ADR-0011 §补充）：
> `add_proxy_binding` 是共享原子, 同时被 ProxyDeployWorker(对外 SS) 和
> IPProbeWorker 的 `replace_proxy_binding`(测上游, 仍 socks5) 调用。
> **不能**把它签名硬改成 method/password —— 那会击穿 IPProbeWorker。
> 改成加一个 `protocol` 参数(默认 socks5 保护测上游链), 按协议分支造 inbound。

1. 加对外 inbound 协议常量 + 两个 inbound builder 纯函数(避免两处重复):

```python
INBOUND_PROTOCOL_SOCKS5 = "socks5"
INBOUND_PROTOCOL_SHADOWSOCKS = "shadowsocks"

def _build_socks5_client_inbound(vps_port, user, pwd) -> dict:  # 账密 socks5(原样)
def _build_ss_inbound(vps_port, method, password) -> dict:      # SS(ADR-0011)
    # tag 仍 client-{vps_port} → 路由不变; protocol shadowsocks;
    # settings={method, password, network:"tcp,udp"}; listen 0.0.0.0
```

2. `add_proxy_binding` 加 `*, protocol=INBOUND_PROTOCOL_SOCKS5, method=""`:
   - protocol==socks5(默认) → `_build_socks5_client_inbound(vps_port, inbound_user, inbound_pwd)`(原样)
   - protocol==shadowsocks → `_build_ss_inbound(vps_port, method, inbound_pwd)`(password 复用 inbound_pwd 槽, inbound_user 忽略)
   - 未识别 protocol → 抛新增 `InboundProtocolError`
   - 三件套 append / 冲突检查 / 路由逻辑共享不动
3. `build_proxy_relay_config`(无活跃调用方) client_inbound 改用共享 `_build_socks5_client_inbound`(行为不变, 仍 socks5)。

### 改 `xray/manager.py`

- `apply_proxy_binding`: 签名 inbound_user/pwd → **method/password**; 内部
  `add_proxy_binding(..., inbound_user="", inbound_pwd=password, protocol=shadowsocks, method=method)`
- `replace_proxy_binding`: **签名 + 行为完全不变**(默认走 socks5, IPProbeWorker 零碰)

### 改 `workers/proxy_deploy_worker.py`

- `_deploy_one_binding`:
  - 去掉 `inbound_user`(SS 不用); `password = uuid4().hex`; `method = app_config.SS_METHOD`
  - `apply_proxy_binding(vps_port, proxy_outbound, method, password)`
  - 内 ping → `ShadowsocksProbe().test_internal(client, vps_port, method, password)`
  - 外 ping → `ShadowsocksProbe().test_external(vps_ip, vps_port)`(TCP 可达, 无需账密)
  - 上游 `build_proxy_outbound`(creds["protocol"]) **不动**
- `_mark_done`: `from_new_deployment(protocol=ProxyProtocol.SHADOWSOCKS, method=method,
  inbound_user="", inbound_pwd=password, ...)`

### 改 `test/proxy_deploy_worker/spec.md`

- 对外协议 socks5 → SS; 内/外 ping 改两套描述; 升版本 + 修订历史

### 不动

- 上游 outbound / 默认入口 18440 / routing tag client-<port> / 挑机 SQL / 端口策略 / stage 锁 / task 状态机

---

## 3. 验收交付

### 测试用例(改现有 TC 断言为 SS)

- 部署成功: xray config 里 client-<port> 是 shadowsocks + method; proxy_record
  protocol=shadowsocks + method=aes-256-gcm
- 内 ping 不通 → rollback 三件套(回归)
- 外 ping 不通 → status=pending_fw(回归)
- 上游 outbound 仍 socks5(回归)

### 必跑测试命令

```bash
PYTHONPATH=. uv run pytest test/proxy_deploy_worker/ test/xray/ -q
```

### 实现者完工标准

- [x] 开工改 doing + 确认 T-26/T-28 已 done
- [x] config.py / xray/config.py / xray/manager.py / proxy_deploy_worker.py 改完
- [x] spec.md 改完 + 升版本(v1.3)
- [x] 必跑测试全 PASS(含 ip_probe_worker 回归证明 replace 链 socks5 零回归)
- [x] 没动"不动"清单(尤其上游 outbound / 默认入口 18440 / 路由 / 挑机 SQL / stage 锁 / task 状态机)
- [x] 完成记录已填

---

## 完成记录(done 时追加)

```text
完成日期 / commit: 2026-06-24 / 未 commit(等需求窗口验收)

改动摘要(方案 A —— add_proxy_binding 加 protocol 参数, IPProbeWorker 零碰):
  源码:
  - config.py: 加 SS_METHOD = "aes-256-gcm"
  - xray/config.py: 加 INBOUND_PROTOCOL_SOCKS5/SHADOWSOCKS 常量 + InboundProtocolError
    + UNSUPPORTED_INBOUND_PROTOCOL_MESSAGE; 抽 _build_socks5_client_inbound /
    _build_ss_inbound 两个 inbound builder; add_proxy_binding 加 `*, protocol=socks5,
    method=""` 按协议分支造 inbound(默认 socks5 行为不变); build_proxy_relay_config
    改用共享 socks5 builder(行为不变)
  - xray/manager.py: apply_proxy_binding 签名 inbound_user/pwd → method/password,
    内部传 protocol=shadowsocks; replace_proxy_binding 签名+行为完全不变(默认 socks5)
  - xray/__init__.py: 补导出新常量/错误类
  - workers/proxy_deploy_worker.py: import ShadowsocksProbe + ProxyProtocol(去 test_*
    模块函数 import); _deploy_one_binding 去 inbound_user, method=SS_METHOD +
    password=uuid4().hex, apply 传 method/password, 内 ping ShadowsocksProbe.test_internal,
    外 ping ShadowsocksProbe.test_external; _mark_done 写 protocol=SHADOWSOCKS+method,
    inbound_user="" / inbound_pwd=password; 上游 build_proxy_outbound 不动
  测试:
  - test/proxy_deploy_worker/_helpers.py: 加 make_fake_ss_probe_cls
  - TC-07/08/09/10/11/12/13: test_internal/test_external 模块 patch 换 ShadowsocksProbe
    patch; TC-07 断言 protocol=shadowsocks + method=aes-256-gcm + inbound_user="" +
    新增 TC-07-i 验 apply 收到 method/password
  - spec.md: 升 v1.3, §1/§3/§6/§工具清单 改 SS, §三 修订历史 + 方案 A 备注

验收后健壮性/可读性小改进(2026-06-24, review MEDIUM#1 + LOW#2 + LOW#3, 边界仍只动 xray/config.py + proxy_deploy_worker.py):
  - [MEDIUM#1] xray/config.py add_proxy_binding 协议分支改关键字传参:
    _build_socks5_client_inbound(vps_port, user=, pwd=) / _build_ss_inbound(vps_port, method=, password=),
    inbound_pwd→password 不再靠位置对应; docstring 补"inbound_pwd 即 SS password 透传到 _build_ss_inbound 的 password 形参"
  - [LOW#2] proxy_deploy_worker.py _APPLY_BINDING_ERRORS 加 xc.InboundProtocolError:
    非法 protocol(配置错)→ apply_binding_failed(failed) 终态, 不再泡到外层 except→retriable;
    新增 TC-15 覆盖该分支(返回 failed/apply_binding_failed + task FAILED + msg 含 InboundProtocolError + stage 保持 running + 无 proxy_record)
  - [LOW#3] xray/config.py add_proxy_binding client_tag 改从 client_inbound["tag"] 读,
    消除与 builder 内部 f"client-{vps_port}" 的重复计算(tag 规则单一真相源在 builder)

grep 验证(签名变更调用方):
  - add_proxy_binding: 仅 xray/manager.py apply(SS)+replace(socks5) 调; build_proxy_relay_config 无活跃调用方
  - apply_proxy_binding: workers/proxy_deploy_worker.py(已改 SS) + services/ip_register.py(legacy 死路径, 非活跃, grep 确认无活跃 importer)
  - replace_proxy_binding: workers/ip_probe_worker.py(活跃, 行为零变化, 默认 socks5)
  - 全部确认无遗漏(方案 A 把 IPProbeWorker 完全隔离)

测试命令 / 结果:
  PYTHONPATH=. uv run pytest test/proxy_deploy_worker/ test/xray/ test/ip_probe_worker/ -q
  → 132 passed, 1 skipped in 0.94s  (验收后 +TC-15 共 4 个新断言, 128→132)
  (skip = TC-14_real_server.py 真机 e2e 默认 skip; ip_probe_worker 全 27 个 TC 全绿证明 replace 链零回归)

未覆盖风险:
  - 真机 SS 端到端验证(TC-14 默认 skip): 临时 xray 实例起停 / SS 握手需真机跑一遍确认(业务作者负责)
  - 纳管读 SS 账密未处理(ADR-0011 §7 列 later issue, 本批不做)
  - services/ip_register.py(legacy) 的 apply_proxy_binding 调用现签名已不匹配, 但它是死路径永不执行; legacy 大手术时整体删除

后续任务:
  - T-29: MCP 节点返回加 method + 自产 ss:// share_link
  - later issue: 纳管支持 SS(_parse_outbounds_from_config 读 SS 账密)
```
