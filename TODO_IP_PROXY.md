# 代办：IP 业务 & Proxy 业务

> 此文件用于暂存 IP 和 Proxy 两个业务的设计意图、流程、待加工具、待决策问题。
> 当前阶段：**先把 VPS 业务做完，再回头处理这里的内容**。
> VPS 业务全部完成后，按本文件继续推进。完成后删除本文件。

---

## CLI 命名约定

main.py 子命令统一用「动作缩写 + 对象」的短命名：

| CLI 子命令 | 业务函数 | 状态 |
|-----------|---------|------|
| `rgvps` | `register_vps` | ✅ 已实现 |
| `rgip` | `register_ip` | ⬜ 待做（本 TODO 范围内） |
| `deploy-proxy`（或 `dpproxy`） | `deploy_proxy` | ⬜ 待做 |

**注**：CLI 命令短（方便手敲），Python 函数名长（保持描述性）。两者各取所需，不强制一致。

---

## 业务全景速记

```
VPS 业务（已在推进）           IP 业务（待做）              Proxy 业务（待做）
─────────────────              ──────────────              ──────────────
register_vps  ✅              register_ip                  deploy_proxy
update_vps                    delete_ip                    delete_proxy
install_xray  ← VPS 业务终点
delete_vps

数据流：
  VPS（装好 xray） → 喂给 IP 业务做测试机 → 攒出 IP 表 → 喂给 Proxy 业务做部署
```

---

## IP 业务

### 用户原话（业务意图）

> ip 传入的是代理，它依赖 vps 的安装软件后业务完成后的服务器，跟 vps 的逻辑差不多，但是写入的是另一个表，把代理挂到服务器 xray 核心的某个测试端口测试代理 ip 的联通性，代理能够正常使用就入库。

### 业务流程拆解

```
register_ip(代理_ip, 代理_port, 代理_user, 代理_pwd, 代理_protocol):
  ① 查重（IP 表里有没有这条代理）
  ② 从 VPS 表挑一台「已装 xray」的服务器当测试机
  ③ 给那台 VPS 的 xray 增加一个临时测试出站，绑到测试端口
  ④ SSH 到 VPS 内部 curl 测试端口 → 看返回的 IP 是不是这条代理的 IP
  ⑤ 通过 → 清理测试配置 → 入 IP 表
  ⑥ 失败 → 清理测试配置 → 返回 failed
```

### 需要的新东西

| 名称 | 归属 | 说明 |
|------|------|------|
| `xray.py` 模块 | 根目录 | 纯函数：处理 xray 的 config.json，add_outbound/add_inbound/remove_by_tag |
| IP 表模型 | `db/models.py` | 字段：id / 代理_ip / 代理_port / 代理_user / 代理_pwd(加密) / 代理_protocol / status(unbound/bound) / 创建时间 |
| `ip_service.py` | `services/` | register_ip + delete_ip |

复用现有：vps.py 的 execute_command（curl 测试 + reload xray）+ 待加的 upload_file（传配置）

---

## Proxy 业务

### 用户原话（业务意图）

> 最后 proxy 的业务就是从 ip 中拿一条没有绑定的 ip，从 vps 中拿一台 18441-18450 中端口没被占用完的服务器找一个端口配置代理出口然后测试联通性内部和外部都测。内部通表示配置没问题，外部不通表示安全策略没开放。

### 业务流程拆解

```
deploy_proxy():
  ① 从 IP 表挑一条 status='unbound' 的代理
  ② 从 VPS 表挑一台 'xray_installed=True' 的 VPS
  ③ 查这台 VPS 在 18441-18450 哪些端口被占用了
  ④ 选一个空闲端口
  ⑤ 把代理作为出站、本地端口作为入站，写进 xray 配置 → 上传 → reload
  ⑥ 内部测试：SSH 到 VPS，跑 curl --proxy localhost:出口端口 https://api.ipify.org
  ⑦ 外部测试：从本机（Python requests）跑 --proxy vps_ip:出口端口
  ⑧ 内外都通 → 入 Proxy 表 + IP 表那条标记为 bound
  ⑨ 内通外不通 → 返回「安全策略未开放，建议放行此端口」
  ⑩ 都不通 → 配置失败，回滚 xray 配置
```

### 关键约束

- 端口范围固定：**18441-18450**（VPS 上每台最多 10 个出口）
- 必须**内外都通**才算成功
- 失败要**回滚配置**，不能污染 xray

### 需要的新东西

| 名称 | 归属 | 说明 |
|------|------|------|
| `get_used_ports(client, port_range)` | `vps.py` 新工具 | 在 VPS 跑 `ss -tlnp` 解析输出，返回被占用端口集合 |
| `test_proxy(proxy_url, test_url)` | `proxy.py` 新工具 | **本机**用 requests 走代理发请求，看能不能返回 |
| Proxy 表模型 | `db/models.py` | 字段：id / ip_id(外键→IP表) / vps_id(外键→VPS表) / 出口端口 / status(active/inactive) / 创建时间 |
| `proxy_service.py` | `services/` | deploy_proxy + delete_proxy |

---

## VPS 工具层：服务于 IP/Proxy 的新工具

虽然属于 VPS 工具层，但**只有 IP/Proxy 业务用到**，记在这里：

| 工具 | 用途 | 给谁用 |
|------|------|--------|
| `upload_file(client, local, remote)` | SFTP 传文件（xray 配置） | install_xray / IP / Proxy |
| `download_file(client, remote, local)` | SFTP 下载（可选） | 增量修改 xray 配置时用 |
| `get_used_ports(client, port_range)` | 查端口占用 | Proxy 业务 |

---

## 待决策问题（影响 IP/Proxy 工具与流程设计）

回答这些后才能开始动手。

### Q1：用 xray 还是 v2ray？
两者配置格式有差异，xray.py 的 JSON 操作需要按选定的核心写。

- [ ] **回答**：

### Q2：xray 的配置文件路径？
默认是 `/usr/local/etc/xray/config.json`。是否沿用默认？

- [ ] **回答**：

### Q3：联通性测试用什么 URL？
- `https://api.ipify.org`：返回出口 IP，最准（能直接对比是不是这条代理的 IP）
- `https://www.google.com`：只看通不通，不能验证出口

- [ ] **回答**：

### Q4：本机测试代理是否引入 `requests` 依赖？
- `requests`：API 友好，5 行代码搞定（推荐）
- `urllib`：标准库，无需新依赖但代码冗长

- [ ] **回答**：

### Q5：xray 配置修改用「完整覆盖」还是「增量修改」？
- **完整覆盖**：每次都生成完整 config.json 上传，简单粗暴但安全
- **增量修改**：下载现配 → 改 → 上传，优雅但有并发风险

- [ ] **回答**：

### Q6：VPS 表是否加 `xray_installed` 字段？
IP/Proxy 业务都要挑「已装 xray」的 VPS。如何标记？
- 加 `xray_installed: bool` 字段
- 或用 `status` 字段加枚举值（registered / xray_installed / disabled）
- 或建独立的「VPS 能力表」

- [ ] **回答**：

### Q7（待补）：proxy 失败时的回滚策略
内通外不通时，要不要保留配置等用户手动开端口？还是自动回滚？

- [ ] **回答**：

### Q8（待补）：测试机选取策略
注册 IP 时，从「已装 xray」的 VPS 里挑哪台？随机？最少负载？固定一台？

- [ ] **回答**：

---

## 推进顺序（VPS 完工后参考）

1. 回答完上面所有 Q
2. 根据答案决定加哪些字段、走哪种方案
3. 顺序：
   - 加 `upload_file` 到 vps.py + 测试
   - 写 `xray.py` 模块 + 单元测试（纯函数最易测）
   - 建 IP 表模型 + 测试
   - 写 `ip_service.register_ip` + 测试
   - 加 `get_used_ports` 到 vps.py + 测试
   - 写 `proxy.py` 的 `test_proxy` + 测试
   - 建 Proxy 表模型 + 测试
   - 写 `proxy_service.deploy_proxy` + 测试
   - 全跑通后，更新 main.py 加入 IP/Proxy 路由
