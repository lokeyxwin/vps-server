# 0012. 轻量 SQLite migration runner + 删除 legacy services/

**日期**: 2026-06-24
**状态**: Accepted

---

## Supersedes / 补充

- 补充 [[0008-main-as-worker-runner-and-db-queries-home]] §1 main.py 子命令
  → 加 `migrate` 子命令; `init-db` 改为"全新库才 create_all+baseline"
- 落实 [[0001-workers-replace-services]] §3 + CLAUDE.local §11 "services/ 完工后删"
  → 本 ADR 真正删 services/(grep 证实零活跃 import 后)
- **不含** xray/service.py + xray/config.py 内联(它们是活跃码被 5+ 模块直接 import,
  且 config.py 752 行大量纯函数不宜塞进有状态 manager) —— 单独出设计 ADR 评估

> 注: 被补充的旧 ADR 本身不动(永不改原则); 当下真相以本 ADR + spec.md 为准。

---

## 背景

ADR-0011(T-26)给 `proxy_record` 加了 **NOT NULL** 的 `method` 列。但:

- dev 测试靠 `drop + create_all` 重建(拿到最新 schema), **不是演化**
- 生产是 SQLite 真实数据库, **有节点数据不能 drop**, 且**库没动过**(没有 method 列)
- 项目**完全没有迁移机制**: `init_db` 只 `create_all`(CREATE TABLE IF NOT EXISTS),
  **不给已存在的表加列**

后果: 拉新代码后, 生产任何 load/写 `ProxyRecord` 都 `no such column: method` 直接炸。
这暴露了"dev drop / 生产不能 drop"的迁移缺口, 不是一次性 ALTER 能根治的(下次加字段又踩)。

同时 `services/`(旧业务编排, 4 文件)已是死代码(grep 证实活跃码零 import,
ADR-0008 §11 硬事实), 其中 `ip_register.py` 还残留 ADR-0011 改前的
`apply_proxy_binding(inbound_user=, inbound_pwd=)` 旧签名 —— 该一并清掉。

---

## 决策

### 1. 轻量 migration runner(不上 alembic, YAGNI)

- `db/migrations/NNNN_<slug>.sql` 编号迁移文件(纯 SQL)
- `schema_migrations` 表(`version` TEXT PK + `applied_at`)记台账
- `main.py migrate` 子命令: 建台账表 → 扫 migrations 按号 → 跑**未应用**的 → 记录
  → 返回清晰的 applied / skipped 结果

### 2. `0001_add_proxy_record_method.sql`

```sql
ALTER TABLE proxy_record ADD COLUMN method VARCHAR(32) NOT NULL DEFAULT '';
```
SQLite ALTER ADD COLUMN 安全: 瞬时、不重写表、不丢现有行, 存量 socks5 节点 method 回填 ''。

### 3. baseline 只在"全新库"做(review ①, 关键)

```
init-db:
  ① probe 业务表是否存在(如 proxy_record)
  ② 全新库(业务表都不存在) → create_all(最新 schema) + baseline(把现有迁移全 stamp applied)
  ③ 已有库               → create_all 幂等(不建表) + **绝不 stamp 迁移**
```

**理由**: 全新库 create_all 已含 method 列, 必须 stamp 0001 否则 migrate 会 duplicate
column; 但**已有旧库(缺 method)绝不能 stamp**, 否则 method 永远加不上 → 炸。

### 4. CLI 与 MCP 共享同一 helper(review ②)

`tools/init_db.py`(MCP admin)也跑 `create_all`。抽共享:
```
db/migrate.py::init_db_with_baseline_if_fresh()
```
`main.py init-db` 和 `tools/init_db.py` **都走这一个**, 避免一个加 baseline 一个不加
导致全新库迁移未 stamp 的 duplicate column 风险。

### 5. 0001 加 schema probe 吃历史手工迁移(review ④)

若某库被**手工 ALTER 加过 method 但无台账**(如先前运维), 纯 SQL runner 会 duplicate
column。migrate 应用迁移时做**轻量幂等 probe**: 检测目标列已存在 → 直接 stamp applied
不执行 SQL; 不存在才执行。吃掉历史手工迁移状态(不算上 alembic, 只是防御)。

### 6. 删 services/(死代码, 带走 P2)

删 `services/__init__.py` / `ip_register.py` / `vps_init.py` / `vps_register.py`。
grep 证实活跃码零 import(只 services 内部互引)。`ip_register` 的旧签名死代码随之消失。

### 7. dev / prod 迁移策略

```
全新库(首次部署 / dev 重建): init-db → create_all + baseline → 不重复 ALTER
已有库(生产, 缺 method):     migrate  → 跑未应用 0001 → 保数据演化
```

---

## 备选方案

- **alembic**(被否决): 完整迁移框架, 对个人 SQLite 项目过重, YAGNI
- **一次性 ALTER**(被否决): 不建机制, 下次加字段又踩同样的坑
- **baseline 无条件挂 init-db**(被否决, review ①): 已有旧库会被错误 stamp → method 永不加
- **xray/service+config 内联进本 ADR**(被否决): 活跃码 + 纯函数不宜塞 manager + 当前正常,
  YAGNI; 单独出设计 ADR

---

## 后果

### 好处

- 生产部署阻断(no such column: method)根治
- "dev drop / 生产演化"缺口补上, 以后加字段 = 加 .sql + 部署 migrate, 不再炸生产
- 死代码 services/ 清掉, ip_register 旧签名一并消失
- CLI / MCP init_db 行为统一(共享 helper)

### 引入的新约束

- 以后每个 schema 变更必须配一个 `db/migrations/NNNN_*.sql` + 模型同步
- 生产部署流程多一步 `python main.py migrate`(写进根 README)
- CLAUDE.local 迁移规约更新(由需求窗口统一收口, 不让实现 agent 改)

### 风险

- migrate runner 自身 bug → 生产数据风险。缓解: 验收矩阵 5 条覆盖 fresh/old/idempotent/MCP/CLI
- baseline 的"全新库判断"若误判 → 缓解: 用明确业务表(proxy_record)存在性判断, TC 覆盖

---

## 影响清单(已读代码现状, 已锁定)

| 文件 | 改动 | task |
|------|------|------|
| `db/migrations/0001_add_proxy_record_method.sql` | 新建 | T-30 |
| `db/migrate.py` | 新建: apply_pending + init_db_with_baseline_if_fresh + 0001 probe | T-30 |
| `main.py` | 加 `migrate` 子命令; `_init_db` 改调共享 helper | T-30 |
| `tools/init_db.py` | 改调共享 helper(跟 CLI 一致) | T-30 |
| `README.md`(根, 现存) | 部署章节加迁移步骤(拉代码→migrate→起 worker) | T-30 |
| `services/`(4 文件) | **删** | T-31 |
| 测试 | migration runner TC(验收矩阵 5 条) + 删 services 后全量回归绿 | T-30/31 |
| `CLAUDE.local.md` | 迁移规约 + §11 services 已删 | **需求窗口统一收口(不派 agent)** |

## T-30 验收矩阵(写进 task)

1. **fresh DB**: `init-db` 建最新 schema 且 stamp 0001; 再跑 `migrate` 是 no-op
2. **old DB**: 有 proxy_record 无 method; `migrate` 后列出现 + 默认 '' + 台账有记录
3. **idempotent**: 连续两次 `migrate`, 第二次 no-op
4. **MCP init**: `tools/init_db.py` 跟 CLI 行为一致
5. **CLI**: `main.py --help` 出现 migrate; `migrate` 返回清晰 applied/skipped

## 用户口述原话(关键节选)

> "baseline 不能无条件挂在 init-db 后面... 业务表一个都不存在,才 create_all + baseline_all"
> "影响清单漏了 tools/init_db.py... 抽一个共享 helper"
> "给 0001 加一个现实防护... method 已存在就 stamp applied"
> "T-31 可以并行,但注意它和 T-30 都会碰 CLAUDE.local.md"
