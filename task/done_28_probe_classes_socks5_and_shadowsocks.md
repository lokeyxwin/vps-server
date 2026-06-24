# T-28 连通测试拆类: Socks5Probe(封装现逻辑) + ShadowsocksProbe(新增)

**ID**: T-28
**状态**: waiting
**前置依赖**: 无(可与 T-26 并行)
**后续依赖**: T-27(ProxyDeployWorker 调 ShadowsocksProbe)
**关联 ADR**: docs/adr/0011-* §决策 §4/§5
**关联 spec**: test/proxy_deploy_worker/spec.md(验证段) / test/ip_probe_worker/spec.md(测上游)

---

## 0. 开工前必读 / 领取锁

- [ ] 确认仍 waiting, 改名 doing_28_*.md
- [ ] 读: CLAUDE.md / CLAUDE.local.md / docs/adr/README.md / ADR-0011 /
      `toolbox/proxy_check.py` / `xray/service.py`(test_internal_socks) /
      `workers/ip_probe_worker.py`(用 test_internal_socks 的地方)

---

## 1. 用户原话 / 业务目标

> "原来的 socks5 测连通性的逻辑保留, 封装成类。再实现多个 ss 测试连通性的逻辑"
> "IP 入库前的连通测试, 可以用 socks5 的格式测 不影响 毕竟只是测上游能不能用"
> "外 ping 轻一点的吧 A"(= TCP 可达测)

### 业务理解

连通测试长期有两类: 上游/纳管是 socks5, 对外新节点是 SS。把现有 socks5 测试封成一个
类(行为不变), 再加一个 SS 测试类。SS 验证: 内 ping 在目标 VPS 起临时 xray 端到端测,
外 ping 在 worker 本机做 TCP 可达测(不拉核心)。

### 本任务要解决什么

- `Socks5Probe`: 现有 socks5 内/外 ping 封类, 行为零变化
- `ShadowsocksProbe`: 新增, 给 ProxyDeployWorker 验证 SS 节点用

### 不解决什么

- 不改 ProxyDeployWorker 调用点(T-27)
- 不动纳管读账密(later)

---

## 2. 实现参考

### 改 `toolbox/proxy_check.py`

1. `Socks5Probe` 类 —— 把现有 `test_internal` / `test_external` 搬进类做方法,
   **逻辑一字不改**:

```python
class Socks5Probe:
    def test_internal(self, client, port, user="", pwd="") -> tuple[bool, str]: ...  # 现 test_internal
    def test_external(self, host, port, user="", pwd="") -> bool: ...                # 现 test_external
```

2. `ShadowsocksProbe` 类 —— 新增:

```python
class ShadowsocksProbe:
    def test_internal(self, client, port, method, password) -> tuple[bool, str]:
        # 目标 VPS 起临时 xray 实例: socks-in(临时口) → ss-out(连 127.0.0.1:port, method/password)
        # → curl --socks5 临时口 拿 egress → 返回 (ok, egress)
        # ⚠️ try/finally 兜底 kill 临时进程 + 删临时配置, 不污染主 xray/config
    def test_external(self, host, port) -> bool:
        # socket.create_connection((host, port), timeout) 成功即 True(TCP 可达=安全组放行)
        # 不拉任何核心
```

### `ShadowsocksProbe.test_internal` 实现要点

- 临时配置写到独立路径(如 `/tmp/_ss_probe_<port>.json`), 不碰 `/usr/local/etc/xray/config.json`
- 临时 socks 入口端口选一个不冲突的(随机高位或固定测试口)
- `xray run -c <临时配置> &` 拿 pid → curl → kill pid → rm 临时配置
- curl 拿 body 作 egress IP(参考 `xray.service.test_internal_socks` 的 curl 解析)
- 失败(起不来/curl 不通)返回 (False, "")

### 改 `workers/ip_probe_worker.py`(谨慎)

- ADR §7: 测上游归 Socks5Probe。但 ip_probe 现在直接用 `xray.service.test_internal_socks`
  拿**完整 dict**(含 exit_code, 用于 4 类失败分类)。
- ⚠️ **不能丢 exit_code 分类能力**。两个选择, 实现者判断 + 必要时反问:
  - (a) ip_probe 暂不改(继续用 test_internal_socks), Socks5Probe 只服务 ProxyDeployWorker
        路径的简化 (ok, egress)。ADR §7 的"用 Socks5Probe"理解为协议归属, 调用形态改造延后。
  - (b) Socks5Probe 额外暴露一个返回完整 dict 的方法给 ip_probe 用。
- 默认走 (a)(最小改动, 不碰 exit_code 分类), 若选 (b) 在完成记录说明。

### 不动

- `xray/service.py::test_internal_socks`(socks5 内 ping 底层, 保留)
- 上游 / 默认入口逻辑

---

## 3. 验收交付

### 测试用例

- `Socks5Probe.test_internal/test_external` 行为 == 原函数(mock paramiko/requests 回归)
- `ShadowsocksProbe.test_external`: mock socket, 通=True / 拒=False / 超时=False
- `ShadowsocksProbe.test_internal`: mock SSH exec(起临时 xray + curl), 验证
  通→(True, egress) / 不通→(False, "") / **临时进程必被 kill + 临时配置必被删**(finally)
- ip_probe 测上游路径回归(exit_code 4 类分类不丢)

### 必跑测试命令

```bash
PYTHONPATH=. uv run pytest test/ -k "proxy_check or probe or ip_probe" -q
```

### 实现者完工标准

- [x] 开工改 doing
- [x] Socks5Probe 封装(行为不变) + ShadowsocksProbe(内 ping 临时 xray + 外 ping TCP) 完成
- [x] 临时 xray 实例 try/finally 清理已覆盖测试
- [x] ip_probe exit_code 分类未被破坏(选 a, 已说明)
- [x] 必跑测试全 PASS
- [x] 完成记录已填

---

## 完成记录(done 时追加)

```text
完成日期 / commit: 2026-06-24 / 未 commit(按指令不 git add/commit)
改动摘要(含 ip_probe 走 a 还是 b):
  - toolbox/proxy_check.py:
      · 保留 3 个现有模块级函数(test_socks_proxy / test_internal / test_external)不动
      · 新增 Socks5Probe 类: test_internal / test_external 委托给同名模块级函数,
        逻辑一字不改(行为零变化), 仅换调用形态成类方法
      · 新增 ShadowsocksProbe 类:
          test_internal(client, port, method, password): 目标 VPS 本机起临时 xray
            (socks-in 127.0.0.1:19010 noauth → ss-out 连 127.0.0.1:port, method/password)
            → curl --socks5 临时口拿 egress → (ok, egress)。
            临时配置写 /tmp/_ss_probe.json(独立路径, 绝不碰 /usr/local/etc/xray/config.json),
            nohup xray run 后台起 + 记 pid 到 /tmp/_ss_probe.pid。
            try/finally 兜底: kill $(cat pid) + rm 临时配置/pid/curl 输出。
          test_external(host, port): socket.create_connection 做 TCP 可达测, 不拉任何核心。
      · 新增 import: json / socket / ssh.ops.execute_command / xray.config.XRAY_BIN
        (无循环依赖: xray.config 与 ssh.ops 均不 import toolbox.proxy_check, grep 验证)
  - ip_probe 走【方案 a】: workers/ip_probe_worker.py **未改动**。
      理由: ip_probe 测上游靠 xray.service.test_internal_socks 返回的完整 dict
      (含 exit_code)做 4 类失败分类(proxy_refused/timeout/auth_failed/failed,
      见 _classify_proxy_error)。Socks5Probe.test_internal 只返回 (ok, egress),
      丢掉 exit_code 会破坏分类能力。按 task §2 默认选 a(最小改动): ADR §7 的
      "测上游用 Socks5Probe" 理解为【协议归属】, 调用形态改造延后;
      Socks5Probe 当前只服务 ProxyDeployWorker(T-27)的简化 (ok, egress) 路径。
      ip_probe 的 44 个 TC(含 TC-04 exit_code 4 类分类)全部回归通过, 能力未损失。
测试命令 / 结果(验收后健壮性修复重跑, 最新):
  PYTHONPATH=. uv run pytest test/ -k "proxy_check or probe or ip_probe" -q
  → 128 passed, 266 deselected in 0.51s
  细分:
    test/proxy_check/    24 passed (TC-01/02/03; 本轮 TC-03 加 h/i/j 共 +3)
    test/ip_probe_worker/ 44 passed (回归, 含 exit_code 分类)
    其余 probe_vps 等   60 passed
未覆盖风险:
  - ShadowsocksProbe.test_internal 全程 mock SSH exec, 未在真 VPS 跑;
    真机临时 xray 起停 / 端口 19010 占用 / curl 解析在 T-27 真机联调时验证。
  - 临时 socks 入口端口固定 19010(跟生产 18441+ / 测试 19000 隔离); 若同机并发
    两条 SS 探测会撞端口 —— 当前 ProxyDeployWorker 单台 VPS 单 worker 持锁, 不并发, 暂不冲突。
后续任务:
  - T-27: ProxyDeployWorker 改调 ShadowsocksProbe(内/外 ping) + 凭据改 SS。
  - later issue: ip_probe 若要统一走 Socks5Probe, 需方案 b(Socks5Probe 加返回完整 dict 的方法)。
```

## 验收后健壮性修复(2026-06-24, ShadowsocksProbe, 边界同前)

逐条改了啥(全在 toolbox/proxy_check.py):

1. _write_probe_config 改 base64 写入: Python 端 base64 编码 config_json →
   远程 `echo <b64> | base64 -d > path`(替换原 `cat > path << 'SS_PROBE_EOF'`
   heredoc), 规避 password 含特殊字符/换行时 heredoc 截断或注入。失败仍抛 RuntimeError。
   新增 `import base64`。

2. _start_probe_xray 加启动存活检查: 原 `nohup ... & echo $! > pid ; sleep 1`
   后台化必返 exit_code=0, xray 配错起来就退检测不到。在 sleep 1 后追加
   `kill -0 $(cat {PID}) 2>/dev/null` —— 进程已死则整条命令 exit_code != 0,
   被现有 check 捕获抛"起临时 xray 探测实例失败"。

3. _curl_through_probe 用 --socks5-hostname: `curl --socks5` → `--socks5-hostname`
   (= socks5h, 远端解析 DNS), 跟原 socks5 探测(test_socks_proxy 的 socks5h://)
   DNS 语义对齐, 避免 VPS 本地 DNS 假阴性。

4. 删 EXTERNAL_UNREACHABLE_MESSAGE 里过时端口: 文案写死的"18440-18450"(ADR-0002/0006
   已废固定段)改为泛化"对应节点端口"。常量名不变 —— grep 确认引用方为 legacy
   services/vps_init.py(L18 import / L190 用)+ 无测试断言其文案, 故安全。

补/调测试(test/proxy_check/TC-03_ss_probe_internal.py):
  - TC-03-e: 写入断言从 heredoc(max 行抠 JSON)改成 base64 解码(_decode_write_config),
    并断言命令含 "base64 -d" 且不含 "<<"。
  - TC-03-h(新): _start_probe_xray 命令含 "kill -0" + pid 路径(存活检查)。
  - TC-03-i(新): curl 命令含 "--socks5-hostname" 且不含裸 "--socks5 "。
  - TC-03-j(新): 特殊字符/换行密码经 base64 往返原样保留, 且 "rm -rf"/"echo pwned"
    不在 shell 命令文本里裸出现(证明无注入)。

## 实现过程记录

- 领取锁: waiting_28 → doing_28。
- 必读: CLAUDE.md / CLAUDE.local.md / ADR README / ADR-0011 / 本任务单 /
  toolbox/proxy_check.py / xray/service.py::test_internal_socks(L265-345) /
  workers/ip_probe_worker.py(确认 exit_code 4 类分类靠 test_internal_socks 完整 dict)。
- 参考 test_internal_socks 的 curl __HTTPCODE__/__BODY__ 解析协议, ShadowsocksProbe
  内 ping curl 复用同款解析。
- XRAY_BIN 住 xray/config.py(=/usr/local/bin/xray), 非根 config.py。
- 测试踩坑修正:
  · Socks5Probe.test_internal 委托的模块级 test_internal 内部是【局部 import】
    from xray.service import test_internal_socks, 所以 mock target 必须是
    "xray.service.test_internal_socks"(非 toolbox.proxy_check.test_internal_socks)。
  · 别名 import 模块级 test_internal/test_external, 避开 pytest python_functions=
    ["test_*"] 误把它们当测试函数收集。
- 文件边界: 只改 toolbox/proxy_check.py + 新增 test/proxy_check/(TC-01/02/03 + __init__.py);
  未碰 db/models.py / workers/proxy_deploy_worker.py / xray/ / dev DB。
