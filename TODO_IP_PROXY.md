# 代办：IP 业务

> 此文件暂存 IP 业务的设计意图、流程、待加工具。
> Proxy 作为独立业务流已**取消**——端口绑定（原 deploy_proxy 的事）合并到 rgIP 流程里。
> 业务全跑通后，回头敲定数据表，再删除本文件。

---

## 当前对齐快照（2026-06-05）

**业务流只剩两条**：`rgvps`（已闭环）+ `rgip`（在做）。

### rgIP 业务流程（最终版）

入参字段：`provider_domain / entry_host / entry_port / username / password / protocol / egress_ip / region / expire_date`

```
① 查重：egress_ip 已存在？
   - 未过期 → return duplicate
   - 已过期 + --renew → 更新 expire_date 后返回 ok
   - 已过期 + 无 --renew → return expired_exists
   - 不存在 → 继续

② 挑 VPS：未过期 + 还有闲置端口（18441-18450）的一台
   - 没有 → return no_available_vps

③ 在该 VPS 上找一个空闲端口

④ SSH 连进 VPS（VPSSession，复用 core）

⑤ 改 xray config：加 inbound(vps_port) + outbound(走这条代理) + routing
   - 写入 /usr/local/etc/xray/config.json
   - reload xray

⑥ 内 ping：VPS 内 curl --socks5 localhost:vps_port
   - 不通 → 回滚 xray 配置，return failed（账密/入口可能错）

⑦ 校验 egress_ip：内 ping 返回 body 是不是用户填的 egress_ip
   - 不匹配 → 回滚 xray 配置，return egress_mismatch

⑧ 入事实表（端口绑定记录，详见下文数据结构方向）

⑨ 外 ping：本机用 requests 走 vps_ip:vps_port
   - 通 → 标记可达 + return ok
   - 不通 → 尝试 core.firewall.open_tcp_port_range 开 VPS 本地防火墙 → 重测
     - 通 → ok
     - 还不通 → 标记不可达 + return ok_security_group_blocked
       （提示用户去云控制台开端口；提醒里的端口跟着 config.py 的范围走）
```

### 关键设计原则

- **业务函数走函数链路，不抽 Manager**（YAGNI；只有 rgIP 一个业务，没必要抽类）
- **业务跑通了再敲表**（不凭空设计字段）
- **唯一存在的对象是 VPSSession**（来自 core，复用 VPS 阶段成果）
- **客户端连我们部署的代理需要 auth**（业务层生成账密，纯工具只做拼装）

### 部署成功后要返回的信息（提前记录，落库时用）

- 连接协议（默认 socks5）
- VPS IP / 部署端口
- 客户端 auth 账号 / 密码
- vmess / vless 订阅链接（如未来扩展）

### 数据结构方向（**先不动**，业务跑通再回来敲）

```
VPS 表：增加端口计数字段（闲置数 / 过期数）
        - 可用总数固定 10（端口范围 18441-18450），不存
        - rgIP 写事实表后反向 -1 更新闲置数

IP 表：只放 IP 凭据
        (provider_domain / entry_host / entry_port / username /
         password_encrypted / protocol / egress_ip / region / expire_date)

事实表（端口绑定记录）：
  字段：vps_id / vps_port / inbound_protocol / inbound_user / inbound_pwd_encrypted /
        ip_id / status('using' / 'expired') / 创建时间
  约束：UNIQUE(vps_id, vps_port) + UNIQUE(ip_id)（1:1）
  规模：每台 VPS 最多 10 条，固定（即使端口用满 / 过期，也不新增行；
        过期就改 status='expired'，等下一条 IP 顶上时复用该 vps_port 的行）
```

### 巡检模块（**留待下一轮**）

- 定时扫 VPS / IP 的 expire_date → 更新各表 is_expired 标记
- 触发续费提醒（按 provider_domain 分组）
- 同步事实表 status：IP 到期 → status='expired' → VPS 表过期端口数 +1
- 这一轮不做

### CLI 命名（保留）

| CLI 子命令 | 业务函数 | 状态 |
|-----------|---------|------|
| `rgvps` | `register_vps` | ✅ 已实现 |
| `rgip` | `register_ip` | ⬜ 在做 |
| `rgip --renew` | 同上的续费分支 | ⬜ 在做 |

---

## 工具清单（造工具 → 写业务 → 入库 的顺序）

| # | 工具 | 位置 | 类型 | 状态 |
|---|------|------|------|------|
| 1 | `build_proxy_outbound(host, port, user, pwd, protocol)` | `ip/atom.py` | 纯函数 | ✅ 已做（commit `4fa50ce`） |
| 2 | `build_deploy_config(vps_port, proxy_outbound, inbound_user, inbound_pwd)` | `ip/atom.py` | 纯函数 | ⬜ 下一个 |
| 3 | `reload(client)` | `xray/atom.py` | SSH 操作 | ⬜ 待做 |
| 4 | `get_used_ports(client, start, end)` | `core/ports.py`（新建） | SSH 操作 | ⬜ 待做 |

---

## 已废弃的旧业务全景（仅留作历史参考）

```
旧设计（已废）                  当前设计
─────────────                  ─────────
VPS 业务 ✅                     VPS 业务 ✅
IP 业务（仅入库 IP）            IP 业务（入库 IP + 直接部署到 VPS 端口）
Proxy 业务（再部署端口）        ✗ 取消，合并进 IP 业务
```

---
