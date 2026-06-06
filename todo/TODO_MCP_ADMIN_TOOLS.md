# TODO：MCP 业务工具暴露策略

> 通用工程规则见 [CLAUDE.md](../CLAUDE.md)。本文件只讨论：
> - rgvps / rgip 这两条完整业务怎么暴露成 MCP 工具
> - 从现有代码哪里抽、怎么抽
> - 外部 agent 能看到什么，不能看到什么
> - admin / user 两套 MCP 的工具边界
>
> 不和 [TODO_IP_PROXY.md](TODO_IP_PROXY.md) 合并，避免把“IP 业务实现 TODO”和“MCP 暴露策略 TODO”混在一起。

---

## 1. 当前判断

`rgvps` 和 `rgip` 都不要拆成 SSH / xray / 端口 / ping 这种底层工具给外部 agent 编排。

更合适的形态是：

```
外部 agent
   ↓
MCP 业务工具（register_vps_server / register_proxy_ip / status / retry / repair）
   ↓
services/ 业务函数
   ↓
Manager / atom / SSH / xray / DB
```

原因：现有两条流程内部都有顺序、状态、DB 落点、回滚和安全组兜底。底层步骤拆给外部模型后，模型会在 SSH 中断、xray 配置半成功、DB 未落库等场景里临场猜下一步，风险反而变大。

所以“能机器解决的全交给机器”仍然成立，但这里的机器应是后端业务层，而不是让外部 agent 直接操作 Manager / atom。

---

## 2. 从哪里抽

### 2.1 rgvps 主线

来源：

- `main.py` 的 `rgvps` CLI 分支
- `services/vps_register.py::register_vps`
- `services/vps_init.py::init_vps_xray`
- `xray/manager.py::XrayManager.ensure_installed_and_running`

实际业务链：

```
register_vps
  → 查重
  → SSH 测连 + 采集系统信息
  → VPSRecord 入库
  → init_vps_xray
      → 查 DB 状态：not_registered / already_running / in_progress
      → 标记 xray installing
      → SSH 连接
      → ensure_installed_and_running
      → import_existing_bindings
      → 端口审计并写 idle_port_count
      → best-effort 开本地防火墙
      → 内部 ping
      → 外部 ping
      → 写 xray_status
  → 合成 ok / ok_xray_partial
```

抽法：

- MCP 工具 `register_vps_server` 直接调用 `services.vps_register.register_vps(...)`。
- MCP 工具 `retry_vps_xray_setup` 直接调用 `services.vps_init.init_vps_xray(ip)`。
- MCP 工具 `get_vps_status` 从 `VPSRecord` 查状态，不碰 SSH。

不要抽：

- 不要暴露 `ssh_connect`
- 不要暴露 `install_xray`
- 不要暴露 `start_xray`
- 不要暴露 `open_firewall_ports`
- 不要让 agent 自己按步骤拼 rgvps

### 2.2 rgip 主线

来源：

- `main.py` 的 `rgip` CLI 分支
- `services/ip_register.py::register_ip`
- `xray/manager.py::XrayManager.apply_proxy_binding`
- `xray/manager.py::XrayManager.rollback_proxy_binding`
- `services/proxy_query.py::list_available_proxies`

实际业务链：

```
register_ip
  → 校验 protocol
  → 按 egress_ip 查 IPRecord
  → geoip
  → pick VPS：xray running + active + idle_port_count > 0 + 未过期
  → SSH 连接目标 VPS
  → 用 proxy_record 算已用端口
  → 选最小空闲端口
  → 生成客户端 inbound 账密
  → build_proxy_outbound
  → apply_proxy_binding
      → read_config
      → add_proxy_binding
      → upload_config
      → validate_config
      → reload
  → 内部 ping
      → 不通：rollback_proxy_binding，返回 failed
      → egress 不匹配：rollback_proxy_binding，返回 egress_mismatch
  → 写库：IPRecord + ProxyRecord + idle_port_count -= 1
      → 失败重试 3 次
      → 全失败：新开 SSH 回滚 xray
  → 外部 ping
      → 不通：尝试开本地防火墙后重测
      → 仍不通：返回 ok_security_group_blocked，不回滚 DB 和 xray
  → 返回 node / binding / ping
```

抽法：

- MCP 工具 `register_proxy_ip` 直接调用 `services.ip_register.register_ip(...)`。
- MCP 工具 `get_available_proxy_nodes` 继续调用 `services.proxy_query.list_available_proxies(...)`。
- 后续补 `get_proxy_ip_status` / `get_proxy_binding_status` 从 DB 查 IPRecord + ProxyRecord + VPSRecord，不直接 SSH。
- 后续补 `audit_proxy_bindings` / `sync_proxy_bindings_from_xray_config` 做服务器 config 与 DB 对账。

不要抽：

- 不要暴露 `apply_proxy_binding`
- 不要暴露 `rollback_proxy_binding`
- 不要暴露 `read_config` / `upload_config` / `reload`
- 不要让 agent 自己处理“先 apply、再 ping、失败再 rollback”

---

## 3. 现有代码里的关键边界

### 3.1 rgvps 已经适合做业务级 MCP 工具

`register_vps` 已经会把 xray 部分失败合成 `ok_xray_partial`，并告诉 caller 可以单独重跑 xray 初始化。

这说明 admin MCP 不需要拆底层步骤，只需要补：

- `register_vps_server`
- `get_vps_status`
- `retry_vps_xray_setup`

### 3.2 rgip 也适合做业务级 MCP 工具，但要补“售后工具”

`register_ip` 已经有这些业务兜底：

- 内 ping 不通会回滚 xray
- egress 不匹配会回滚 xray
- 写库失败会重试 3 次
- 写库 3 次全失败会新开 SSH 回滚 xray
- 外 ping 失败不会回滚，因为这通常是云安全组问题，节点配置和 DB 都是有效的

但仍有一个重要窗口：

```
apply_proxy_binding 成功
  → SSH / 进程在内部 ping 前中断
  → xray config 里可能已经有 binding
  → DB 里可能还没有 IPRecord / ProxyRecord
```

这个窗口不是“拆给 agent 编排”能解决的，应该由后端提供对账/修复工具解决。

因此 admin MCP 需要补：

- `get_proxy_ip_status`
- `audit_proxy_bindings`
- `sync_proxy_bindings_from_xray_config`
- 必要时再做 `repair_proxy_binding`

### 3.3 业务工具必须吞掉意外异常

现在 `register_ip` 对 SSH 连接错误、xray 配置错误、DB 写入错误已有很多 status，但 MCP 对外工具最好再加一层兜底：

```
try:
    result = register_ip(...)
except Exception as exc:
    return {"status": "internal_error", "message": "...", "next_action": "..."}
```

原则：MCP 工具调用不要因为内部 RuntimeError / 未分类异常让 MCP server 崩掉；外部 agent 应该拿到结构化 status，再决定回复用户还是调用售后工具。

---

## 4. 对外暴露工具名建议

### 4.1 user MCP

只暴露用户可用的查询/导出能力。

| 工具名 | 场景 | 后端来源 |
|--------|------|----------|
| `get_available_proxy_nodes` | 用户要“给我一个新加坡节点/美国节点/列出可用节点” | `services.proxy_query.list_available_proxies` |

### 4.2 admin MCP：业务入口

| 工具名 | 场景 | 后端来源 |
|--------|------|----------|
| `register_vps_server` | 管理员给一台新 VPS，要求登记并初始化 xray | `services.vps_register.register_vps` |
| `register_proxy_ip` | 管理员给一条上游代理，要求登记并部署成可用节点 | `services.ip_register.register_ip` |
| `retry_vps_xray_setup` | VPS 已入库，但 xray 初始化失败或部分失败，需要重试 | `services.vps_init.init_vps_xray` |

### 4.3 admin MCP：状态与售后

| 工具名 | 场景 | 后端来源/待实现 |
|--------|------|----------------|
| `get_vps_status` | 查 VPS 是否入库、是否过期、xray 状态、闲端口数 | 待读 `VPSRecord` |
| `get_proxy_ip_status` | 查某个 egress_ip 是否已入库、是否过期、绑定到哪个 VPS/端口 | 待读 `IPRecord + ProxyRecord` |
| `get_proxy_binding_status` | 查某个 proxy_id 或 VPS:port 的绑定状态 | 待读 `ProxyRecord + VPSRecord + IPRecord` |
| `audit_proxy_bindings` | 对账：xray config 里的 binding 与 DB 是否一致 | 待实现 SSH 读取 + DB 比对 |
| `sync_proxy_bindings_from_xray_config` | 把 xray config 已存在但 DB 缺失的 binding 抄录进 proxy_record | 复用 `import_existing_bindings` 思路 |
| `repair_proxy_binding` | 对单个异常 binding 做修复/回滚/标记 | 待 audit 工具跑通后再定 |

---

## 5. 工具描述写法约束

MCP 工具描述必须服务于场景，不要只描述功能。

不要这样写：

```text
登记 VPS。
```

应该写成：

```text
当管理员提供 VPS 的 IP、SSH 用户名、密码、端口和到期日，并要求“登记这台 VPS”
或“把这台服务器纳入代理系统”时调用。工具会完成 SSH 测连、系统信息采集、
VPS 入库和 xray 初始化。返回 ok 表示已登记并初始化完成；返回 ok_xray_partial
表示 VPS 已入库但 xray 初始化失败，应先调用 get_vps_status 查看状态，再调用
retry_vps_xray_setup 重试。不要在未调用本工具前承诺登记成功；不要让用户手动执行
SSH / xray / 端口步骤。
```

每个工具描述都必须包含：

- 典型用户说法
- 什么时候调用
- 必填参数从用户话里怎么抽
- 返回 `status` 后 agent 应该怎么答
- 返回失败或部分成功后下一步调用什么售后工具
- 反例：什么时候不要调用、不要编造什么

---

## 6. 入参抽取原则

### register_vps_server

从用户输入抽：

- `ip`：VPS 登录 IP
- `username`：SSH 用户名，通常 root
- `password`：SSH 登录密码
- `port`：SSH 端口；用户不说时默认 22
- `expire_date`：到期日；用户不说时传 None
- `provider_domain`：服务商域名；用户不说时传空字符串

边界：

- 不要把服务商控制台密码当 SSH 密码。
- 用户给的信息缺 IP / 用户名 / 密码时，不要调用工具，先让用户补齐。
- 注册失败不要自行猜端口或密码。

### register_proxy_ip

从用户输入抽：

- `entry_host`：上游代理入口 host
- `entry_port`：上游代理入口端口
- `username`：上游代理账号
- `password`：上游代理密码
- `protocol`：默认 socks5；用户明确说 http 才传 http
- `egress_ip`：服务商控制台看到的出口 IP
- `provider_domain`：服务商域名；用户不说时传空字符串
- `expire_date`：到期日；用户不说时传 None

边界：

- `entry_host` 不是最终给同事的小火箭 IP；小火箭用的是返回里的 `node.host`。
- `egress_ip` 是身份键，不能省。
- 用户缺上游入口、端口、账号、密码、出口 IP 时，不要调用工具，先让用户补齐。
- 返回 `ok_security_group_blocked` 时，不要说失败；要告诉管理员节点已部署，但云安全组需要放行对应端口。

---

## 7. 实现顺序建议

### 第一阶段：保持一条龙业务，做 admin MCP 包装

1. `tools/register_vps_server.py`
2. `tools/register_proxy_ip.py`
3. `tools/retry_vps_xray_setup.py`
4. `tools/__init__.py` 或 admin 专用 registry 暴露这些工具
5. 单测 mock services 函数，确认 MCP 参数透传和 status 返回

### 第二阶段：补状态查询

1. `services/vps_status.py`
2. `services/proxy_status.py`
3. `tools/get_vps_status.py`
4. `tools/get_proxy_ip_status.py`
5. `tools/get_proxy_binding_status.py`

### 第三阶段：补对账/修复

1. `services/proxy_audit.py`
2. `audit_proxy_bindings`
3. `sync_proxy_bindings_from_xray_config`
4. 根据真实异常样本再决定是否需要 `repair_proxy_binding`

不要一开始就做大而全 repair。先 audit，看清楚异常形态，再做修复动作。

---

## 8. 决策结论

- `rgvps` / `rgip` 暴露成业务级 MCP 工具，不拆底层。
- 外部 agent 只负责理解用户意图、调用业务工具、根据 status 调售后工具。
- 后端业务层负责 SSH、xray、端口、DB、重试、回滚、对账。
- user MCP 只给查询可用节点。
- admin MCP 给登记、状态、重试、对账、修复。
- 工具描述必须围绕业务场景写，不让外部模型自己推理编排。
