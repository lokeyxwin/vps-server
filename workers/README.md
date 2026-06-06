# workers/ —— 新业务编排层（取代旧 services/）

## 这里住的是「工人」

每个工人 = 一个业务流程的执行者，对应一个 `xxx_worker.py` 文件。
工人是**新业务层**：取代 `services/vps_register.py` / `services/ip_register.py` 这类
旧同步阻塞业务函数。

**旧 `services/` 不删，留作对照参考**。

## 在场工人（4 个）

| 文件 | 工人名 | 触发方式 | 同步/异步 | 主要职责 |
|------|--------|----------|----------|----------|
| `ssh_worker.py` | SSHWorker | rgvps 入口调用 | 同步 | 敲门、看版本、登记 VPS、派 install_xray 任务 |
| `xray_worker.py` | XrayWorker | task=install_xray | 异步 | 把 xray 装上去、常驻、自启 |
| `ip_probe_worker.py` | IPProbeWorker | rgip 入口调用 | 同步 | 用测试 VPS 验证上游 IP 凭据通不通 |
| `proxy_deploy_worker.py` | ProxyDeployWorker | task=deploy_proxy | 异步 | 生产 VPS 池里挑机挂出口 + 内外 ping |

## 封存（先不动）

`_shelved/` 子目录里：

- `health_check_worker.py` —— 定时探活
- `expiry_worker.py` —— 定时看到期
- `cleanup_worker.py` —— 过期 IP 配置清理 + 释放端口

## 工人和工具的关系

工人**主动**做事：扫 task 表、抢锁、写数据库、决定下一步派什么任务。
工具箱**被动**被调：不写表、不抢锁、不决定流程。

工人去 `kits/install_xray/` 拿对应的格子用：

| 工人 | 用哪些格子 |
|------|----------|
| SSHWorker | `probe`（仅 version） |
| XrayWorker | `service` + `probe` |
| IPProbeWorker | `config` + `probe` |
| ProxyDeployWorker | `config` + `probe` |

注：`probe` 模块的命名待统一，见 `issue/2026-06-06-probe-and-test-naming.md`。

## 数据来源

- 资源池（VPS 列表）：`db.models.VPSRecord`
- 任务队列：`db.models.Task`（待建表）
- 上游代理：`db.models.IPRecord`
- 部署成果：`db.models.ProxyRecord`
- 测试 VPS：`config.PROBE_VPS`（不入业务表）

## 注意

- 工人之间**只通过 task 表接力**，不直接调用别人
- 工人**只调 `kits/` + `core/` + `db/`**，不调 `services/`（旧代码）
- 工人之间共享资源（VPS）的协调，靠 task 表的 `vps_id` + `locked_until` 字段
