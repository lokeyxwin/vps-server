# T-21 删除 ip_record.status 字段 — proxy_record 当唯一真相源

**ID**: T-21
**状态**: waiting
**前置依赖**: ADR-0010
**后续依赖**: 后续 CleanupWorker / ExpiryWorker 启用时不再维护 ip.status
**关联 ADR**: docs/adr/0010-drop-ip-record-status.md
**关联 spec**: test/ip_probe_worker/spec.md (本任务同批升 v2→v3)

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`
- [ ] 开始写代码前, 已将文件名从 `waiting_21_*.md` 改为 `doing_21_*.md`

### 必读清单

领取后, 写代码前必须显式 Read:

- [ ] `CLAUDE.md` / `CLAUDE.local.md`
- [ ] `docs/adr/README.md`
- [ ] `docs/adr/0010-drop-ip-record-status.md` (本任务关联 ADR)
- [ ] `docs/adr/0001-workers-replace-services.md` (被本 ADR 局部 supersede)
- [ ] `test/ip_probe_worker/spec.md` v2 (要升 v3 同步去掉 status 描述)
- [ ] 本任务点名要改的源码 + 测试文件 (见 §2)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "纳管 proxy 已经把端口抽出来写入表但是 IP 表还记为可用, 会有歧义"
> "porxy 表有事实就代表使用中"
> "IP 的状态机就不需要了, 因为一旦写入是靠任务派发任务完成就配置好, 没配置好再加重试, 完美设计啊"

### Claude 整理后的业务理解

- **触发**: T-18 端到端纳管后 DB 出现 ip_record.status='usable' 但 proxy_record.status='using' 自相矛盾
- **根因**: ip_record.status 是把"这条 IP 在不在 proxy_record 里挂着"复述了一遍, XrayWorker 纳管路径写默认 USABLE / ProxyDeployWorker 部署路径写 USING, 两条路径漂移
- **业务规约改变**: 单一真相源 = proxy_record 是否有 ip_id 关联. agent 看"配好没"用 `task.status='done' + proxy_node != null` 判定
- **数据流改动**:
  - 读取: 不再读 ip.status; LEFT JOIN proxy_record 表达"空闲"
  - 写入: IPProbeWorker 入库不写 status; ProxyDeployWorker / XrayWorker 不再写 ip.status
- **MCP contract 改动**: `get_ip_registration_status` 返回 dict 去掉 ip.status; description 改判定规则

### 本任务解决什么

删 IPStatus enum + IPRecord.status 字段 + 同步删 5 处生产代码触点 + 改 6 处测试. 消除双轨数据漂移可能性.

### 本任务不解决什么

- CleanupWorker / ExpiryWorker 的具体行为 (后续 ADR)
- VPS / IP 过期 15 天阈值 (后续 ADR)
- ProxyStatus 评估 (3 档真业务状态, 不在范围)
- VPSStage 评估 (不相关)
- is_active 字段评估 (不相关)
- 生产 MySQL 迁移 (本项目仍 SQLite)

---

## 2. 实现参考

### 验收锚点

- `docs/adr/0010-drop-ip-record-status.md` §决策 1-4 + §影响清单
- `test/ip_probe_worker/spec.md` v3 (本任务同批升)

### 改动文件清单

#### 改 `db/models.py`

```text
1. 删 class IPStatus 整段 (L314-334)
2. 删 IPRecord.status 字段定义 (L385 附近: status: Mapped[str] = mapped_column(...))
3. 删 from_xxx 工厂方法里的 status 入参 (L442 附近)
4. IPRecord docstring 提及 status 状态机的句子删掉
```

#### 改 `db/queries.py`

```text
1. query_ip_status 返回 dict 删 "status": ip.status (L171)
2. 顶部 import 验证 (本来就没 import IPStatus, 不用动)
3. _build_proxy_node / list_available_proxies 不动 (它们走 proxy_record.status / ip.is_active 不依赖 ip.status)
```

#### 改 `tools/get_ip_registration_status.py`

```text
1. description 删 "ip.status=usable / using" 相关转告规则
2. description 删 L65 反例 "不要把 ip.status=usable 转告 '配好了'"
3. description 新增规则:
   - task.status=done + proxy_node 非空 → "配好了, 节点 ...account/pwd"
   - task.status=done + proxy_node 空 → 异常 (本来该有节点), 让用户联系管理员
4. 其他 status 含义保留 (in_progress / pending / failed 各 last_error_code 等)
```

#### 改 `workers/proxy_deploy_worker.py`

```text
1. 删 IPStatus import (L36 附近)
2. 删 _mark_done 里 ip.status = IPStatus.USING 一行 (L551)
3. _mark_done 其他逻辑不动 (proxy_record INSERT / vps stage 释放 / task 写 done 等)
```

#### 改 `workers/ip_probe_worker.py`

```text
1. 删 IPStatus import (L40)
2. 删顶部 docstring 提及 IPStatus 的句子 (L18)
3. 删 L538 附近注释 "ip_record.status = usable"
4. _persist_ip / 入库分支保持不变 (本来就没显式写 status, 走 ORM default 也将随字段删除)
```

#### 改 `test/_data_structures/test_ip_record_status.py`

```text
整文件删 (整个文件围绕 IPStatus 枚举的 schema TC, 字段删了就无意义)
```

#### 改 `test/proxy_deploy_worker/TC-07_full_happy_using.py`

```text
1. 删 IPStatus import (L29)
2. 删 ip.status == IPStatus.USING 断言 (L152)
3. 新增断言: 用 session.query(ProxyRecord).filter(ProxyRecord.ip_id == ip.id).first() 验证 proxy_record 存在
```

#### 改 `test/proxy_deploy_worker/TC-09_inner_ping_rollback.py`

```text
1. 删 IPStatus import (L22)
2. 删 ip.status == IPStatus.USABLE 断言 (L115)
3. 新增断言: 验证 session.query(ProxyRecord).filter(ProxyRecord.ip_id == ip.id).first() 返回 None (没这条 ip_id 的 proxy_record)
```

#### 改 `test/proxy_deploy_worker/_helpers.py`

```text
1. 删 IPStatus import (L18)
2. _new_ip 默认 status 入参删 (L143)
3. 函数体内 ip.status = status 一行删 (L155)
```

#### 改 `test/ip_probe_worker/TC-08_queued_success.py`

```text
1. 删 IPStatus import (L34 改为只 from db.models import IPRecord, IPTask, TaskStatus)
2. 删 rec.status == IPStatus.USABLE 断言 (L116)
```

#### 改 `test/ip_probe_worker/spec.md`

```text
1. v2 §6 关于 ip.status 状态机的整段描述删掉
2. 升 v3, 修订历史加: v3 2026-06-10 删 ip_record.status, 改用 proxy_record 存在性表达"在用"
3. 用户原话节选段加本次对话原话
```

#### 改 `test/proxy_deploy_worker/spec.md` (实现窗口反问后补入, 需求窗口疏漏)

```text
1. §6 收尾伪代码段 L166-170 删 `# ip_record: usable → using` + `ip.status = IPStatus.USING` 两行
2. 升 v1.1 → v1.2, 修订历史加: v1.2 2026-06-10 §6 收尾伪代码去掉 ip.status=USING (随 ADR-0010 / T-21)
```

> 注: ADR-0010 §影响清单 漏列了 `test/proxy_deploy_worker/spec.md`, 实现窗口照
> 任务单 §2 实施时发现 spec 里有伪代码段写了 `ip.status = IPStatus.USING`,
> 按 CLAUDE.md §5.6 停下来反问需求窗口, 需求窗口确认本任务一并改并补入本清单。
> ADR-0010 本身不动 (永不改原则)。

#### 不动

- `db/models.py::ProxyStatus` (3 档真业务状态)
- `db/models.py::VPSStage` (不相关)
- `db/models.py::IPRecord.is_active / expire_date / egress_ip / ...` 其他字段
- `workers/xray_worker.py` (本来就没 import IPStatus 也没写 status)
- `services/` 任何文件 (已退出活跃路径)
- 任何 MCP 工具 Tool 签名 (只改 description 文字)
- `tools/get_available_proxy_nodes.py` (不依赖 ip.status, 走 ip.is_active)

### 缺工具 / 缺信息先报告

实现者遇到以下情况停下来报告:

- 改 description 时拿不准 "task.status=done + proxy_node 空" 该提示什么转告话术
- 改测试断言时发现现有 fixture / _helpers 调用链跟描述对不上
- dev 库重建后跑测试发现某条 TC 假设了 ip.status 字段存在 (本任务清单外的 TC)

---

## 3. 验收交付

### 测试用例

本任务是 schema 删字段 + 测试同步, 不引入新行为, 不新增 TC.

需要改的现有 TC:
- `test/proxy_deploy_worker/TC-07_full_happy_using.py`: 把 ip.status 断言换成 proxy_record 存在性断言
- `test/proxy_deploy_worker/TC-09_inner_ping_rollback.py`: 把 ip.status 断言换成 proxy_record 不存在断言
- `test/ip_probe_worker/TC-08_queued_success.py`: 删 ip.status 断言
- `test/_data_structures/test_ip_record_status.py`: 整文件删

### 必跑测试命令

```bash
PYTHONPATH=. uv run pytest test/_data_structures test/ip_probe_worker test/proxy_deploy_worker test/xray_worker test/mcp_tools -v --tb=short
```

(memory `pytest TC 收集坑`: 现有项目 TC-NN_*.py 跟 pytest 默认 pattern 不匹配, 上面命令走的是项目 pytest.ini 配置; 如收集为 0, 改为显式 glob `test/<dir>/TC-*.py` 或显式列文件)

### 完工后验证步骤

1. 跑必跑测试命令 → 全 PASS
2. dev SQLite 用户手动清: `rm vps_server.db`
3. `PYTHONPATH=. uv run python main.py init-db` 重建
4. (可选)端到端再跑一次 register_vps `203.0.113.10` 走纳管路径, 审 DB 确认 ip_record 表无 status 列

### 实现者完工标准

> ⚠️ 全部打勾才允许改 `doing` → `done`

- [ ] 开工前已 waiting → doing
- [ ] 所有 §2 改动文件清单完成
- [ ] 必跑测试命令跑过且全部 PASS
- [ ] 对照 ADR-0010 §决策 1-4 + 影响清单核对一致
- [ ] 没改"不动"清单文件
- [ ] dev 库清+init-db 重建验证过
- [ ] 完成记录段已填

### 实现过程记录 (实现者完工时填)

```text
改动文件:
生产代码 (4 文件):
- db/models.py         删 class IPStatus 整段 + IPRecord.status 字段 + from_form status 入参
- db/queries.py        query_ip_status 返回 dict 去掉 "status": ip.status, docstring 同步改
- tools/get_ip_registration_status.py
                       description 重写: 加 task.status=done+proxy_node=null 异常分支, 删 ip.status 反例
- workers/proxy_deploy_worker.py
                       删 IPStatus import + _mark_done 里 ip.status = IPStatus.USING 一行 + docstring 同步
- workers/ip_probe_worker.py
                       删 IPStatus import + 顶部 docstring 提及 + _persist_and_dispatch docstring 同步

测试 (5 文件):
- test/_data_structures/test_ip_record_status.py            整文件删 (6 TC 整体随枚举失效)
- test/proxy_deploy_worker/_helpers.py                      insert_ip 去掉 status 入参 + ip.status=status 一行
- test/proxy_deploy_worker/TC-07_full_happy_using.py        删 IPStatus import; tc07e 改为 proxy_record 存在性断言
- test/proxy_deploy_worker/TC-09_inner_ping_rollback.py     删 IPStatus import; tc09d 改为 proxy_record 不存在断言
- test/ip_probe_worker/TC-08_queued_success.py              删 IPStatus import + rec.status 断言

spec (2 文件):
- test/ip_probe_worker/spec.md                              v2 → v3: §1 §3 §6 §9 §F §G 同步去 status, 修订历史加 v3
- test/proxy_deploy_worker/spec.md                          v1.1 → v1.2: §6 收尾伪代码删 ip.status=USING 两行 (任务单 §2 补遗)

任务单 (1 文件):
- task/doing_21_*.md                                        §2 补 test/proxy_deploy_worker/spec.md 改动条目 (反问后补入)

测试结果:
- PYTHONPATH=. uv run pytest test/_data_structures test/ip_probe_worker/TC-*.py
                            test/proxy_deploy_worker/TC-*.py test/xray_worker/TC-*.py
                            test/mcp_tools/TC-*.py
  → 211 passed, 3 skipped (skip 都是预存的真机 / 锁原子性 TC, 跟本次无关)

偏差 / 风险:
- 需求窗口疏漏: ADR-0010 §影响清单漏列 test/proxy_deploy_worker/spec.md
  伪代码段 (L166-170) 里 ip.status = IPStatus.USING 两行。实现窗口照清单实施时
  发现该 spec 跟代码冲突, 按 CLAUDE.md §5.6 停下来反问需求窗口。
  需求窗口确认本任务一并改并补入任务单 §2 (本任务单 §2 已补)。
  ADR-0010 本身不动 (永不改原则), 留作"档案瑕疵"。
- dev SQLite 重建步骤: 实现窗口完成代码 + 测试后不自动 rm 数据库, 用户手动跑:
  rm vps_server.db && PYTHONPATH=. uv run python main.py init-db
```

---

## 完成记录 (done 时追加)

```text
完成日期: 2026-06-10
完成 commit: (跟本任务单同 commit, 见 git log)
任务状态: doing -> done

改动摘要:
- 删除 IPStatus 枚举类 + IPRecord.status 字段 + from_form status 入参 (db/models.py)
- 删除 query_ip_status 返回 dict 的 "status": ip.status 字段 (db/queries.py)
- 删除 ProxyDeployWorker / IPProbeWorker 的 IPStatus import + 触点 (workers/*.py)
- 重写 get_ip_registration_status description 判定规则:
  task.status=done + proxy_node != null = "配好", proxy_node=null = 异常 (tools/*.py)
- 整文件删 test_ip_record_status.py 6 TC (字段没了 schema TC 无意义)
- TC-07 / TC-09 改为 proxy_record 存在性断言代替旧 ip.status 断言
- _helpers.py / TC-08 同步去 IPStatus
- spec.md 双升: ip_probe_worker v2→v3, proxy_deploy_worker v1.1→v1.2

测试命令:
PYTHONPATH=. uv run pytest test/_data_structures test/ip_probe_worker/TC-*.py
              test/proxy_deploy_worker/TC-*.py test/xray_worker/TC-*.py
              test/mcp_tools/TC-*.py

测试结果: 211 passed, 3 skipped (预存 skip, 跟本次无关), 0 failed

未覆盖风险:
- ADR-0010 §影响清单漏列 test/proxy_deploy_worker/spec.md 一项, 实现窗口反问后补入
  任务单 §2; ADR 本身不动 (永不改原则), 留作"档案瑕疵"
- 真机端到端验证 (跑一次 register_vps 走纳管路径审 DB) 留给用户手动执行;
  实现窗口确认: dev SQLite 由用户手动 rm vps_server.db + python main.py init-db 重建

后续任务:
- 跑过 dev 库重建 + 真机端到端纳管 1 次, 确认 ip_record 表无 status 列且业务流不受影响
- 未来加 CleanupWorker / ExpiryWorker 时, "释放 IP 回池子" 直接走删 proxy_record 即可,
  不需要再维护 IP 表上的状态字段 (ADR-0010 §决策 §2 已论证)
```
