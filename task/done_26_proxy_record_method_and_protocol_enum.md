# T-26 proxy_record 加 ProxyProtocol 常量类 + method 字段

**ID**: T-26
**状态**: waiting
**前置依赖**: 无(地基, 可与 T-28 并行)
**后续依赖**: T-27(配置+worker 用 method/protocol) / T-29(返回用 method)
**关联 ADR**: docs/adr/0011-client-inbound-socks5-to-shadowsocks.md §决策 §3
**关联 spec**: 无(纯模型层, 行为由 ADR 约束)

---

## 0. 开工前必读 / 领取锁

- [ ] 确认仍是 waiting, 开工前改名 `doing_26_*.md`
- [ ] 读: CLAUDE.md / CLAUDE.local.md / docs/adr/README.md / ADR-0011 / `db/models.py`(ProxyStatus + ProxyRecord 段)

---

## 1. 用户原话 / 业务目标

> "入库的时候增加一个枚举"

### 业务理解

对外节点从 socks5 改 Shadowsocks 后, proxy_record 要能记下"这条节点用什么协议 +
什么加密方式", 才能拼出标准 `ss://` 给客户端。本任务只动模型层(地基), 不碰业务流。

### 本任务要解决什么

- proxy_record 能区分 socks5 / shadowsocks 两种协议(常量类约束)
- proxy_record 能存 SS 加密方式(method 字段)

### 不解决什么

- 不改 ProxyDeployWorker 部署逻辑(T-27)
- 不改连通测试(T-28)
- 不改对外返回(T-29)

---

## 2. 实现参考

### 改 `db/models.py`

1. 新增 `ProxyProtocol` 常量类(放在 `ProxyStatus` 附近, 同款常量类风格, 非 Enum):

```python
class ProxyProtocol:
    """对外客户端 inbound 协议常量。"""
    SOCKS5      = "socks5"        # 旧节点 / 纳管存量
    SHADOWSOCKS = "shadowsocks"   # 新部署默认(ADR-0011)
```

2. `ProxyRecord` 加 `method` 字段(SS 加密方式, socks5 节点留空):

```python
# 客户端连接侧 protocol 之后
method: Mapped[str] = mapped_column(String(32), default="", nullable=False)
```

3. `from_new_deployment`(L261) 加 `method` 参数 + 传入:

```python
def from_new_deployment(cls, *, ..., protocol: str = ProxyProtocol.SOCKS5,
                        method: str = "") -> "ProxyRecord":
    return cls(..., protocol=protocol, method=method, ...)
```

4. `ProxyRecord.protocol` 注释更新(指向 ProxyProtocol 常量类)。

### 不动

- `from_extracted_binding`(纳管工厂) 不强制改(纳管 later); 若签名共用注意 method 默认空
- 不碰 ProxyStatus / 其他字段

### dev SQLite 迁移

```sql
ALTER TABLE proxy_record ADD COLUMN method VARCHAR(32) NOT NULL DEFAULT '';
```
(生产库存量已手动加过, 见已完成的运维升级; dev 库由实现者跑一次)

---

## 3. 验收交付

### 测试用例

- `ProxyProtocol` 常量值正确(socks5 / shadowsocks)
- `ProxyRecord.method` 字段存在, 默认空串
- `from_new_deployment(protocol=SHADOWSOCKS, method="aes-256-gcm")` 落库后读回一致
- `__repr__` 不泄露密码(回归)

### 必跑测试命令

```bash
PYTHONPATH=. uv run pytest test/_data_structures/ test/db/ -q
```

### 实现者完工标准

- [x] 开工已改 doing
- [x] ProxyProtocol 类 + method 字段 + from_new_deployment 参数 完成
- [x] dev 库已 ALTER TABLE 加列
- [x] 必跑测试全 PASS
- [x] 没动"不动"清单
- [x] 完成记录已填(测试结果原样贴)

---

## 完成记录(done 时追加)

```text
完成日期: 2026-06-24
完成 commit: (留需求窗口统一提交)
改动摘要:
  - db/models.py:
    · 新增 ProxyProtocol 常量类 (SOCKS5="socks5" / SHADOWSOCKS="shadowsocks"),
      放 ProxyStatus 之后, 常量类风格 (非 Enum), 与 ProxyStatus 一致。
    · ProxyRecord 加 method 字段 (String(32) default="" nullable=False), 放 protocol 之后;
      protocol 字段注释更新指向 ProxyProtocol, 默认值字面量 "socks5" 改用 ProxyProtocol.SOCKS5。
    · from_new_deployment 加 method: str = "" 参数 + 传入 cls(method=method);
      protocol 默认值 "socks5" 改用 ProxyProtocol.SOCKS5。
    · from_extracted_binding (纳管工厂): protocol fallback 字面量 "socks5" 改用
      ProxyProtocol.SOCKS5 (验收顺手修, 消除字面量重复); method 纳管 later 不传, 保持现状。
  - 新增 test/_data_structures/test_proxy_record_method.py (6 个 in-memory SQLite 用例):
    TC-01 ProxyProtocol 常量值 / TC-02 method 在 ORM mapping / TC-03 method 默认空串 +
    protocol 默认 socks5 / TC-04 SHADOWSOCKS+method round-trip / TC-05 column 默认值
    (protocol=socks5, method="") / TC-06 __repr__ 不泄露密码。
  - dev SQLite 迁移: db/vps_server.db 与 db/vps_server_test.db 均执行
    `ALTER TABLE proxy_record ADD COLUMN method VARCHAR(32) NOT NULL DEFAULT '';`
    (PRAGMA 已确认两库均有 method 列)。
测试命令 / 结果:
  PYTHONPATH=. uv run pytest test/_data_structures/ test/db/ -q
  → 53 passed, 1 skipped in 1.38s
  (验收顺手修 from_extracted_binding 字面量后重跑, 结果同前。
   唯一 skip: test/_data_structures/test_vps_task.py:262 "等真机 PostgreSQL/MySQL
   多连接环境验证抢锁原子性", 既有用例, 与本任务改动无关。新增 6 个用例全 PASS 无 skip。)
未覆盖风险:
  - 纳管工厂 from_extracted_binding 仅复用了 protocol 常量, method 纳管 later 不传
    (纳管支持 SS 列 later 单独 issue, ADR-0011 §决策 §7)。
  - 本任务只动模型层 (地基), 部署/连通测试/对外返回的 SS 改造在 T-27 / T-28 / T-29。
后续任务: T-27 (配置+worker 用 method/protocol) / T-29 (返回用 method)
```
