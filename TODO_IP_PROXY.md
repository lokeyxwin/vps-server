# TODO：IP 业务

> 通用工程规则全在 [CLAUDE.md](CLAUDE.md)。本文件**只讲 IP 业务自己的事**：
> - 业务流程拆解
> - DB 字段设计
> - 实现步骤清单
> - 待你拍板的决策点
>
> 业务全跑通后删除本文件。

---

## 1. 业务身份

**rgIP**：把一条上游代理（云服务商买的）登记进系统，**同时部署到一台 VPS 的某个端口**作为对外出口。
登记 = 部署 = 写两张表（IP 表 + proxy 表）。**不再有独立的 deploy_proxy 业务**——端口绑定已经合并进 rgIP。

最终用户感知：客户端连 `VPS_IP:vps_port` (账密 socks5) → 流量通过上游代理出去 → 外网看到的是上游代理的 egress_ip。

---

## 2. rgIP 业务流程（最终版）

### 入参

```python
register_ip(
    entry_host,        # 上游代理入口（域名或 IP）
    entry_port,        # 上游代理入口端口
    username,          # 上游代理账号
    password,          # 上游代理密码
    protocol,          # 'socks5' / 'http'
    egress_ip,         # 用户在云服务商控制台看到的出口 IP（业务身份键）
    region,            # 地区（用户填）
    provider_domain,   # 服务商控制台域名（如 brightdata.com）
    expire_date,       # 到期日期
    renew=False,       # --renew 续费分支
) -> dict
```

### 主流程

```
① 查重（按 egress_ip）
    - 不存在 → 继续
    - 存在 + 未过期 → return duplicate（提示已登记 + 到期时间）
    - 存在 + 已过期 + --renew → 只 UPDATE expire_date，return ok_renewed
    - 存在 + 已过期 + 无 --renew → return expired_exists（提示加 --renew）

② 挑 VPS：未过期 + idle_port_count > 0
    SELECT FROM vps_record
    WHERE (expire_date IS NULL OR expire_date >= today)
      AND idle_port_count > 0
      AND xray_status = 'running'
    ORDER BY idle_port_count DESC, id ASC  # 闲端口最多的优先，相同则先注册的优先
    LIMIT 1
    - 没有合适 VPS → return no_available_vps

③ SSH 连进这台 VPS

④ 工具编排层算空闲端口集合
    available = vps.get_available_ports(PROXY_PORT_RANGE_START, END)
    # 已扣 OS 占用 + COMMON_RESERVED
    # 再扣已有 proxy_record 行（防御性，理论上应一致）
    available -= 已被该 VPS 的 proxy_record 行占用的端口
    - available 为空（罕见竞态）→ return no_available_port

⑤ 挑端口
    vps_port = min(available)  # 取最小，行为可预测

⑥ 生成客户端 inbound 账密
    inbound_user, inbound_pwd = xray.config.generate_random_auth()

⑦ 拼 outbound + 完整 config
    proxy_outbound = xray.config.build_proxy_outbound(
        host=entry_host, port=entry_port, user=username, pwd=password,
        protocol=protocol, tag=f"proxy-out-{vps_port}",
    )
    proxy_outbound["_meta"] = {  # 业务约定字段，extract_port_bindings 读这个
        "egress_ip": egress_ip,
        "egress_country": region,
    }

    # 关键：要保留 VPS 上已有的其他客户端 inbound（其他 IP 业务部署的），
    # 不能直接 build_proxy_relay_config 覆盖单条
    current_config = xray.config.read_config(vps.client) or build_vps_direct_config()
    # 把新的 inbound + outbound 加到 current_config 的 inbounds[] / outbounds[]
    # 再加 routing 规则
    # —— 这一步可能需要新工具 xray.config.add_proxy_binding(current_config, ...)

⑧ 上传 + 校验 + reload
    xray.config.upload_config(vps.client, new_config)
    xray.config.validate_config(vps.client)  # xray test -confdir
    xray.service.reload(vps.client)

⑨ 内 ping（验证配置生效 + 出口 IP 匹配）
    result = xray.service.test_internal_socks(vps.client, port=vps_port)
    # 不通 → 回滚 xray config，return failed（账密/入口可能错）
    # 通 → 校验 result["body"] == egress_ip
    #     不匹配 → 回滚 xray config，return egress_mismatch
    #     匹配 → 入库 ProxyRecord（status=USING）+ IPRecord，进入外 ping

⑩ 外 ping（从本机走 vps_ip:vps_port 测试）
    result = core.test_socks_proxy(vps_ip, vps_port, user=inbound_user, pwd=inbound_pwd)
    # 通 → status=ok，更新 VPS 表 idle_port_count -= 1
    # 不通 → 尝试 core.firewall.open_tcp_port_range(client, vps_port, vps_port)
    #        重测外 ping
    #        - 通 → ok
    #        - 还不通 → 安全组没开 → status=ok_security_group_blocked
    #          提示用户去 666clouds 控制台开 vps_port 入方向

⑪ 返回 dict 含完整节点信息（最终给用户看的）
    {
        "status": "ok" / "ok_security_group_blocked" / ...,
        "node": {
            "protocol": "socks5",
            "host": vps_ip,
            "port": vps_port,
            "username": inbound_user,
            "password": inbound_pwd,
            # 可选：subscription_link 等 vmess/vless 链接（暂不做）
        },
        "binding": {
            "vps_id": ..., "ip_id": ..., "proxy_id": ...,
        },
        "ping": {"internal": ..., "external": ...},
    }
```

### 业务级状态枚举（rgIP 自己的）

| status | 含义 | 入库吗 |
|--------|------|------|
| `ok` | 内通 + 外通 + egress 匹配 | IP 表 + proxy 表 |
| `ok_security_group_blocked` | 内通 + 外不通（自动开 VPS 防火墙后仍不通） | 同上，提示用户开安全组 |
| `ok_renewed` | --renew 命中过期记录，只刷 expire_date | 只 UPDATE IP 表 |
| `duplicate` | egress_ip 已存在且未过期 | 不入库 |
| `expired_exists` | egress_ip 已存在但过期，没传 --renew | 不入库 |
| `no_available_vps` | 没有"未过期 + 有闲端口"的 VPS | 不入库 |
| `no_available_port` | 罕见竞态：选的 VPS 端口被刚抢走 | 不入库 |
| `egress_mismatch` | 内 ping 通但实测出口 IP 跟用户填的不一样 | 不入库 + 回滚 xray |
| `failed` | 内 ping 不通（账密/入口可能错） | 不入库 + 回滚 xray |
| 4 种连接错 | auth_failed/timeout/refused/failed | 同 rgvps |

---

## 3. DB 设计

### 新增 `IPRecord`（ip_record 表）

```python
class IPProtocol:
    SOCKS5 = "socks5"
    HTTP = "http"

class IPRecord(Base):
    __tablename__ = "ip_record"

    id: int PK
    provider_domain: str(255) default=""     # 服务商域名（续费提醒分组用）

    # 上游入口
    entry_host: str(255) NOT NULL            # 域名或 IP
    entry_port: int NOT NULL
    username: str(128) NOT NULL              # 明文，跟 VPSRecord.username 一致
    password_encrypted: bytes NOT NULL       # 密文
    protocol: str(16) NOT NULL               # IPProtocol 常量约束

    # 出口（业务身份）
    egress_ip: str(64) UNIQUE INDEX NOT NULL # 唯一键
    region: str(64) default=""

    # 生命周期
    expire_date: date NULL
    is_expired: int default=0                # 0/1，巡检维护（暂不实现巡检）

    created_at / updated_at
```

工厂方法：`IPRecord.from_form(...)` 同 VPSRecord 风格，加密在内。
辅助方法：`get_password()` 解密。

### `ProxyRecord` 已建好

字段见 db/models.py（ProxyRecord 类）。**rgIP 业务在这张表加一行**（不是用 from_extracted_binding，因为这次是新部署不是从 xray 抠出来——可能要加新工厂方法 `from_new_deployment(vps_id, vps_port, ip_id, ...)`，或者直接构造）。

⚠️ **当前 ProxyRecord 没有 ip_id FK 字段**（denormalized 设计避免依赖 IP 表）。**写 rgIP 时要决定**：
- 选项 A：保持 denormalized，proxy_record 通过 egress_ip 反查 IPRecord
- 选项 B：给 proxy_record 加 `ip_id` 字段，FK 到 ip_record
- 我（Claude）倾向 **B**——proxy 表本质是"IP 部署到 VPS 端口的关系表"，有 ip_id 后续 JOIN 查节点信息会方便。

### `VPSRecord.idle_port_count` 维护

- rgvps 端口审计阶段写入（已有）
- rgIP 成功部署一条 → -1
- 未来 IP 过期巡检识别到 → +1（先不做）

---

## 4. 工具清单（rgIP 需要的）

### 已有可直接复用

| 工具 | 位置 |
|------|------|
| `VPSSession.from_record` / `.get_available_ports` / `.is_port_free` | core |
| `core.test_socks_proxy(vps_ip, vps_port, user, pwd)` | core（**确认是否支持 user/pwd 入参**，可能要补） |
| `core.firewall.open_tcp_port_range` | core |
| `xray.config.build_proxy_outbound` | xray.config |
| `xray.config.generate_random_auth` | xray.config |
| `xray.config.read_config` / `upload_config` / `validate_config` | xray.config |
| `xray.service.reload` | xray.service |
| `xray.service.test_internal_socks` | xray.service |
| `XrayManager.import_existing_bindings` | xray.manager |

### 要新造

| 工具 | 位置 | 干啥 |
|------|------|------|
| `xray.config.add_proxy_binding(current, vps_port, proxy_outbound, in_user, in_pwd)` | xray.config | 纯函数：往现有 config dict 里追加一组「客户端 inbound + 上游 outbound + routing 规则」，不破坏原有配置 |
| `xray.config.remove_proxy_binding(current, vps_port)` | xray.config | 纯函数：回滚用，删掉指定 vps_port 对应的 inbound/outbound/routing 三件套 |
| `IPRecord.from_form(...)` | db.models | 工厂方法（密码加密） |
| `services/ip_register.py::register_ip(...)` | services | 业务函数 |
| 可能：`test_socks_proxy` 支持 user/pwd 入参 | core.proxy_check | 业务部署的 inbound 是有账密的，外 ping 时要带 |

---

## 5. 实现顺序

按 CLAUDE.md "原子 → Manager → 业务" 走：

| Step | 内容 |
|------|------|
| 1 | DB: `IPRecord` + `IPProtocol` + 工厂方法 + ORM 测试 |
| 2 | DB: 如选 B 方案，`ProxyRecord` 加 `ip_id` FK |
| 3 | atom: `xray.config.add_proxy_binding` + `remove_proxy_binding` 纯函数 + 测试 |
| 4 | atom: `core.proxy_check.test_socks_proxy` 加 `user/pwd` 参数（如果还没） + 测试 |
| 5 | Manager: 如需要加 `XrayManager.apply_proxy_binding(vps_port, ...)` 复合方法（看业务编排里有没有重复逻辑值得抽） |
| 6 | 业务: `services/ip_register.py::register_ip` + 业务测试（全 status 路径 mock 覆盖） |
| 7 | 真服务器跑：用真代理（你提供）+ 你那台 666clouds VPS 验证 rgIP 全链路 |
| 8 | 巡检模块：暂不做 |

---

## 6. 待你拍板的决策点

### Q1：ProxyRecord 加 ip_id FK 还是保持 denormalized？

| 选项 | 优点 | 缺点 |
|------|------|------|
| A: 保持 denormalized | proxy_record 独立，不依赖 IP 表 | 查节点完整信息要 JOIN egress_ip（字符串匹配） |
| B: 加 ip_id FK（推荐） | JOIN 方便，关系清晰 | proxy_record schema 要改（drop + recreate） |

### Q2：续费分支 CLI 怎么写？

- 选项 A：`rgip --renew`（一个子命令两种行为）
- 选项 B：独立子命令 `renewip`

### Q3：rgIP 业务级 status 枚举叫啥？

参考上面的 9 种 status，认了就这么定。

### Q4：客户端 inbound 协议是否硬编码 socks5？

之前对齐过：**部署的对外协议固定 socks5**（手机/电脑都能用）。
如果未来要支持 http/vmess，再扩展。**当前业务函数入参不暴露这个**。

### Q5：上游代理"出口国家"字段从哪里取？

之前说："IP 业务那边等下回去加，取备注里的字符串"——意思是用户登记 IP 时直接传 `region` 参数，业务直接落库。读出来时 `extract_port_bindings` 走 `_meta["egress_country"]` 已经定好。✅ 不需要再问。

### Q6：rgIP 失败时是否回滚 xray 配置？

- 内 ping 不通 → 是的，回滚（用 `remove_proxy_binding` + upload + reload）
- egress 不匹配 → 同上
- 外 ping 不通（安全组）→ **不回滚**（配置正确，只是云端没开端口）
- 这个流程我已经写进上面流程图里，确认即可

---

## 7. 跑业务前的环境检查

- VPS 表里要有**至少 1 台 xray_status='running' 且 idle_port_count > 0**的 VPS
- 当前你已经有 `203.0.113.10`（idle_port_count=10）✅
- 准备一条真实上游代理凭据（任何 socks5/http 都行）
- 跑命令大致是：

```bash
uv run python main.py rgip \
  --entry-host proxy.example.com \
  --entry-port 1080 \
  --user xxx \
  --pwd 'yyy' \
  --protocol socks5 \
  --egress 1.2.3.4 \
  --region US \
  --provider brightdata.com \
  --ed 2027-01-01
```

main.py 也要等业务写完再加 rgip 子命令。

---

## 8. 历史决策档案（仅供查阅）

- proxy 业务已合并进 rgIP，不再独立 ❌
- ip 包目录已删（无 ip-specific atom，所有"代理凭据→config"翻译都属于 xray.config 领域）✅
- 工具清单：rgIP 自己需要的工具大部分已造好，rgvps 阶段已经把基建打牢 ✅
