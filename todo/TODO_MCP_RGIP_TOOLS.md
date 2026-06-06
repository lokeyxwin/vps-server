# TODO：MCP rgip 业务工具

> 本文件只讨论 rgip：登记上游代理 IP + 部署成可用代理节点。
> rgvps 单独放在 [TODO_MCP_RGVPS_TOOLS.md](TODO_MCP_RGVPS_TOOLS.md)，不要混在一起。
>
> 通用 MCP 工具层规则见 [CLAUDE.md](../CLAUDE.md)。

---

## 1. 已拍板

- rgip 对外暴露为业务级 MCP 工具，不拆成 apply xray config / rollback / reload / ping 等底层工具。
- 外部 agent 只负责抽取用户提供的上游代理信息、调用业务工具、根据 status 调状态/修复工具。
- 后端 `services.ip_register.register_ip(...)` 负责挑 VPS、选端口、写 xray、内部 ping、写库、外部 ping和回滚。
- user MCP 只暴露 `get_available_proxy_nodes`，不给用户登记/修复类工具。
- admin MCP 才暴露 rgip 登记、状态、重试、对账、修复类工具。
- rgip 的具体售后工具先独立讨论，不和 rgvps 混在一起。

---

## 2. 口语化业务流（讨论用）

这一节给人看，用来讨论流程；工具名和函数名只是落地时再映射。

### 2.1 正常登记一条上游代理

用户：

```text
帮我登记这条代理 IP：入口地址、端口、账号、密码、协议、出口 IP、到期时间。
```

agent：

```text
先检查信息够不够。
如果缺入口地址、入口端口、账号、密码、出口 IP，就先问用户补齐。
如果协议没说，默认按 socks5 处理。
信息齐了，就把这条上游代理交给后端登记和部署。
```

后端：

```text
先看这个出口 IP 有没有登记过。
没登记过，就查出口 IP 的地区信息。
然后挑一台可用 VPS。
挑到 VPS 后，选一个空闲端口。
生成给同事连接用的新账号密码。
把这条上游代理写进 VPS 的 xray 配置。
先在服务器内部测试，确认流量真的从用户填的出口 IP 出去。
确认没问题后，再把 IP 和端口绑定写入数据库。
最后从本机外部测试这个节点能不能连。
```

如果全部成功：

```text
agent 告诉管理员：这条代理已经登记并部署好了。
如果是给同事用，再整理成小火箭填写格式。
```

### 2.2 出口 IP 已存在

用户：

```text
帮我登记这个出口 IP。
```

后端发现：

```text
这个出口 IP 数据库里已经有了。
```

agent：

```text
不要直接重复登记。
先查这条出口 IP 当前状态。
如果它还可用，就告诉管理员：这条已经登记过，并说明它当前绑定在哪台 VPS、哪个端口。
如果它已过期或状态异常，再进入续费/修复讨论，不要自己新建一条重复记录。
```

### 2.3 没有可部署的 VPS

后端发现：

```text
没有一台 VPS 同时满足：可用、没过期、xray 正在跑、还有空闲端口。
```

agent：

```text
告诉管理员：现在没有可承载新代理的 VPS。
下一步不是重试这条代理，而是先新增/修复 VPS，或者释放已有端口。
```

### 2.4 上游代理信息不通

后端发现：

```text
配置已经尝试写到 VPS，但服务器内部测试走不通。
```

后端行为：

```text
自动把刚写进去的 xray 配置撤回，不留下脏配置。
```

agent：

```text
告诉管理员：这条上游代理没有部署成功，常见原因是入口地址、端口、账号或密码不对。
不要把这个节点交给用户使用。
```

### 2.5 实测出口 IP 跟用户填的不一致

后端发现：

```text
代理能通，但实测出去的 IP 不是用户填的出口 IP。
```

后端行为：

```text
自动撤回刚写进去的 xray 配置。
不写入数据库。
```

agent：

```text
告诉管理员：这条代理能连，但出口 IP 对不上。
优先让管理员核对是不是填错了出口 IP，或者上游账号拿错了。
不要把这个节点交给用户使用。
```

### 2.6 节点已部署，但外部连不上

后端发现：

```text
服务器内部测试通过，数据库也写好了。
但是从外部连 VPS 端口不通。
后端已经尝试打开 VPS 本机防火墙，还是不通。
```

agent：

```text
不要说登记失败。
应该告诉管理员：节点已经部署完成，但云服务商安全组大概率没放行这个端口。
下一步是去云控制台放行对应 TCP 端口。
```

### 2.7 服务器配置和数据库可能不一致

可能发生：

```text
xray 配置已经写进服务器，但 SSH 或进程在内部测试 / 写库前中断。
结果可能是：服务器上有这条绑定，数据库里没有完整记录。
```

agent：

```text
不要自己猜该回滚还是补库。
应该先查状态，再做对账。
对账结果能说明“服务器有、数据库没有”或“数据库有、服务器没有”后，再决定修复动作。
```

这一块就是后续要讨论的售后工具核心。

---

## 3. 待讨论

### 3.1 `get_proxy_ip_status`

待聊场景：

- 用户/管理员问“这个出口 IP 登记了吗”
- `register_proxy_ip` 返回 `already_exists` 后，agent 需要查清楚这条 IP 当前是不是 active、是否过期、绑定在哪台 VPS 的哪个端口
- `register_proxy_ip` 返回异常后，agent 需要确认 DB 里有没有留下 IPRecord / ProxyRecord

待定问题：

- 入参用 `egress_ip` 还是 `ip_id`，还是二者都支持？
- 返回是否应该包含明文节点账密？
- 是否只读 DB，还是允许实时 SSH 对账？

暂定倾向：

- 第一版只读 DB。
- 默认按 `egress_ip` 查，因为它是 rgip 的业务身份键。
- 是否返回明文节点账密待讨论；如果返回，要确认这是 admin MCP 还是 user MCP。

### 3.2 `retry_proxy_ip_registration`

待聊场景：

- `register_proxy_ip` 因 SSH 连接短暂失败、DB 写入短暂失败、内部 ping 临时失败而没有完成
- 管理员希望“不重新填一遍所有参数，直接重试这条登记”

待定问题：

- 如果第一次失败时没有落 IPRecord，重试工具从哪里拿原始上游账号密码？
- 是否需要先引入 operation 记录表保存一次 rgip 尝试的入参和阶段？
- 没有 operation 记录时，`retry_proxy_ip_registration` 是否只能要求用户重新提供完整参数？

暂定倾向：

- 第一版先不做纯 retry。
- 先让 agent 重新调用 `register_proxy_ip`，因为目前失败态大多不落库，系统没有保存完整原始入参。
- 真要做 retry，需要先设计 operation/job 表。

### 3.3 `repair_proxy_binding`

待聊场景：

- xray config 已经写入 binding，但 SSH/进程在内部 ping 或写库前中断，导致服务器有配置、DB 没记录
- DB 有 ProxyRecord，但服务器 xray config 缺 binding
- DB 和 xray config 的端口、账号、出口元信息不一致

待定问题：

- repair 是自动修，还是先 audit 再让 agent 二次确认？
- repair 支持哪些动作：回滚服务器 config、补 DB、标记异常、重新部署？
- 是否应该先做 `audit_proxy_bindings`，等它能准确识别异常类型后再做 repair？

暂定倾向：

- 不先做大而全 `repair_proxy_binding`。
- 先做 `audit_proxy_bindings` 和 `sync_proxy_bindings_from_xray_config`。
- repair 等真实异常样本出现后再定义动作集合。

---

## 4. 已否决

- 否决：把 `XrayManager.apply_proxy_binding` 直接暴露给 agent。
  - 原因：它只负责写 xray config，不负责内 ping、DB、外 ping和业务回滚。
- 否决：把 `rollback_proxy_binding` 直接暴露给 agent。
  - 原因：回滚需要知道 last_config / vps_port / 业务阶段，外部 agent 不应掌握这些内部状态。
- 否决：让 agent 自己执行“apply → 内 ping → 失败 rollback → 写库 → 外 ping”。
  - 原因：这正是 `services.ip_register.register_ip` 已经封装好的业务状态机。

---

## 5. 从哪里抽

来源：

- CLI 入口：`main.py` 的 `rgip` 分支。
- 业务入口：`services.ip_register.register_ip(...)`。
- 查询可用节点：`services.proxy_query.list_available_proxies(...)`。
- xray binding 编排：`xray.manager.XrayManager.apply_proxy_binding(...)`，只允许后端业务层调用，不暴露给 MCP。
- xray binding 回滚：`xray.manager.XrayManager.rollback_proxy_binding(...)`，只允许后端业务层调用，不暴露给 MCP。

当前 rgip 业务链：

```
register_ip
  → 校验 protocol
  → 按 egress_ip 查 IPRecord
  → geoip
  → pick VPS
  → SSH 连接目标 VPS
  → 算端口
  → 生成客户端 inbound 账密
  → build_proxy_outbound
  → apply_proxy_binding
  → 内部 ping
      → 不通：rollback_proxy_binding
      → egress 不匹配：rollback_proxy_binding
  → 写库：IPRecord + ProxyRecord + idle_port_count -= 1
      → 全失败：新开 SSH 回滚 xray
  → 外部 ping
      → 不通：尝试开本地防火墙后重测
      → 仍不通：返回 ok_security_group_blocked
  → 返回 node / binding / ping
```

---

## 6. 对外工具草案

### 6.1 `register_proxy_ip`

待后续细聊。

### 6.2 `get_proxy_ip_status`

待后续细聊。

### 6.3 `retry_proxy_ip_registration`

待后续细聊。

### 6.4 `repair_proxy_binding`

待后续细聊。

### 6.5 `audit_proxy_bindings`

待后续细聊。

### 6.6 `sync_proxy_bindings_from_xray_config`

待后续细聊。

---

## 7. 当前结论

rgip 比 rgvps 更需要“状态 / 对账 / 修复”售后工具，因为它会增量修改 xray proxy binding，并且存在“服务器配置已变、DB 未落”的窗口。

但第一轮不要急着定义大而全 repair。应该先讨论清楚：

1. `get_proxy_ip_status` 查什么、返回什么。
2. `retry_proxy_ip_registration` 是否需要 operation/job 表。
3. `repair_proxy_binding` 是不是必须建立在 audit 结果之上。
