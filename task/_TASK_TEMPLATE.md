# T-NN <task title>

**ID**: T-NN
**状态**: waiting
**前置依赖**: <none | T-XX | ADR/spec dependency>
**后续依赖**: <none | T-XX | downstream user>
**关联 ADR**: <docs/adr/NNNN-xxx.md>
**关联 spec**: <test/<group>/spec.md>

> 使用方法: 新建任务时复制本文件, 改名为 `task/waiting_NN_<slug>.md`,
> 然后按实际任务替换占位符。不要把本模板改成具体任务。
>
> 实现窗口领取任务时, 开始写代码前先把文件名改成
> `task/doing_NN_<slug>.md`, 表示本任务已被领取, 避免多个窗口抢同一张任务单。
> 实现 + 测试 + 验收完成后再改成 `task/done_NN_<slug>.md`。

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名从 `waiting_NN_<slug>.md` 改为
      `doing_NN_<slug>.md`。

如果目标任务已经是 `doing`, 说明已有窗口领取。不要抢同一任务,
先问用户或换任务。

### 必读清单

领取后、写代码前, 必须显式读取:

- [ ] `CLAUDE.md`
- [ ] `CLAUDE.local.md`
- [ ] `docs/adr/README.md`
- [ ] 本任务相关的 `docs/adr/*.md`
- [ ] 本任务关联的 `test/<group>/spec.md`
- [ ] 本任务点名要改的源码文件
- [ ] 本任务点名要改/新增的测试文件

未读完上面文件前, 禁止:

- 写代码
- 写测试
- 把任务改成 `done`
- 给出"我已经理解实现方案"的结论

正例:

```text
我先领取任务改为 doing,然后按任务单读取 ADR/spec/目标源码,读完再实现。
```

反例:

```text
ADR/spec 还没读,要不要我先读?
```

---

## 1. 用户原话 / 业务目标

### 用户原话

> 在这里保留用户关键原话。
> 原话用于校准业务意图, 不直接当代码实现方案。

### Claude 整理后的业务理解

- 外部输入: <user input / request / event>
- 第一件事: <first business step>
- 主要流程:
  1. <step 1>
  2. <step 2>
  3. <step 3>
- 判断分支:
  - <condition A> -> <business outcome A>
  - <condition B> -> <business outcome B>
- 数据流:
  - 读取: <table / record / external system>
  - 写入: <table / record / task / event>
- 同步 / 异步边界:
  - 同步完成: <what returns during this call>
  - 异步接力: <task_type / worker / follow-up flow>
- 成功返回: <success response in business language>
- 失败返回: <failure categories in business language>

### 本任务要解决什么

用大白话写清楚这个任务完成后, 用户/系统能得到什么结果。

### 本任务不解决什么

- <non-goal 1>
- <non-goal 2>

---

## 2. 实现参考

### 验收锚点

- `<test/<group>/spec.md>` <section / version>
- `<docs/adr/NNNN-xxx.md>` <section>
- `<CLAUDE.local.md>` <project-specific rule, if any>

### 改动文件清单

#### 改 `<path/to/existing_file.py>`

```text
写清楚要改什么, 不要只写"实现功能"。
```

#### 新建 `<path/to/new_file.py>`

```text
写清楚文件职责、类/函数轮廓、谁调用它。
```

#### 不动

```text
列出明确不能碰的文件/模块/旧代码。
```

### 实现轮廓

```python
# 这里写给实现者看的技术参考:
# - 函数/类命名
# - 关键参数
# - 返回结构
# - 状态迁移
# - 重要副作用
```

### 数据结构 / 状态迁移

| 字段 / 状态 | 含义 | 谁读 | 谁写 |
|---|---|---|---|
| `<field_or_state>` | `<meaning>` | `<reader>` | `<writer>` |

### 缺工具 / 缺信息先报告

实现者遇到以下情况必须停下来报告, 不要自己拍板:

- spec / ADR 没写清楚的业务判断。
- 需要新增工具、改共享工具或改外部接口。
- 发现实现轮廓和验收锚点冲突。
- 发现会影响非目标文件或下游任务。

---

## 3. 验收交付

### 测试用例

#### TC-NN-a `<test/<group>/TC-xx_name.py>`

业务故事:

```text
写产品视角的人话测试故事。
```

输入:

- <input 1>
- <input 2>

预期:

- <expected output>
- <required side effect>

不应发生:

- <forbidden behavior>

### 必跑测试命令

```bash
<test command>
```

### 实现者完工标准

- [ ] 开工前已将任务文件从 `waiting` 改为 `doing`。
- [ ] 目标文件按本任务实现完成。
- [ ] 测试文件按本任务新增/修改完成。
- [ ] 必跑测试命令通过。
- [ ] 对照 spec / ADR 验证业务流程、数据流、判断分支一致。
- [ ] 没有改动"不动"清单里的文件。
- [ ] 如有偏差或缺工具, 已在实现过程记录里说明并等待/记录用户拍板。

### 实现过程记录(实现者完工时填)

```text
改动文件:
- <path>

新增工具/方法:
- 名字: <name>
  住: <path>
  干啥: <purpose>
  测试: <TC id>
  审批: <user approval / task / issue>

测试结果:
- <command> -> <result>

偏差 / 风险:
- <none | details>
```

### Claude 验收检查清单

□ 对照用户原话 / 业务目标检查实现没有跑偏  
□ 对照 ADR 检查业务流程图、数据流图、判断分支一致  
□ 对照 spec 检查输入、输出、副作用、失败分支一致  
□ 跑必跑测试命令并记录结果  
□ 检查实现者完工标准全部满足  
□ 偏差但合理 -> 抛给用户决策  
□ 偏差不合理 -> 打回实现者修改  

---

## 完成记录(done 时追加)

> 任务完成后再填。waiting 阶段不要预填。

```text
完成日期:
完成 commit:
任务状态: doing -> done
改动摘要:
测试命令:
测试结果:
未覆盖风险:
后续任务:
```
