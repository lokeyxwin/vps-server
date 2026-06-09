# 部署教程 — vps_server

> 目标:在一台 Linux 服务器上把 `main.py worker-loop` 跑成 systemd 守护服务,
> 实现**开机自启 + 崩溃自动重启 + 优雅退出**。
>
> 心智模型(ADR-0008 §决策 §1):
> - `mcp_server.py` 前台收单 → 由 MCP 客户端 (Claude Desktop 等) 拉起,**不进 systemd**
> - `main.py worker-loop` 后端常驻 → **本教程的部署对象**

---

## 0. 部署清单

| 物料 | 说明 |
|---|---|
| 一台 Linux 服务器 | 推荐 Ubuntu 22.04+ / Debian 12 / CentOS 9,有 systemd 即可 |
| Python 3.10+ | 项目用 3.13 开发,3.10+ 应该都能跑 |
| [uv](https://docs.astral.sh/uv/) | 项目依赖管理工具 |
| git | 拉代码 |
| 业务账号 | 建议用专用账号 (如 `vps_server`),不用 root 跑 |
| `.env` | 项目根目录的环境变量文件,内含加密密钥 |

---

## 1. 系统准备

### 1.1 装基础工具

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y git curl

# CentOS / Rocky / Alma
sudo dnf install -y git curl
```

### 1.2 装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# uv 会装到 ~/.local/bin/uv, 加到 PATH (临时)
export PATH="$HOME/.local/bin:$PATH"
```

### 1.3 建业务账号 (建议)

```bash
sudo useradd -r -m -d /home/vps_server -s /bin/bash vps_server
```

> 为啥不用 root: xray 装机通过 SSH 远程到目标 VPS,本机不需要 root 权限。
> 留个最小权限账号能限制崩溃时的爆炸半径。

---

## 2. 落代码

```bash
# 选定部署根目录 (本教程统一用 /opt/vps_server, 按你喜好改)
sudo mkdir -p /opt/vps_server
sudo chown vps_server:vps_server /opt/vps_server

# 切换到业务账号操作
sudo -u vps_server -i
cd /opt/vps_server

# 拉代码
git clone <你的仓库地址> .

# 装依赖 (uv 会自动建 .venv)
uv sync
```

完成后目录结构:
```
/opt/vps_server/
├── .venv/                ← uv 建的虚拟环境
├── main.py               ← worker-loop 入口
├── mcp_server.py
├── config.py
├── db/                   ← sqlite 文件会落这里
├── workers/
├── tools/
└── ...
```

---

## 3. 配 `.env` (敏感)

```bash
# 还在 vps_server 账号下, 在 /opt/vps_server 里
cp .env.example .env
chmod 600 .env             # 别让别人偷看密钥
vim .env
```

需要填的字段:

```ini
# 密码加密密钥 — 首次本地用下面命令生成一次, 之后永远别改 (改了 = 旧密文全废)
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=<贴上面命令的输出>

# MySQL 密码 — 用 sqlite 不用填
MYSQL_PASSWORD=

# ipinfo.io token — 查 IP 归属地用, 不填走匿名极小额度
# 注册 https://ipinfo.io/signup 拿免费 50k 次/月
IPINFO_TOKEN=
```

> ⚠️ `ENCRYPTION_KEY` 是**生产命脉**。换一台机部署同一个 DB → 必须复用同一把 key。
> 备份到密码管理器,别只存生产机一份。

---

## 4. DB 选型

### 4.1 SQLite (默认,适合个人/小团队)

不用改,`config.py::DB_TYPE = "sqlite"` 已经是默认。文件落在 `/opt/vps_server/db/vps_server.db`。

systemd unit 的 `ReadWritePaths=/opt/vps_server/db` 就是给这个开权限。

### 4.2 MySQL (可选,生产规模再切)

```bash
# 1. 装 MySQL/MariaDB, 建库
sudo mysql -e "CREATE DATABASE vps_server CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 2. 改 config.py
# DB_TYPE = "mysql"

# 3. .env 填 MYSQL_PASSWORD

# 4. 建表 (跑一次 Python 起个 session 就会触发 create_all, 或手动跑迁移脚本)
```

切了 MySQL 后 systemd unit 的 `ReadWritePaths` 那行可以去掉。

---

## 5. 先手动跑一次确认通

落 systemd unit 之前,先确认手动能跑:

```bash
# 还在 vps_server 账号下, /opt/vps_server 里
PYTHONPATH=. uv run python main.py --help
# 应看到:
# usage: vps-server [-h] ACTION ...
# positional arguments:
#   ACTION
#     worker-loop  启动 worker 调度循环 (常驻进程)

PYTHONPATH=. uv run python main.py worker-loop
# 应看到:
# HH:MM:SS [INFO] main.worker_loop: main.worker-loop 启动: poll_interval=2s, workers=[XrayWorker, ProxyDeployWorker]
# (然后挂在那里轮询)

# Ctrl+C (= SIGINT) 应看到优雅退出:
# HH:MM:SS [INFO] main.worker_loop: 收到信号 2, 准备优雅退出
# HH:MM:SS [INFO] main.worker_loop: main.worker-loop 已退出
```

跑通了再进 systemd。

---

## 6. 装 systemd unit

### 6.1 改 unit 文件里的 4 处占位

打开 `deploy/vps-server-worker.service`,改 4 行:

```ini
User=vps_server                                # ← 你的业务账号
Group=vps_server                               # ← 你的业务组
WorkingDirectory=/opt/vps_server               # ← 部署路径
EnvironmentFile=/opt/vps_server/.env           # ← .env 路径
```

`ExecStart` 也用绝对路径指向 venv 里的 python (默认已经写好,改路径前缀即可):

```ini
ExecStart=/opt/vps_server/.venv/bin/python main.py worker-loop
```

### 6.2 装 + 启动

```bash
# 切回 root (sudo) 装 unit
exit                                                                    # 退出 vps_server 账号

sudo cp /opt/vps_server/deploy/vps-server-worker.service /etc/systemd/system/
sudo systemctl daemon-reload                                            # 让 systemd 看见新 unit
sudo systemctl enable vps-server-worker                                 # 开机自启
sudo systemctl start vps-server-worker                                  # 立即拉起

# 或者一行:
# sudo systemctl enable --now vps-server-worker
```

### 6.3 验证

```bash
sudo systemctl status vps-server-worker
# 应看到 Active: active (running) since ...

sudo journalctl -u vps-server-worker -n 20
# 应看到最近 20 行日志, 含 "worker-loop 启动" 和后续 poll 信息
```

---

## 7. 守护机制怎么生效的

| 场景 | systemd 行为 | 对应配置 |
|---|---|---|
| 开机重启 | 启动时自动拉起 | `enable` + `WantedBy=multi-user.target` |
| 进程被 kill / 崩了 | 等 5s 自动拉起 | `Restart=on-failure` + `RestartSec=5s` |
| 优雅停 | systemd 发 SIGTERM, 等当前轮跑完 | `KillSignal=SIGTERM` + `TimeoutStopSec=120` |
| 卡住 120s 不退 | 强杀 (SIGKILL) | `SendSIGKILL=yes` |
| 5 分钟内崩 5 次 | 放弃,等人介入 | `StartLimitBurst=5` (防雪崩) |
| 主动 exit 0 (优雅退出) | **不重启** | `Restart=on-failure` (而不是 `always`) |

---

## 8. 日常运维命令

```bash
# 看状态
sudo systemctl status vps-server-worker

# 实时看日志
sudo journalctl -u vps-server-worker -f

# 看最近 100 行 + 时间戳
sudo journalctl -u vps-server-worker -n 100 --no-pager

# 看今天的日志
sudo journalctl -u vps-server-worker --since today

# 重启 (优雅 stop + start)
sudo systemctl restart vps-server-worker

# 停 (优雅退出, 等当前轮跑完)
sudo systemctl stop vps-server-worker

# 改代码后重启
cd /opt/vps_server && sudo -u vps_server git pull && sudo -u vps_server uv sync
sudo systemctl restart vps-server-worker

# 关掉开机自启 (不再拉起, 但不停当前进程)
sudo systemctl disable vps-server-worker
```

---

## 9. MCP server 那一头怎么连

`mcp_server.py` 是 stdio 接口的 MCP server,**不进 systemd**。
由 MCP 客户端配置文件指向它即可。

### Claude Desktop / Claude Code 配置示例

编辑 MCP 客户端的配置(具体路径看客户端文档,Claude Desktop 在
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vps_server": {
      "command": "/opt/vps_server/.venv/bin/python",
      "args": ["mcp_server.py"],
      "cwd": "/opt/vps_server",
      "env": {
        "PYTHONPATH": "/opt/vps_server"
      }
    }
  }
}
```

> 如果 MCP 客户端跑在你笔电,worker-loop 跑在远程服务器,
> 中间需要 SSH 隧道或自己包一层 SSE/HTTP MCP transport。
> 本项目当前只实现 stdio,要远程跑需要额外封装(本教程范围外)。

---

## 10. 常见问题排查

### Q1 `systemctl start` 后立刻 fail

```bash
sudo journalctl -u vps-server-worker -n 50 --no-pager
```

最常见原因:
- `ENCRYPTION_KEY` 没填或格式错 → `cryptography.fernet.InvalidKey`
- `WorkingDirectory` 路径错 → `ChdirError`
- `.env` 文件权限不让 `vps_server` 账号读 → `chmod 644 .env`(密钥别 chmod 777)
- `.venv` 不存在 → 业务账号没跑 `uv sync`

### Q2 systemctl status 显示 active 但 worker 没干活

- 看 journalctl 是否一直只输出 "worker-loop 启动" 后就静默 → 正常(没 task 时 idle 轮询不打日志)
- 查 DB 里 `vps_task` 有没有 pending 任务 → 没的话 worker 当然没活

### Q3 频繁崩溃 (5 分钟 5 次后挂了)

```bash
sudo systemctl reset-failed vps-server-worker     # 清掉 "崩太多次放弃" 状态
sudo journalctl -u vps-server-worker -n 200       # 看具体崩在哪
# 修代码 / 修配置后:
sudo systemctl start vps-server-worker
```

### Q4 怎么知道 SIGTERM 真的优雅退出了

```bash
sudo systemctl stop vps-server-worker
sudo journalctl -u vps-server-worker -n 5
```
应看到末尾两行:
```
收到信号 15, 准备优雅退出
main.worker-loop 已退出
```
如果只有"收到信号"没有"已退出" → 当前轮可能卡住了,等 `TimeoutStopSec` 触发强杀。

---

## 11. 不进 systemd 的轻量替代

不想用 systemd (开发机 / Docker 里跑) 也有选择:

| 方案 | 命令 | 适合 |
|---|---|---|
| nohup | `nohup uv run python main.py worker-loop > worker.log 2>&1 &` | 临时跑跑,机器一重启就丢 |
| tmux/screen | `tmux new -s vps; uv run python main.py worker-loop` | 开发期手动盯着看 |
| supervisor | 配 `[program:vps-server-worker]` | 不想用 systemd 但想要自启 |
| docker | 进 container 里跑 `main.py worker-loop` | 容器化部署,健康检查靠 docker 自身 |

systemd 是 Linux 服务器最稳的选,本教程主推这个。其他方案不在本文展开。
