# T-22 修 probe_vps.bootstrap step ⑤ 端口 — 19000 → 18440

**ID**: T-22
**状态**: waiting
**前置依赖**: T-19 (probe_vps.bootstrap 主实现)
**关联 ADR**: `docs/adr/0009-probe-vps-bootstrap-decoupled.md` §决策 §3 step ⑤ — 本任务修正其实现, ADR 档案保留不动(永不改原则)
**关联 spec**: `test/ip_probe_worker/spec.md` v3 §5 端口规则

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`
- [ ] 开始写代码前, 已将文件名从 `waiting_22_*.md` 改为 `doing_22_*.md`

### 必读清单

- [ ] `CLAUDE.md` / `CLAUDE.local.md`
- [ ] `docs/adr/0009-probe-vps-bootstrap-decoupled.md` §决策 §3
- [ ] `probe_vps/bootstrap.py` (本任务唯一改动的源码)
- [ ] `probe_vps/config.py` (注释 L60-61 是设计意图金标准)
- [ ] `config.py::XRAY_DEFAULT_PORT` (= 18440)
- [ ] `test/probe_vps/TC-03_bootstrap_idempotent.py` + `TC-04_bootstrap_fresh.py` (要改断言)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "19000 是给谁用？18440 是谁在用 我无非就这两个端口是固定的"
> "那就改 bootstrap step ⑤ 端口到 18440 这明明就是 bug 他妈的"
> "这个很简单, 就是改个端口, 然后 ssh 上去把 19000 配置移除 重新跑一次 init 就可以了"

### bug 本质

T-19 实现 `bootstrap.ensure_ready` step ⑤ 时把"必装默认入口"装到了 `PROBE_TEST_PORT` (19000), 应该是 `XRAY_DEFAULT_PORT` (18440)。

端口职责分离 (设计意图):
- **18440** = xray 必装默认入口 (socks5/freedom 常驻)
- **19000** = IPProbeWorker 临挂端口 (默认应该**空着**)

T-19 实现错位 → 19000 上挂着常驻 `probe-direct` → IPProbeWorker 来临挂时撞 `PortAlreadyBoundError`。

### 本任务解决

把 step ⑤ 端口从 `PROBE_TEST_PORT` 改成 `XRAY_DEFAULT_PORT`, 真机手动清掉脏数据。完事。

### 不解决

- `xray.config.remove_proxy_binding` 通用化 (另一种修法, 不本任务)
- ADR-0009 文档修订 (永不改原则, 留档案)
- inbound tag `probe-direct` → `default-direct` 命名统一 (本任务保持 probe-direct 不动, 想统一另起任务)

---

## 2. 改动文件清单

### 改 `probe_vps/bootstrap.py`

```text
1. 顶部 docstring step ⑤ 描述:
   "PROBE_TEST_PORT (19000) 上没 socks5/freedom inbound → add + reload"
   → "XRAY_DEFAULT_PORT (18440) 上没 socks5/freedom inbound → add + reload"

2. import 加 from config import XRAY_DEFAULT_PORT

3. ensure_ready 主入口 ⑤ 步两处调用换常量:
   _has_socks5_freedom_inbound(cfg, PROBE_TEST_PORT)
     → _has_socks5_freedom_inbound(cfg, XRAY_DEFAULT_PORT)
   _append_socks5_freedom_inbound(cfg, PROBE_TEST_PORT)
     → _append_socks5_freedom_inbound(cfg, XRAY_DEFAULT_PORT)

4. 同段 log info "port=%d ..." 的实参从 PROBE_TEST_PORT 换 XRAY_DEFAULT_PORT
   (2 处 log)

5. ProbeVPSHandle 返回字段 inbound_port=PROBE_TEST_PORT 不动
   (这是给 IPProbeWorker "你可以在 19000 临挂" 的信号, 跟 ⑤ 装哪无关)
```

### 改 `test/probe_vps/TC-03_bootstrap_idempotent.py`

```text
TC-03 断言"19000 已有 socks/freedom → 跳过 add"。改成 18440:
- 顶部 docstring "19000 已有" → "18440 已有"
- _make_cfg_with_19000_inbound (或类似名) 函数里 PROBE_TEST_PORT → XRAY_DEFAULT_PORT
- 任何 assertEqual / assertIn 涉及 19000 的改成 18440
- handle.inbound_port == PROBE_TEST_PORT 这条**不变** (handle 字段语义不动)
```

### 改 `test/probe_vps/TC-04_bootstrap_fresh.py`

```text
TC-04 断言"fresh 装机后 19000 被 add"。改成 18440:
- 顶部 docstring "19000 inbound" → "18440 inbound"
- test_fresh_uploaded_config_contains_19000_inbound 函数名 → _18440_
- 内部 PROBE_TEST_PORT 断言 → XRAY_DEFAULT_PORT
- handle.inbound_port == PROBE_TEST_PORT 这条**不变**
```

### 不动

- `docs/adr/0009-*.md` (永不改原则)
- `xray/config.py` (build_vps_direct_config / add_proxy_binding / remove_proxy_binding 都不动, bug 不在它)
- `xray/manager.py::replace_proxy_binding` (不动)
- `workers/ip_probe_worker.py` (不动)
- `probe_vps/config.py` (PROBE_TEST_PORT=19000 不动, 注释也不动)
- `_append_socks5_freedom_inbound` 函数体里 tag="probe-direct" 不动 (统一命名另开任务)
- TC-01 / TC-02 / TC-05 (没断言 19000 inbound)

---

## 3. 验收交付

### 必跑测试命令

```bash
PYTHONPATH=. uv run pytest test/probe_vps/TC-*.py -v --tb=short
```

### 完工后真机清理 (用户手动, 不在代码)

T-19 在测试 VPS `203.0.113.20` 上实际装了脏数据 `probe-direct@19000`, 不清掉的话即使代码改了, ensure_ready 跑也不会动 19000 (它只看 18440), 但 IPProbeWorker 还是会撞。

```bash
# 1. SSH 进测试 VPS
ssh root@203.0.113.20    # 或 PROBE_VPS_1_* 凭据

# 2. 编辑 /usr/local/etc/xray/config.json (或安装路径)
#    删 inbounds 数组里 tag="probe-direct" / port=19000 那条
#    删 routing.rules 里 inboundTag 含 "probe-direct" 那条
#    (outbounds 不动)

# 3. reload xray
systemctl reload xray

# 4. 退出
exit

# 5. 本地重跑 init-probe-vps 验证幂等
PYTHONPATH=. uv run python main.py init-probe-vps
# 预期: 18440 上 default-direct 健在 → 跳过 add; 19000 空着
```

### 实现者完工标准

- [ ] 开工前已 waiting → doing
- [ ] `probe_vps/bootstrap.py` 按 §2 改完 (4 处: import + docstring + 2 处调用 + 2 处 log)
- [ ] TC-03 / TC-04 断言已改 18440
- [ ] 必跑测试全部 PASS
- [ ] 没改 "不动" 清单文件
- [ ] 真机清理 + 重跑 init 由用户手动验证 (实现窗口不替跑)
- [ ] 完成记录段已填

### 实现过程记录

```text
改动文件:
- probe_vps/bootstrap.py (5 处: docstring step ⑤ / import XRAY_DEFAULT_PORT /
  _has 调用 / _append 调用 / 2 处 log + 1 处 error msg 端口常量切换 +
  _append_socks5_freedom_inbound 加 4 行幂等检查)
- test/probe_vps/TC-03_bootstrap_idempotent.py (顶部 docstring + _ready_config
  port 改 XRAY_DEFAULT_PORT + 加 import)
- test/probe_vps/TC-04_bootstrap_fresh.py (顶部 docstring + test 函数名
  _19000_ → _18440_ + filter 改 XRAY_DEFAULT_PORT + 加 import)

测试结果:
- PYTHONPATH=. uv run pytest test/probe_vps/TC-*.py -v --tb=short
  → 34 passed in 0.15s

偏差 / 风险:
- 偏差: 跟用户对齐后追加修了 _append_socks5_freedom_inbound 函数体 (4 行幂等),
  原因: 端口切到 18440 后跟 build_vps_direct_config baseline 自带的
  default-direct@18440 端口重叠, 不加幂等会写出双 18440 inbound (xray 启动撞).
  用户拍板"做一个幂等, 最终效果 18440 是初始化端口, 19000 空配置".
- 风险: 真机 203.0.113.20:19000 上仍挂着 T-19 时实装的 probe-direct 脏配置;
  用户已确认会脱离任务手动 SSH 清掉. 不清的话 ensure_ready 后续跑只看 18440
  不会动 19000, IPProbeWorker 临挂 19000 时仍会撞 PortAlreadyBoundError.
```

---

## 完成记录 (done 时追加)

```text
完成日期: 2026-06-10
完成 commit: (本次提交后补)
任务状态: doing -> done
改动摘要:
  1. probe_vps/bootstrap.py step ⑤ 装默认入口的端口从 PROBE_TEST_PORT (19000)
     切到 XRAY_DEFAULT_PORT (18440), 恢复端口职责分离: 18440 = bootstrap init
     常驻 / 19000 = IPProbeWorker 临挂.
  2. _append_socks5_freedom_inbound 加 4 行幂等检查 (起完 baseline 后再调一次
     _has_socks5_freedom_inbound, 已有就直接 return), 修原代码隐藏 bug:
     port=XRAY_DEFAULT_PORT 时跟 baseline 自带 default-direct@18440 双叠.
  3. TC-03 / TC-04 断言 + docstring + 函数名同步切到 18440 (handle.inbound_port
     字段语义不动, 仍是 19000 给 IPProbeWorker 信号).

测试命令: PYTHONPATH=. uv run pytest test/probe_vps/TC-*.py -v --tb=short
测试结果: 34 passed in 0.15s (全 PASS)

未覆盖风险:
  - 真机 203.0.113.20 测试 VPS 上的 19000 probe-direct 脏配置未清, 用户手动
    SSH 清 + 重跑 init-probe-vps 验证.
  - _append_socks5_freedom_inbound 幂等加在内层, 外层 ensure_ready step ⑤
    的 _has 预检查仍保留, 两层都做 (没拆), 后续若想精简可一层. 当前选保守.

后续任务:
  - 用户手动真机清理 19000 脏数据 (任务单 §3 步骤已列, 用户执行).
  - 若想 inbound tag 统一 (probe-direct → default-direct), 另起任务, 本任务
    范围内 probe-direct tag 保持不动.
```
