# T-23 update_ip_expire_date —— 第一个 update_* 写入 MCP 工具

**ID**: T-23
**状态**: waiting
**前置依赖**: none
**后续依赖**: 「按出口IP查已登记 IP」查询工具(本任务不做, 用户拍板「查不到走 register_ip 登记入口」, 暂不配)
**关联 ADR**: docs/adr/0008-main-as-worker-runner-and-db-queries-home.md §3.3(ABCD 4 条规则, 本工具是规则 B/D 举的范例)
**关联 spec**: test/mcp_tools/spec.md(§1 总账 + §6.6 + §8 不变量 + §三 修订历史, 本任务同批改 v2)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名从 `waiting_23_update_ip_expire_date.md` 改为
      `doing_23_update_ip_expire_date.md`。

### 必读清单

领取后、写代码前, 必须显式读取:

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`(尤其 §13 心智模型 + §14 MCP 工具上线评估 + §14.3 ABCD)
- [ ] `docs/adr/README.md`
- [ ] `docs/adr/0008-main-as-worker-runner-and-db-queries-home.md`(§3.3 ABCD)
- [ ] `test/mcp_tools/spec.md`
- [ ] `db/queries.py`(看 3 个现有查询函数范式 + 顶部 docstring 预留的「未来加 update_*」位置)
- [ ] `db/models.py::IPRecord`(expire_date 字段 + is_active 字段语义)
- [ ] `db/session.py`(session_scope 退出自动 commit)
- [ ] `tools/get_ip_registration_status.py`(TOOL + handler 范式 + description 模板样板)
- [ ] `tools/__init__.py`(ALL_TOOLS 注册形态)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "补一个更新IP的到期日的工具"

> "补一下这些IP的到期时间, 只补已登记的"(配一张供应商面板截图: 出口IP + 到期时间)

> "查不到就跳过, 查不到的走登记入口就完事了, 也有可能下次纳管的时候就出现了"

> "只设具体日期(必填)" / "允许任意合法日期"(对齐问答拍板)

### Claude 整理后的业务理解

- 外部输入: agent 拿到 `ip_id` + 一个到期日字符串(YYYY-MM-DD)
- 第一件事: 校验日期格式
- 主要流程:
  1. 校验 `expire_date` 是不是合法 YYYY-MM-DD(不合法直接返 invalid_date, 不碰 DB)
  2. 按 `ip_id` 主键精准定位 ip_record(查不到返 not_found)
  3. 只 patch `expire_date` 单列, 退出事务自动 commit
  4. 返回更新后的 ip 基本信息
- 判断分支:
  - 日期格式非法 -> `invalid_date`(DB 无改动)
  - ip_id 不存在 -> `not_found`(DB 无改动)
  - 命中 -> `ok` + 更新后 ip
- 数据流:
  - 读取: `ip_record`(按主键)
  - 写入: `ip_record.expire_date`(**仅此一列**)
- 同步 / 异步边界: 全同步, handler 直接调业务函数返回, 不建 task, 不派 worker
- 成功返回: "已把 IP <egress_ip> 的到期日更新为 <expire_date>"
- 失败返回: ip_id 找不到 / 日期格式错

### 批量看图场景(写进工具 description, 属 agent 编排逻辑)

用户甩一张供应商面板/表格截图(含出口IP + 到期时间), 说「补一下这些 IP 的到期
时间, 只补已登记的」。agent 对截图每一行:

1. 用出口IP 在现有 `get_available_proxy_nodes` 结果里匹配 -> 拿到 `ip_id`
2. 匹配到(已登记可用)-> 调 `update_ip_expire_date(ip_id, 该行到期日)` 精准补
3. **匹配不到**(过期/没挂的, 不在可用节点列表)-> **跳过, 绝不自动登记**,
   记入「未登记跳过」清单, 提示用户「这几条要纳入管理请走 register_ip,
   下次纳管时再进来」
4. 本工具一次只改一条, agent 逐行循环调; 跑完给三段汇总
   (✅已补 N 条 / ⏭️没登记跳过 M 条 / 让用户决定 M 条要不要 register_ip)

### 本任务要解决什么

给系统一个**精准改某条已登记 IP 到期日**的后台写入工具, 满足
CLAUDE.local.md §14.3 ABCD 4 条规则(主键精准 / 白名单单列 / 不整对象覆盖 /
命名反映约束)。它是项目第一个落地的 `update_*` 写入工具。

### 本任务不解决什么

- ❌ 不做「按出口IP / 列出全部已登记 IP(含过期)-> 给 ip_id」的查询工具
  (用户拍板「查不到走 register_ip」, 批量场景过期 IP 暂用现有可用节点查询匹配,
   匹配不到就跳过)
- ❌ 不动 `is_active`(过期标记归巡检 ExpiryWorker, 不在本工具白名单)
- ❌ 不允许把 expire_date 清回 null(用户拍「只设具体日期(必填)」)
- ❌ 不拦过去的日期(用户拍「允许任意合法日期」, 只校验格式)
- ❌ 不碰 vps_record / proxy_record / 任何 task 表
- ❌ 不清理 spec §1/§7 既有 stale(init_db / init_probe_vps 漏登总账, 另议)

---

## 2. 实现参考

### 验收锚点

- `test/mcp_tools/spec.md` §6.6(本任务同批新增)+ §8 不变量
- `docs/adr/0008-*.md` §3.3 ABCD 4 条规则
- `CLAUDE.local.md` §14.3(ABCD)+ §14.1(admin/user 评估: 本工具归 admin)

### 改动文件清单

#### 改 `db/queries.py`

新增写入业务函数 `update_ip_expire_date(ip_id, expire_date) -> dict`。
顶部 docstring 已预留位置(「未来加写入函数 update_* 时遵循 §14.3 ABCD」)。
import 处已有 `from datetime import date` + `IPRecord` + `session_scope`, 直接复用。

#### 新建 `tools/update_ip_expire_date.py`

照 `tools/get_ip_registration_status.py` 范式, 导出且仅导出 `TOOL` + `handler`。
handler 只做协议转换, 调 `db.queries.update_ip_expire_date`, 不写业务。

#### 改 `tools/__init__.py`

- import `TOOL` / `handler`
- `ALL_TOOLS` 新增一档分类注释 `# ---------- 写入修改工具 (admin) ----------`
  并加 `(_update_ip_expire_date_tool, _update_ip_expire_date_handler)`
- 顶部 docstring「工具暴露分类」补一行「写入修改工具 (admin): update_ip_expire_date」

#### 改 `test/mcp_tools/spec.md`（本任务同批落, 写入前列 diff 给用户审）

- 标题 + §1 标题「5 个工具」-> 「6 个工具」
- §1 总账加第 6 行 update_ip_expire_date(类别「写入修改」)
- §6 新增 §6.6 status 映射(ok / not_found / invalid_date)
- §8 不变量 #6 改措辞「加新工具需 ADR 背书命名 + status_code 映射」, 注明本工具
  由 ADR-0008 §3.3 背书
- §三 修订历史加 v2 2026-06-13

#### 新建 `test/mcp_tools/TC-12_update_ip_expire_date.py`

#### 不动

```text
- db/models.py（不加字段不改 IPRecord, expire_date 已 nullable）
- workers/* / services/* / vps_record / proxy_record / 任何 task 表
- mcp_server.py（admin/user 拆 server 仍留下波）
- is_active 字段（归巡检, 不在白名单）
- spec §1/§7 里 init_db / init_probe_vps 既有 stale（不在本任务范围）
```

### 实现轮廓

```python
# db/queries.py
def update_ip_expire_date(ip_id: int, expire_date: str) -> dict:
    """白名单 patch: 只改 ip_record.expire_date 单列 (CLAUDE.local.md §14.3 ABCD).
    不碰 is_active / egress_ip 等任何其他字段. 只校验格式, 不拦过去日期."""
    try:
        parsed = date.fromisoformat(expire_date)
    except (ValueError, TypeError):
        logger.info("update_ip_expire_date: ip_id=%s 日期非法 %r → invalid_date",
                    ip_id, expire_date)
        return {"status": "invalid_date", "expire_date": expire_date}

    with session_scope() as s:                  # 退出自动 commit
        ip = s.get(IPRecord, ip_id)             # 规则 A 主键精准
        if ip is None:
            return {"status": "not_found"}
        ip.expire_date = parsed                 # 规则 B/C 白名单单列 patch
        s.flush()
        logger.info("update_ip_expire_date: ip_id=%s → %s", ip_id, parsed.isoformat())
        return {
            "status": "ok",
            "ip": {
                "id": ip.id,
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
                "expire_date": ip.expire_date.isoformat(),
                "is_active": ip.is_active,       # 只读回显, 不改
            },
        }


# tools/update_ip_expire_date.py
TOOL = Tool(
    name="update_ip_expire_date",
    title="更新某条已登记 IP 的到期日",
    description=(... 见下「description 要点」...),
    inputSchema={
        "type": "object",
        "properties": {
            "ip_id": {"type": "integer", "description": "IP 主键 id（精准定位, 必填）。"},
            "expire_date": {"type": "string",
                            "description": "到期日 YYYY-MM-DD（必填, 如 2026-06-18）。"},
        },
        "required": ["ip_id", "expire_date"],
        "additionalProperties": False,
    },
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False,   # 可逆: 改回即可
        idempotentHint=True, openWorldHint=False,
    ),
)

async def handler(arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    result = update_ip_expire_date(
        ip_id=args.get("ip_id"),
        expire_date=args.get("expire_date"),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
```

### description 要点（教 agent, 写进 TOOL.description）

- 一句话意图 + 入参（ip_id 精准 / expire_date YYYY-MM-DD）
- 典型场景: 用户「把这条 IP 到期日改成 X」、批量看图补（见 §1 批量场景四步）
- 返回 status 转告:
  - `ok`: 「已把 IP <egress_ip> 的到期日更新为 <expire_date>」
  - `not_found`: 「没找到这条 IP, 确认 ip_id；可能它根本没登记, 要登记走 register_ip」
  - `invalid_date`: 「日期格式要 YYYY-MM-DD（如 2026-06-18）」
- 反例:
  - ❌ 不准拿 egress_ip 字符串模糊定位, 必须 ip_id（规则 A）
  - ❌ 批量场景查不到 ip_id 的别硬造、别自动登记, 跳过并提示走 register_ip
  - ❌ 不要一次想传一个 list 批量改, 本工具一次一条, agent 逐行循环

### 数据结构 / 状态迁移

| 字段 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `ip_record.expire_date` | IP 到期日（nullable） | 巡检 / list_available_proxies / 本工具回显 | **本工具（唯一白名单写入列）** + register_ip 入库 |
| `ip_record.is_active` | 过期标记 | 巡检 / 挑机查询 | 巡检 ExpiryWorker（**本工具不碰**） |

### 缺工具 / 缺信息先报告

- 若发现 IPRecord 没有 expire_date 字段或类型不符 -> 停下报告(应已是 `date | None`)
- 若发现 session_scope 不 commit -> 停下报告(应自动 commit)

---

## 3. 验收交付

### 测试用例

#### TC-12-a `test/mcp_tools/TC-12_update_ip_expire_date.py::test_update_ok`

业务故事: 库里有一条已登记 IP（expire_date 为 null 的纳管 IP）, 调工具补一个具体
到期日, 应成功写入。

输入: 已存在的 ip_id + `"2026-06-18"`
预期: status=ok; **原生查 DB** expire_date == 2026-06-18
不应发生: 抛异常 / 其他字段被改

#### TC-12-b `::test_whitelist_no_collateral_write` ⭐（ABCD 规则 C 核心）

业务故事: 改 expire_date 后, is_active / egress_ip / country_code 必须原封不动。

输入: 一条 is_active=1 egress_ip=X country_code=SG 的 IP, 改 expire_date
预期: 改后这 3 个字段值跟改前完全一致
不应发生: is_active 被顺手改 / 整对象覆盖把别的字段写回默认值

#### TC-12-c `::test_not_found`

输入: 不存在的 ip_id（如 999999）
预期: status=not_found; DB 无任何新增/改动
不应发生: 抛异常 / 误建行

#### TC-12-d `::test_invalid_date`

输入: 存在的 ip_id + 非法日期(`"2026/06/18"` 和 `"foo"` 各一例)
预期: status=invalid_date; 该 IP 的 expire_date 保持原值不变
不应发生: 把非法串写进 DB / 抛未捕获异常

#### TC-12-e `::test_tool_registered`

预期: `update_ip_expire_date` 在 `ALL_TOOLS` 里; `Tool.name == "update_ip_expire_date"`
(== 文件 stem); annotations.readOnlyHint is False

### 必跑测试命令

```bash
PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。

- [ ] 开工前已将任务文件从 `waiting` 改为 `doing`。
- [ ] `db/queries.py` 新增 `update_ip_expire_date` 完成。
- [ ] `tools/update_ip_expire_date.py` 新建完成。
- [ ] `tools/__init__.py` 注册完成。
- [ ] `test/mcp_tools/spec.md` 改 v2 完成（写入前 diff 已给用户审）。
- [ ] `test/mcp_tools/TC-12_update_ip_expire_date.py` 5 个用例完成。
- [ ] **必跑测试命令跑过且全部 PASS**。
- [ ] 对照 spec §6.6 / ADR-0008 §3.3 验证 ABCD 4 条规则全部满足。
- [ ] 没有改动「不动」清单里的文件（尤其 is_active / db/models.py / task 表）。
- [ ] 如有偏差或缺工具, 已记录并等用户拍板。
- [ ] 完成记录段已填（测试结果原样贴出）。

### 实现过程记录（实现者完工时填）

```text
改动文件:
- db/queries.py                                  新增 update_ip_expire_date()
- tools/update_ip_expire_date.py                 新建 (TOOL + handler)
- tools/__init__.py                              注册「写入修改工具 (admin)」一档
- test/mcp_tools/TC-12_update_ip_expire_date.py  新建 5 用例
- test/mcp_tools/TC-01_registration_and_ordering.py  7→8 工具 (用户拍板同批改)
- test/mcp_tools/spec.md                         v2 (需求窗口已落, 本批一起 commit)

新增工具/方法:
- 名字: update_ip_expire_date
  住: db/queries.py + tools/update_ip_expire_date.py
  干啥: 白名单 patch ip_record.expire_date 单列
  测试: TC-12-a..e
  审批: 用户 2026-06-13 对话拍板（只设具体日期 / 允许过去日期 / 查不到跳过走 register_ip）

测试结果:
- PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v -> 62 passed in 0.82s

偏差 / 风险:
- 偏差(已拍板): TC-01 写死「7 个工具」与新增工具冲突, 用户 2026-06-13 拍板同批改 8 个
  + 五段顺序(数据查询→写入修改 admin→运维)。不在原任务单改动文件清单内, 已确认。
- 无其他偏差。is_active / db/models.py / 任何 task 表均未碰; ABCD 4 条规则全满足。
```

### Claude 验收检查清单

□ 对照用户原话 / 业务目标检查实现没有跑偏
□ 对照 ADR-0008 §3.3 检查 ABCD 4 条规则
□ 对照 spec §6.6 检查输入、输出、失败分支一致
□ 跑必跑测试命令并记录结果
□ 检查实现者完工标准全部满足
□ 偏差但合理 -> 抛给用户决策
□ 偏差不合理 -> 打回实现者修改

---

## 完成记录（done 时追加）

> 任务完成后再填。waiting 阶段不要预填。

```text
完成日期: 2026-06-13
完成 commit: (本次功能 commit)
任务状态: doing -> done
改动摘要:
  - db/queries.py 新增 update_ip_expire_date(ip_id, expire_date): 白名单 patch
    ip_record.expire_date 单列, 只校验 YYYY-MM-DD 格式, 不拦过去日期, 不碰 is_active。
  - 新建 tools/update_ip_expire_date.py (TOOL + handler), description 写全批量看图
    四步场景 + ok/not_found/invalid_date 三档转告 + 查不到走 register_ip 不自动登记。
  - tools/__init__.py 加「写入修改工具 (admin)」一档注册。
  - 新建 TC-12 五用例 (含 TC-12-b 白名单不越界核心)。
  - 同批改 TC-01 7→8 工具 (用户拍板) + spec.md v2 (需求窗口已落)。
测试命令: PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v
测试结果: 62 passed in 0.82s
未覆盖风险:
  - 本工具只校验日期格式, 不拦过去日期 (用户拍板「允许任意合法日期」), 误填一个很早的
    到期日会让该 IP 被巡检判过期, 属预期行为, 由用户/agent 负责填对。
后续任务:
  - 「按出口IP查已登记 IP (含过期)」查询工具暂不做 (用户拍板查不到走 register_ip)。
  - admin/user 真正拆两套 MCP server 仍留下波 (ADR-0007 §8 + ADR-0008 §3.1)。
```
