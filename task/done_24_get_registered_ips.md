# T-24 get_registered_ips —— 查全量已登记 IP(过期+未过期), 补「按出口IP补到期日」批量场景的缺口

**ID**: T-24
**状态**: waiting
**前置依赖**: T-23 update_ip_expire_date(已 done; 本工具产出的 ip_id 喂给它)
**后续依赖**: none
**关联 ADR**: 无新 ADR(CLAUDE.local §14.5: 加查询工具不开 ADR, 除非引入新架构决策; 本工具是 ADR-0007 §8「留下波」的查询工具落地)
**关联 spec**: test/mcp_tools/spec.md(§1 总账 +1 行 + 新增 §6.7 + §三 修订历史 v3, 本任务同批改)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名从 `waiting_24_get_registered_ips.md` 改为
      `doing_24_get_registered_ips.md`。

### 必读清单

领取后、写代码前, 必须显式读取:

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`(尤其 §14.1 admin/user 评估 + §14.5 工具集增量)
- [ ] `docs/adr/README.md`
- [ ] `docs/adr/0008-main-as-worker-runner-and-db-queries-home.md`(§2 db/queries.py 边界)
- [ ] `docs/adr/0010-drop-ip-record-status.md`(ip 没有 status 字段, 过期看 is_active + expire_date)
- [ ] `test/mcp_tools/spec.md`
- [ ] `db/queries.py`(看 `list_available_proxies` 范式 —— 本工具是它的兄弟查询)
- [ ] `db/models.py::IPRecord`(字段现状)
- [ ] `tools/get_available_proxy_nodes.py`(TOOL + handler 范式样板)
- [ ] `tools/__init__.py`(ALL_TOOLS 注册形态)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "现在的 update_ip_expire_date 工具是从 proxy 表查可用节点的 IP 从而修改 IP 的
>  到期日期; 现在需要做一个新工具叫做 get 什么什么 IP nodes 这个工具查询的是 IP 表
>  的过期和未过期; 查出来是一个数组; 可以看到到期 IP 和未到期的形状, 然后再
>  update_ip_expire_date 传入出口IP 更新到期日期"

> 对齐拍板: 命名 `get_registered_ips` / 定位用 ip_id(update 工具不动)/ 只返核心
> 5 字段(不 join proxy)/ 全量返回无过滤 / 暴露 admin / 上游密码绝不返

### Claude 整理后的业务理解

- 痛点: 现在 agent 拿 ip_id 的唯一来源是 `get_available_proxy_nodes`(查 proxy_record
  的 USING 可用节点), 过期/没挂的 IP 在 proxy 表没有 USING 行 → agent 查不到它们的
  ip_id → 改不了到期日(Telegram 实测: -13天/-14天 那两条补不了)。
- 本工具补缺口: 直接查 `ip_record` 表本身, 把所有已登记 IP(过期 + 未过期)全列出来,
  每条带 ip_id, agent 按出口IP对上截图 → 拿 ip_id → 调 `update_ip_expire_date` 精准改。
- 外部输入: 无参(全量)
- 数据流:
  - 读取: `ip_record`(全表, 不 join)
  - 写入: 无(纯只读)
- 同步 / 异步边界: 全同步只读
- 成功返回: 一个数组, 每条是一条已登记 IP 的核心信息(可用的 + 过期的都在)
- 失败返回: 无业务失败(空库返 `[]`)

### 批量补到期日完整闭环(本工具补上后)

```
用户甩面板截图「只补已登记的」
   ↓
agent 调 get_registered_ips() 拿全量已登记 IP 数组(过期+未过期都在)
   ↓
对截图每一行的出口IP:
   ├─ 在数组里匹配到 → 拿该条 ip_id → 调 update_ip_expire_date(ip_id, 到期日)
   └─ 数组里没有 → 没登记 → 跳过, 提示走 register_ip(下次纳管再进来)
   ↓
汇总: ✅已补 N 条 / ⏭️没登记跳过 M 条
```

### 本任务要解决什么

给系统一个**列全量已登记 IP(过期 + 未过期)**的只读查询工具, 让 agent 能拿到
过期/没挂 IP 的 ip_id, 配合 T-23 `update_ip_expire_date` 跑通「看截图批量补到期日」。

### 本任务不解决什么

- ❌ 不 join proxy_record(不返「这条 IP 挂在哪台 VPS」, 用户拍只要核心 5 字段)
- ❌ 不加过滤参数(全量返回, agent 自己筛; 用户拍 YAGNI)
- ❌ **绝不返回上游密码**(`password_encrypted` / 明文都不返)—— 补到期日不需要凭据
- ❌ 不改 `update_ip_expire_date`(定位仍用 ip_id, ABCD 规则 A; 出口IP 只是 agent
  在本工具结果里对截图用的匹配键, 实际传给 update 的是 ip_id)
- ❌ 不开新 ADR(CLAUDE.local §14.5)

---

## 2. 实现参考

### 验收锚点

- `test/mcp_tools/spec.md` §6.7(本任务同批新增)
- `CLAUDE.local.md` §14.1(admin/user: 本工具 admin)+ §14.5(加工具不开 ADR/不 assert 总数)
- `docs/adr/0010-*.md`(ip 无 status 字段, 过期判定看 is_active + expire_date)

### 改动文件清单

#### 改 `db/queries.py`

新增只读查询函数 `get_registered_ips() -> list[dict]`。照 `list_available_proxies`
范式(同文件兄弟函数), 但**查单表 ip_record 不 join**, 不过滤, 返全量。

#### 新建 `tools/get_registered_ips.py`

照 `tools/get_available_proxy_nodes.py` 范式, 导出 `TOOL` + `handler`。
handler 调 `db.queries.get_registered_ips`, json.dumps 数组返 TextContent。

#### 改 `tools/__init__.py`

import + `ALL_TOOLS` 在「数据查询工具」一档加
`(_get_registered_ips_tool, _get_registered_ips_handler)`。顶部 docstring
数据查询分类补一行。

#### 改 `test/mcp_tools/spec.md`（本任务同批落, 写入前列 diff 给用户审）

- §1 总账加一行 get_registered_ips(类别「数据查询」)
- §6 新增 §6.7 返回形状
- §三 修订历史加 v3
- (按 §14.5: 不动固定数字, 总账加行即可; TC-01 白名单 set +1 行)

#### 新建 `test/mcp_tools/TC-13_get_registered_ips.py`

#### 改 `test/mcp_tools/TC-01_registration_and_ordering.py`

`_EXPECTED_NAMES_SET` + `_EXPECTED_NAMES_ORDER` 各加 `get_registered_ips`
(放「数据查询」段, 即 get_available_proxy_nodes 之后)。**不写死总数**(§14.5)。

#### 不动

```text
- update_ip_expire_date（定位仍 ip_id, 不改）
- db/models.py（不加字段）
- proxy_record / vps_record / 任何 task 表
- password 字段（绝不返）
```

### 实现轮廓

```python
# db/queries.py
def get_registered_ips() -> list[dict]:
    """列出全部已登记 IP(过期 + 未过期), 单表 ip_record, 不 join, 不过滤.

    给 agent 补到期日批量场景用: 拿到过期/没挂 IP 的 ip_id, 配合
    update_ip_expire_date 精准改. 绝不返上游密码.

    返回 list[dict], 每条:
        {"ip_id": int, "egress_ip": str,
         "country_code": str, "country_name": str, "city": str,
         "expire_date": "2026-06-18" | None, "is_active": 1 | 0}
    空库返 [].
    """
    with session_scope() as s:
        rows = (
            s.query(IPRecord)
            .order_by(IPRecord.is_active.asc(),      # 过期(0)排前, 方便补
                      IPRecord.country_code,
                      IPRecord.egress_ip)
            .all()
        )
        return [
            {
                "ip_id": ip.id,
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
                "country_name": ip.country_name,
                "city": ip.city,
                "expire_date": ip.expire_date.isoformat() if ip.expire_date else None,
                "is_active": ip.is_active,
            }
            for ip in rows
        ]


# tools/get_registered_ips.py
TOOL = Tool(
    name="get_registered_ips",
    title="列出全部已登记 IP（过期+未过期）",
    description=(... 见下「description 要点」...),
    inputSchema={"type": "object", "properties": {},
                 "required": [], "additionalProperties": False},
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)

async def handler(arguments: dict | None) -> list[TextContent]:
    result = get_registered_ips()
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
```

### description 要点（教 agent）

- 一句话意图: 列**全部**已登记 IP(过期 + 未过期), 跟 `get_available_proxy_nodes`
  (只列可用代理节点)区分开 —— 本工具能看到过期/没挂的 IP。
- 典型场景: 用户甩面板截图「补这些 IP 的到期时间, 只补已登记的」→ agent 先调本工具
  拿全量 → 按出口IP对截图 → 匹配到的拿 ip_id 调 `update_ip_expire_date`; 没匹配到的
  跳过提示走 `register_ip`(见 §1 闭环图)。
- 字段说明: `is_active=0` 表示已过期/停用; `expire_date=null` 表示纳管时未知到期日。
- 反例:
  - ❌ 别拿本工具结果直接当「可用代理节点」给用户连(那是 get_available_proxy_nodes,
    且本工具不返账密)。
  - ❌ 别用 egress_ip 去调 update_ip_expire_date —— 用本工具结果里的 ip_id。

### 数据结构 / 返回字段（抠信息类, 字段命名见上轮廓）

| 字段 | 含义 | 来源 |
|---|---|---|
| `ip_id` | IP 主键, 喂给 update_ip_expire_date | ip_record.id |
| `egress_ip` | 出口IP（agent 对截图的键） | ip_record.egress_ip |
| `country_code/name`, `city` | 地区 | ip_record geoip 字段 |
| `expire_date` | 到期日, null=纳管未知 | ip_record.expire_date |
| `is_active` | 1可用/0过期 | ip_record.is_active |

### 缺工具 / 缺信息先报告

- 若发现需要 join proxy / 加过滤才能满足 → 停下报告(本任务明确不做)。

---

## 3. 验收交付

### 测试用例

#### TC-13-a `test/mcp_tools/TC-13_get_registered_ips.py::test_returns_all_active_and_expired`

业务故事: 库里有过期(is_active=0)和未过期(is_active=1)的 IP, 调工具应**两种都返**。
输入: 预置 1 条 active + 1 条 过期 + 1 条 expire_date=null 纳管
预期: 数组含全部 3 条; 各条 is_active / expire_date 形状正确
不应发生: 漏掉过期的 / 只返可用的

#### TC-13-b `::test_shape_has_ip_id_no_password` ⭐（安全）

预期: 每条含 `ip_id` + `egress_ip` + `expire_date` + `is_active`; **不含任何
password / username / entry_host 字段**
不应发生: 泄露上游凭据

#### TC-13-c `::test_empty_db_returns_empty_list`

输入: 空 ip_record
预期: 返 `[]`(不抛异常)

#### TC-13-d `::test_tool_registered`

预期: `get_registered_ips` 在 ALL_TOOLS; `Tool.name=="get_registered_ips"`;
annotations.readOnlyHint is True

### 必跑测试命令

```bash
PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。

- [ ] 开工前已将任务文件从 `waiting` 改为 `doing`。
- [ ] `db/queries.py` 新增 `get_registered_ips` 完成。
- [ ] `tools/get_registered_ips.py` 新建完成。
- [ ] `tools/__init__.py` 注册完成。
- [ ] `test/mcp_tools/spec.md` 改 v3 完成（写入前 diff 已给用户审）。
- [ ] `TC-13` 新建 + `TC-01` 白名单 set/order 各 +1 行(**不写死总数**)。
- [ ] **必跑测试命令跑过且全部 PASS**。
- [ ] 验证返回**不含密码/上游凭据**(TC-13-b)。
- [ ] 没有改动「不动」清单(update_ip_expire_date / db/models.py / 各表)。
- [ ] 如有偏差或缺工具, 已记录并等用户拍板。
- [ ] 完成记录段已填（测试结果原样贴出）。

### 实现过程记录（实现者完工时填）

```text
改动文件:
- db/queries.py                              (新增 get_registered_ips)
- tools/get_registered_ips.py                (新建工具模块)
- tools/__init__.py                          (注册 + docstring)
- test/mcp_tools/TC-13_get_registered_ips.py (新建, TC-13-a..d)
- test/mcp_tools/TC-01_registration_and_ordering.py (白名单 set/order 各 +1)
- test/mcp_tools/spec.md                     (v3, 需求窗口已落定, 随本 commit 入库)

新增工具/方法:
- 名字: get_registered_ips
  住: db/queries.py + tools/get_registered_ips.py
  干啥: 列全量已登记 IP(过期+未过期), 只读, 不返密码
  测试: TC-13-a..d
  审批: 用户 2026-06-14 对话拍板(命名/定位ip_id/核心5字段/全量/admin/不返密码)

测试结果:
- PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v -> 66 passed in 0.64s

偏差 / 风险:
- none。实现轮廓与 spec §6.7 一致, 单表 ip_record 不 join, 返回 7 字段不含任何凭据。
```

### Claude 验收检查清单

□ 对照用户原话 / 业务目标检查实现没有跑偏
□ 对照 spec §6.7 检查返回形状一致
□ 确认不返密码/上游凭据
□ 跑必跑测试命令并记录结果
□ 检查实现者完工标准全部满足
□ 偏差但合理 -> 抛给用户决策

---

## 完成记录（done 时追加）

> 任务完成后再填。waiting 阶段不要预填。

```text
完成日期: 2026-06-14
完成 commit: (本次功能 commit)
任务状态: doing -> done
改动摘要: 新增只读查询工具 get_registered_ips —— 列全量已登记 IP(过期+未过期),
          单表 ip_record 不 join、无过滤、绝不返上游凭据, 给「看面板截图批量补
          到期日」拿过期 IP 的 ip_id 配合 update_ip_expire_date。
          db/queries.py 加业务函数 + tools/get_registered_ips.py 协议适配 +
          tools/__init__.py 数据查询档注册 + TC-13 新建 + TC-01 白名单 +1。
测试命令: PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v
测试结果: 66 passed in 0.64s (TC-13-a..d 全 PASS, 含 TC-13-b 无密码验证)
未覆盖风险: 无。纯只读单表查询, 不写库、不改"不动"清单(update_ip_expire_date /
            db/models.py / 各表 均未碰)。
后续任务: none
```
