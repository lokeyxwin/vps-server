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

### 改 `xray/config.py`

1. 抽 `_build_ss_inbound(vps_port, method, password)` 纯函数(避免两处重复):

```python
def _build_ss_inbound(vps_port: int, method: str, password: str) -> dict:
    return {
        "tag": f"client-{vps_port}",          # tag 不变 → 路由规则照旧
        "port": vps_port,
        "listen": "0.0.0.0",
        "protocol": "shadowsocks",
        "settings": {"method": method, "password": password, "network": "tcp,udp"},
    }
```

2. `add_proxy_binding` + `build_proxy_relay_config` 的 client_inbound 改用 `_build_ss_inbound`。
   签名 inbound_user/inbound_pwd → **method/password**(SS 无 user)。

### 改 `xray/manager.py`

- `apply_proxy_binding` / `replace_proxy_binding` 签名同步: inbound_user/pwd → method/password(透传)

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

- [ ] 开工改 doing + 确认 T-26/T-28 已 done
- [ ] config.py / xray/config.py / xray/manager.py / proxy_deploy_worker.py 改完
- [ ] spec.md 改完 + 升版本
- [ ] 必跑测试全 PASS
- [ ] 没动"不动"清单(尤其上游 outbound / 默认入口 / 路由)
- [ ] 完成记录已填

---

## 完成记录(done 时追加)

```text
完成日期 / commit:
改动摘要:
测试命令 / 结果:
未覆盖风险:
后续任务:
```
