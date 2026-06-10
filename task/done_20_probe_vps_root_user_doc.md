## T-20 init-probe-vps SSH 用户权限约束 docs 补充

**ID**: T-20
**状态**: waiting
**创建日期**: 2026-06-10
**前置依赖**: T-19 ✅ done (commit d39be50, init-probe-vps 已落地真机端到端验证通过)
**后续依赖**: 无
**关联 ADR**: `docs/adr/0009-probe-vps-bootstrap-decoupled.md` (§决策 §6.1 init-probe-vps 子命令)
**关联事件**: T-19 真机验证 2026-06-10 11:20-11:27 发现 `PROBE_VPS_1_USER=ubuntu`
                因权限不足导致 install / write_config / systemctl reload 全挂的隐性约束

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] T-19 已 done (d39be50)
- [ ] 本任务仍是 waiting
- [ ] 写代码前已将文件名改为 `task/doing_20_probe_vps_root_user_doc.md`

### 必读清单

- [ ] `CLAUDE.md` / `CLAUDE.local.md`
- [ ] `docs/adr/0009-probe-vps-bootstrap-decoupled.md` (主依据 §6.1)
- [ ] `README.md` §3.4 (现状)
- [ ] `probe_vps.example.py` (现状, 模板)
- [ ] `task/done_19_*.md` (上游, 看完成记录 commit hash)
- [ ] `xray/service.py::INSTALL_COMMAND / UNINSTALL_COMMAND` (确认命令字符串没 sudo 前缀, 解释权限要求)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "云服务商的账号给的没有权限,需要临时提权或者用 root 就没有那么多问题,这属于分支,
>  交给客户端的模型去搞定吧"
>
> "如果 init 装不上 就查看(是否账户权限不够, 如果不够让用户 / 模型自行 ssh 到服务器
>  用 sudo 提权跑或者直接给 root 两种方式)"
>
> "写到部署流程的 readme 上 提前告知请用 root 权限跑 init; 减少出错"

### Claude 整理后的业务理解

- **外部触发**: T-19 真机验证暴露隐性约束 — 云厂商默认账号 (`ubuntu` / `centos` /
  `admin` 等) 直接跑 init-probe-vps 必挂在 install 阶段
  (`error: You must run this script as root!`) 或 add inbound 阶段 (`Permission denied`)
- **主要做的事**:
  1. 根 `README.md` §3.4 加 "⚠️ 关于 SSH 用户权限" 小节, 提前告知 PROBE_VPS_N_USER
     必须是 root 或带免密 sudo 用户
  2. `probe_vps.example.py` 模板 `PROBE_VPS_N_USER` 那行 + 模板顶部 docstring 加注释
  3. 顺手补 T-19 完成记录里的 commit hash (`完成 commit: d39be50`)
- **不改代码**: bootstrap / xray.service / init_probe_vps MCP 工具一律不动, 失败的
  引导留给 agent (agent 看到 message 里 "Permission denied" 应该引导用户切 root)
- **数据流**: 纯 docs, 不动 ORM / worker / tools
- **同步 / 异步边界**: N/A, docs only
- **成功返回**: 用户看 README 提前知道这条约束, 不踩坑
- **失败返回**: N/A

### 本任务要解决什么

T-19 真机验证暴露的隐性约束 (PROBE_VPS_N_USER 必须 root) 提前 documented, 减少
未来部署者 (包括 agent) 在 init-probe-vps 失败时排查时间, 直接切 root 修复.

### 本任务不解决什么

- ❌ 不改 `bootstrap.ensure_ready` 加权限预检 (失败 message 已经够清晰, 引导交 agent)
- ❌ 不改 `xray.service.INSTALL_COMMAND` 加 sudo 前缀 (那是另一个范围, 要配套
  免密 sudo 配置, 复杂度大, 当前没需求)
- ❌ 不动 `mcp_server.py` / `tools/init_probe_vps.py` 实际代码
- ❌ 不动 ADR-0009 (永不改原则)
- ❌ 不引入 PROBE_VPS_N_USER 启动预检 / 校验逻辑

---

## 2. 实现参考

### 验收锚点

- `docs/adr/0009-*.md` §决策 §6.1 init-probe-vps 子命令
- T-19 真机验证日志 (2026-06-10 11:20-11:27) 暴露的具体失败 message

### 改动文件清单

#### 改 `README.md` §3.4

在现有 "agent 查询时看到 probe_vps_not_ready → 重跑 init-probe-vps" 那行之后
插入 "⚠️ 关于 SSH 用户权限" 小节. 内容:

```markdown
**⚠️ 关于 SSH 用户权限** (T-19 真机验证经验):

`init-probe-vps` 走的 SSH 用户 (`PROBE_VPS_N_USER`) 必须能 **写
`/usr/local/etc/xray/`** + **跑 `systemctl reload xray`** —— 也就是
**root 或带免密 sudo 的用户**.

云服务商默认给的非 root 账号 (`ubuntu` / `centos` / `admin` 等) 直接跑会挂:

- install 阶段: `error: You must run this script as root!` → 退码 1
- 或 add inbound 阶段: `Permission denied` 写 `config.json` 失败

错误 message 会清晰返回 `probe_vps_not_ready + Permission denied / 必须 root`,
**agent 收到这个 status 应当引导用户切 root**, 而不是让用户对着权限错误自己排查.

两种修法选一个 ——

1. **直接给 root** (推荐, 测试机我们自己的, 安全可控):
   ```bash
   # 测试机上, 在云厂商账号 (如 ubuntu) 的 shell 里
   sudo passwd root                                                          # 设 root 密码
   sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
   sudo systemctl restart ssh
   ```
   然后改 `~/.zshrc.local`: `PROBE_VPS_N_USER="root"` + 对应 PWD 改成 root 密码,
   `source ~/.zshrc.local` 重新跑.

2. **给非 root 账号开免密 sudo** (配套要改项目代码加 sudo 前缀, 当前未做,
   留待真有需求再开 task).
```

#### 改 `probe_vps.example.py`

模板顶部 docstring 现有"凭据走系统环境变量..."一段后,加一段权限约束说明:
"凭据走系统环境变量..." 段后插入:

```python
"""
...原 docstring...

⚠️ PROBE_VPS_N_USER 必须是 root 或带免密 sudo 的用户:
   - 装 xray (xray.service.INSTALL_COMMAND 不带 sudo) 要 root
   - 写 /usr/local/etc/xray/config.json + systemctl reload xray 也要 root
   - 云厂商默认的 ubuntu / centos / admin 直接跑 init-probe-vps 必挂
     (error: You must run this script as root! 或 Permission denied)
   - 见根 README §3.4 "关于 SSH 用户权限" 小节排查指引

...
```

`PROBE_VPS_1_USER` 占位行从 `"root"` 起步 (实际现状是 `root` 还是其他, 实施时确认).

#### 改 `task/done_19_probe_vps_bootstrap_and_init_tools.md`

完成记录段把 `完成 commit: (commit 后填)` 改成 `完成 commit: d39be50`.
另在 "启动验证" 段追加 T-20 当时再跑的真机闭环结果:

```text
启动验证 (T-19 commit d39be50 后, T-20 期间补真机端到端):
- ... (T-19 原内容保留)
- 后续真机端到端 (T-20 期间, 11:22-11:27): 用户在测试机 sudo 装好 xray (绕开 paramiko
  没 root 权限) + 改 PROBE_VPS_1_USER=root 后:
  - 11:25:02 init-probe-vps (xray 已装路径) → 2 秒完成, 退码 0 ✓
  - 11:27:14 卸载 xray 重跑 (全新装机路径) → 11 秒完成 (install 9s + add inbound 1s),
    退码 0 ✓
  → ADR-0009 §3 6 步全路径真机闭环验证拿到
```

#### 不动

- ADR-0001~0009 (永不改原则)
- bootstrap.py / xray/service.py / xray/manager.py 实际代码
- main.py / tools/init_*.py 实际代码
- mcp_server.py
- 所有 test/*.py (本任务不加 / 不改 TC)
- workers/ / db/ / config.py

### 实现轮廓

3 处 docs 改动, 无代码改动, 不涉及 TC.

### 数据结构 / 状态迁移

N/A

### 缺工具 / 缺信息先报告

- 如发现 README.md §3.4 当前已含"权限相关"说明 (跟本任务草改重复) → 报告先合并
- 如发现 probe_vps.example.py 当前 PROBE_VPS_1_USER 占位非 `root` → 改成 `root` 之前先报告

---

## 3. 验收交付

### 测试用例

N/A (docs only).

### 必跑测试命令

```bash
# 文档改动不破坏现有测试 (跑一次回归确认)
PYTHONPATH=. VPS_SERVER_TESTING=1 .venv/bin/pytest \
  test/probe_vps/TC-*.py test/main/TC-*.py test/mcp_tools/TC-*.py \
  test/ip_probe_worker/TC-*.py --tb=short
```

预期: 141 passed, 跟 T-19 完工后一致.

### 启动验证 (本任务无, T-19 真机已验)

N/A.

### 实现者完工标准

> ⚠️ 全部打勾才允许改 doing → done.

- [x] T-19 已 done (前置, d39be50)
- [x] 任务文件改为 doing
- [x] `README.md` §3.4 加 "⚠️ 关于 SSH 用户权限" 小节 (含 2 种修法 + 报错表)
- [x] `probe_vps.example.py` 顶部 docstring 加权限约束段 + L11 行尾注释 (USER 占位本来已是 root)
- [x] `task/done_19_*.md` 完成记录补 commit hash d39be50 + 启动验证段追加 T-20 期间真机端到端 (双向闭环) + 偏差段更新真根因
- [x] 必跑测试 141 passed (docs only, 无回归)
- [x] 完成记录段已填

### 实现过程记录 (实现者完工时填)

```text
改动文件:
- README.md
- probe_vps.example.py
- task/done_19_probe_vps_bootstrap_and_init_tools.md

新增文件: none

删除文件: none

测试命令: (上面"必跑测试命令")
测试结果: 141 passed

启动验证: N/A (docs only)

偏差 / 风险: <none | details>
```

### Claude 验收检查清单

- [ ] README.md §3.4 新小节措辞清晰 + 含 root 切换步骤
- [ ] probe_vps.example.py 注释清晰 + 占位改 root
- [ ] T-19 完成记录补 commit hash 完整
- [ ] 必跑测试无回归
- [ ] 不动清单确认没碰

---

## 4. 完成记录 (done 时追加)

```text
完成日期: 2026-06-10
完成 commit: (commit 后填)
任务状态: doing -> done

改动摘要:
- README.md §3.4 在 "重跑场景"列表之后插入 "⚠️ 关于 SSH 用户权限" 小节:
  - 标注 PROBE_VPS_N_USER 必须 root / 免密 sudo 的能力要求 (写 config + reload xray)
  - 列两个阶段的实际报错 (install / add inbound) 让 agent 见 message 即识别
  - 提供 2 种修法: (1) 直接给 root (推荐, 含 sudo passwd / sshd_config 切换三连
    + 一行 hash 同步小技巧避免密码进 chat); (2) 免密 sudo (需配套改代码,
    当前未做)
- probe_vps.example.py 顶部 docstring 在 "PROBE_VPS_POOL 长度 1-3" 后追加
  "⚠️ PROBE_VPS_N_USER 必须是 root" 段, 含失败 message 关键字 + 引用根 README
  §3.4 排查指引; L11 PROBE_VPS_1_USER 占位行尾加注释 (占位本来已是 root)
- task/done_19_*.md:
  - 完成 commit hash 补 d39be50
  - 启动验证段改成 "T-19 commit d39be50 后, T-20 期间补真机端到端", 追加
    11:25 幂等路径 + 11:27 全新装机路径双向闭环的真实日志摘要
  - 偏差段更新真根因 (从 "测试机 GitHub 网络不通" 改成 "INSTALL_COMMAND 不带
    sudo + paramiko ubuntu 用户无 root 权限"), 引用 T-20 docs 补的位置

测试命令:
  PYTHONPATH=. VPS_SERVER_TESTING=1 .venv/bin/pytest \
    test/probe_vps/TC-*.py test/main/TC-*.py test/mcp_tools/TC-*.py \
    test/ip_probe_worker/TC-*.py --tb=short

测试结果: 141 passed (docs only, 跟 T-19 完工后一致, 0 回归)

未覆盖风险:
- 文档约束靠用户自觉, 没代码层强制 (启动预检 PROBE_VPS_N_USER==root). 真有
  类似的"配错挂在权限错误"反复发生, 可单独开 task 做 ensure_ready 启动预检
  或 INSTALL_COMMAND 加 sudo 前缀.
- README 这条约束放在 §3.4, 如果未来根 README 章节重排, 这条约束可能漂移.
  缓解: 关键字 "关于 SSH 用户权限" 内嵌 PROBE_VPS_N_USER 关键词, grep 可定位.

后续任务: 真有需求改 xray.service 加 sudo 前缀支持免密 sudo 用户时, 单独开 task
```
