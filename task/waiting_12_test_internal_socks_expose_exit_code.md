# T-12 test_internal_socks 暴露 exit_code + stderr(向后兼容小手术)

**ID**: T-12
**状态**: waiting
**前置依赖**: 无
**后续依赖**: T-13(IPProbeWorker `_classify_proxy_error` 依赖新返回字段)
**关联 ADR**: 无直接 ADR(纯工具层小改)
**关联 spec**: [[test/ip_probe_worker/spec.md]] v2 §E(改动说明)+ §7 失败分类表

---

## 0. 开工前必读 / 领取锁

### 领取锁

- [ ] 已确认目标任务仍是 `waiting`。
- [ ] 开始写代码前, 已将文件名从 `waiting_12_...md` 改为 `doing_12_...md`。

### 必读清单

- [ ] `CLAUDE.md` §7.6 注释只写实现事实
- [ ] `test/ip_probe_worker/spec.md` v2 §E + §7
- [ ] `xray/service.py::test_internal_socks` 当前实现(`service.py:254-312`)
- [ ] `toolbox/proxy_check.py::test_internal`(当前消费 service 函数,只读 ok/body)
- [ ] `xray/manager.py::test_internal_socks`(透传 service,只是包装)
- [ ] `ssh/ops.py::execute_command` 返回 dict 形状(确认 `exit_code` / `stderr` 字段存在)

---

## 1. 用户原话 / 业务目标

### 用户原话

> "AUTH 精准说密码说请校验, 超时的话重试 3 次; 拒接我想不到什么原因"
> (拍板 IPProbeWorker 要 4 种 status 分类 + 温馨文案)

> "a"(确认接受小手术: test_internal_socks 加 exit_code + stderr 字段)

### 整理后的业务理解

- **外部输入**: 无(纯工具层改造)
- **影响业务**: IPProbeWorker 失败分类
  - 当前 `test_internal_socks` 返回 `{ok, http_code, body, error}` 丢失了 curl exit code
  - IPProbeWorker 要分 `proxy_auth_failed / proxy_timeout / proxy_refused / proxy_failed` 四种
  - 加 `exit_code` + `stderr` 后, IPProbeWorker 内部 `_classify_proxy_error(exit_code, stderr)` 私有方法分类
- **数据流**: 无(纯返回 dict 结构变化)

### 本任务要解决什么

`xray.service.test_internal_socks` 返回 dict **追加 2 个字段**(不删现有键):

```python
{
    "ok": ...,           # 不动
    "http_code": ...,    # 不动
    "body": ...,         # 不动
    "error": ...,        # 不动
    "exit_code": int,    # ⭐ 新增: curl 命令的 shell exit code
    "stderr": str,       # ⭐ 新增: curl 的 stderr 输出(便于 IPProbeWorker 关键字匹配)
}
```

### 本任务不解决什么

- ✗ 不实现 `_classify_proxy_error` 逻辑(住 T-13 工人内部私有方法)
- ✗ 不动 `XrayManager.test_internal_socks`(纯透传, 自动跟随)
- ✗ 不动 `toolbox/proxy_check.py::test_internal`(只读 ok/body, 不受影响)
- ✗ 不引入新工具函数(改动只在 `test_internal_socks` 一处)
- ✗ 不动 XrayWorker(只读 ok/body, 不受影响)
- ✗ 不动现有任何 TC(向后兼容)

---

## 2. 实现参考

### 验收锚点

- `test/ip_probe_worker/spec.md` v2 §E 改动说明
- `ssh/ops.py::execute_command` 返回 dict 应含 `exit_code` / `stdout` / `stderr` 三键
- 现有 `xray/config.py::upload_config` 已使用 `result["exit_code"]` / `result["stderr"]`(`config.py:644-649`),证明 execute_command 确实返回这些字段

### 改动文件清单

#### 改 `xray/service.py::test_internal_socks`

```text
当前实现 (service.py:289-312) 只读 result["stdout"] 解析:
   result = execute_command(client, cmd, timeout=timeout + 5)
   out = result["stdout"]
   ...
   return {"ok": ok, "http_code": http_code, "body": body, "error": ...}

改动:
   1. result 同样取, 但同时拿 result["exit_code"] 和 result["stderr"]
   2. 返回 dict 追加 exit_code + stderr 两个键
   3. 现有 4 个键值含义不变 (ok / http_code / body / error)
   4. 函数 docstring 更新返回值说明

curl 命令本身 (cmd 字符串构造) 不动 —— 当前命令把 curl stderr 也用 2>&1 合并到 stdout 了,
为了拿到独立 stderr, 需要去掉 2>&1。但去掉后会影响现有 stdout 中 __HTTPCODE__ 解析。

解决方案:
   curl 命令改成 ——
   curl ... 2>/tmp/_xray_internal_test.err
   (stderr 单独写到临时文件, 命令尾巴 cat 那个文件读出来)
   
或者更干净:
   curl ... 2>&1 不变 (现有 stdout 解析逻辑保留),
   stderr 字段从 result["stderr"] 取 (虽然 curl stderr 重定向到了 stdout,
   但 paramiko 通道的 stderr 可能仍有 shell 错误)
   —— 这样实现简单但 stderr 字段可能多数情况是空, 接受这个折中。

实现者拍板用哪种方式, 写到 docstring 里明确。
```

#### 改 `xray/manager.py::test_internal_socks`(纯透传, 文档更新)

```text
方法本身不改 (纯透传), 但 docstring 提一句:
"返回 dict 含 ok/http_code/body/error/exit_code/stderr (后两者用于 IPProbeWorker 错误分类)"
```

#### 改 `test/xray/test_service.py`(如有,确认 test_internal_socks 现有 TC 不挂)

```text
跑现有 test_internal_socks 相关 TC, 确认返回 dict 多 2 个键不影响断言。
如果旧 TC 用 == 严格断言 dict 全字段, 改为只断言关键键存在。
```

#### 新建 `test/xray/test_test_internal_socks_structure.py`(或加到现有测试)

```text
TC-12-a: 返回 dict 含 exit_code (int) + stderr (str), 老 4 个键也仍在
TC-12-b: ok=False 时 exit_code 非 0
TC-12-c: ok=True (mock curl 成功) 时 exit_code=0

mock 方式: monkeypatch execute_command 返回不同 result 结构
```

#### 不动

```text
- toolbox/proxy_check.py (test_internal / test_external 都只读 ok/body)
- workers/xray_worker.py (透传 ok, 不读新字段)
- xray/config.py
- 其他任何业务文件
```

### 实现轮廓

```python
# xray/service.py::test_internal_socks 改后:

def test_internal_socks(
    client: paramiko.SSHClient,
    port: int = config.XRAY_DEFAULT_PORT,
    test_url: str = "https://api.ipify.org",
    timeout: int = config.CONNECTIVITY_TEST_TIMEOUT,
    user: str = "",
    pwd: str = "",
) -> dict:
    """在服务器内部测试 xray socks5 是否真的能转发请求。
    
    [现有 docstring 主体]
    
    返回:
        {
            "ok": bool,
            "http_code": int | None,
            "body": str,
            "error": str | None,
            "exit_code": int,        # curl shell exit code (IPProbeWorker 用于分类)
            "stderr": str,           # paramiko 通道 stderr (curl stderr 已 2>&1 入 stdout, 此字段多为 shell 层错误)
        }
    
    IPProbeWorker 通过 exit_code 区分:
      7  → CURLE_COULDNT_CONNECT  (refused)
      28 → CURLE_OPERATION_TIMEDOUT (timeout)
      97 → CURLE_PROXY            (常为 socks auth 失败)
      其他 → 兜底 failed
    """
    # [现有 cmd 构造 + 解析 stdout 逻辑不动]
    
    result = execute_command(client, cmd, timeout=timeout + 5)
    out = result["stdout"]
    exit_code = result["exit_code"]   # ⭐ 新增
    stderr = result["stderr"]          # ⭐ 新增
    
    # [现有 http_code / body 解析不动]
    
    ok = http_code == 200
    return {
        "ok": ok,
        "http_code": http_code,
        "body": body,
        "error": None if ok else f"http_code={http_code} body={body!r}",
        "exit_code": exit_code,    # ⭐ 新增
        "stderr": stderr,          # ⭐ 新增
    }
```

### 数据结构 / 状态迁移

| 字段 | 改前 | 改后 |
|---|---|---|
| `ok` | bool | bool(不变) |
| `http_code` | int \| None | int \| None(不变) |
| `body` | str | str(不变) |
| `error` | str \| None | str \| None(不变) |
| `exit_code` | (不存在) | int **新增** |
| `stderr` | (不存在) | str **新增** |

### 缺工具 / 缺信息先报告

- 如果发现 `ssh.ops.execute_command` 实际不返回 `exit_code` / `stderr` 字段 → 立即停下报告(spec / 任务单基于"已返回"假设)
- 如果发现 XrayWorker / `toolbox.proxy_check.test_internal` 真的有断言 dict 全字段(==),需要先评估影响再改

---

## 3. 验收交付

### 测试用例

#### TC-12-a `test/xray/test_test_internal_socks_structure.py`

业务故事:

```text
IPProbeWorker 调 test_internal_socks 后, 拿到的 dict 必须含 exit_code (int) + stderr (str),
同时老 4 个键 (ok/http_code/body/error) 仍存在, 现有调用方不受影响。
```

输入:

- mock execute_command 返回 `{exit_code: 0, stdout: "...__HTTPCODE__200__BODY__...", stderr: ""}`
- 调 test_internal_socks(client_mock, port=19000, user="u", pwd="p")

预期:

- 返回 dict 包含全部 6 个键: ok, http_code, body, error, exit_code, stderr
- 6 键类型一致(`exit_code` int, `stderr` str)
- ok=True 时 exit_code == 0
- ok=False 时 exit_code != 0

#### TC-12-b 现有调用方不挂

```text
跑 test/xray_worker/TC-*.py 中任何调 test_internal / test_internal_socks 的用例,
确认全部 PASS (向后兼容)。
```

### 必跑测试命令

```bash
VPS_SERVER_TESTING=1 pytest test/xray/test_test_internal_socks_structure.py test/xray_worker/ -v
```

(如果 `test/xray/` 目录不存在, 新建并加 `__init__.py`)

### 实现者完工标准

- [ ] 开工前文件名 waiting → doing
- [ ] `xray/service.py::test_internal_socks` 返回 dict 加 `exit_code` + `stderr` 两键
- [ ] 函数 docstring 更新返回值说明
- [ ] `xray/manager.py::test_internal_socks` docstring 顺手提一句"两个新字段供 IPProbeWorker 用"
- [ ] 新增 / 改的测试都 PASS
- [ ] 现有 XrayWorker / toolbox.proxy_check 相关 TC 全 PASS(向后兼容验证)
- [ ] 没改 `toolbox/proxy_check.py` / `workers/xray_worker.py` / `xray/config.py`
- [ ] 完成记录段已填

### 实现过程记录

```text
改动文件:
- xray/service.py
- xray/manager.py (docstring)
- test/xray/test_test_internal_socks_structure.py (新增)

测试结果:
- VPS_SERVER_TESTING=1 pytest ... -> <result>

stderr 字段填充策略 (实现者拍板写入):
- <方案 A: 保留 2>&1 写入 stdout, stderr 字段从 result["stderr"] 拿(多为空) | 方案 B: ...>

偏差 / 风险:
- <none | details>
```

### Claude 验收检查清单

□ 对照 spec v2 §E 检查字段名 / 类型
□ 检查老 4 个键值含义未变
□ 跑 xray_worker TC 验证向后兼容
□ 跑新增 TC 验证新字段
□ 检查 toolbox.proxy_check 不受影响
□ 偏差但合理 -> 抛给用户决策
□ 偏差不合理 -> 打回实现者修改

---

## 完成记录(done 时追加)

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
