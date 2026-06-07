# T-03 XrayManager 加新方法占位(纳管所需) — v2 对齐 spec v5 + ADR-0004

**ID**: T-03
**前置依赖**: 无(可与 T-01 / T-02 并行)
**后续依赖**: T-07 XrayWorker 实现需要这些方法存在(实现可在 T-07 阶段填,本任务只建占位)

> **v2 变化**(2026-06-08, 落 ADR-0004 + spec v5):
> - 加 `is_enabled()` 占位（ADR-0004 §1 引出）
> - 删 `has_outbounds()` 占位（spec v5 §4 改用 outbound 协议判定, 不需要单独工具）
> - 删 `test_internal()` 占位（搬到 `toolbox/proxy_check.py`, 走 T-08 任务单）
> - `extract_existing_outbounds()` 返回字段加 `outbound_protocol`（区分 freedom vs socks 用,直进直出判定靠它）

---

## 验收锚点

- `docs/adr/0004-xray-worker-flow-refinements.md` §1（自启）+ §3（直进直出判定）
- `test/xray_worker/spec.md` **v5** §3 三分支 + §4 统一收尾 + §二 A 工具清单
- `CLAUDE.md` §7.4 占位用 `pass` + 类的实例方法主推

## 改动文件清单

### 改 `xray/manager.py`

```
给 class XrayManager 加 2 个新方法占位(方法体仅 pass + docstring):

  ① extract_existing_outbounds(self) -> list[dict]
     抠出现有出口配置(纳管核心)
     ⭐ 抠信息类: 返回 list[dict], 每条形如:
        {
          "vps_port": int,            # 服务器上的端口号 (inbound 监听端口)
          "inbound_protocol": str,    # 入口协议: 通常是 "socks" / "socks5"
          "inbound_user": str,        # 入口账号 (noauth 时空串)
          "inbound_pwd": str,         # 入口密码 (明文, 内部用; noauth 时空串)
          "outbound_protocol": str,   # ⭐ v2 新增: outbound 协议
                                      #   "freedom" → 直进直出, 不纳管
                                      #   "socks" / "socks5" → 代理出口, 走纳管流程
                                      #   其他 → 兜底按"非直进直出"处理
          "upstream_host": str,       # 上游入口域名/IP (freedom 时空串)
          "upstream_port": int,       # 上游入口端口 (freedom 时 0)
          "upstream_user": str,       # 上游账号 (freedom 时空串)
          "upstream_pwd": str,        # 上游密码 (freedom 时空串)
          "egress_ip": str,           # 出口 IP (从 outbound 备注读, 无则 "")
          "egress_country": str,      # 出口国家 (同上, 无则 "")
        }
     空配置 → 返回 [] (不抛错!)
     旧代码参考: xray/config.py::extract_port_bindings 或
              xray/manager.py::import_existing_bindings

  ② is_enabled(self) -> bool                       ⭐ v2 新增 (ADR-0004 §1)
     查 systemd 有没有给 xray 设开机自启
     SSH 执行类似 systemctl is-enabled xray 的命令, 解析返回值
     返回 True/False
     旧代码参考: 当前 xray/service.py 里有 enable() 但没有 is_enabled(),
              新造, 实现走 systemctl is-enabled (退码 0 = enabled)
```

### 不动

```
旧 xray/service.py / xray/config.py 函数全部沿用, 实现这 2 个新方法时
内部可以调它们 (不必"先函数后封类"重复)。

不动 services/* / workers/* / tools/* / toolbox/*

⚠️ test_internal 不再在 XrayManager 上加占位
   它搬到 toolbox/proxy_check.py 走 T-08
```

### 不新建测试

```
本任务**只建占位** (pass + docstring), 不测行为。
真测试在 T-07 XrayWorker 实现时一并写 (集成场景测)。
```

---

## 实现轮廓(实现者参考)

```python
class XrayManager:
    # ... 现有方法 ...

    # ---- 新增占位 (等 T-07 实现填) ----

    def extract_existing_outbounds(self) -> list[dict]:
        """抠出现有出口配置 (纳管核心)。

        ⭐ 抠信息类。空配置返回 [] 不抛错。

        字段大类见 task/waiting_03_*.md 注释,
        字段命名细节见 test/xray_worker/spec.md v5 §4 + §二.

        关键字段: outbound_protocol 决定走"直进直出"还是"纳管"路径
            "freedom" → 直进直出, XrayWorker 跳过纳管
            "socks" / "socks5" → 代理出口, XrayWorker 走内 ping + 写库/remove

        实现等任务单 T-07 填 (可参考 xray.config.extract_port_bindings).
        """
        pass

    def is_enabled(self) -> bool:
        """查 systemd 有没有给 xray 设开机自启。

        SSH 执行 `systemctl is-enabled xray`:
            退码 0 + stdout 'enabled' → True
            其他                       → False

        实现等任务单 T-07 填。
        """
        pass
```

---

## 实现者完工标准

```
- [ ] xray/manager.py 加 2 个新方法占位 (pass + docstring)
- [ ] 每个 docstring 含"实现等任务单 T-07 填"标记
- [ ] extract_existing_outbounds docstring 明确字段含义, 特别是 outbound_protocol
- [ ] 不动其他文件
- [ ] uv run python -c "from xray.manager import XrayManager; \
        m = XrayManager.__dict__; \
        assert 'extract_existing_outbounds' in m; \
        assert 'is_enabled' in m" 不报错
- [ ] commit 标题: chore(xray): XrayManager 加 extract_existing_outbounds + is_enabled 占位
```

---

## Claude 验收检查清单

```
□ git diff xray/manager.py:
    - 2 个新方法存在 (extract_existing_outbounds / is_enabled)
    - 每个方法体仅 pass + docstring
    - 没有动其他方法
    - **没有**加 has_outbounds / test_internal (按 v2 决定砍掉)
□ 实现者没乱填实现 (本任务**禁止**真实现, 留 T-07)
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理 (实现了真逻辑) → 打回让实现者改成 pass
```
