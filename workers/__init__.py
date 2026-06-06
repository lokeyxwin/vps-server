"""workers 包 —— 新业务编排层(工人住这里).

每个工人 = 一个 class, 一个 .py 文件自包含.
工人之间只通过 task 表接力,不直接互调.

详见:
- CLAUDE.local.md §1 目录布局
- CLAUDE.local.md §9 工人阵容
- tests_behavior/<worker>/spec.md (各工人行为规约)
"""
