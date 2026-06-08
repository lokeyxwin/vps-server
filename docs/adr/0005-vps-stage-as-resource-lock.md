# 0005. vps_record.stage 作 VPS 资源锁(supersede ADR-0001 §决策 §4 单一 task 锁)

**日期**: 2026-06-08
**状态**: Accepted

---

## Supersedes / 补充

- **supersede(局部)** [[0001-workers-replace-services]] §决策 §4
  原决策: "一台 VPS 同时只能被 1 个 worker 持锁(task.locked_until 软锁, worker 抢到 task = 抢到 task.vps_id 那台机的操作权)"
  本 ADR 改: **两层锁并存**, vps_record.stage 是 VPS 资源锁(跨工人 / 跨业务部门), vps_task 表是任务并发锁(同一任务防多工人抢)

> 注: ADR-0001 文件本身不动(永不改原则), 其余决策(worker 替代 services / task 表协调 / MCP 三类工具暴露等)**全部保留有效**, 只是 §决策 §4 那一条被本 ADR 取代。当下真相以本 ADR + 后续 ADR + spec.md 为准。

---

## 背景

T-07(XrayWorker 装机+纳管)完工真机端到端跑通后, 用户审 DB 出现一个直觉不顺的现象:

```
vps_task.status='done'  (任务完工)
vps_record.stage='running'  (机器仍是 running)
```

用户原话:

> "task表拿到的那个任务结束了, 数据写完了 vps对应的vps不得还回去吗还占着干啥呀 不给别人用吗"

翻代码发现 **两处文档对 `VPSStage.RUNNING` 的语义定义直接冲突**:

| 文档 | RUNNING 的语义 | 完工后应是 |
|------|---------------|----------|
| `db/models.py:23-34` 注释 | "工人正在用, 别的工人挑机时跳过"(= 资源占用锁) | 改回 `CONNECTABLE`(释放) |
| `test/xray_worker/spec.md` v5.1 §2/§4/§9 | "xray 装机/纳管完成的最终态"(= 生命周期阶段) | 保持 `RUNNING`(不释放) |

`workers/xray_worker.py::_mark_done` 跟 spec 一致(写 RUNNING 不释放), 跟 db/models.py 注释冲突。

跟用户对齐时, 用户明确表达心智:

> "任务表是任务的锁, 这个任务我拿了 你不要再跟我抢了; vps表 running 是为了告诉其他部门说 这台服务器有人正在 running 不要拿"
> "后面还有 proxy 工人要干活呢"

也就是说, **用户脑子里两层锁分开**:
- **task 锁**: 工人之间防抢同一张任务单
- **VPS 资源锁**: 跨工人 / 跨业务部门(XrayWorker / ProxyDeployWorker / 未来巡检等), 防同一台 VPS 同时被多个业务操作

而 ADR-0001 §决策 §4 当时定的是 "只有一把锁, 住在 task 表" —— 直接跟用户当下心智冲突。需要新开 ADR 把这条改掉。

---

## 决策

### 1. vps_record.stage 是 VPS 资源占用锁(跨工人, 跨业务部门)

**枚举语义**:

| 值 | 含义 |
|----|------|
| `CONNECTABLE` | 没工人占用 + SSH 通过验证, **可被任意工人抢** |
| `RUNNING` | 有工人正在干这台, **别的工人 / 别的业务部门跳过** |

**状态迁移**:

```
SSHWorker 入库     → stage='connectable'(写默认值)
工人抢到 task 后    → stage='running'(占资源锁, SSH 之前先写)
工人完工           → stage='connectable'(释放回池子, 别人能拿)
工人失败           → stage 保持 'running'(锁住等人介入)
```

### 2. vps_task 仍是任务并发锁(职责不变)

`vps_task.status='in_progress' + locked_until + worker_id` 是任务级别的原子锁, 防多 worker 抢同一张任务单。

跟 vps_record.stage 是**两层不同维度的锁**:
- task 锁 = 同一任务防抢
- stage 锁 = 同一 VPS 防多业务同时操作

### 3. 失败一律保持 stage='running'(等人介入)

不管 task 是 `failed`(终态)还是回 `pending`(等重试), **vps.stage 都保持 `running`**, 不主动释放。

理由:
- 失败的机器不应该被其他业务(比如 ProxyDeployWorker)挑去当好机用
- 等用户/未来引入的"维修工人"来人工排障 / 主动修复后再释放

**已知后果**: 长期挂在 running 但 task 不再 in_progress 的 VPS 需要人工/维修工人来处理。本 ADR 范围内**不引入自动维修**, 留待后续封存的工人(`workers/_shelved/`)启用时再单独决策。

### 4. ProxyDeployWorker(后续) 挑机查询条件预演

按本 ADR 语义, 后续 ProxyDeployWorker 挑机查询应当是:

```sql
SELECT * FROM vps_record
 WHERE stage='connectable'      -- 没人占着
   AND xray_version != ''       -- 装过 xray
   AND is_active = 1            -- 在保有效期
```

不需要 join vps_task 看锁状态, 因为 stage 已经包含资源占用信息。

---

## 备选方案

### 方案 A: 保留 ADR-0001 §决策 §4 单一 task 锁(被否决)

ProxyDeployWorker 挑机时改成 join vps_task 表看有没有 `in_progress` 的任务:

```sql
SELECT v.* FROM vps_record v
LEFT JOIN vps_task t ON t.vps_id = v.id AND t.status='in_progress'
 WHERE t.id IS NULL  -- 没有 active task
   AND v.xray_version != ''
   AND v.is_active = 1
```

**否决理由**:
- 多一次 join, 不如 vps.stage 一眼直接
- 跨业务线时(XrayWorker 写 vps_task, 未来巡检写 vps_task 或别的 task 表), join 逻辑分散, 易漂移
- 用户业务直觉"vps 自己应该有占用状态"更贴合 —— vps 是被操作对象, 它的"忙/闲"应该自己表达, 不靠外部表反推

### 方案 B: 完全去掉 stage 字段, 靠间接信号推算(被否决)

`xray_version` 空 / `is_active=0` / `vps_task.locked_until` 等字段组合推算 vps 状态。

**否决理由**:
- 推算逻辑分散到每个挑机查询里, 加新业务时每条查询都要改, 易漂移
- 业务语言里 "这台机现在在干嘛" 是一个清晰的单一概念, 用单一字段表达最自然
- ProxyDeployWorker / 巡检 / 未来其他工人挑机时, 每个都要重写推算逻辑, 维护成本高

### 方案 C: 引入新枚举值(被否决)

不复用 `RUNNING`, 而是引入新值如 `PROVISIONED` / `READY` 表达"装机完成且空闲"。

**否决理由**:
- 用户明确说"running 就是告诉别人有人正在用", 直接用 `RUNNING / CONNECTABLE` 二值最简洁
- 装机进度本来就在 `xray_version` 字段表达, 没必要在 stage 里再加一维
- 多一个枚举值多一处状态迁移, YAGNI

---

## 后果

### 好处

- **资源锁和任务锁职责分明**, 不再互相覆盖
- **ProxyDeployWorker 等后续工人挑机查询简洁**, 一个 stage 字段表达"忙/闲"
- **失败 stage='running' 自然形成"等人介入"信号**, 符合用户业务直觉
- **db/models.py:23-34 注释**(写于早期)无意中保留了正确语义, 本 ADR 把它扶正
- **跟用户业务心智(task 锁 vs 资源锁两层)直接对齐**, 后续工作不再纠结

### 引入的新约束

- 每个工人抢到 task 后, **必须主动写 vps.stage='running'**(SSH 之前的一步)
- 工人完工时, **必须主动写 vps.stage='connectable'**(_mark_done 的一步)
- 工人失败时, **不主动改 vps.stage**(保持 running)
- 新增工人(如 ProxyDeployWorker / 巡检)都要遵守这套协议
- 失败挂 running 的 VPS 需要"维修工人"或人工介入(本 ADR 范围外)

### 风险

- **风险**: 工人代码忘写 stage 迁移, 锁状态漂移
  缓解: spec.md §9 加不变量 + TC 覆盖完工/失败 stage 迁移
- **风险**: 失败的 VPS 永久挂 running, 没人理 → 资源浪费
  缓解: 后续封存的工人启用时单独决策(本 ADR 接受短期风险)
- **风险**: ADR-0001 §决策 §4 旧理解可能仍存在于 commit message / 早期文档 / 注释
  缓解: ADR-0005 顶部 supersede 标注, spec.md / db/models.py 注释同步对齐

---

## 用户口述原话(关键节选)

> "task表拿到的那个任务结束了, 数据写完了 vps对应的vps不得还回去吗还占着干啥呀 不给别人用吗"

> "任务表是任务的锁, 这个任务我拿了 你不要再跟我抢了; vps表 running 是为了告诉其他部门说 这台服务器有人正在 running 不要拿 能理解吗 后面还有 proxy 工人要干活呢"

> "失败了就继续锁住吧 后面再引入专门维修的"
> (引出 §决策 §3 失败保持 running)

---

## 影响清单(已锁定, 在下游 task 单里逐项落)

| 文件 | 改动 | 落地任务单 |
|------|------|----------|
| `docs/adr/0001-workers-replace-services.md` | **不动**(永不改), §决策 §4 被本 ADR 局部 supersede | — |
| `docs/adr/0005-*.md`(本文件) | 新建 | — |
| `test/xray_worker/spec.md` | v5.1 → v5.2: §1 工人定位 / §2 入口契约 / §4 成功出口 / §9 不变量 / §三 修订历史多处改两层锁语义 | T-09 |
| `workers/xray_worker.py` | `process_task` 抢到 task 后追加写 stage=running 一段; `_mark_done` 改写 stage=connectable; 失败分流不动 | T-09 |
| `db/models.py:23-34` | 注释已是正确语义, 末尾加 "见 ADR-0005" 引用 | T-09 |
| `test/xray_worker/TC-*.py` | 完工断言 `stage='running'` → `'connectable'`(TC-07/TC-08 等); 失败断言不变; 新增 TC-15 lock_semantics 锁状态机 | T-09 |
| `dev_smoke_xray_worker.py` 顶部 docstring | 验收预期 `stage='running'` → `'connectable'` | T-09 |
| 现有 DB 数据 | 跳过单独 UPDATE, 用户清库后跑完整端到端 smoke 验证(SSHWorker → XrayWorker 链路) | T-09 验收 |
