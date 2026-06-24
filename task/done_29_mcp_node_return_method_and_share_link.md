# T-29 MCP 节点返回加 method + 自产 ss:// share_link

**ID**: T-29
**状态**: waiting
**前置依赖**: **T-26**(method 字段) + **T-27**(部署出 SS 节点)
**后续依赖**: 无(本批收尾)
**关联 ADR**: docs/adr/0011-* §决策 §8
**关联 spec**: test/mcp_tools/spec.md

---

## 0. 开工前必读 / 领取锁

- [ ] 确认仍 waiting + 确认 T-26/T-27 已 done
- [ ] 改名 doing_29_*.md
- [ ] 读: CLAUDE.md / CLAUDE.local.md / docs/adr/README.md / ADR-0011 / ADR-0007 / ADR-0008 /
      test/mcp_tools/spec.md / `db/queries.py`(_build_proxy_node + list_available_proxies) /
      `tools/get_available_proxy_nodes.py` / `tools/get_ip_registration_status.py`

---

## 1. 用户原话 / 业务目标

> "项目自己产 ss:// 链接... 兼容两种, 同时导入小火箭后小火箭分享也能被安卓代理软件使用"

### 业务理解

项目掌握节点全信息, 直接吐标准 `ss://` 链接 + method, agent/用户一次拿全, 不依赖任何
单个客户端的私有分享格式。二维码不在后端生成, 只返链接文本, 渲染交客户端。

### 本任务要解决什么

MCP 查询节点时返回里多 `method` + `share_link`(SS 节点拼 `ss://`)。

### 不解决什么

- 不在后端生成二维码图片(只返链接文本)
- 不改部署逻辑(T-27)

---

## 2. 实现参考

### 新建 `toolbox/share_link.py`

```python
import base64
from urllib.parse import quote

def build_ss_url(method: str, password: str, host: str, port: int, tag: str = "") -> str:
    """拼 SIP002 标准 ss:// 链接(无 padding base64url)。"""
    userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
    suffix = f"#{quote(tag)}" if tag else ""
    return f"ss://{userinfo}@{host}:{port}{suffix}"
```

### 改 `db/queries.py`

- `_build_proxy_node`(L199 返回 dict) 加:
  - `"method": proxy.method`
  - `"share_link": <按 protocol 拼>` —— shadowsocks → `build_ss_url(method, pwd, vps_ip,
    vps_port, tag=国家-ip_id)`; socks5 → 留空串或 socks 链接(本批 socks5 已是存量, 可留空)
- `list_available_proxies`(L267 返回 dict) 同样加 `method` + `share_link`

### 改 `tools/get_available_proxy_nodes.py` / `tools/get_ip_registration_status.py`

- description 补: 返回含 `method` + `share_link`(标准 ss://), 教 agent 怎么把 share_link
  给用户(可直接发, 或让用户扫码)
- test/mcp_tools/spec.md 同步(返回字段说明)

### 不动

- 不新增 MCP 工具(ADR-0011 §8 + §14.5 走"现有工具加字段")
- 不改写入工具 / task 表

---

## 3. 验收交付

### 测试用例

- `build_ss_url("aes-256-gcm","pwd","1.2.3.4",8388,"SG-42")` == 预期 SIP002 串
  (base64url 无 padding, host:port 明文, #tag urlencode)
- `list_available_proxies` / `_build_proxy_node` 返回含 method + 正确 share_link(SS 节点)
- socks5 存量节点 share_link 行为符合约定(留空或 socks)

### 必跑测试命令

```bash
PYTHONPATH=. uv run pytest test/mcp_tools/ -k "share or proxy or queries" -q
PYTHONPATH=. uv run pytest test/ -k "share_link" -q
```

### 实现者完工标准

- [x] 开工改 doing + 确认 T-26/T-27 已 done(method 字段 + ProxyProtocol + from_new_deployment method 参数均已入库)
- [x] toolbox/share_link.py 新建 + db/queries.py 加字段 + 两个 tools description 改
- [x] test/mcp_tools/spec.md 同步
- [x] 必跑测试全 PASS
- [x] 没新增工具 / 没碰写入工具
- [x] 完成记录已填

---

## 完成记录(done 时追加)

```text
完成日期 / commit: 2026-06-24 / 未 commit(保持 doing, 待需求窗口验收)

改动摘要:
  1. 新建 toolbox/share_link.py::build_ss_url(method, password, host, port, tag="")
     纯函数(无状态), 拼 SIP002 标准 ss:// 链接:
       - userinfo = base64url(method:password) 去 padding
       - host:port 明文; tag 走 urlencode; 空 tag 不带 # 后缀
  2. db/queries.py:
     - import ProxyProtocol + build_ss_url
     - _build_proxy_node(经 query_ip_status) 返回 dict 加 "method" + "share_link"
       (shadowsocks → 拼 ss:// tag=ip_id; socks5 → share_link 空串)
     - list_available_proxies 抽内部小工具 _build_available_proxy 收口单行组装,
       返回 dict 加 "method" + "share_link"(shadowsocks → 拼 ss:// tag=国家-ip_id;
       socks5 → 空串), 同步两处 docstring 返回形状说明
  3. tools/get_available_proxy_nodes.py + tools/get_ip_registration_status.py
     description 补返回含 method + share_link, 教 agent 优先用 ss:// share_link 交付
     (shadowsocks 直接发链接扫码/粘贴导入小火箭/v2rayNG/Clash; socks5 存量 share_link
     空, 走手动填写, 不伪造 ss://)
  4. test/mcp_tools/spec.md §6.4 + §6.5 返回字段加 method + share_link, 转告话术改
     "优先用 ss:// share_link", 修订历史 +v5
  5. 新建 test/mcp_tools/TC-15_build_ss_url.py(5 用例: SIP002 格式 + 无 padding +
     往返 + tag urlencode + 空 tag) + TC-16_node_return_method_share_link.py(5 用例:
     query_ip_status / list_available_proxies 两路径 × SS/socks5 + host:port 对齐,
     in-memory SQLite + monkeypatch session_scope, from_new_deployment 造记录保证
     get_inbound_pwd 往返)

  不新增 MCP 工具(ALL_TOOLS 未动, ADR-0011 §8 + §14.5 走"现有工具加字段");
  未碰 workers/ / xray/ / db/models.py / toolbox/proxy_check.py。

测试命令 / 结果:
  $ PYTHONPATH=. uv run pytest test/mcp_tools/ -k "share or proxy or queries" -q
    → 10 passed, 55 deselected in 0.51s
  $ PYTHONPATH=. uv run pytest test/ -k "share_link" -q
    → 5 passed, 404 deselected in 0.33s
  $ PYTHONPATH=. uv run pytest test/mcp_tools/TC-15_build_ss_url.py test/mcp_tools/TC-16_node_return_method_share_link.py -v
    → 10 passed in 0.12s
  $ PYTHONPATH=. uv run pytest test/mcp_tools/ -q   (回归)
    → 65 passed in 0.32s
  $ PYTHONPATH=. uv run pytest test/ -q             (全项目回归)
    → 406 passed, 3 skipped in 3.67s
      (3 skipped = 真服务器测试, 默认 skip, 符合约定; 0 failed)

未覆盖风险:
  - share_link 是后端自产的标准 ss:// 文本, 实际客户端导入连通性靠真机验证
    (单测只验 SIP002 格式正确 + base64 往返, 不验客户端真导入)
  - socks5 存量节点 share_link 留空(本批焦点 SS); socks5 标准分享链接 later
  - _build_proxy_node 的 tag 用 ip_id(此函数路径没查 country_code, 按任务单约定);
    list_available_proxies 路径有 country_code 用"国家-ip_id"

后续任务:
  - 无(T-29 是 ADR-0011 批4 收尾)。socks5 分享链接 / 后端生成二维码若需要再开新 issue
```
