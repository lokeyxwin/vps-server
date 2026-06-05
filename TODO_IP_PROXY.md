# TODO：IP 业务

> 通用工程规则全在 [CLAUDE.md](CLAUDE.md)。本文件**只讲 IP 业务自己的事**：
> - 业务流程拆解
> - DB 字段设计
> - 实现步骤清单
> - 关键决策档案（已拍板的不再讨论）
>
> 业务全跑通后删除本文件。

---

## 1. 业务身份

**rgIP**：把一条上游代理（云服务商买的）登记进系统，**同时部署到一台 VPS 的某个端口**作为对外出口。
登记 = 部署 = 写两张表（IPRecord + ProxyRecord）。**不再有独立的 deploy_proxy 业务**——端口绑定已经合并进 rgIP。

最终用户感知：客户端连 `vps_ip:vps_port` (账密 socks5) → 流量通过上游代理出去 → 外网看到的是上游代理的 egress_ip。

---

## 2. rgIP 业务流程（最终版）

### 入参

```python
register_ip(
    entry_host,        # 上游代理入口（域名或 IP，可重复）
    entry_port,        # 上游代理入口端口
    username,          # 上游代理账号
    password,          # 上游代理密码
    protocol,          # 'socks5' / 'http'
    egress_ip,         # 用户在云服务商控制台看到的出口 IP（业务身份键，唯一）
    provider_domain,   # 服务商控制台域名（如 brightdata.com）
    expire_date,       # 到期日期
    user_label="",     # 可选 自由备注（"梯子A线" 等，**不参与查询**）
    renew=False,       # --renew 续费分支
) -> dict
```

> ⚠️ **`region` 字段已删** — 地区改由 geoip 权威填充（country_code/country_name/city/region_name），用户不再传 region。

### 主流程

```
① 查 IP 表（按 egress_ip 身份键）
    - 不存在 → 继续
    - 存在 + is_active=1（未过期）→ return duplicate
    - 存在 + is_active=0 + --renew → 只 UPDATE expire_date + is_active=1 → return ok_renewed
    - 存在 + is_active=0 + 无 --renew → return expired_exists（提示加 --renew）

② 挑 VPS
    WHERE xray_status='running'
      AND is_active=1
      AND idle_port_count > 0
    ORDER BY idle_port_count DESC, id ASC
    LIMIT 1
    - 无合适 → return no_available_vps

③ SSH 连进 VPS

④ 算空闲端口
    available = vps.get_available_ports(PROXY_PORT_RANGE_START, END)  # 18441-18450
    available -= 该 VPS 已有 proxy_record 占用的端口
    - 空集 → return no_available_port

⑤ vps_port = min(available)   # 18441 占了就 18442，行为可预测

⑥ 生成客户端 inbound 账密
    inbound_user, inbound_pwd = xray.config.generate_random_auth()

⑦ 拼新 config（保留别的 IP 部署的 inbound，不能覆盖）
    current_config = xray.config.read_config(client) or build_vps_direct_config()
    proxy_outbound = xray.config.build_proxy_outbound(
        host=entry_host, port=entry_port, user=username, pwd=password,
        protocol=protocol, tag=f"proxy-{vps_port}",  # region 后面 geoip 出来再改写 tag
    )
    new_config = xray.config.add_proxy_binding(
        current_config, vps_port, proxy_outbound, inbound_user, inbound_pwd,
    )
    # 同 vps_port 已被占 → 抛 PortAlreadyBoundError → 业务转 no_available_port

⑧ 上传 + xray test -confdir + reload
    xray.config.upload_config(client, new_config)
    xray.config.validate_config(client)
    xray.service.reload(client)

⑨ 内 ping + geoip（先 sync，未来可并行）
    ping_result = xray.service.test_internal_socks(client, port=vps_port)
    # 不通 → 回滚 xray config (remove_proxy_binding + upload + reload) → return failed
    # 通了 → 对比 ping_result["body"] vs egress_ip
    #   不匹配 → 回滚 → return egress_mismatch
    #   匹配 → 进 ⑨.5

⑨.5 geoip 查询（认 IP 的地区身份）
     geo = core.geoip.lookup_egress(egress_ip)
     # 失败 → country/city 字段落空 + 日志 warning（不阻断业务）
     # 可选：拿到 country_code 后回写 outbound.tag = f"proxy-{country_code}-{vps_port}"
     #       并重新 upload + reload；或者懒得改 tag 也行（业务不依赖 tag）

⑩ 写库
    IPRecord(geo 字段 + 用户传的 entry_*/egress_ip/...) + is_active=1 → insert
    ProxyRecord(vps_id, vps_port, ip_id=新 IP.id, inbound_user, inbound_pwd_encrypted,
                upstream_host=entry_host, egress_ip=egress_ip,
                egress_country=geo["country_code"], status=USING) → insert
    VPSRecord.idle_port_count -= 1

⑪ 外 ping（从本机走 vps_ip:vps_port 验证）
    result = core.test_socks_proxy(vps_ip, vps_port, user=inbound_user, pwd=inbound_pwd)
    - 通 → return ok
    - 不通 → core.firewall.open_tcp_port_range(client, vps_port, vps_port) 重测
        - 通 → return ok
        - 还不通 → 安全组没开 → return ok_security_group_blocked
                   （配置正确，节点能连，只是云端入方向没放，不回滚 xray 不删 DB）

⑫ 返回完整节点信息
    {
        "status": "ok" / "ok_security_group_blocked" / "ok_renewed" / ...,
        "node": {
            "protocol": "socks5",
            "host": vps_ip, "port": vps_port,
            "username": inbound_user, "password": inbound_pwd,
            "country_code": geo["country_code"], "city": geo["city"],
        },
        "binding": {"vps_id": ..., "ip_id": ..., "proxy_id": ...},
        "ping": {"internal": "ok", "external": "ok"/"blocked"},
    }
```

### 业务级状态枚举（rgIP）

| status | 含义 | 入库 | 回滚 xray |
|--------|------|------|---------|
| `ok` | 全链路 OK | ✅ | — |
| `ok_security_group_blocked` | 内通 + 外不通（自动开 VPS 防火墙后仍不通） | ✅ | ❌ |
| `ok_renewed` | --renew 命中过期记录，只刷 expire_date | ✅ 仅 UPDATE IPRecord | — |
| `duplicate` | egress_ip 已存在且未过期 | ❌ | — |
| `expired_exists` | egress_ip 已存在但过期，没传 --renew | ❌ | — |
| `no_available_vps` | 没有"未过期 + xray running + 有闲端口"的 VPS | ❌ | — |
| `no_available_port` | 罕见竞态：选的 VPS 端口被刚抢走 | ❌ | — |
| `egress_mismatch` | 内 ping 通但实测出口 IP 跟用户填的不一致 | ❌ | ✅ |
| `failed` | 内 ping 不通（上游账密/入口/端口错） | ❌ | ✅ |
| 4 种连接错 | auth_failed/timeout/refused/failed | ❌ | — |

---

## 3. DB 设计

### 3.1 新增 `IPRecord`（ip_record 表）

```python
class IPProtocol:
    SOCKS5 = "socks5"
    HTTP = "http"

class IPRecord(Base):
    __tablename__ = "ip_record"

    id: int PK

    # 上游入口（云服务商控制台拿的凭据）
    entry_host: str(255) NOT NULL              # 域名或 IP，可重复
    entry_port: int NOT NULL
    username: str(128) NOT NULL                # 明文
    password_encrypted: bytes NOT NULL         # 密文
    protocol: str(16) NOT NULL                 # IPProtocol 常量约束

    # 出口（业务身份键）
    egress_ip: str(64) UNIQUE INDEX NOT NULL

    # 地区身份（geoip 权威）
    country_code: str(8)  default=""           # 'US' / 'SG' / 'HK' / ...   ← 查询主键
    country_name: str(64) default=""           # 'United States' / 'Singapore'
    city: str(64)         default=""           # 'Los Angeles' / 'Singapore'   ← 城市级查询
    region_name: str(64)  default=""           # 'California' / ''

    # 元信息
    provider_domain: str(255) default=""
    expire_date: date NULL
    is_active: int default=1                   # 1=可用 / 0=过期（巡检维护，暂不实现）
    user_label: str(64) default=""             # 自由备注，不参与查询

    # 时间戳
    created_at / updated_at
```

工厂方法：
- `IPRecord.from_form(entry_host, ..., password, ..., geo=None)` — 密码加密；geo 是 `core.geoip.lookup_egress` 的返回，可空
- `get_password()` — 解密
- `__repr__` — 屏蔽 username/password

### 3.2 VPSRecord 加 `is_active`

```python
is_active: int default=1     # 1=可用 / 0=过期（巡检维护）
```

业务函数 ② 挑 VPS 时读这个 + 兜底再比 `expire_date` vs today（双保险）。

### 3.3 ProxyRecord 加 `ip_id` FK

```python
ip_id: int | None = ForeignKey("ip_record.id", ondelete="RESTRICT")
```

- rgIP 业务新增的 binding 必填 `ip_id`
- `from_extracted_binding`（rgvps 端口审计转抄）填 `None`（这时不知道对应哪条 IP，巡检模块未来回填）
- 新工厂方法 `from_new_deployment(vps_id, vps_port, ip_id, inbound_user, inbound_pwd, upstream_host, egress_ip, egress_country, protocol="socks5")`

### 3.4 `idle_port_count` 维护

- rgvps 端口审计阶段写入（已有）—— **基于 18441-18450 区间** (上限 10)
- rgIP 成功部署 → `-= 1`
- 未来 IP 过期巡检 → `+= 1`（暂不做）

### 3.5 端口区间约定

```python
# config.py
XRAY_DIRECT_PROBE_PORT = 18440    # xray 自用直出端口：让 xray 有 inbound 能 running + rgvps 探安全组
PROXY_PORT_RANGE_START = 18441    # 业务可分配端口区间起点
PROXY_PORT_RANGE_END   = 18450    # 业务可分配端口区间终点（含），共 10 个
```

⚠️ **18440 不算业务端口**，不计入 `idle_port_count`。
⚠️ **rgIP 部署新端口（18441+）外 ping 不通时，单独开那个新端口的防火墙**（不是 18440）—— 18440 通不代表 18441 通，业务要严谨。

---

## 4. 工具清单（rgIP 需要的）

### 4.1 已有可直接复用

| 工具 | 位置 |
|------|------|
| `VPSSession.from_record` / `.get_available_ports` / `.is_port_free` | core |
| `core.proxy_check.test_socks_proxy(vps_ip, vps_port, user, pwd)` | core（**需确认是否支持 user/pwd 入参**） |
| `core.firewall.open_tcp_port_range` | core |
| `xray.config.build_proxy_outbound` | xray.config |
| `xray.config.build_vps_direct_config` | xray.config |
| `xray.config.generate_random_auth` | xray.config |
| `xray.config.read_config` / `upload_config` / `validate_config` | xray.config |
| `xray.service.reload` | xray.service |
| `xray.service.test_internal_socks` | xray.service |
| `XrayManager.import_existing_bindings` | xray.manager |

### 4.2 要新造

| 工具 | 位置 | 干啥 |
|------|------|------|
| `IPRecord.from_form(...)` + `get_password()` + `__repr__` | db.models | 工厂方法 + 加密 |
| `ProxyRecord.from_new_deployment(...)` | db.models | 新部署用的工厂（区别于已有的 from_extracted_binding） |
| `core.geoip.lookup_egress(ip) -> dict` | core | 调 ipinfo.io，失败兜底返回空字段 |
| `xray.config.add_proxy_binding(current, vps_port, proxy_outbound, in_user, in_pwd)` | xray.config | 纯函数：往现有 config 追加一组 inbound+outbound+routing |
| `xray.config.remove_proxy_binding(current, vps_port)` | xray.config | 纯函数：回滚用，删一组 inbound+outbound+routing |
| `core.proxy_check.test_socks_proxy` 加 user/pwd | core.proxy_check | 业务部署的 inbound 有账密，外 ping 要带 |
| `services/ip_register.py::register_ip(...)` | services | 业务函数 |

---

## 5. 实现顺序

按 CLAUDE.md "原子 → Manager → 业务" 走：

| Step | 内容 | 备注 |
|------|------|------|
| 0 | 更新本文档沉淀决策 | （正在做） |
| 1 | DB: `IPRecord` + `IPProtocol` + 工厂方法 + ORM 测试 | |
| 2 | DB: VPSRecord 加 `is_active` + 测试 + dev SQLite ALTER | 不写巡检逻辑 |
| 3 | DB: ProxyRecord 加 `ip_id` FK + `from_new_deployment` + 测试 + dev SQLite drop+create_all | |
| 4 | config: `XRAY_DIRECT_PROBE_PORT=18440` + `PROXY_PORT_RANGE_START=18441` + 影响面修正 + 测试 | vps_init 端口审计跟着改 |
| 5 | atom: `core.geoip.lookup_egress` + 测试（mock HTTP） | 无 token 也能跑 |
| 6 | atom: `xray.config.add_proxy_binding` / `remove_proxy_binding` + 测试 | |
| 7 | 业务: `services/ip_register.py::register_ip` + 全 status 路径测试 | |
| 8 | 真服务器跑：203.0.113.10 + 用户真实上游代理凭据 | |
| 9 | 巡检模块（is_active 维护）：暂不做 | |

---

## 6. 关键决策档案（已拍板）

> 这里记录所有"对齐过、不再讨论"的设计决策，避免后续会话重复发问。

### 6.1 架构 / 命名
- ip 包目录已删 — 无 IP-specific atom，所有"代理凭据→config"翻译归 xray.config
- proxy 业务已合并进 rgIP — 不再独立 deploy_proxy
- 业务函数命名 `register_ip`（动词_对象），CLI 子命令 `rgip`

### 6.2 数据模型
- ProxyRecord 加 `ip_id` FK（denormalize 已有 egress_ip/egress_country 留作冗余查询字段）
- IPRecord `egress_ip` 是业务身份键，唯一索引；`entry_host` 可重复（同一入口域名分多条 egress）
- `entry_host + entry_port + username` **不加联合 unique**
- VPS + IP 都用 `is_active: int default=1`（1=可用 0=过期），不用 `is_expired`（语义直观）
- ProxyRecord status 用 `using/expired`（事实表，过期不删行）
- 不存 `country_name` 等字段做唯一约束 — 只 `egress_ip` 一列 unique

### 6.3 地区方案（geoip）
- **用户不再传 `region`**，业务从 ipinfo.io 拉权威数据
- IPRecord 存 5 个地区字段：`country_code`（ISO，查询主键）/`country_name`/`city`/`region_name`/`user_label`（自由备注）
- API：`ipinfo.io` + 环境变量 `IPINFO_TOKEN`（50k/月免费）
- 无 token 也能跑（匿名 + 日志 warning）；查询失败兜底空字段，不阻断业务
- 中英文别名映射（"美国"→US / "洛杉矶"→Los Angeles）放 `core/region_alias.py`（**Step 7 之后再做**，先 MVP）

### 6.4 性能
- 内 ping + geoip **暂不并行**（先 sync 跑通，YAGNI）
- 体感慢再用 `concurrent.futures.ThreadPoolExecutor` 包一层
- **不引 asyncio**（跟现有 sync 代码风格冲突）

### 6.5 端口
- `XRAY_DIRECT_PROBE_PORT = 18440`（xray 自用直出 + rgvps 探安全组用）
- `PROXY_PORT_RANGE_START = 18441` / `END = 18450`（10 个业务端口）
- 端口选择策略：`min(available)`（行为可预测）
- VPS 选择策略：`ORDER BY idle_port_count DESC, id ASC`（闲端口多的优先，相同先注册的优先）

### 6.6 回滚策略
- 内 ping 不通 → 回滚 xray（remove_proxy_binding + upload + reload）
- egress 不匹配 → 同上
- 外 ping 不通（云端安全组）→ **不回滚**（配置对，DB 也写，只是云端没开端口）
- 失败回滚是 atom 层抛错 → 业务层捕获 → 业务层做回滚（不是 atom 自回滚）

### 6.7 续费
- CLI：`rgip --renew`（不独立 renewip 子命令）
- 续费只 UPDATE `expire_date` + `is_active=1`，不动 ProxyRecord（端口绑定保留）

---

## 7. MCP 入口规划（参考，不深入实现）

> 业务函数 `register_ip` 写完后，未来接 MCP 时按这个边界走。**Step 7 不实现 MCP，只确保业务函数返回结构符合 MCP 需要**。

### 7.1 双入口权限隔离

| 入口 | 权限 | 用途 |
|------|------|------|
| `admin_mcp` | 全表读写 | 管理员管 VPS/IP/proxy_record，查上游凭据 |
| `user_mcp` | 只读 + 受限字段 | 用户拿节点信息（不暴露上游凭据） |

### 7.2 权限边界 = 表边界（自然对齐）

| 表 | admin 看到 | user 看到 |
|----|-----------|-----------|
| `vps_record` | 全字段（含 SSH 账号密码密文/解密） | 只 `ip`（当 proxy host）+ `country_code`（如有） |
| `ip_record` | 全字段（含上游账号密码密文/解密） | **完全不暴露** |
| `proxy_record` | 全字段 | `host/port/inbound_user/inbound_pwd（解密发送）/protocol/country_code/city` |

→ **user MCP 永远不查 ip_record**，只 JOIN `proxy_record` + `vps_record`。
→ inbound_pwd 解密只发给真实下游使用，不写日志。

### 7.3 用户查询 API（设想）

```python
# 用户：给我一条美国节点
def find_node(query: str) -> dict | None:
    # query = "美国" / "US" / "Los Angeles" / "洛杉矶"
    iso = region_alias.resolve(query)  # → "US" or None
    
    if iso:
        # 国家级匹配
        return query proxy_record WHERE egress_country = iso AND status = 'using' LIMIT 1
    
    # 城市级模糊匹配
    en = region_alias.cn_city_to_en(query)  # "洛杉矶" → "Los Angeles"
    return query proxy_record JOIN ip_record WHERE city LIKE %en% LIMIT 1
```

返回结构：

```python
{
    "protocol": "socks5",
    "host": "<vps_ip>",
    "port": 18443,
    "username": "<inbound_user>",
    "password": "<inbound_pwd_明文>",
    "region": {"country_code": "US", "city": "Los Angeles"},
    "config_link": "socks://user:pwd@host:port#US-LA",   # 可选
}
```

---

## 8. 跑业务前的环境检查

- VPS 表：至少 1 台 `xray_status=running` + `is_active=1` + `idle_port_count > 0`
- 当前 `203.0.113.10`（idle_port_count=10）✅
- 准备一条真实上游代理凭据（socks5/http 都行）
- `.env` 加 `IPINFO_TOKEN=xxx`（可选，无 token 也能跑）
- CLI 命令大致：

```bash
uv run python main.py rgip \
  --entry-host proxy.example.com \
  --entry-port 1080 \
  --user xxx \
  --pwd 'yyy' \
  --protocol socks5 \
  --egress 1.2.3.4 \
  --provider brightdata.com \
  --ed 2027-01-01 \
  --label '梯子A'        # 可选
```

main.py 等业务写完再加 rgip 子命令。
