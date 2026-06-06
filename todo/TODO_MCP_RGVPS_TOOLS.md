# TODO：rgvps 一条龙服务与 MCP 入口

> 本文件只讨论 rgvps：登记 VPS + 初始化 xray。
> rgip 单独放在 [TODO_MCP_RGIP_TOOLS.md](TODO_MCP_RGIP_TOOLS.md)，不要混在一起。
>
> 当前方向：先把 rgvps 做成后端自己会处理错误、自己会收尾、自己给最终结论的一条龙服务；MCP 对外只暴露一个入口工具。

---

## 1. 已拍板

- 对外只展示一个 rgvps 入口工具，暂定工具名：`rgvps`。
- 不暴露 SSH、xray、端口、防火墙、状态查询、重试等底层或售后工具。
- agent 只负责：
  - 看用户给的信息够不够。
  - 缺必要信息就问用户补齐。
  - 信息齐了就调用一次 `rgvps`。
  - 把 `rgvps` 返回的最终结果告诉用户。
- rgvps 的错误处理、重试、收尾、状态落库都放在后端业务代码里，不让 agent 编排。
- 控制台日志给开发/运维看，MCP 返回值给 agent 看，两者不要混在一起。
- 工具描述只写场景和使用边界，不写内部 SSH/xray 编排细节。

---

## 2. 业务行为流

这一节用人话描述 rgvps 应该怎么跑，后面代码实现要服务这个流程。

### 2.1 用户登记新 VPS

用户：

```text
帮我登记这台 VPS：IP、账号、密码、端口、到期时间、服务商。
```

agent：

```text
先看信息够不够。
IP、SSH 账号、SSH 密码缺任何一个，都先问用户补齐。
端口没说就默认 22。
到期时间和服务商没说可以留空。
信息齐了，只调用一次 rgvps。
```

后端：

```text
查数据库有没有这个 IP。
如果没有，SSH 连上去确认账号密码可用，并采集系统信息。
采集成功后，把 VPS 基础信息写入数据库。
入库后继续初始化 xray。
xray 初始化过程中，后端自己处理安装、启动、自启、端口审计、本机防火墙、内部检测、外部检测。
最后返回一个最终结果。
```

### 2.2 VPS 已存在

当前业务规则：

```text
数据库里已有这个 IP，就返回 duplicate。
本次不重复写入。
本次不继续初始化 xray。
```

agent：

```text
告诉用户：这台 VPS 已经登记过，本次没有重复写入。
不要继续调用其他 MCP 工具。
```

### 2.3 SSH 信息有问题

账号密码错：

```text
后端返回 auth_failed。
agent 告诉用户：SSH 登录失败，优先核对服务器登录账号和服务器登录密码。
```

连接超时：

```text
后端返回 timeout。
agent 告诉用户：可能是 IP 不通、SSH 端口没放行、云安全组或服务器防火墙挡住了。
```

连接被拒：

```text
后端返回 refused。
agent 告诉用户：包到了服务器，但这个端口没有 SSH 服务监听，优先核对 SSH 端口和 sshd。
```

### 2.4 xray 没有完整成功

目标行为：

```text
后端先自己处理能处理的重试和收尾。
如果最后仍然没完整成功，返回最终结果。
agent 只转述结果，不继续调用状态/重试工具。
```

注意：

```text
VPS 已入库但 xray 没完整成功，不等于整条登记完全失败。
返回时要让 agent 明白：VPS 已经进系统了，问题发生在 xray 阶段。
```

### 2.5 内部通，外部不通

场景：

```text
服务器内部走 xray 已经通了，但从外部连 VPS 端口不通。
```

目标表达：

```text
这不是普通 xray 安装失败。
这通常是云服务商安全组入方向没有放行端口。
agent 应该告诉用户去云控制台放行对应 TCP 端口。
```

---

## 3. 服务对象思路

rgvps 第一阶段要先整理成一个“会自处理错误的一条龙服务”。

建议形态：

```text
RgVpsService
  输入：VPS 登记信息
  输出：统一的 RgVpsResult
```

它可以继续复用现有函数：

- `services.vps_register.register_vps`
- `services.vps_init.init_vps_xray`
- `core.VPSSession`
- `xray.XrayManager`

但对 MCP 来说，只能看到一个入口和一个最终结果。

### 3.1 服务对象负责什么

- 参数归一化：端口默认 22，服务商默认为空，到期日可空。
- 查重：已存在直接返回 `duplicate`，不重复写。
- SSH 测连和系统信息采集。
- VPS 基础信息入库。
- 调用 xray 初始化流程。
- 对可重试错误做业务内部重试。
- 对不能自动修的错误给出明确阶段和用户下一步。
- 把所有结果整理成统一返回形状。

### 3.2 服务对象不负责什么

- 不替用户猜 SSH 密码。
- 不绕过已存在规则强行覆盖 VPS 记录。
- 不把内部 SSH/xray 步骤暴露给 agent。
- 不把控制台日志塞进 MCP 返回值。

---

## 4. 统一返回形状

MCP 工具应该返回结构化结果，agent 只看这个结果回答用户。

建议形状：

```json
{
  "status": "ok",
  "stage": "done",
  "message": "VPS 已登记完成，xray 已初始化完成",
  "next_action": "none",
  "ip": "1.2.3.4",
  "details": {}
}
```

字段含义：

| 字段 | 含义 |
|------|------|
| `status` | 最终业务结果，例如 `ok` / `duplicate` / `auth_failed` / `ok_xray_partial` |
| `stage` | 问题发生在哪个稳定业务阶段，不写内部函数名 |
| `message` | 给 agent 转述给用户的短说明 |
| `next_action` | 下一步业务动作，不写具体工具名 |
| `ip` | 本次处理的 VPS IP |
| `details` | 可选补充信息，给 agent/管理员看，不放控制台长日志 |

稳定阶段建议：

| stage | 含义 |
|-------|------|
| `precheck` | 查重或参数前置阶段 |
| `ssh_connect` | SSH 连接阶段 |
| `collect_system_info` | 系统信息采集阶段 |
| `persist_vps` | VPS 基础信息入库阶段 |
| `xray_setup` | xray 安装/启动/自启/检测阶段 |
| `external_access` | 外部访问检测阶段 |
| `done` | 完成 |

`next_action` 建议：

| next_action | agent 行为 |
|-------------|------------|
| `none` | 不需要后续动作 |
| `provide_correct_ssh_credentials` | 让用户核对 SSH 账号密码 |
| `check_ssh_port_or_security_group` | 让用户核对 SSH 端口、云安全组、防火墙 |
| `open_cloud_security_group_port` | 让用户去云控制台放行端口 |
| `manual_admin_check` | 需要管理员按 message 排查 |

---

## 5. 当前 status 映射

### 5.1 成功

```json
{
  "status": "ok",
  "stage": "done",
  "message": "VPS 已登记完成，xray 已初始化完成",
  "next_action": "none"
}
```

### 5.2 已存在

```json
{
  "status": "duplicate",
  "stage": "precheck",
  "message": "这台 VPS 已经登记过，本次没有重复写入",
  "next_action": "none"
}
```

### 5.3 SSH 认证失败

```json
{
  "status": "auth_failed",
  "stage": "ssh_connect",
  "message": "SSH 登录失败，请核对服务器登录账号和服务器登录密码",
  "next_action": "provide_correct_ssh_credentials"
}
```

### 5.4 SSH 连接超时

```json
{
  "status": "timeout",
  "stage": "ssh_connect",
  "message": "SSH 连接超时，请核对 IP、SSH 端口、云安全组或服务器防火墙",
  "next_action": "check_ssh_port_or_security_group"
}
```

### 5.5 SSH 连接被拒

```json
{
  "status": "refused",
  "stage": "ssh_connect",
  "message": "SSH 连接被拒，请核对 SSH 端口和服务器 sshd 状态",
  "next_action": "check_ssh_port_or_security_group"
}
```

### 5.6 xray 没完整成功

```json
{
  "status": "ok_xray_partial",
  "stage": "xray_setup",
  "message": "VPS 已入库，但 xray 初始化没有完整成功",
  "next_action": "manual_admin_check"
}
```

### 5.7 内部通但外部不通

```json
{
  "status": "external_unreachable",
  "stage": "external_access",
  "message": "xray 在服务器内部已可用，但外部访问不通，通常需要在云安全组放行端口",
  "next_action": "open_cloud_security_group_port"
}
```

---

## 6. 唯一 MCP 工具描述草案

工具名：

```text
rgvps
```

标题：

```text
登记 VPS 并初始化 xray
```

描述草案：

```text
当管理员要求“登记这台 VPS”“把这台服务器纳入代理系统”时调用本工具。
本工具是一条龙入口：后端会完成 VPS 查重、SSH 测连、系统信息采集、VPS 入库、xray 初始化、端口审计和连通性检测。
调用前必须拿到 VPS IP、SSH 用户名和 SSH 密码；SSH 端口缺省为 22；到期时间和服务商可为空。
只调用本工具一次，不要拆分调用 SSH、xray、端口、防火墙或重试工具。
如果返回 ok，告诉用户 VPS 已登记完成。
如果返回 duplicate，告诉用户这台 VPS 已登记过，本次没有重复写入。
如果返回 auth_failed / timeout / refused，按 message 让用户核对 SSH 信息或安全组。
如果返回 ok_xray_partial，告诉用户 VPS 已入库，但 xray 初始化没有完整成功，并按 message 转述原因。
如果返回 external_unreachable，告诉用户 xray 内部可用，但外部访问不通，通常需要去云控制台放行端口。
不要编造登记结果；不要让用户自己理解内部日志；不要承诺工具返回值之外的状态。
```

输入参数：

- `ip`：VPS 登录 IP，必填。
- `username`：SSH 用户名，必填，通常是 root。
- `password`：SSH 登录密码，必填。
- `port`：SSH 端口，默认 22。
- `expire_date`：到期日，可空。
- `provider_domain`：服务商域名，可空。

---

## 7. 第一版实现顺序

1. 梳理 rgvps 的统一返回形状：`status / stage / message / next_action / ip / details`。
2. 在业务层补一个结果归一化函数，先不急着大改所有底层函数。
3. 补 `register_vps` 对系统信息采集阶段 `RuntimeError` 的捕获，避免命令执行中断直接冒泡。
4. 梳理 xray 初始化结果到 rgvps 返回结果的映射，尤其是 `external_unreachable` 不要被误说成普通安装失败。
5. 再做 `tools/rgvps.py`，只包装这一条业务入口。
6. admin MCP 第一阶段只注册 `rgvps`。
7. 单测：
   - mock `register_vps` 成功。
   - mock `duplicate`。
   - mock SSH 三类错误。
   - mock `ok_xray_partial`。
   - mock `external_unreachable`。
   - 验证 MCP 工具只返回结构化结果。

---

## 8. 待讨论

### 8.1 已存在 VPS 是否永远只返回 duplicate

当前规则是：

```text
已存在 = 拒绝写入 = 不继续初始化 xray
```

需要用户拍板：

- 是否一直坚持这个规则？
- 还是未来允许“已存在但 xray 没好时，rgvps 入口内部自动修复”？

第一版暂定：

```text
保持 duplicate，不做自动修复。
```

### 8.2 xray 初始化失败哪些可以自动重试

需要用户拍板：

- SSH 命令执行中断后，是否允许业务层重新建立 SSH 再重试当前阶段？
- `service_not_active` 是否自动再 start 一次？
- `install_failed` 是否自动再装一次，还是直接返回人工排查？

第一版暂定：

```text
先补明确的 SSH/命令执行中断兜底。
安装脚本明确失败先不自动重复安装。
```

### 8.3 `external_unreachable` 应该算什么 status

当前代码里 `register_vps` 会把 `init_vps_xray` 的 `external_unreachable` 压成 `ok_xray_partial`。

需要用户拍板：

- 保留 `ok_xray_partial`，message 里说明是安全组问题。
- 还是让 rgvps 最终直接返回 `external_unreachable`，这样 agent 更容易理解。

暂定倾向：

```text
直接返回 external_unreachable，因为这不是 xray 没装好，而是外部入口没通。
```

### 8.4 是否需要持久化 operation/job

如果进程在一条龙中途死掉，单纯的函数内重试无法恢复。

需要用户拍板：

- 第一版是否只处理函数运行期间的错误？
- 还是现在就设计 operation/job 表，记录每次 rgvps 的阶段和结果？

第一版暂定：

```text
先不做 operation/job 表。
依赖 VPSRecord.xray_status 等现有字段记录关键状态。
```

---

## 9. 已否决

- 否决第一阶段暴露 `get_vps_status` / `retry_vps_xray_setup`。
  - 原因：这会把一条龙拆回 agent 编排，偏离当前目标。
- 否决把 `XrayManager.ensure_installed_and_running` 直接暴露给 agent。
  - 原因：它属于后端编排层，需要 SSH client，不是业务入口。
- 否决让 agent 自己按顺序调用 SSH、安装、启动、自启、防火墙、ping。
  - 原因：这些顺序和异常处理必须由后端业务层兜住。
- 否决把控制台日志作为 MCP 返回内容。
  - 原因：日志给开发/运维排障，MCP 返回给 agent 做决策。
