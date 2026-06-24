# T-32 MySQL migration runner 真验(生产上线 gate)

**ID**: T-32
**状态**: waiting
**类型**: 验证 task(需真 MySQL 环境, dev 无 MySQL server 跑不了)
**关联 ADR**: docs/adr/0012-sqlite-migration-runner-and-drop-services.md §补充
**⚠️ 这是生产 MySQL 上线的显式 GATE —— T-32 通过前禁止在生产库跑 migrate**

---

## 背景

生产 = MySQL(用户 2026-06-24 确认)。但 socks5→SS 改造(ADR-0011)+ migration runner
(ADR-0012)**全程只在 SQLite 测**。MySQL 兼容只做了**代码级跨库修复**:
- `inspect` 替 `sqlite_master` / `PRAGMA`(e258416)
- `_stamp` 去 `INSERT OR IGNORE` 改标准 SQL(38b1a3d)
- pymysql 已补进 pyproject

**这些没有一行在真 MySQL 上跑过。** 本 task 是上线前置 gate, 真验过才算 MySQL 闭环。

---

## 前提(执行前确认)

- [ ] pymysql 已装(pyproject 已补, `uv sync` 确认)
- [ ] 有 **MySQL 测试库 / 生产备份副本** —— **绝不在生产库首验**
- [ ] 已 `mysqldump` 备份生产库

---

## 验证步骤(全在 MySQL 测试库/副本上)

### A. 已有库演化(模拟生产现状: 有数据、缺 method 列)
1. 副本库(有 proxy_record 但无 method 列) + `config.DB_TYPE="mysql"` + `MYSQL_*` 指向副本
2. 跑 `python main.py migrate`
   - 验: 输出 `applied: ['0001']`
   - 验: `SHOW COLUMNS FROM proxy_record` 有 `method`(VARCHAR(32) NOT NULL DEFAULT '')
   - 验: `SELECT * FROM schema_migrations` 有 `0001` 行
3. 幂等: 再跑 `migrate` → `applied: []`(no-op)
4. probe 防御: 手动给某副本加 method 列但清空 schema_migrations → `migrate` → stamp 0001 **不报 duplicate column**

### B. 全新库(模拟首次部署)
5. 空 MySQL 库 + `init-db` → 验 create_all 建全表(proxy_record 含 method)+ schema_migrations 已 baseline 0001
6. 接着 `migrate` → no-op

### C. 业务 smoke(端到端 MySQL)
7. 起 `main.py worker-loop` + 造一条 ip_task → ProxyDeployWorker 写 proxy_record
   - 验: 写入成功(无 `Unknown column 'method'`), protocol=shadowsocks + method=aes-256-gcm
8. `get_available_proxy_nodes` 返回含 method + `ss://` share_link

---

## 验收

- [ ] A/B/C 全过, 无任何 MySQL 报错(语法/类型/事务)
- [ ] 完成记录贴每步实际输出 + MySQL 版本号
- [ ] **通过后才解锁生产 migrate**(更新本 task 为 done + 生产可上线)

## 不通过怎么办

- 任何步骤 MySQL 报错 → 停下记录具体错误 + SQL, 回报需求窗口
- 常见可能: 类型映射 / charset / 事务 DDL 行为 / inspect 在 MySQL 的差异

---

## 完成记录(done 时追加)
```text
完成日期:
MySQL 版本:
A/B/C 各步实际输出:
发现的 MySQL 问题(若有) + 修复:
结论: 生产 migrate 是否解锁
```
