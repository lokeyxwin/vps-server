# vps-server

一个**给个人/小团队**用的 VPS 资产管理 + 代理出口自动化工具。

核心解决一个真实痛点：
你手上有一堆 VPS 服务器，想把它们**统一纳管 + 自动跑起 xray**，再用代理服务商给的 IP 通过这些 VPS **做成可用的代理出口池**——但全程不想手动 SSH 登录每台机器一条命令一条命令敲。

> 现阶段：**VPS 这一边的能力已经完整闭环**。IP / Proxy 两个领域留了占位，规划写在 `TODO_IP_PROXY.md`。

---

## 目前能做什么（VPS 全部能力）

### 一键注册 + 自动备好 xray

```bash
uv run python main.py rgvps --ip 1.2.3.4 --user root --pwd 'xxx' --port 22 --ed 2026-12-31
```

这一条命令背后做了 11 件事：

1. **查重**——同一个 IP 不会重复入库
2. **SSH 测连**——验证 IP / 端口 / 用户名 / 密码都正确
3. **采集系统信息**——拿到操作系统名和版本（CentOS / Ubuntu / …）
4. **密码加密落盘**——用 Fernet 对称加密存进数据库，数据库直接读出来是密文 bytes
5. **基础字段入库**——VPS 这条记录就有了
6. **检测 xray 是否已装**——已装跳过装机；没装跑官方一键脚本
7. **写默认 xray config**——如果 config 是空的（很多 x-ui 类面板装完是空的），自动写一份「监听 18440 socks 直出」的最小可用配置
8. **`systemctl start xray`** —— 服务跑起来
9. **`systemctl enable xray`** —— 开机自启
10. **服务器本地防火墙开放 18440-18450/tcp**——自动识别 firewalld / ufw 调用对应命令
11. **连通性双向验证**：
    - 内部 ping：服务器上 `curl --socks5 127.0.0.1:18440` 验证 xray 自身在响应
    - 外部 ping：从本机 `requests` 走 socks5 → VPS:18440 验证云安全组放行

跑完后数据库里这台 VPS 的状态字段全部填好（`xray_status='running'` + 版本号 + 时间戳 + 操作日志），可用 Navicat 等任意 SQLite 工具查看。

### 已装 xray 的纳管（imported 路径）

很多 VPS 你以前已经手动装过 xray 在生产用。直接 `rgvps` 同样能纳管：
- 检测到已装 → **跳过安装命令**，不会破坏现有二进制
- xray_status_message 自动标记「**已在服务器上安装，本次仅纳管同步**」，跟全新装区分
- 返回的 status 是 `imported` 而非 `ok`

### xray 单独操作（已注册 VPS 的运维入口）

```bash
uv run python main.py xrayinit --ip 1.2.3.4
```

不重新注册，只跑「确保 xray 装好并跑起来」流程。常用场景：
- xray 服务挂了（stopped / inactive）→ 业务自动重启
- xray 没装 → 装上
- config 被人误删 → 写默认 config 重新拉起

### 错误分类与排查提示

连接出错时**不只是"失败了"**——程序识别 4 种具体场景并给出可执行的排查清单：

| 错误类型 | 触发场景 | 提示给用户的诊断 |
|---------|---------|----------------|
| `auth_failed` | 用户名或密码错 | 密码源给错（控制台 ≠ SSH）/ OCR 易错 o0Il1 / 服务器禁用了密码登录 |
| `timeout` | TCP 包发出无应答 | 云安全组未放行 / 服务器本地防火墙 DROP / IP 不可达 |
| `refused` | 包到了但没人监听 | SSH 服务没起 / 端口号错 / 防火墙 REJECT |
| `failed` | 其他未分类错误 | 看日志 |

xray 失败也有 4 种细分：`install_failed` / `verify_failed` / `service_not_active` / `enable_failed`，每个都附**具体修复命令**（如 `systemctl status xray --no-pager -l`）。

### 业务流程可观察

控制台用**分层日志**告诉你每一步发生了什么：

```
──────────────────────────────────────────
HH:MM:SS ▶ services.vps_register: register_vps 开始 ip=…
HH:MM:SS [INFO] core.ssh: 尝试连接 root@…
HH:MM:SS [INFO] paramiko.transport: Authentication successful!
HH:MM:SS [INFO] core.ssh: 连接成功 root@…
──────────────────────────────────────────
HH:MM:SS ▶ services.vps_install_xray: install_xray_on_vps 开始 ip=…
HH:MM:SS [INFO] xray.manager: 检测到 xray 已装 version=Xray 26.x …
HH:MM:SS [INFO] xray.manager: xray config 为空，写入默认 config（监听 18440 直出）
HH:MM:SS [INFO] xray.manager: xray 服务未 active，尝试 systemctl start xray
──────────────────────────────────────────
HH:MM:SS ▶ services.vps_install_xray: 内部 ping ok=True body=…
HH:MM:SS [INFO] core.proxy_check: 外部 socks5 通 → 出口 IP=…
──────────────────────────────────────────
HH:MM:SS ▶ services.vps_install_xray: 完成 status=imported version=… actions=…
HH:MM:SS ▶ services.vps_register: register_vps 全流程成功 xray=imported
```

`▶` 是业务边界，`[INFO]/[WARNING]` 是原子事件。每个业务被一对分隔线框起来，扫一眼就能数出几个业务跑了、各走到哪一步。

---

## 未来规划：IP / Proxy

**只剩两个领域要做**（详细设计见 `TODO_IP_PROXY.md`）：

- **IP 业务**：注册代理服务商给的 IP → 借一台已装 xray 的 VPS 当测试机做联通验证 → 通过就入 IP 表
- **Proxy 业务**：自动从 IP 表 + VPS 表凑料 → 在某台 VPS 的 18441-18450 某个端口配 xray 出口 → 内外联通测试 → 入 Proxy 表，形成可用的代理出口池

VPS 这边已经搭好了一切基础设施（SSH 会话、SFTP、防火墙、socks5 探活、xray 全生命周期管理），IP/Proxy 业务可以直接复用，**理论上写几个业务函数就行**。

---

## 项目结构

```
config.py / log.py / .env             全局配置与日志
core/                                  通用基础设施
  ssh.py / session.py                  SSH 协议 + VPSSession 会话类
  security.py                          Fernet 加解密
  firewall.py / proxy_check.py         防火墙 + 本机 socks 探测
db/                                    数据持久化（SQLAlchemy）
  models.py                            VPSRecord（含 xray 5 字段生命周期）
xray/                                  xray 软件管理
  atom.py / manager.py                 atom 函数 + XrayManager（含 ensure_installed_and_running 高层方法）
services/                              业务编排
  vps_register.py                      注册 + 装 xray 全流程
  vps_install_xray.py                  单独装/重装 xray
ip/, proxy/                            未来 IP / Proxy 领域占位
test/                                  108 个测试，全 mock 可跑
```

数据库一行命令切换：`.env` 里 `DB_TYPE=sqlite`（开发）或 `mysql`（生产），业务代码零改动。

---

## 怎么开始

```bash
# 1. 安装依赖
uv sync

# 2. 生成加密密钥写到 .env
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())" >> .env

# 3. 跑测试确认环境 OK（108 个全过）
uv run python -m unittest discover -s test -p "test_*.py"

# 4. 注册第一台 VPS
uv run python main.py rgvps --ip <ip> --user root --pwd '<pwd>' --port 22

# 5. 用 Navicat / DB Browser 打开 db/vps_server.db 看入库结果
```

帮助：
```bash
uv run python main.py --help
uv run python main.py rgvps --help
uv run python main.py xrayinit --help
```

---

## 设计原则

整个项目坚持**自下而上分层**：

```
入口（CLI / 未来 MCP / Web）
   ↓
业务层（services/）  —— 编排+决策，不直接动 SSH/DB 细节
   ↓
领域类（XrayManager / VPSSession）  —— 封装单领域生命周期
   ↓
原子函数（core / xray atom）  —— 单一动作，无状态
   ↓
基础设施（DB / 加密 / 日志 / 第三方库）
```

每层只依赖下一层。**业务函数永远在 services/，CLI 永远在 main.py，原子永远只跟它的依赖打交道**。

---

## 测试覆盖

- 108 个 mock 测试覆盖所有业务分支
- 6 个真服务器测试（默认跳过；配置 `VPS_TEST_IP/USER/PASSWORD/PORT` 环境变量触发）
- 关键安全场景有专门验证：密码落盘确实是密文（绕过 ORM 用原生 SQL 检查）/ `__repr__` 不泄露密码 / 错密钥解密失败 / 多并发不重复装

```bash
# 跑所有 mock 测试
uv run python -m unittest discover -s test -p "test_*.py"

# 带真服务器跑（小心，会真去 SSH）
VPS_TEST_IP=x.x.x.x VPS_TEST_USER=root VPS_TEST_PASSWORD='...' \
  uv run python -m unittest discover -s test -p "test_*.py"
```
