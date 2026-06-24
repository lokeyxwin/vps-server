# T-31 删除 legacy services/(死代码 + ip_register 旧签名 P2)

**ID**: T-31
**状态**: waiting
**前置依赖**: 无(可与 T-30 并行)
**关联 ADR**: docs/adr/0012-sqlite-migration-runner-and-drop-services.md §决策 §6

---

## 0. 开工前必读 / 领取锁

- [ ] 确认仍 waiting, 改名 `doing_31_*.md`
- [ ] 读: CLAUDE.md / CLAUDE.local.md(§11 旧 services) / docs/adr/README.md / **ADR-0012** /
      ADR-0008 §11(services 退出活跃路径硬事实) / `services/` 下 4 个文件(确认要删的内容)

---

## 1. 业务目标

`services/`(旧业务编排, 4 文件)已是死代码(ADR-0008 §11: 活跃码零 import)。其中
`ip_register.py` 还残留 ADR-0011 改前的 `apply_proxy_binding(inbound_user=, inbound_pwd=)`
旧签名(review P2)。本任务把整个 services/ 删掉, 旧签名死代码随之消失。

### 不解决什么
- 不做 migration runner(T-30)
- 不改 CLAUDE.local.md(**需求窗口统一收口, 你别碰** —— T-30 也会碰它, 避免抢编辑)
- 不内联 xray/service+config(另案)

---

## 2. 实现参考

### 删除前再确认(硬约束: 删之前自己 grep 一遍, 不能盲删)
```bash
# 必须输出为空(除 services/ 内部互引), 否则停下报告
git grep -n "from services\|import services" -- '*.py' | grep -vE "^services/|^test/"
```
若有任何活跃 import(workers/tools/db/main/xray 等) → **停下报告**, 不删。

### 删除目标
- `services/__init__.py`
- `services/ip_register.py`
- `services/vps_init.py`
- `services/vps_register.py`
(整个 services/ 目录清空/删除)

### 连带检查
- `test/` 下有没有 import services 的测试 → 有的话一并删/改(那是测死代码的测试)
- `git grep "services" -- '*.py'` 扫一遍残留引用(注释提及可留, import/调用要清)

---

## 3. 验收交付

### 必跑测试
```bash
PYTHONPATH=. uv run pytest test/ -q   # 全量必须全绿(删死代码不该断任何活路径)
```

### 实现者完工标准
- [ ] 开工改 doing
- [ ] 删前已 grep 确认零活跃 import(输出贴进完成记录)
- [ ] services/ 4 文件已删 + test 残留引用已清
- [ ] 全量测试全绿(证明删的确实是死代码)
- [ ] **没碰 CLAUDE.local.md**(需求窗口收口)
- [ ] 完成记录已填
- [ ] 保持 doing_31, 不 commit

---

## 完成记录(done 时追加)
```text
完成日期 / commit: 2026-06-24 / 未 commit(按任务单保持 doing)

删了哪些文件:
  - services/__init__.py      (git rm)
  - services/ip_register.py   (git rm,含 ADR-0011 改前的 apply_proxy_binding 旧签名死代码)
  - services/vps_init.py      (git rm)
  - services/vps_register.py  (git rm)
  - services/__pycache__/     (未跟踪缓存,清理)
  整个 services/ 目录已不存在。

  test/ 下未删任何文件:grep 命中 services 的 5 个 test 文件都不依赖 services 模块,
  而是「守护测试 / 数据结构测试」——
    test/db/TC-02_no_services_residue.py  断言 services 子文件不存在(删后更绿)
    test/db/TC-03_no_services_imports.py  grep 全项目无活跃 import services(EXCLUDE_DIRS 含 services/test)
    test/db/TC-04_tools_use_db_queries.py 断言 tools 文件不含 from services
    test/_data_structures/test_proxy_record_method.py / test_vps_record_v4.py / test_vps_task.py
                                          仅 docstring 文字提及「不应 import services/*」
  这些是保护「services 退出活跃路径」硬事实的守护测试,不是测死代码的测试,故保留。

grep 零活跃 import 证据:
  $ git grep -n "from services\|import services" -- '*.py' | grep -vE "^services/|^test/"
  (无输出,exit 1 = 零活跃 import)

  删后全项目 *.py 残留 services 引用全部为注释 / docstring 提及 / logger 名字符串
  (如 workers/*.py 的 get_logger("services.xxx") 是日志层命名前缀,非 import)
  + 守护测试断言,无任何 import/调用,符合任务单「注释/docstring 可留」。

测试命令 / 结果:
  $ PYTHONPATH=. uv run pytest test/ -q
  406 passed, 3 skipped in 3.98s
  (3 skipped 为默认 skip 的真服务器测试,符合测试契约)
```
