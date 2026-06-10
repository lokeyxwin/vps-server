# vps-server

**VPS 资产管理 + 代理出口自动化** — 给个人 / 小团队用的后端服务。

你手上有一堆 VPS(自建 / 服务商租) + 一堆上游代理 IP(从代理商买的),想把它们
"**自动装好 xray + 自动挂出口 IP + 自动验证可用性**"凑成一个**可用的代理节点池**,
agent 一句"给我一个新加坡节点"就能拿到 host:port + 账密。

这就是本项目要解决的事。

---

## 目录

- [§1 这是什么](#1-这是什么)
- [§2 架构 30 秒](#2-架构-30-秒)
- [§3 快速部署 — 从零到能跑](#3-快速部署--从零到能跑)
- [§4 跑成常驻守护服务](#4-跑成常驻守护服务)(Linux / macOS / Windows 三选一)
- [§5 MCP 客户端怎么连](#5-mcp-客户端怎么连)
- [§6 端口配置 + 改端口怎么办](#6-端口配置--改端口怎么办)
- [§7 日常运维](#7-日常运维)
- [§8 文档导航 + 设计原则](#8-文档导航--设计原则)

---

## 1. 这是什么

### 1.1 业务流(端到端)

```text
用户 / agent
   │  ① register_vps (一台 VPS 凭据)
   ├──▶ 后端: SSH 测连 → 入库 → 派后台装机任务
   │
   │  ② register_ip (一条上游 IP 凭据)
   ├──▶ 后端: 测试机临时挂 outbound 校验账密 → 通则入库 → 派后台部署任务
   │
   ▼
后端常驻 worker (异步)
   ├─ XrayWorker         扫装机任务 → SSH 进 VPS 装 xray + 起 + 自启 +
   │                                    纳管已有出口配置
   └─ ProxyDeployWorker  扫部署任务 → 挑机 + 高位随机端口 + 配上线 +
                                       内外 ping → 入 proxy_record
   ▼
agent 调
   get_available_proxy_nodes("SG") → 返回可用节点 host:port + 账密 + 出口 IP + 地区
```

### 1.2 跟谁交互

- **agent / 用户** — 通过 MCP 工具调 `register_vps` / `register_ip` / 查询节点
- **VPS** — 后端 SSH 远程操作(装 xray / 改 config / 开防火墙端口)
- **上游代理服务商** — 后端拿凭据测连通,本身不存 API key
- **数据库** — SQLite (默认 dev) / MySQL (生产)

### 1.3 当前能力

| 业务 | 状态 |
|---|---|
| VPS 登记 + xray 装机 + 纳管已有配置 | ✅ 完整闭环 |
| 上游 IP 登记 + 校验账密 | ✅ |
| 测试机自举 (没装 xray 自动装) | ✅ (ADR-0009 + T-19) |
| 代理部署 + 内外 ping 验证 | ✅ |
| 可用节点查询(按地区) | ✅ |
| 装机失败 / 容量满 / 安全组未放行 等失败状态查询 | ✅ |
| 巡检(IP 到期 / VPS 续费) | ⏸ 封存 worker,后续启用 |

---

## 2. 架构 30 秒

### 2.1 二进程模型

```text
mcp_server.py        前台收单     接 MCP 协议, 分发工具 handler
                                  (Claude Desktop / OpenClaw / Codex 拉起)
       │
       └─ 调 db/queries.py / workers/ssh_worker / workers/ip_probe_worker

main.py worker-loop  后端常驻     扫 task 表 + 推异步段 worker
                                  (systemd / launchd / Windows 服务 拉起)
       │
       └─ 串行调度 XrayWorker.run_once() + ProxyDeployWorker.run_once()
```

**两进程独立**:MCP server 挂了不影响装机 worker,反之亦然。

### 2.2 四层职责

| 层 | 位置 | 干啥 |
|---|---|---|
| 协议适配 | `tools/*.py` | MCP arguments → 业务函数 → JSON 包 TextContent |
| 异步业务 | `workers/*.py` | 扫 task 表 + 编排原子工具 + 状态机 |
| 业务函数 | `db/queries.py` | MCP 工具调的所有业务函数(读写都在,白名单 patch) |
| ORM | `db/models.py` | 表结构 + ORM 模型 |

### 2.3 MCP 工具暴露面(双壳)

| 壳 | 端口 | 工具 |
|---|---|---|
| **admin** | 47180 | `register_vps` / `register_ip` / `init_db` / `init_probe_vps` / 全部查询 |
| **user** | 47181 | `get_available_proxy_nodes` / `get_vps_registration_status` / `get_ip_registration_status` (只读) |

详见 §5 客户端配置。

---

## 3. 快速部署 — 从零到能跑

> 目标:在一台新机器上 5 步起来,先确认能跑通,再考虑 §4 常驻守护。
> 任何 OS 都能跑(部署本身平台无关),Linux 推荐生产用。

### 3.1 物料清单

| 物料 | 说明 |
|---|---|
| Python 3.10+ | 项目用 3.13 开发,3.10+ 应该都能跑 |
| [uv](https://docs.astral.sh/uv/) | 项目依赖管理工具 |
| git | 拉代码 |
| **dev 机** | macOS / Linux / Windows 任一,自己开发 / 测试用 |
| **生产机** | 推荐 Ubuntu 22.04+ / Debian 12 / CentOS 9(有 systemd) |
| `.env` | 加密密钥 / 可选 token,项目根目录 |
| 测试 VPS × 1 | 给 `IPProbeWorker` 校验上游 IP 用,自己掏钱租一台 |

### 3.2 装依赖

```bash
# 1. 装 uv (macOS / Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Windows PowerShell:
# irm https://astral.sh/uv/install.ps1 | iex

# 2. 拉代码 + 装依赖
git clone <仓库地址> vps_server
cd vps_server
uv sync          # 自动建 .venv + 装依赖
```

### 3.3 配 `.env`(敏感)

```bash
cp .env.example .env
chmod 600 .env             # macOS / Linux: 别让别人偷看
# Windows: 用 NTFS 权限或不管 (dev 机)

# 编辑 .env:
```

```ini
# 密码加密密钥 — 首次本地用下面命令生成一次, 之后永远别改
# 改了 = 老 DB 里所有密文全废, 没法解
# 生成命令:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=<上面命令的输出>

# MySQL 密码 — 用 SQLite 不用填
MYSQL_PASSWORD=

# ipinfo.io token — 查 IP 归属地用, 不填走匿名极小额度
IPINFO_TOKEN=

# 测试 VPS 凭据 (给 IPProbeWorker 校验上游 IP 用, 最多 3 台)
PROBE_VPS_1_IP=
PROBE_VPS_1_PORT=22
PROBE_VPS_1_USER=root
PROBE_VPS_1_PWD=
```

> ⚠️ `ENCRYPTION_KEY` 是**生产命脉**。换一台机部署同一个 DB → 必须复用同一把 key。
> 备份到密码管理器,别只存生产机一份。

### 3.4 初始化(首次必做两步)

```bash
# 跑测试确认环境 OK
PYTHONPATH=. VPS_SERVER_TESTING=1 uv run pytest -q

# ① 建 DB 表
PYTHONPATH=. uv run python main.py init-db
# 看到 "init-db 完成: N 张表已就绪" 算成功

# ② 装测试 VPS (xray 装 + 起 + 留 inbound 19000, 幂等)
PYTHONPATH=. uv run python main.py init-probe-vps
# 看到 "init-probe-vps 完成: host=... inbound_port=19000" 算成功
```

**这两步什么时候要重跑**:
- 加新 ORM 表 → 重跑 `init-db`(已有表不动,只补新表)
- 换测试 VPS / 测试机 xray 挂了 → 重跑 `init-probe-vps`
- agent 查询时看到 `probe_vps_not_ready` → 重跑 `init-probe-vps`

### 3.5 手动跑通确认

```bash
# 看子命令
PYTHONPATH=. uv run python main.py --help
# 应看到: init-db / init-probe-vps / worker-loop 三个子命令

# 起 worker-loop (前台跑, Ctrl+C 退)
PYTHONPATH=. uv run python main.py worker-loop
# 看到:
# HH:MM:SS [INFO] main.worker_loop: main.worker-loop 启动: poll_interval=2s, ...
# (静默轮询, 没 task 时不打日志)
#
# Ctrl+C (= SIGINT) 后看到:
# HH:MM:SS [INFO] main.worker_loop: 收到信号 2, 准备优雅退出
# HH:MM:SS [INFO] main.worker_loop: main.worker-loop 已退出
```

**这一步通过 = 后端能跑了**。下一步要么手动跑 MCP server (调一次试试),
要么进 §4 做成守护服务长期跑。

---

## 4. 跑成常驻守护服务

> 上面 §3 是"手动起前台进程",Ctrl+C 就停 + 机器重启就丢。
> §4 是"做成守护服务" — 开机自启 + 崩了自动拉起 + 优雅退出。
> Linux 用 **systemd**,macOS 用 **launchd**,Windows 用 **NSSM**(三选一,看你部署到哪)。

### 4.1 什么是"守护 unit / plist / service" + 你要守护几个进程

#### 4.1.1 守护服务管理器(3 OS)

不同 OS 有不同的"守护服务管理器",共同要求:**一份文本配置文件**告诉操作系统
"这个进程怎么拉起 / 用哪个账号跑 / 崩了怎么处理 / 怎么优雅停"。
我们项目这份配置都放在 `deploy/` 目录,3 个 OS 各自一份。

| OS | 管理器 | 配置文件格式 | 我们的文件 |
|---|---|---|---|
| Linux | systemd | `.service` (INI 风格) | `deploy/vps-server-worker.service` |
| macOS | launchd | `.plist` (XML) | `deploy/com.vps-server.worker.plist` |
| Windows | NSSM (推荐) | NSSM 控制台 + 命令行 | `deploy/install-windows-nssm.md` |

#### 4.1.2 守护几个进程 — 看你用哪些 MCP 客户端

**核心区别**: stdio 客户端**自己拉子进程**,HTTP 客户端**只是连接已监听的端口**。

| 你用的客户端 | 要守护几个进程 | 守护谁 |
|---|---|---|
| **只 Claude Desktop / Claude Code**(stdio) | 1 个 | `main.py worker-loop` |
| **只 OpenClaw / Codex**(HTTP) | 3 个 | `worker-loop` + `mcp_server admin (47180)` + `mcp_server user (47181)` |
| **共存**(Claude + OpenClaw + Codex) | 3 个 | 同上(Claude Desktop 走 stdio,自己拉另一个子进程,不影响其他 3 个) |

**为什么 stdio 不守护 mcp_server.py**:
- Claude Desktop 启动时按 config 的 `command` 直接 `fork+exec` 拉子进程
- Claude 关闭时子进程被回收
- 你重启 Claude → 自动拉新子进程
- 所以 `mcp_server.py` stdio 模式**永远不要进守护管理器** —— 否则两个进程并发抢同一 stdio 会出错

**为什么 HTTP 必须守护 mcp_server.py**:
- OpenClaw / Codex 只是按 `url` 发 HTTP 连接,**不会拉子进程**
- 进程没人启动 = 端口没人监听 = 客户端 `connection refused`
- 客户端重启不影响 HTTP 进程(进程不归客户端管)
- 所以 **HTTP MCP server admin + user 两个都要守护**,跟 `worker-loop` 一样进 systemd / launchd / NSSM

#### 4.1.3 心智图

```text
[共存场景 — 三客户端通吃, 3 进程守护]

        守护层 (systemd / launchd / NSSM)
        ├── main.py worker-loop                                      ← 进程 1
        ├── mcp_server.py --role admin --http  (127.0.0.1:47180)     ← 进程 2
        └── mcp_server.py --role user  --http  (127.0.0.1:47181)     ← 进程 3
                  ▲                            ▲
                  │ HTTP 连接                   │ HTTP 连接
        ┌─────────┴────────┐         ┌─────────┴───────┐
   OpenClaw 5.12             Codex (GPT auth)        Claude Desktop ─────fork+exec
   (重启不影响后端)            (重启不影响后端)         │
                                                     ▼
                                            mcp_server.py (stdio 子进程)
                                            (Claude 自己拉, 关闭就回收)
```

只要守护那 3 个进程不挂,所有客户端来去自由,工具永远可见。

---

### 4.2 Linux — systemd

```bash
# 1. 改 deploy/vps-server-worker.service 里的 4 处占位
#    User= / Group= / WorkingDirectory= / EnvironmentFile=
#    跟你实际的业务账号 + 部署路径对齐

# 2. 装到系统
sudo cp deploy/vps-server-worker.service /etc/systemd/system/
sudo systemctl daemon-reload                     # 让 systemd 看见新 unit

# 3. 起 + 开机自启 (一行)
sudo systemctl enable --now vps-server-worker

# 4. 验证
sudo systemctl status vps-server-worker          # 应看到 Active: active (running)
sudo journalctl -u vps-server-worker -f          # 实时看日志
```

**unit 文件长这样(截选)**:

```ini
[Unit]
Description=VPS Server — worker loop
After=network-online.target

[Service]
Type=simple
User=vps_server
WorkingDirectory=/opt/vps_server
EnvironmentFile=/opt/vps_server/.env
ExecStart=/opt/vps_server/.venv/bin/python main.py worker-loop

KillSignal=SIGTERM
TimeoutStopSec=120           # 给 120s 跑完手头活
Restart=on-failure           # 崩了 5s 后自动拉起
RestartSec=5s
StartLimitBurst=5            # 5 分钟崩 5 次就放弃 (防雪崩)

NoNewPrivileges=yes          # 安全收紧
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
```

完整文件见 `deploy/vps-server-worker.service`,顶部注释列了 4 处必改占位。

---

### 4.3 macOS — launchd

> dev 机用得多,生产偶尔。

```bash
# 1. 改 deploy/com.vps-server.worker.plist 里的路径占位
#    把 /Users/<you>/path/to/vps_server 换成你实际路径

# 2. 装到 LaunchAgents (用户级, 当前账号登录就拉起)
cp deploy/com.vps-server.worker.plist ~/Library/LaunchAgents/

# 3. 启 + 注册
launchctl load -w ~/Library/LaunchAgents/com.vps-server.worker.plist

# 4. 验证
launchctl list | grep vps-server                  # 应看到一行带 PID
tail -f /tmp/vps-server-worker.log                # 日志默认输出到这里

# 停 + 取消注册
launchctl unload -w ~/Library/LaunchAgents/com.vps-server.worker.plist
```

**plist 守护机制**:
- `RunAtLoad=true` — 装上就拉起
- `KeepAlive=true` — 崩了自动拉起
- `ProcessType=Background` — 标记后台服务
- 日志重定向到 `/tmp/vps-server-worker.log`(可改成项目目录下)

完整文件见 `deploy/com.vps-server.worker.plist`。

> ⚠️ **macOS 盖电脑会挂起进程**。dev 用足够,生产别上 macOS。

---

### 4.4 Windows — NSSM

> [NSSM](https://nssm.cc/) = Non-Sucking Service Manager,Windows 上最简
> "把任何 exe / 命令包成 Windows 服务" 的工具。Windows 自带 sc.exe 也能做但麻烦。

```powershell
# 1. 装 NSSM (Chocolatey 或者下载 zip 解压加 PATH)
choco install nssm

# 2. 注册服务
nssm install vps-server-worker `
  "C:\Path\To\vps_server\.venv\Scripts\python.exe" `
  "main.py worker-loop"

nssm set vps-server-worker AppDirectory "C:\Path\To\vps_server"
nssm set vps-server-worker AppEnvironmentExtra "PYTHONPATH=C:\Path\To\vps_server"
nssm set vps-server-worker AppStdout "C:\Path\To\vps_server\logs\worker.log"
nssm set vps-server-worker AppStderr "C:\Path\To\vps_server\logs\worker.err"
nssm set vps-server-worker Start SERVICE_AUTO_START      # 开机自启
nssm set vps-server-worker AppExit Default Restart       # 崩了自动重启
nssm set vps-server-worker AppRestartDelay 5000          # 5 秒退避

# 3. 启 + 验证
nssm start vps-server-worker
nssm status vps-server-worker                            # 应看到 SERVICE_RUNNING

# 4. 看日志 / 停 / 卸
Get-Content C:\Path\To\vps_server\logs\worker.log -Tail 50 -Wait
nssm stop vps-server-worker
nssm remove vps-server-worker confirm
```

完整 PowerShell 安装脚本 + NSSM 各参数解释见
`deploy/install-windows-nssm.md`(含一键脚本 `deploy/install-windows.ps1`)。

> ⚠️ Windows 上**优雅退出依赖 NSSM 的 SIGINT 模拟**(因为 Windows 没 SIGTERM)。
> 我们 `main.py` 的 SIGINT handler 一致管,NSSM stop 时会等当前轮跑完。

---

## 5. MCP 客户端怎么连

### 5.1 stdio 模式 — Claude Desktop / Claude Code

最简,客户端自己拉子进程,**不用守护**。编辑客户端配置文件:

**Claude Desktop**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "vps_admin": {
      "command": "/path/to/vps_server/.venv/bin/python",
      "args": ["mcp_server.py", "--role", "admin"],
      "cwd": "/path/to/vps_server",
      "env": { "PYTHONPATH": "/path/to/vps_server" }
    },
    "vps_user": {
      "command": "/path/to/vps_server/.venv/bin/python",
      "args": ["mcp_server.py", "--role", "user"],
      "cwd": "/path/to/vps_server",
      "env": { "PYTHONPATH": "/path/to/vps_server" }
    }
  }
}
```

Windows 的 `command` 写 `.venv\Scripts\python.exe`。

### 5.2 HTTP 模式 — OpenClaw 5.12 + Codex (GPT auth) 共存

> ⚠️ **当前 mcp_server.py 是 stdio 实现**。走 §5.2 需要后续把 stdio
> 改成 HTTP transport + 同时挂 `/sse` 和 `/mcp` 两个 endpoint
> (改造任务后续单独开,跟 §5.1 stdio 不冲突 — 同代码两套启动方式)。

#### 5.2.1 为什么必须 4 条注册

```text
admin 壳  127.0.0.1:47180          user 壳  127.0.0.1:47181
├── /sse  → OpenClaw 注册          ├── /sse  → OpenClaw 注册
└── /mcp  → Codex 注册             └── /mcp  → Codex 注册

= 4 条注册全填齐才完整 (2 客户端 × 2 壳)
```

**关键认知**:OpenClaw 里 GPT auth 用 Codex 插件,**不代表** GPT 自动读到
OpenClaw 的 `mcp.servers`。两边各自独立:
- OpenClaw 可见 → `~/.openclaw/openclaw.json` 的 `mcp.servers`
- Codex / GPT auth 可见 → `~/.codex/config.toml` 的 `[mcp_servers.*]`

#### 5.2.2 客户端 config 模板

**OpenClaw 侧** `~/.openclaw/openclaw.json`:

```json
{
  "mcp": {
    "servers": {
      "vps_admin": { "url": "http://127.0.0.1:47180/sse", "transport": "sse" },
      "vps_user":  { "url": "http://127.0.0.1:47181/sse", "transport": "sse" }
    }
  }
}
```

**Codex 侧** `~/.codex/config.toml`:

```toml
[mcp_servers.vps_admin]
url = "http://127.0.0.1:47180/mcp"
startup_timeout_sec = 120

[mcp_servers.vps_user]
url = "http://127.0.0.1:47181/mcp"
startup_timeout_sec = 120
```

#### 5.2.3 反例 — 别踩

| 反例 | 后果 | 正解 |
|---|---|---|
| 只在 OpenClaw 注册, 期望 GPT auth 也能看到 | GPT 调不到工具 | OpenClaw + Codex 两边都注册 |
| 两边写同一个端点 | Codex 不识别 SSE → 启动报错 | OpenClaw 走 `/sse`, Codex 走 `/mcp` |
| admin / user 端口写反 | user 拿到 admin 权限(权限边界破了) | 严格 47180=admin / 47181=user |
| 监听 0.0.0.0 不绑 127.0.0.1 | admin 端点对公网暴露,任何人能调 register_vps | 必须绑 127.0.0.1,agent 同机访问 |

---

## 6. 端口配置 + 改端口怎么办

### 6.1 项目固定端口清单

所有端口常量都在 `config.py`,改一处即可:

| 用途 | 默认 | config.py 常量 | 改了要重启什么 |
|---|---|---|---|
| MCP admin 壳监听 | 47180 | `MCP_ADMIN_PORT` | mcp_server.py admin 实例 |
| MCP user 壳监听 | 47181 | `MCP_USER_PORT` | mcp_server.py user 实例 |
| MCP 监听地址 | 127.0.0.1 | `MCP_LISTEN_HOST` | 同上 |
| xray 默认 inbound | 18440 | `XRAY_DEFAULT_PORT` | (远程 VPS 上,跟本机无关) |
| 测试机 inbound | 19000 | `probe_vps.config.PROBE_TEST_PORT` | 重跑 `init-probe-vps` |
| worker-loop idle 间隔 | 2s | `POLL_INTERVAL_SECONDS` | worker-loop 进程 |

### 6.2 改 MCP 端口怎么办

```bash
# 1. 改 config.py 一行
# MCP_ADMIN_PORT = 47180  →  47200  (示例)

# 2. 重启 mcp_server (按你部署方式)
#    Linux: sudo systemctl restart vps-server-mcp-admin
#    macOS: launchctl kickstart -k gui/$(id -u)/com.vps-server.mcp-admin
#    Windows: nssm restart vps-server-mcp-admin
#    Claude Desktop: 退 Claude 再开 (client 自己拉的子进程)

# 3. 改客户端 config 4 处 url 里的端口数字
#    (OpenClaw × 2 + Codex × 2)

# 4. 重启客户端
```

**端口被占了**怎么办:
```bash
# 看哪个进程占了
lsof -iTCP:47180 -sTCP:LISTEN          # macOS / Linux
netstat -ano | findstr :47180          # Windows

# 杀掉, 或改 config.py 用别的端口 (推荐 47000-47999 段避开框架默认)
```

---

## 7. 日常运维

### 7.1 常用命令(Linux systemd 为例)

```bash
# 状态
sudo systemctl status vps-server-worker

# 实时日志
sudo journalctl -u vps-server-worker -f

# 重启(代码改了 / config 改了)
cd /opt/vps_server && sudo -u vps_server git pull && sudo -u vps_server uv sync
# 如果引入新 ORM 表, 先跑 init-db:
# sudo -u vps_server PYTHONPATH=. .venv/bin/python main.py init-db
sudo systemctl restart vps-server-worker

# 停
sudo systemctl stop vps-server-worker

# 关闭开机自启
sudo systemctl disable vps-server-worker
```

### 7.2 看业务有没有干活

```bash
# 数据库直查(SQLite, 生产 MySQL 类似)
sqlite3 db/vps_server.db "SELECT id, ip, stage, xray_version FROM vps_record;"
sqlite3 db/vps_server.db "SELECT id, ip_id, status, last_error_code FROM ip_task;"
```

### 7.3 常见问题

| 现象 | 排查 |
|---|---|
| `systemctl status` 立刻 fail | `journalctl -u xxx -n 50` 看日志,常见:`ENCRYPTION_KEY` 没填 / `.venv` 不存在 / 路径错 |
| worker 启动但没干活 | 看 `vps_task` / `ip_task` 表有没有 pending 任务,没的话就是没人调 `register_vps` / `register_ip` |
| `no such table` 错误 | 首次部署忘跑 `init-db`,或加新表后没重跑 |
| MCP 工具调不到 | 看 §5.2.3 反例(4 条注册没填齐 / 端口写反) |
| `probe_vps_not_ready` 状态 | 测试机 xray 挂了,重跑 `init-probe-vps` |
| 频繁崩溃 5 次后挂了 | systemd 防雪崩兜底,`systemctl reset-failed xxx` 后排查根因再起 |

---

## 8. 文档导航 + 设计原则

### 8.1 想看什么找哪里

| 想知道 | 看哪里 |
|---|---|
| 项目当前能干啥 + 怎么部署 | **本文件** (README.md) |
| 业务流细节 / 每个 worker 的行为契约 | `test/<worker>/spec.md`(产品视角金标准) |
| "我们当时为什么这么决定" | `docs/adr/NNNN-*.md`(架构决策档案) |
| 协作守则 / 写代码姿态 | `CLAUDE.md`(全局) + `CLAUDE.local.md`(项目特有) |
| 当前进行中的活 | `task/doing_*.md` |
| 已完工的任务回顾 | `task/done_*.md` |
| 还没拍板的痛点 | `issue/YYYY-MM-DD-*.md` |
| 系统启动配置 | `deploy/`(systemd unit / launchd plist / NSSM 脚本) |

### 8.2 设计原则(简版)

1. **二进程分离** — MCP 收单 vs worker 常驻,职责单一,互不拖累
2. **异步状态机** — task 表是协调媒介,worker 主动扫 + 软锁防抢
3. **MCP 暴露 3 类工具** — 写入意图 / 状态查询 / 数据查询;**绝不暴露内部子动作**(SSH / 装机 / 开端口 等)
4. **DB 写入白名单 patch** — 业务表只允许 "主键精准定位 + 白名单字段更新",禁止整对象覆盖
5. **失败有 status code** — 每种失败有独立 code,agent 能精确转告用户怎么处理
6. **失败也写库** — 状态字段配 message 详情,不留"成功一半"灰色地带

详见 `docs/adr/`(ADR-0001 ~ 0009)。

### 8.3 测试覆盖

```bash
# 跑 mock 测试(快, 几秒, 250+ 个)
PYTHONPATH=. VPS_SERVER_TESTING=1 uv run pytest -q

# 跑某个 worker 的测试
PYTHONPATH=. VPS_SERVER_TESTING=1 uv run pytest test/xray_worker/TC-*.py -v

# 真服务器测试 (默认 skip; 配 env 触发, 走 PROBE_VPS_*)
# 谨慎 — 会真去 SSH
```

---

## 反馈

bug / 改进 / 痛点 → 写 `issue/YYYY-MM-DD-主题.md`,贴本对话拍板后变 task 落地。
