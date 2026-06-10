# Windows 守护安装 — NSSM

> Windows 没 systemd 也没 launchd, 用 [NSSM](https://nssm.cc/) (Non-Sucking
> Service Manager) 把 Python 进程包成 Windows 服务最简. Windows 自带的
> `sc.exe` 也能做, 但配置文件不友好, NSSM 是社区共识.

---

## 0. 装 NSSM

```powershell
# 方式 A: Chocolatey 一键
choco install nssm

# 方式 B: 官网下载 + 加 PATH
# 1. https://nssm.cc/download → 下 zip → 解压到 C:\nssm
# 2. 把 C:\nssm\win64 加进系统 PATH
# 3. 新开 PowerShell, 跑 nssm --version 确认能用
```

---

## 1. 守护 main.py worker-loop (必装)

```powershell
# 设变量 (按你实际路径改)
$ProjectRoot = "C:\Path\To\vps_server"
$PythonExe   = "$ProjectRoot\.venv\Scripts\python.exe"

# 注册服务
nssm install vps-server-worker $PythonExe "main.py worker-loop"
nssm set vps-server-worker AppDirectory          $ProjectRoot
nssm set vps-server-worker AppEnvironmentExtra   "PYTHONPATH=$ProjectRoot"
nssm set vps-server-worker AppStdout             "$ProjectRoot\logs\worker.log"
nssm set vps-server-worker AppStderr             "$ProjectRoot\logs\worker.err"
nssm set vps-server-worker Start                 SERVICE_AUTO_START      # 开机自启
nssm set vps-server-worker AppExit Default       Restart                  # 崩了自动重启
nssm set vps-server-worker AppRestartDelay       5000                     # 5 秒退避
nssm set vps-server-worker AppStopMethodConsole  120000                   # 优雅停: 给 120s
nssm set vps-server-worker Description           "vps-server worker-loop"

# 拉起
nssm start vps-server-worker

# 验证
nssm status vps-server-worker                                              # SERVICE_RUNNING
Get-Content "$ProjectRoot\logs\worker.log" -Tail 50 -Wait
```

---

## 2. 守护 HTTP MCP server (用 OpenClaw / Codex 才装)

> ⚠️ 当前 `mcp_server.py` 是 stdio 实现, HTTP 模式需要后续改造.
> 本节给目标命令, 改造任务落地后直接用.

### 2.1 admin 壳 (47180)

```powershell
nssm install vps-server-mcp-admin $PythonExe "mcp_server.py --role admin --http"
nssm set vps-server-mcp-admin AppDirectory         $ProjectRoot
nssm set vps-server-mcp-admin AppEnvironmentExtra  "PYTHONPATH=$ProjectRoot"
nssm set vps-server-mcp-admin AppStdout            "$ProjectRoot\logs\mcp-admin.log"
nssm set vps-server-mcp-admin AppStderr            "$ProjectRoot\logs\mcp-admin.err"
nssm set vps-server-mcp-admin Start                SERVICE_AUTO_START
nssm set vps-server-mcp-admin AppExit Default      Restart
nssm set vps-server-mcp-admin AppRestartDelay      5000
nssm set vps-server-mcp-admin Description          "vps-server MCP admin (47180)"

nssm start vps-server-mcp-admin
```

### 2.2 user 壳 (47181)

```powershell
nssm install vps-server-mcp-user $PythonExe "mcp_server.py --role user --http"
nssm set vps-server-mcp-user AppDirectory          $ProjectRoot
nssm set vps-server-mcp-user AppEnvironmentExtra   "PYTHONPATH=$ProjectRoot"
nssm set vps-server-mcp-user AppStdout             "$ProjectRoot\logs\mcp-user.log"
nssm set vps-server-mcp-user AppStderr             "$ProjectRoot\logs\mcp-user.err"
nssm set vps-server-mcp-user Start                 SERVICE_AUTO_START
nssm set vps-server-mcp-user AppExit Default       Restart
nssm set vps-server-mcp-user AppRestartDelay       5000
nssm set vps-server-mcp-user Description           "vps-server MCP user (47181)"

nssm start vps-server-mcp-user
```

---

## 3. 日常运维

```powershell
# 看所有 vps-server 服务状态
Get-Service vps-server-*

# 重启 (代码改了 / config 改了)
nssm restart vps-server-worker

# 看日志 (实时)
Get-Content "$ProjectRoot\logs\worker.log" -Tail 50 -Wait

# 停
nssm stop vps-server-worker

# 卸 (服务 + NSSM 注册都清掉)
nssm stop vps-server-worker
nssm remove vps-server-worker confirm
```

---

## 4. 优雅退出说明

Windows 没 POSIX SIGTERM, NSSM 用以下顺序模拟优雅停:

```
1. 发 Ctrl+C / Ctrl+Break  →  我们 main.py 的 SIGINT handler 接住, 置 _stop=True
2. 等 AppStopMethodConsole 毫秒数 (上面设的 120000 = 120 秒)
3. 超时强杀 (TerminateProcess)
```

跟 Linux systemd `KillSignal=SIGTERM + TimeoutStopSec=120` 等效.

---

## 5. 常见问题

| 现象 | 排查 |
|---|---|
| `nssm install` 弹 GUI | NSSM 默认 GUI 模式, 走命令行加足参数即可 (上面示例就是) |
| 服务装上但不跑 | `nssm status xxx`, 看 `nssm get xxx AppDirectory` / `nssm get xxx Application` 路径有没有错 |
| 启动立刻 fail | 看 `AppStderr` 路径下的 `.err` 日志, 常见: PYTHONPATH 没设 / .venv 路径错 / `ENCRYPTION_KEY` 没填 |
| 日志没出来 | `logs\` 目录可能不存在, 手动 `mkdir logs` |
| `Get-Content -Wait` 卡住 | 文件还没创建 → 等服务真启动后再 tail |

---

## 6. 替代方案 (不用 NSSM)

如果不想装 NSSM, 也可以用:

- **Windows Task Scheduler**: GUI 设"开机触发 + 失败重试", 但优雅退出和日志麻烦
- **WinSW** ([github.com/winsw/winsw](https://github.com/winsw/winsw)): NSSM 替代, XML 配置, 跟 systemd 风格更近
- **Docker Desktop**: 把整个项目装容器里, Windows 通过 Docker 拉起 (跨平台一致性最好, 但要装 Docker)

简单优先选 NSSM, 团队多人维护选 WinSW 或 Docker.
