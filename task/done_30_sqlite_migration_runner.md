# T-30 轻量 SQLite migration runner + 0001 method 迁移

**ID**: T-30
**状态**: waiting
**前置依赖**: 无(可与 T-31 并行)
**关联 ADR**: docs/adr/0012-sqlite-migration-runner-and-drop-services.md

---

## 0. 开工前必读 / 领取锁

- [ ] 确认仍 waiting, 改名 `doing_30_*.md`
- [ ] 读: CLAUDE.md / CLAUDE.local.md / docs/adr/README.md / **ADR-0012** / ADR-0008(main.py 子命令) /
      `main.py`(_init_db + _build_parser) / `tools/init_db.py` / `db/engine.py` / `db/base.py` /
      `db/models.py`(确认 proxy_record + method 列已在 ORM) / `README.md`(根, 找部署章节)

---

## 1. 业务目标

项目缺迁移机制(dev drop 重建 / 生产不能 drop)。ADR-0011 给 proxy_record 加了 NOT NULL
的 method 列,生产库没这列, 拉代码必炸 `no such column: method`。本任务建轻量 migration
runner 根治, 并把 method 列作为 0001 迁移。

### 不解决什么
- 不删 services/(T-31)
- 不改 CLAUDE.local.md(**需求窗口统一收口, 你别碰**)
- 不内联 xray/service+config(另案)

---

## 2. 实现参考(严格按 ADR-0012 §决策)

### 新建 `db/migrations/0001_add_proxy_record_method.sql`
```sql
ALTER TABLE proxy_record ADD COLUMN method VARCHAR(32) NOT NULL DEFAULT '';
```

### 新建 `db/migrate.py`
- `schema_migrations` 表(version TEXT PK, applied_at) —— 没有就建
- `apply_pending(engine) -> dict`: 扫 `db/migrations/*.sql` 按号排序, 跑未在台账的,
  逐个 stamp。**返回 {applied:[...], skipped:[...]}** 给 CLI 打印
  - ⚠️ **0001 schema probe(ADR §5)**: 应用前 `PRAGMA table_info(proxy_record)` 检测 method
    列是否已存在 → 存在则 stamp applied **不执行 SQL**(吃历史手工迁移); 不存在才执行
- `init_db_with_baseline_if_fresh(engine) -> dict`(ADR §3/§4, CLI+MCP 共享):
  - probe 业务表(如 proxy_record)是否存在
  - **全新库**(业务表都不存在) → `Base.metadata.create_all` + **baseline**(现有迁移全 stamp)
  - **已有库** → create_all 幂等 + **绝不 stamp 迁移**(让 migrate 去演化)

### 改 `main.py`
- 加 `migrate` 子命令(跟 init-db 同款 argparse) → 调 `apply_pending` + 打印 applied/skipped
- `_init_db` 改调 `init_db_with_baseline_if_fresh`(不再裸 create_all)

### 改 `tools/init_db.py`
- MCP init_db handler 改调 `init_db_with_baseline_if_fresh`(跟 CLI 走同一 helper)

### 改根 `README.md`
- 部署章节加: 生产部署 = 拉代码 → `python main.py migrate` → 起 worker-loop;
  首次部署用 `init-db`

---

## 3. 验收交付 —— 验收矩阵(ADR-0012, 全部要 TC 覆盖)

1. **fresh DB**(in-memory/临时): init-db 建最新 schema(含 method) + stamp 0001; 再 migrate = no-op
2. **old DB**: 造一个有 proxy_record 但无 method 的库; migrate 后 method 列出现 + 默认 '' + 台账有 0001
3. **idempotent**: 连续两次 migrate, 第二次 applied 为空(no-op)
4. **0001 probe**: 库已手工有 method 列但台账空 → migrate stamp 0001 但不执行 SQL(不 duplicate column)
5. **MCP init**: `tools/init_db.py` 跟 CLI init-db 行为一致(都走 helper)
6. **CLI**: `main.py --help` 出现 migrate; migrate 返回清晰 applied/skipped

### 必跑测试
```bash
PYTHONPATH=. uv run pytest test/ -q   # 全量(确认没破坏 + 新 TC 全过)
```

### 实现者完工标准
- [x] 开工改 doing
- [x] db/migrate.py + 0001.sql + main.py migrate + init-db helper + tools/init_db.py + README 改完
- [x] 验收矩阵 6 条都有 TC 且全 PASS(test/migrate/ 21 TC + test/mcp_tools parity)
- [x] **没碰 CLAUDE.local.md**(需求窗口收口)
- [x] 完成记录已填(测试结果原样)
- [x] 保持 doing_30, 不 commit

---

## 完成记录(done 时追加)
```text
完成日期: 2026-06-24
改动摘要:
  - db/migrate.py 新建: apply_pending(扫 migrations + 0001 schema probe + stamp 台账)
    + init_db_with_baseline_if_fresh(全新库 create_all+baseline / 已有库绝不 stamp)
  - db/migrations/0001_add_proxy_record_method.sql 新建(ALTER ADD COLUMN method)
  - main.py: 加 migrate 子命令 + _init_db 改调共享 helper + _migrate 异常兜底(return 1)
  - tools/init_db.py: 改调共享 helper(跟 CLI 一致, 修了 TC-10 旧 mock 回归)
  - README.md: 部署章节加 migrate 步骤
  - test/migrate/ TC-01~04(21 TC, 验收矩阵 6 维) + test/mcp_tools/TC-10 更新
  需求窗口接管收尾(实现 agent 输出反复截断不可靠):
  - TC-10 mock 回归修复(旧 db.base.Base mock → mock helper 源头)
  - MEDIUM-1 _migrate 异常兜底 + TC(code-reviewer adversarial 发现)
  - LOW-3 迁移文件"每文件一条语句"约定写进 db/migrate.py docstring
测试命令 / 结果:
  PYTHONPATH=. uv run pytest test/ -q → 423 passed, 3 skipped(真机默认 skip) in 4.34s
  code-reviewer adversarial: 0 CRITICAL / 0 HIGH / 1 MEDIUM(已修) / 4 LOW
未覆盖风险:
  - LOW-1 SQLite DDL 非原子(ALTER 自动提交), 但 0001 probe 重试自愈
  - runner 做了代码级跨库修复(inspect 替 PRAGMA/sqlite_master + _stamp 去 INSERT OR IGNORE);
    生产=MySQL 但全程 SQLite 测, **MySQL 兼容待 T-32 真库验证**(生产上线 gate), 未真验前不算 MySQL 闭环
```
