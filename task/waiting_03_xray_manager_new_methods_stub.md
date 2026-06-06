# T-03 XrayManager 加新方法占位(纳管所需)

**ID**: T-03
**前置依赖**: 无(可与 T-01/T-02 并行)
**后续依赖**: T-07 XrayWorker 实现需要这些方法存在(实现可在 T-07 阶段填,
            本任务只建占位)

---

## 验收锚点

- `docs/adr/0003-xray-worker-three-branches-unified-tail.md` 统一收尾 7 步
- `tests_behavior/xray_worker/spec.md` §3 统一收尾 + §11 §工具清单
- `CLAUDE.md` §7.4 占位用 pass + 类的实例方法主推

## 改动文件清单

### 改 `xray/manager.py`

```
给 class XrayManager 加 3 个新方法占位(方法体仅 pass + docstring):

  ① extract_existing_outbounds(self) -> list[dict]
     抠出现有出口配置(纳管核心)
     ⭐ 抠信息类:返回 list[dict],每条形如:
        {
          "vps_port": int,            # 服务器上的端口号
          "inbound_user": str,        # 入口账号
          "inbound_pwd": str,         # 入口密码(明文,内部用)
          "upstream_host": str,       # 上游入口域名/IP
          "upstream_port": int,       # 上游入口端口
          "upstream_user": str,       # 上游账号
          "upstream_pwd": str,        # 上游密码(明文)
          "upstream_protocol": str,   # socks5 / http
          "egress_ip": str,           # 出口 IP(从 outbound 备注读,无则 "")
          "egress_country": str,      # 出口国家(同上,无则 "")
        }
     空配置 → 返回 [](不抛错!)
     旧代码参考:xray/config.py::extract_port_bindings 或 xray/manager.py::
              import_existing_bindings

  ② has_outbounds(self) -> bool
     看配置里有没有"非默认"的出口(除 18440 默认入口之外的)
     用于辅助分支决策(但 spec v3 中已弱化,因为统一收尾会处理)
     占位实现:return False (实现时 grep config tags 判断)

  ③ test_internal(self, port: int, user: str = "", pwd: str = "") -> bool
     在服务器内部 ping 指定端口的 inbound
     返回 True/False (通/不通)
     旧代码参考:xray/service.py::test_internal_socks
     (老方法返回 dict,新方法简化为 bool)
```

### 不动

```
旧 xray/service.py / xray/config.py 函数全部沿用,实现这 3 个新方法时
内部可以调它们(不必"先函数后封类"重复)。

不动 services/* / workers/* / tools/*
```

### 不新建测试

```
本任务**只建占位**(pass + docstring),不测行为。
真测试在 T-07 XrayWorker 实现时一并写(集成场景测)。
```

---

## 实现轮廓(实现者参考)

```python
class XrayManager:
    # ... 现有方法 ...

    # ---- 新增占位(等 T-07 实现填) ----

    def extract_existing_outbounds(self) -> list[dict]:
        """抠出现有出口配置(纳管核心)。

        ⭐ 抠信息类。空配置返回 [] 不抛错。

        字段大类见 task/waiting_03_*.md, 字段命名细节见
        tests_behavior/xray_worker/spec.md §3 统一收尾 ②.

        实现等任务单 T-07 填(可参考 xray.config.extract_port_bindings).
        """
        pass

    def has_outbounds(self) -> bool:
        """看配置里有没有非默认出口(除 18440 之外)。

        实现等任务单 T-07 填.
        """
        pass

    def test_internal(self, port: int, user: str = "", pwd: str = "") -> bool:
        """在服务器内部 ping 指定端口的 socks5 inbound。

        返回 True/False(通/不通).
        实现等任务单 T-07 填(参考 xray.service.test_internal_socks).
        """
        pass
```

---

## 实现者完工标准

```
- [ ] xray/manager.py 加 3 个新方法占位(pass + docstring)
- [ ] 每个 docstring 含"实现等任务单 T-07 填"标记
- [ ] 不动其他文件
- [ ] uv run python -c "from xray.manager import XrayManager; \
        m = XrayManager.__dict__; \
        assert 'extract_existing_outbounds' in m; \
        assert 'has_outbounds' in m; \
        assert 'test_internal' in m" 不报错
- [ ] commit 标题: chore(xray): XrayManager 加 3 个纳管方法占位
```

---

## Claude 验收检查清单

```
□ git diff xray/manager.py:
    - 3 个新方法存在(extract_existing_outbounds / has_outbounds / test_internal)
    - 每个方法体仅 pass + docstring
    - 没有动其他方法
□ 实现者没乱填实现(本任务**禁止**真实现,留 T-07)
□ 偏差但合理 → 抛给用户决策
□ 偏差不合理(实现了真逻辑)→ 打回让实现者改成 pass
```
