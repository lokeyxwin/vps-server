# T-25 get_registered_vps —— 查全量已登记 VPS(装/未装、忙/闲、过期/未过期)

**ID**: T-25
**状态**: waiting
**前置依赖**: none
**后续依赖**: 未来可选 `update_vps_expire_date`(对称 update_ip_expire_date; 本工具产出的 vps_id 喂给它)
**关联 ADR**: 无新 ADR(CLAUDE.local §14.5: 加查询工具不开 ADR, 除非引入新架构决策)
**关联 spec**: test/mcp_tools/spec.md(§1 总账 +1 行 + 新增 §6.8 + §三 修订历史 v4, 本任务同批改)

> 配方同 T-24 `get_registered_ips`(列全量 ip_record 的兄弟), 本任务是它在 VPS 表的对称版。

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名 `waiting_25_get_registered_vps.md` 改为
      `doing_25_get_registered_vps.md`。

### 必读清单

领取后、写代码前, 必须显式读取:

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`(§14.1 admin/user + §14.5 工具集增量)
- [ ] `docs/adr/README.md`
- [ ] `docs/adr/0005-vps-stage-as-resource-lock.md`(stage 资源锁语义 connectable/running)
- [ ] `docs/adr/0008-main-as-worker-runner-and-db-queries-home.md`(db/queries.py 边界)
- [ ] `test/mcp_tools/spec.md`
- [ ] `db/queries.py`(看 `get_registered_ips` 范式 —— 本工具照抄改 VPS)
- [ ] `db/models.py::VPSRecord`(字段现状)
- [ ] `tools/get_registered_ips.py`(TOOL + handler 范式样板, T-24 产物)
- [ ] `tools/__init__.py`(ALL_TOOLS 注册形态)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "按照这个配方, 继续写一个获取 VPS 表的查询工具"

> 对齐拍板: 命名 `get_registered_vps` / 用途「运维看 VPS 池全貌 + 拿 vps_id」通用一套
> 字段 / **不返 SSH 凭据(port/username/password 都不返)** / 全量返回 / 暴露 admin

### Claude 整理后的业务理解

- 用途: 跟 `get_registered_ips` 对称 —— 一眼看所有已登记 VPS 的形状(装没装 xray /
  忙闲 stage / 挂了几条代理 used_port_count / 到期 / 是否过期), 顺带拿到 vps_id
  供未来 `update_vps_expire_date` 用。
- 外部输入: 无参(全量)
- 数据流:
  - 读取: `vps_record`(全表, 不 join)
  - 写入: 无(纯只读)
- 同步 / 异步边界: 全同步只读
- 成功返回: 一个数组, 每条是一台已登记 VPS 的运维核心信息(装的/没装的/过期的都在)
- 失败返回: 无业务失败(空库返 `[]`)

### 本任务要解决什么

给系统一个**列全量已登记 VPS**的只读查询工具, 运维/agent 一眼看 VPS 池全貌,
并拿到 vps_id。

### 本任务不解决什么

- ❌ **绝不返回 SSH 凭据**: `password_encrypted`/明文密码、`port`(SSH端口)、
  `username`(登录名)都不返(用户拍「不返凭据」)
- ❌ 不 join(不返「这台挂了哪些 IP 出口明细」, 只返 used_port_count 计数)
- ❌ 不加过滤参数(全量返回, agent 自己筛)
- ❌ 不做 `update_vps_expire_date`(本任务只查; 写入工具另开 task, 走 §14.3 ABCD)
- ❌ 不开新 ADR(CLAUDE.local §14.5)

---

## 2. 实现参考

### 验收锚点

- `test/mcp_tools/spec.md` §6.8(本任务同批新增)
- `CLAUDE.local.md` §14.1(admin)+ §14.5(加工具不开 ADR/不 assert 总数)
- `docs/adr/0005-*.md`(stage 语义)

### 改动文件清单

#### 改 `db/queries.py`

新增只读查询函数 `get_registered_vps() -> list[dict]`。照同文件 `get_registered_ips`
范式: 单表 `vps_record`, 不 join, 不过滤, 全量。**不返任何凭据字段**。

#### 新建 `tools/get_registered_vps.py`

照 `tools/get_registered_ips.py` 范式, 导出 `TOOL` + `handler`。

#### 改 `tools/__init__.py`

import + `ALL_TOOLS` 在「数据查询工具」一档加
`(_get_registered_vps_tool, _get_registered_vps_handler)`(放 get_registered_ips 之后)。

#### 改 `test/mcp_tools/spec.md`（本任务同批落, 写入前列 diff 给用户审）

- §1 总账加一行 get_registered_vps(类别「数据查询」)
- §6 新增 §6.8 返回形状
- §三 修订历史加 v4
- 不动固定数字(§14.5)

#### 新建 `test/mcp_tools/TC-14_get_registered_vps.py`

#### 改 `test/mcp_tools/TC-01_registration_and_ordering.py`

`_EXPECTED_NAMES_SET` + `_EXPECTED_NAMES_ORDER` 各加 `get_registered_vps`
(放「数据查询」段, get_registered_ips 之后)。**不写死总数**(§14.5)。

#### 不动

```text
- db/models.py（不加字段）
- ip_record / proxy_record / 任何 task 表
- password / port / username（SSH 凭据, 绝不返）
- get_vps_registration_status（单台装机进度查询, 跟本工具职责不同, 不动）
```

### 实现轮廓

```python
# db/queries.py
def get_registered_vps() -> list[dict]:
    """列出全部已登记 VPS(装/未装、忙/闲、过期/未过期), 单表 vps_record, 不 join, 不过滤.

    运维看 VPS 池全貌 + 拿 vps_id. **绝不返 SSH 凭据(密码/端口/登录名)**.

    返回 list[dict], 每条:
        {"vps_id": int, "ip": str,
         "os_name": str, "os_version": str,
         "xray_version": str,            # ""=还没装 xray
         "stage": str,                   # connectable=空闲 / running=有工人在用
         "used_port_count": int,         # 挂了几条业务代理
         "expire_date": "2026-06-18" | None,
         "is_active": 1 | 0,             # 1可用/0过期
         "provider_domain": str}
    空库返 [].
    """
    with session_scope() as s:
        rows = (
            s.query(VPSRecord)
            .order_by(VPSRecord.is_active.asc(),     # 过期(0)排前
                      VPSRecord.ip)
            .all()
        )
        return [
            {
                "vps_id": v.id,
                "ip": v.ip,
                "os_name": v.os_name,
                "os_version": v.os_version,
                "xray_version": v.xray_version,
                "stage": v.stage,
                "used_port_count": v.used_port_count,
                "expire_date": v.expire_date.isoformat() if v.expire_date else None,
                "is_active": v.is_active,
                "provider_domain": v.provider_domain,
            }
            for v in rows
        ]


# tools/get_registered_vps.py
TOOL = Tool(
    name="get_registered_vps",
    title="列出全部已登记 VPS（装/未装、忙/闲、过期/未过期）",
    description=(... 见下「description 要点」...),
    inputSchema={"type": "object", "properties": {},
                 "required": [], "additionalProperties": False},
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False,
        idempotentHint=True, openWorldHint=False,
    ),
)

async def handler(arguments: dict | None) -> list[TextContent]:
    result = get_registered_vps()
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    return [TextContent(type="text", text=payload)]
```

### description 要点（教 agent）

- 一句话意图: 列**全部**已登记 VPS(装的 + 没装的 + 过期的都在), 看池子全貌 + 拿 vps_id。
- 跟 `get_vps_registration_status`(按 vps_id/task_id 查**单台**装机进度)区分:
  本工具是**列全量**, 不带 task 进度。
- 字段说明:
  - `xray_version=""` → 还没装 xray
  - `stage=running` → 有工人正在操作这台(挑机会跳过); `connectable` → 空闲
  - `used_port_count` → 挂了几条业务代理
  - `is_active=0` → 已过期/停用; `expire_date=null` → 未知到期日
- 反例:
  - ❌ 本工具**不返 SSH 账密/端口**, 别拿它的结果当「登录 VPS 的凭据」。
  - ❌ 别拿它当「可用代理节点列表」给用户连(那是 get_available_proxy_nodes)。

### 数据结构 / 返回字段（抠信息类）

| 字段 | 含义 | 来源 |
|---|---|---|
| `vps_id` | VPS 主键(喂给未来 update_vps_expire_date) | vps_record.id |
| `ip` | 入口IP | vps_record.ip |
| `os_name/os_version` | 操作系统 | vps_record |
| `xray_version` | xray 版本, ""=未装 | vps_record |
| `stage` | connectable空闲/running占用 | vps_record |
| `used_port_count` | 挂了几条代理 | vps_record |
| `expire_date` | 到期日, null=未知 | vps_record |
| `is_active` | 1可用/0过期 | vps_record |
| `provider_domain` | 服务商域名 | vps_record |

### 缺工具 / 缺信息先报告

- 若发现需要 join / 加过滤 / 返凭据才能满足 → 停下报告(本任务明确不做)。

---

## 3. 验收交付

### 测试用例

#### TC-14-a `test/mcp_tools/TC-14_get_registered_vps.py::test_returns_all_states`

业务故事: 库里有「装了 xray running」「没装 connectable」「过期 is_active=0」的 VPS,
调工具应**全返**。
输入: 预置 ≥3 条覆盖 装/未装、忙/闲、过期/未过期
预期: 数组含全部; 各条 xray_version / stage / is_active / expire_date 形状正确
不应发生: 漏掉没装 xray 的 / 漏掉过期的

#### TC-14-b `::test_shape_no_credentials` ⭐（安全）

预期: 每条含 `vps_id` + `ip` + `xray_version` + `stage` + `used_port_count` +
`expire_date` + `is_active`; **不含 password / port / username 任何凭据字段**
不应发生: 泄露 SSH 凭据

#### TC-14-c `::test_empty_db_returns_empty_list`

输入: 空 vps_record
预期: 返 `[]`(不抛异常)

#### TC-14-d `::test_tool_registered`

预期: `get_registered_vps` 在 ALL_TOOLS; `Tool.name=="get_registered_vps"`;
annotations.readOnlyHint is True

### 必跑测试命令

```bash
PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v
```

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`。

- [ ] 开工前已将任务文件从 `waiting` 改为 `doing`。
- [ ] `db/queries.py` 新增 `get_registered_vps` 完成。
- [ ] `tools/get_registered_vps.py` 新建完成。
- [ ] `tools/__init__.py` 注册完成。
- [ ] `test/mcp_tools/spec.md` 改 v4 完成（写入前 diff 已给用户审）。
- [ ] `TC-14` 新建 + `TC-01` 白名单 set/order 各 +1 行(**不写死总数**)。
- [ ] **必跑测试命令跑过且全部 PASS**。
- [ ] 验证返回**不含任何 SSH 凭据**(TC-14-b)。
- [ ] 没有改动「不动」清单(get_vps_registration_status / db/models.py / 各表)。
- [ ] 如有偏差或缺工具, 已记录并等用户拍板。
- [ ] 完成记录段已填（测试结果原样贴出）。

### 实现过程记录（实现者完工时填）

```text
改动文件:
- db/queries.py                              (新增 get_registered_vps)
- tools/get_registered_vps.py                (新建工具模块)
- tools/__init__.py                          (注册 + docstring)
- test/mcp_tools/TC-14_get_registered_vps.py (新建, TC-14-a..d)
- test/mcp_tools/TC-01_registration_and_ordering.py (白名单 set/order 各 +1)
- test/mcp_tools/spec.md                     (v4, 需求窗口已落定, 随本 commit 入库)

新增工具/方法:
- 名字: get_registered_vps
  住: db/queries.py + tools/get_registered_vps.py
  干啥: 列全量已登记 VPS, 只读, 不返凭据
  测试: TC-14-a..d
  审批: 用户 2026-06-14 对话拍板(命名/通用字段/不返凭据/全量/admin)

测试结果:
- PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v -> 70 passed in 0.67s

偏差 / 风险:
- none。实现轮廓与 spec §6.8 一致, 单表 vps_record 不 join, 返 10 字段不含任何 SSH 凭据。
```

### Claude 验收检查清单

□ 对照用户原话 / 业务目标检查实现没有跑偏
□ 对照 spec §6.8 检查返回形状一致
□ 确认不返任何 SSH 凭据
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
改动摘要: 新增只读查询工具 get_registered_vps —— 列全量已登记 VPS(装/未装、忙/闲、
          过期/未过期), 单表 vps_record 不 join、无过滤、绝不返 SSH 凭据(密码/端口/
          登录名都不返), 给运维看 VPS 池全貌 + 拿 vps_id(备未来 update_vps_expire_date)。
          db/queries.py 加业务函数 + tools/get_registered_vps.py 协议适配 +
          tools/__init__.py 数据查询档注册(get_registered_ips 之后) + TC-14 新建 +
          TC-01 白名单 +1。
测试命令: PYTHONPATH=. uv run python -m pytest test/mcp_tools/ test/db/ -v
测试结果: 70 passed in 0.67s (TC-14-a..d 全 PASS, 含 TC-14-b 无凭据安全验证)
未覆盖风险: 无。纯只读单表查询, 不写库、不改"不动"清单(get_vps_registration_status /
            db/models.py / 各表 均未碰)。
后续任务: 可选 update_vps_expire_date(对称 update_ip_expire_date, 走 §14.3 ABCD)
```
