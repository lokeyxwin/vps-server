# workers/_shelved/ —— 封存的工人

这里住的是**设计已经聊过、但当前业务主线先不需要落地**的工人。
等核心 4 个工人（SSHWorker / XrayWorker / IPProbeWorker / ProxyDeployWorker）
跑通后再回来写这一批。

## 封存清单

### `health_check_worker.py` —— 定时探活

**职责**：周期性扫所有 production_ready 的 VPS，对每条已部署的 inbound 做
内外 ping，更新 `last_internal_ping_ok` / `last_external_ping_ok` 字段。

**为什么会发现外不通**：云服务商安全策略组没放行 18441-18450 段。
工人不能帮用户改策略组（在服务商控制台），只能标记状态让外部查询时知道
"为啥数据库说能用但我连不上 = 安全策略组没开"。

### `expiry_worker.py` —— 定时看到期

**职责**：周期扫 `vps_record` + `ip_record` 的 `expire_date`，到期就改
`is_active=0`。**不碰服务器**，只看日期。完全不会跟别人抢资源。

### `cleanup_worker.py` —— 过期配置清理

**职责**：当 IP 过期（is_active=0）但还绑在某台生产 VPS 的某个端口上时，
这个工人负责去 SSH 上去**移除过期 IP 的 xray 配置 + 释放端口**。
释放后该端口又能给新的 IP 用。

**触发条件**：过期满 N 天（具体阈值待定）才动手，避免误清。

## 设计共识（封存项的共性）

- 都是**定时驱动**，不进 task 表
- 都不被 agent 主动触发
- 都只在主业务流跑通后再实现
