# 0010. 删除 ip_record.status 字段 — proxy_record 当唯一真相源

**日期**: 2026-06-10
**状态**: Accepted

---

## Supersedes / 补充

- supersede(局部) [[0001-workers-replace-services]] §决策 §1 工人清单引出的"IPProbeWorker / ProxyDeployWorker 协同维护 ip_record.status" 隐含规约
- 沉档 `issue/2026-06-09-ip-record-status-冗余.md`(本 ADR 落地后标"已沉 ADR-0010")

> 注: 被本 ADR 修订的旧 ADR 本身不动(永不改原则); 当下真相以本 ADR + 后续 ADR + spec.md 为准。

---

## 背景

T-18 完工后跑端到端首次纳管 (vps_id=1 / ip_id=1, 生产 VPS `203.0.113.10` + SG 出口 `198.51.100.10`), 用户审 DB 发现:

```
ip_record:    id=1  egress=198.51.100.10  is_active=1  status='usable'  ← 说"还没挂"
proxy_record: id=1  vps_id=1 vps_port=11080 ip_id=1   status='using'  ← 说"挂着跑"
```

两条数据指同一件事自相矛盾.

调代码找根因:
- `workers/proxy_deploy_worker.py:551` 正常部署路径显式写 `ip.status = IPStatus.USING`
- `workers/xray_worker.py::_upsert_managed` 纳管路径**没设 status**, 走 ORM 默认值 `IPStatus.USABLE`
- 两条路径写库不一致, 漂移已发生

grep 全项目验证 ip.status 触点:
- **没任何 SELECT 用 ip.status 过滤业务流**; `list_available_proxies` 走 `proxy_record.status=USING + is_active=1`
- ip.status 实际只在 MCP 对外 contract 上有触点: `db/queries.py:171` 返回 dict 塞 `"status": ip.status` + `tools/get_ip_registration_status.py` description 教 agent "看到 usable 不要说配好了"
- 本质上 ip.status 是把"这条 IP 在不在 proxy_record 里挂着"这件事**复述了一遍**

---

## 决策

### 1. 删 IPStatus + IPRecord.status 字段

- `db/models.py` 删 `class IPStatus` 整段 + `IPRecord.status` 字段定义 + `from_xxx` 工厂 status 入参 + docstring 提及

### 2. "这条 IP 在不在用" 单一真相源 = proxy_record 表

业务判定:

```sql
-- 这条 IP 还能不能被挑? (IPProbeWorker / ProxyDeployWorker 用)
SELECT ip.* FROM ip_record ip
LEFT JOIN proxy_record p ON p.ip_id = ip.id AND p.status <> 'inactive'
WHERE ip.is_active = 1 AND p.id IS NULL
```

- 有 proxy_record.ip_id 关联(且 status<>'inactive') = 在用
- 没有 = 空闲, 可挑

### 3. MCP 对外 contract 改 description 判定规则

`tools/get_ip_registration_status.py` description:
- 删 "ip.status=usable / using" 相关转告规则
- 新规则: `task.status='done' + proxy_node != null` 表达 "配好了"
- `task.status='done' + proxy_node == null` 表达异常 (本来该有节点)

### 4. dev SQLite 迁移

用户手动清 `vps_server.db` + 重跑 `python main.py init-db` 重建. 现有 1 条 ip_record + 1 条 proxy_record 是已知 bug 状态(纳管路径写错的产物), 不留.

生产数据库本项目仍 SQLite, **无 MySQL 迁移需求**.

---

## 备选方案

### 方案 A (被否决): 保留字段 + 修 XrayWorker 纳管 bug

显式在 `_upsert_managed` 加 `status=IPStatus.USING`, 跟 `_mark_done` 对齐.

**否决理由**:
- 治标. 双轨数据 (status 字段 vs proxy_record 存在性) 已经漂过一次
- 未来 CleanupWorker / ExpiryWorker 启用时, 删 proxy_record 都得记得同步把 ip.status 改回 USABLE, 漏一次又漂
- ip.status 是 derived 信息, 不该独立持久化

### 方案 C (被否决): 改语义为"上游健康度"

留字段, 改成 healthy / probe_failed / expired 等枚举.

**否决理由**:
- "上游健康度" 已经被 `is_active` 字段表达
- 加新枚举是 YAGNI, 当前业务规模没有"细分上游健康度"的需求
- 改名 + 改维护逻辑算重构, 不如直接删

---

## 后果

### 好处

- **单一真相源** (proxy_record 是事实表), 不可能再漂
- 未来 CleanupWorker / ExpiryWorker 等清理工人启用时, 删 proxy_record 就完事, 不用维护 ip.status 字段(用户在对话里推导出来的"释放 IP 回池子"动作天然由 `LEFT JOIN proxy_record IS NULL` 表达, 零字段维护)
- MCP 对外 contract 改成 `task.status + proxy_node` 信号组合, 跟"任务派发 → 完成即配好, 没配好再加重试"的 worker 模型自洽

### 引入的新约束

- dev SQLite 需用户清库 + init-db 重建
- `test/ip_probe_worker/spec.md` v2 §6 关于 status 描述同步去掉, 升 v3 加修订历史
- 未来加 IPRecord 字段时, 必须避免重新引入"状态机字段"(状态由 proxy_record 存在性 + task 表派发表达)

### 风险

- **MCP 对外 contract 变化**: agent 文案规则改了, agent 上下文里如果有旧 description 缓存可能转告不准
  缓解: tools/get_ip_registration_status.py description 改完后, 新 agent 调用会拉新 description, 旧上下文不持久化

---

## 用户口述原话 (关键节选)

> "纳管 proxy 已经把端口抽出来写入表但是 IP 表还记为可用, 会有歧义是吗"
> — 引出本议题

> "有任务表锁IP, 那proxy表就不需要using状态了吧, 能配置在事实表的就表示能用了"
> — issue 原话, 引出"proxy_record 是事实源"

> "porxy 表有事实就代表使用中"
> "未来要一个轮询 agent ... VPS 过期 15 天了就干活, 把 proxy 表清理 ... 然后把上游代理抽出来写到 IP 表任务这样 IP 工人也不会闲着又能循环起来; 基于这个情况, IP 的状态机就不需要了, 因为一旦写入是靠任务派发任务完成就配置好, 没配置好再加重试, 完美设计啊"
> — 本次对话, 引出本 ADR 决策 + 未来清理 worker 自洽性论证

---

## 影响清单 (已锁定, T-21 落地)

| 文件 | 现状 | 改动 | 落地任务单 |
|------|------|------|----------|
| `db/models.py` L314-334 | `class IPStatus` (USABLE / USING) | 删整段 | T-21 |
| `db/models.py` L385 | `IPRecord.status` 字段 default=USABLE | 删字段定义 | T-21 |
| `db/models.py` L442 | `from_xxx` 工厂 `status=IPStatus.USABLE` 入参 | 删 status 入参 | T-21 |
| `db/models.py` IPRecord docstring | 提及 status 状态机 | 同步去掉 status 表述 | T-21 |
| `db/queries.py` L171 | 返回 dict `"status": ip.status` | 删此行 | T-21 |
| `tools/get_ip_registration_status.py` description | 含 "ip.status=usable / using" 转告规则 + L65 反例 | 删 ip.status 相关规则; 新增 `task.status=done + proxy_node 非空/空` 判定规则 | T-21 |
| `workers/proxy_deploy_worker.py` L36 | `IPStatus` import | 删 | T-21 |
| `workers/proxy_deploy_worker.py` L551 | `ip.status = IPStatus.USING` | 删 | T-21 |
| `workers/ip_probe_worker.py` L18 | docstring 提及 IPStatus | 删 | T-21 |
| `workers/ip_probe_worker.py` L40 | `IPStatus` import | 删 | T-21 |
| `workers/ip_probe_worker.py` L538 | 注释 "ip_record.status = usable" | 删 | T-21 |
| `workers/xray_worker.py` | 本来就没 import IPStatus, `_upsert_managed` 也没写 status | **不动** | — |
| `test/_data_structures/test_ip_record_status.py` | 整文件围绕 IPStatus 枚举 | 整文件删 | T-21 |
| `test/proxy_deploy_worker/TC-07_full_happy_using.py` L29,152 | import + 断言 `ip.status==USING` | 删 import + 改断言为 "proxy_record 存在 + ip_id 关联" | T-21 |
| `test/proxy_deploy_worker/TC-09_inner_ping_rollback.py` L22,115 | import + 断言 `ip.status==USABLE` | 删 import + 改断言为 "proxy_record 没这条 ip_id 的行" | T-21 |
| `test/proxy_deploy_worker/_helpers.py` L18,143,155 | import + `_new_ip` 默认 status 入参 | 删 | T-21 |
| `test/ip_probe_worker/TC-08_queued_success.py` L34,116 | import + 断言 `rec.status==USABLE` | 删 import + 删断言 | T-21 |
| `test/ip_probe_worker/spec.md` | v2 §6 关于 status 描述 | v2→v3 去掉 status, 加修订历史 | T-21 |
| dev SQLite | 现有 1 条 ip_record (status='usable' bug 态) + 1 条 proxy_record | 用户清库 + init-db 重建 | T-21 验收时 |
| `issue/2026-06-09-ip-record-status-冗余.md` | 状态"待拍板" | 标 "已沉 ADR-0010" | T-21 commit 同批 |
