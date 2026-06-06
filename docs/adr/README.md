# docs/adr/ —— Architecture Decision Records

## 这里住的是「我们当时为什么这么决定」

ADR = Architecture Decision Record。每个文件记录一项架构决策的**当时背景 +
方案选择 + 影响后果**。

跟 `CLAUDE.md` / `CLAUDE.local.md` 不同：

| 文件 | 答什么问题 | 寿命 |
|------|----------|------|
| ADR | **当时为什么这么定** | 永久,几乎不改 |
| CLAUDE.md / CLAUDE.local.md | **以后干活怎么做** | 长期,随项目演化 |
| spec.md | 应该达到什么效果(验收标准) | 长期,可改 |
| issue | 还没拍板的事 | 短,决策完归档 |
| task | 拍板了的具体活 | 中,做完归档 |

## 命名规则

```
NNNN-决策名-kebab-case.md

NNNN: 4 位数字编号,从 0001 开始,递增,永不复用
```

例:
- `0001-workers-replace-services.md`
- `0002-task-table-as-single-coordination.md`
- `0003-mcp-transport-stdio-first.md`

## 一份 ADR 长什么样(模板)

```markdown
# NNNN. <决策名>

**日期**:YYYY-MM-DD
**状态**:Accepted / Deprecated / Superseded by NNNN

## 背景

什么时候、为什么需要做这个决策?当时遇到了什么具体问题或痛点?

## 决策

我们最终选择了什么?用一两段话清楚地说明。

## 备选方案

考虑过哪些其他方案?为什么没选?

## 后果

这个决策带来什么好处?引入什么新的约束/成本?

## 用户口述原话(可选)

如果这个决策来自用户的关键对话,把原话节选附在这里(金标准存档)。
```

## 永不改原则

ADR 写完不改。如果决策变了:
- **不删旧 ADR**(历史档案)
- 新写一份 ADR(下一个编号)
- 在新 ADR 顶部标"Supersedes ADR-NNNN"
- 把旧 ADR 状态改成"Superseded by NNNN-xxx"

## 什么决策值得开 ADR

- 影响多个模块、影响长期演进方向的决策
- "为什么不用 X 用 Y" 这种需要后人理解的选择
- 从根本上改变项目形态的转折(脚本→服务、单点→分布式、单表→分库分表)

**不开 ADR 的**:
- 实现细节(变量名、函数签名)
- 临时绕路
- 一次性 bug 修复
- 还没拍板的事(那是 issue 不是 ADR)
