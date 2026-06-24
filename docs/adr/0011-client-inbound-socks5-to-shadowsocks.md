# 0011. 对外客户端 inbound 协议 socks5 → Shadowsocks

**日期**: 2026-06-24
**状态**: Accepted

---

## Supersedes / 补充

- 补充 + 局部 supersede [[0006-proxy-deploy-worker]]
  → §决策 §1 "对外暴露带账密 socks5 inbound" 改为 Shadowsocks inbound
  → §决策 §8 收尾流程的内/外 ping 验证方式(原 socks5 单一通道 → 按协议分 socks5/SS 两套)
- 补充 [[0007-mcp-tools-naming-and-conventions]] / [[0008-main-as-worker-runner-and-db-queries-home]]
  → 节点返回 dict 加 `method` + `share_link` 字段
- **不动**(本 ADR 不触及):
  - [[0002-takeover-mode-handled-by-xray-worker]] / [[0003]] / [[0004]] 纳管(纳管支持 SS 列 later 单独 issue)
  - [[0004]] §3「直进直出」判定 + §6 默认入口 18440(仍 socks5 noauth→freedom,内部组件)

> 注: 被本 ADR 补充的旧 ADR 本身不动(永不改原则); 当下真相以本 ADR + spec.md 为准。

---

## 背景

自建 socks5 节点导入小火箭(Shadowrocket, iOS)正常,但用小火箭分享二维码给安卓
v2rayNG 导入不进去。

根因: socks5 是裸协议, **没有统一的分享 URI 标准**。小火箭和 v2rayNG 各用私有
`socks://` 编码, 互不相认。不是 bug, 是协议天生短板。

用户已**实测验证**: 换成 Shadowsocks(SIP002 `ss://` 标准) 后, 安卓 v2rayNG 能正常
导入并使用; 且节点导入小火箭后, 小火箭再分享出来的 SS 码也是标准格式, v2rayNG 同样
能扫 —— "两种导入方式"一并满足。

存量 socks5 节点已通过一次性手动运维升级为 SS(T 之外的运维动作, 已完成)。本 ADR
处理**代码侧**: 让 ProxyDeployWorker 以后新部署的对外节点直接是 SS, 并把相关模型 /
验证 / 对外返回一起对齐。

边界(三处 socks5 只动一处):
- ① 对外 inbound(给客户端连) ← 本 ADR 改 SS
- ② 上游 outbound(连上游 IP 的跳板) ← 不动(上游就是 socks5)
- ③ 默认入口 18440(noauth socks→freedom) ← 不动(内部组件)

---

## 决策

### 1. 对外客户端 inbound 协议 socks5 → shadowsocks

ProxyDeployWorker 部署的对外 inbound, 协议从 socks5 改 Shadowsocks。
**理由**: SS 有 SIP002 `ss://` 跨客户端标准, 小火箭 / v2rayNG / Clash 全系扫码都通;
裸 socks5 是明文且无统一分享格式。

### 2. 加密方式 aes-256-gcm

**理由**: 兼容性最好(所有现代 SS 客户端都支持), 不需要域名 / 证书 / TLS。
SS2022 老客户端支持差, 跟"最大兼容"目标冲突, 排除。
- password 沿用部署时生成的随机串(原 `inbound_pwd = uuid4().hex`)
- SS **没有 username 概念**, 原 `inbound_user` 字段保留但 SS 节点不使用

### 3. proxy_record 加 method 字段 + ProxyProtocol 常量类

- `db/models.py` 新增 `ProxyProtocol` 常量类(`SOCKS5="socks5"` / `SHADOWSOCKS="shadowsocks"`),
  跟 `ProxyStatus` 同款常量类风格(非 Enum)
- `ProxyRecord` 加 `method: str`(SS 加密方式, socks5 节点留空)
- `from_new_deployment` 加 `method` 参数, protocol 显式传 `shadowsocks`

**理由**: 光记 protocol 不够, SS 还要存加密方式才能拼出 `ss://`。

### 4. 连通性测试拆两套类

- `Socks5Probe`: 现有 socks5 内/外 ping 逻辑**原样封装成类, 行为不变**。
  用户: IPProbeWorker 测上游 + 纳管(later)
- `ShadowsocksProbe`: **新增**, 给 ProxyDeployWorker 部署 SS 后验证

**理由**: 上游与纳管仍是 socks5, 对外是 SS, 两类长期共存, 各自封装一个类最清晰。

### 5. SS 验证机制 —— 内 ping 临时 xray 端到端, 外 ping TCP 可达

- **内 ping**(目标 VPS 本机): 起一个**临时 xray 实例**(socks-in → ss-out 连
  `127.0.0.1:vps_port`), curl 临时 socks 口走通 → 拿出口 IP, 测完 kill 临时实例 + 删临时配置。
  完整验证 SS 握手 + 加密 + 密码 + 上游出口。
- **外 ping**(worker 本机): **TCP 端口可达测**(`socket.connect(vps_ip, vps_port)`),
  不拉任何核心。

**理由**: 内 ping 已端到端验透 SS 协议层; 外部唯一没覆盖的是"公网到端口"= 安全组放行,
而 SS 跑在 TCP 上, 外部 TCP 能连上 = 安全组放行了。两者合 = 客户端必能用。外 ping 不拉
核心 → 不与 worker 本机其他进程(如小火箭)冲突, 也最轻。

### 6. 上游 outbound + 默认入口不动

- 上游 outbound(`build_proxy_outbound`) 仍 socks5 —— 上游就是 socks5, 与客户端无关
- 默认入口 18440(socks noauth→freedom) 仍 socks5 —— 内部组件, 不对外, ADR-0004 §3/§6 不变

### 7. IP 入库前测上游继续 socks5; 纳管读 SS 账密 later

- IPProbeWorker 测上游用 `Socks5Probe`(测的是上游 socks5 能不能用, 不复杂化)
- 纳管的 `_parse_outbounds_from_config` 读 `accounts`(socks5 形态), SS 节点没 accounts →
  重装 / 纳管自己挂的 SS 机时会读空。**列 later 单独 issue**, 本批不做。

### 8. MCP 节点返回加 method + 自产 ss:// share_link

- `db/queries.py` 的 `_build_proxy_node` / `list_available_proxies` 返回加 `method` + `share_link`
- 新增 `toolbox/share_link.py::build_ss_url(method, password, host, port, tag)` 纯函数(SIP002)
- 二维码不在后端生成, 只返 `share_link` 文本, 渲染交客户端

**理由**: 项目掌握节点全信息, 直接吐标准 `ss://`, agent / 用户一次拿全, 不依赖任何
单个客户端的私有分享格式。

---

## 备选方案

### 协议: VMess / VLESS / Trojan(被否决)

- VMess: V2Ray 母语, 互通也稳, 但字段多(uuid/alterId/加密), 改动比 SS 大, 对"自建简单
  分享"场景无额外收益
- VLESS: 裸跑等于明文(安全无提升), 鼓励配 TLS/Reality 才有意义, 当前不划算
- Trojan: 强制 TLS + 域名 + 证书, 对快速自建分享过重
- **结论**: SS 改动最小 + 不需要证书 + SIP002 真跨端标准 + 自带加密, 最贴合需求

### 外 ping: 完整外部 SS 验证(被否决为默认)

- 方案 a(worker 本机起临时 SS 客户端完整测): 需在 worker 本机拉核心, 与小火箭等进程
  可能冲突
- 方案 b(借测试 VPS 从公网完整 SS 验证): 真外部 + 完整, 但要 SSH 测试机 + 起临时核心 +
  清理, 重
- **结论**: 选 TCP 可达测 —— 配合内 ping 逻辑上已闭环, 最轻, 不碰本机。完整外部验证
  作为未来"想要更强保证"时的可选增强, 本 ADR 不做。

### 纳管顺带升级 socks5→SS(被否决)

- 把"接管"和"改协议"耦合, 重新引入断生产风险, 违反 ADR-0002 §2 不断生产 + 单一职责
- 以后自己挂的本来就是 SS, 纳管撞 socks5 概率极低; 真撞到原样接管最安全
- **结论**: 纳管保持不动, 存量靠一次性运维清理(已完成)

---

## 后果

### 好处

- 对外节点跨客户端分享彻底通(SIP002 标准), 小火箭 / v2rayNG / Clash 都能扫
- 裸 socks5 明文 → SS 加密, 顺带补安全短板
- 纳管 / 端口策略 / 挑机 / 锁 / task 状态机全不动, 改动面收敛在"对外协议 + 验证 + 返回"
- 内 ping 端到端 + 外 ping TCP, 验证不碰 worker 本机, 最轻

### 引入的新约束

- `proxy_record` 多一列 `method`(dev SQLite: `ALTER TABLE ADD COLUMN`; 生产存量已手动加)
- 连通测试从"函数"变"两个类", 调用方(ProxyDeployWorker / IPProbeWorker)跟着改调用形态
- ProxyDeployWorker 内 ping 依赖目标 VPS 能起临时 xray 实例(VPS 本来就装了 xray, 成本低)
- `apply_proxy_binding` / `add_proxy_binding` 签名变(inbound 账密 → method/password)

### 风险

- **临时 xray 实例残留**: 内 ping 起的临时实例若没 kill 干净 → 端口 / 进程泄漏
  缓解: spec/task 里 try/finally 兜底 kill + 删临时配置, TC 覆盖
- **外 ping TCP 可达 ≠ 完整 SS 验证**: 极端下端口通但 SS 配错(内 ping 已覆盖此风险, 概率极低)
  缓解: 内 ping 端到端已验协议; 真出问题用户重发 register_ip
- **纳管读 SS 账密未处理**: 重装自己 SS 机时纳管读空 → later issue 跟进, 当前规模极少触发

---

## 用户口述原话(关键节选)

> "提issue 自有服务器搭建的 socks5 类型节点, 导入小火箭后, 通过小火箭分享二维码给
> 安卓手机的 v2rayng 代理软件导入不进去 解决方案是改配置导入类型使用能够兼容两种
> 导入方式的类型"
> (引出本 ADR 议题)

> "我验证过了, ss 协议是可以被安卓导入并使用的"
> (引出 §决策 §1 拍板 SS)

> "上游接 socks5 没问题, 我们自己配置的入口变成兼容性更好的, 纳管的 socks5 全部手动
> 操作一次 换成新的接口"
> (引出 §决策 §6 上游不动 + 存量手动升级)

> "IP 成功入库后被消费时配置 socks5 的逻辑改成 ss 即可, 其他照旧... 入库的时候增加
> 一个枚举"
> (引出 §决策 §1 / §3)

> "原来的 socks5 测连通性的逻辑保留, 封装成类。再实现多个 ss 测试连通性的逻辑...
> IP 入库前的连通测试, 可以用 socks5 的格式测 不影响 毕竟只是测上游能不能用 不必要
> 复杂化"
> (引出 §决策 §4 / §7)

> "外 ping... 我本机有小火箭进程要一直开着, 我怕影响, 有什么其他方式验证" → "轻一点的吧 A"
> (引出 §决策 §5 外 ping 用 TCP 可达)

---

## 补充(2026-06-24, T-27 实现前发现并修正)

下面 §影响清单原把 `add_proxy_binding` 当作只服务对外部署, **漏看它是 socks5/SS 共享底层**:

- `apply_proxy_binding → add_proxy_binding`: 服务 ProxyDeployWorker 对外部署 → 走 SS
- `replace_proxy_binding → add_proxy_binding`: 服务 IPProbeWorker 测上游(本 ADR §7) → 必须保持 socks5

**处理(用户 2026-06-24 拍板, 方案 A, 决策不变, 只补实现路径)**:
`add_proxy_binding` 加 `protocol` 参数(默认 `socks5`), 按协议造 socks5(user/pwd) 或
SS(method/password) inbound。`apply_proxy_binding` 传 `shadowsocks`;
`replace_proxy_binding` 保持默认 socks5。**IPProbeWorker / 纳管一行不碰**, 符合 §7。

## 影响清单(已读代码现状, 已锁定, 下游 task 落地)

| 文件 | 现状 | 改动 | 批次/task |
|------|------|------|----------|
| `db/models.py` | `ProxyRecord.protocol` String default socks5; 无 method; `from_new_deployment`(L261) protocol 参数 | 加 `ProxyProtocol` 常量类 + `method` 字段 + `from_new_deployment` 加 method 参数 | 批1 / T-26 |
| `xray/config.py` | `add_proxy_binding`(L491) socks5/SS 共享底层 | 加 `protocol` 参数(默认 socks5)按协议造 inbound; 抽 `_build_ss_inbound`; build_proxy_relay_config 无活跃调用方 | 批2 / T-27 |
| `xray/manager.py` | `apply_proxy_binding`(L242) 对外部署 / `replace_proxy_binding`(L281) 测上游 | apply 改走 SS(protocol=shadowsocks + method/password); **replace 保持 socks5 不变**(IPProbeWorker 零碰) | 批2 / T-27 |
| `workers/proxy_deploy_worker.py` | L409 凭据 / L426 apply / L458 内 L478 外 ping(socks5) / L542 protocol="socks5" | 凭据改 SS; ping 改 ShadowsocksProbe; `_mark_done` 写 protocol/method | 批2 / T-27 |
| `toolbox/proxy_check.py` | 三函数 test_socks_proxy / test_internal / test_external | 封 `Socks5Probe` 类(行为不变) + 新增 `ShadowsocksProbe` 类 | 批3 / T-28 |
| `workers/ip_probe_worker.py` | L55/L457 调 test_internal_socks 测上游 | 改调 `Socks5Probe`(行为不变, 换调用形态) | 批3 / T-28 |
| `toolbox/share_link.py` | 不存在 | 新建 `build_ss_url` 纯函数(SIP002) | 批4 / T-29 |
| `db/queries.py` | `_build_proxy_node`(L199) / `list_available_proxies`(L267) 返回无 method/share_link | 返回加 method + share_link | 批4 / T-29 |
| `tools/get_available_proxy_nodes.py` / `tools/get_ip_registration_status.py` | description 按 socks5 | 补 SS / share_link 说明 | 批4 / T-29 |
| `test/proxy_deploy_worker/spec.md` | 对外 socks5 + 内外 ping socks5 | 改对外 SS + 两套 ping; 升版本 + 修订历史 | 批2/3 随 task |
| `db/queries.py` 等的 TC + `test/proxy_deploy_worker/TC-*` | 断言 socks5 | 随各批改断言为 SS | 各批随 task |
| `xray/manager.py::_parse_outbounds_from_config`(L59 读 accounts) | socks5 形态 | **later**(纳管支持 SS, 单独 issue) | later |
