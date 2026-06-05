"""xray 配置层：构造 config dict 的纯函数 + 配置文件 SSH 操作。

本模块包含两类东西：

1. 纯函数（不接 SSH / DB）：
   - build_proxy_outbound       ← 上游代理凭据 → outbound dict 片段
   - generate_random_auth       ← 随机账密（给客户端 inbound 用）
   - build_vps_direct_config    ← 完整 config：VPS 自用直出（freedom outbound）
   - build_proxy_relay_config   ← 完整 config：客户端走上游代理（代理跳转）
   - extract_port_bindings      ← 从 config dict 抠出每条客户端 inbound 绑定信息

2. SSH 操作（接 paramiko client）：
   - get_config_size / is_config_blank
   - write_default_config       ← 把 VPS 直出场景默认 config 写到服务器
   - upload_config              ← 上传任意 config dict 到 DEFAULT_CONFIG_PATH
   - validate_config            ← 跑 `xray test -c` 验证 config 语法
   - read_config                ← 从服务器拉 config.json 解析成 dict

xray 服务层（install/start/stop/enable/disable/...）在 xray/service.py。

约定（业务侧扩展）：
- outbound 的自定义元数据走 `_meta` 字典（下划线前缀=非 xray 标准字段）
- 当前消费方：extract_port_bindings 从 `_meta` 读 egress_ip / egress_country
- xray 自身忽略未知字段，不影响功能
"""

from __future__ import annotations

import copy
import json
import secrets
import string

import paramiko

import config
from core.ssh import execute_command


# ============================================================
# 协议常量（外部业务可直接 import 使用）
# ============================================================

PROTOCOL_SOCKS5 = "socks5"
PROTOCOL_HTTP = "http"

# 业务层入参校验时用这个集合；扩展协议在此注册
SUPPORTED_PROTOCOLS = (PROTOCOL_SOCKS5, PROTOCOL_HTTP)

# xray config.json 里的 protocol 字段跟我们用户层略不同：
# 对外讲 "socks5"，xray 里写 "socks"。集中映射避免业务层硬编码。
_XRAY_PROTOCOL_NAME = {
    PROTOCOL_SOCKS5: "socks",
    PROTOCOL_HTTP: "http",
}


# ============================================================
# 路径 / 端口常量
# ============================================================

DEFAULT_CONFIG_PATH = "/usr/local/etc/xray/config.json"
DEFAULT_PORT = config.XRAY_DEFAULT_PORT


# ============================================================
# 错误文案
# ============================================================

UNSUPPORTED_PROTOCOL_MESSAGE = (
    "不支持的代理协议。当前仅支持 socks5 / http；"
    "如需扩展，请在 xray.config.SUPPORTED_PROTOCOLS 注册新值，"
    "并在 _XRAY_PROTOCOL_NAME 补充映射。"
)

PORT_CONFLICTS_WITH_DEFAULT_MESSAGE = (
    "客户端入站端口与 xray 默认直出端口冲突。"
    "rgIP 业务的端口应从 PROXY_PORT_RANGE_START..END 范围（默认 18441-18450）挑选；"
    "默认端口 18440 给 VPS 自用直出（default-direct），不能被客户端 inbound 占用。"
)

CONFIG_WRITE_FAILED_MESSAGE = (
    "向 xray config 文件写入内容失败。"
    "常见原因：用户无写权限 / 目录不存在 / 磁盘满。"
    "建议：登录服务器跑 `ls -l /usr/local/etc/xray/` 看权限和挂载状态。"
)

CONFIG_VALIDATION_FAILED_MESSAGE = (
    "xray test -c config.json 校验失败：配置语法或语义错误。"
    "建议：登录服务器跑 `xray test -confdir /usr/local/etc/xray/` 看具体行号；"
    "或先回滚到上一个已知能用的 config 再排查。"
)

CONFIG_READ_FAILED_MESSAGE = (
    "从服务器读取 xray config.json 失败。"
    "常见原因：① 文件存在但 JSON 损坏（非空但无法 parse）；② 用户无读权限。"
    "建议：登录服务器跑 `cat /usr/local/etc/xray/config.json` 看实际内容。"
)

PORT_ALREADY_BOUND_MESSAGE = (
    "目标端口已经被现有 inbound 占用，不能重复绑定。"
    "通常意味着 rgvps 端口审计与实际 xray config 不一致（罕见竞态）。"
    "建议：让业务返回 no_available_port，让 caller 重新挑端口或检查 proxy_record 是否已有该端口的行。"
)

OUTBOUND_TAG_CONFLICT_MESSAGE = (
    "目标 outbound tag 已存在于当前 config。"
    "caller 应给 proxy_outbound 起一个该 config 里不重复的 tag。"
    "建议 tag 命名：proxy-{country_code}-{vps_port} 或 proxy-{vps_port}，"
    "确保跨多次部署不重复。"
)


# ============================================================
# 错误类
# ============================================================

class UnsupportedProtocolError(ValueError):
    """传入了 SUPPORTED_PROTOCOLS 之外的协议字符串。"""


class PortConflictError(ValueError):
    """客户端入站端口跟 xray 默认直出端口（DEFAULT_PORT）撞了。"""


class PortAlreadyBoundError(ValueError):
    """目标 vps_port 已被 current config 里某条 inbound 占用。"""


class OutboundTagConflictError(ValueError):
    """proxy_outbound 的 tag 跟 current config 里已有 outbound tag 撞了。"""


class ConfigWriteError(RuntimeError):
    """写 config 文件到服务器失败（权限 / 目录 / 磁盘等）。"""


class ConfigValidationError(RuntimeError):
    """xray test -c 校验 config 失败。"""


class ConfigReadError(RuntimeError):
    """读取 / 解析服务器上 xray config.json 失败（非"文件不存在"，那种返回 {}）。"""


# ============================================================
# 内部常量：默认直出场景的 inbound / outbound dict
# ----- 这俩是 build_vps_direct_config 和 build_proxy_relay_config 共用的"砖" -----
# ============================================================

# VPS 自用直出 inbound：监听 DEFAULT_PORT 的免认证 socks5
_DEFAULT_DIRECT_INBOUND = {
    "tag": "default-direct",
    "port": DEFAULT_PORT,
    "listen": "0.0.0.0",
    "protocol": "socks",
    "settings": {"auth": "noauth", "udp": True},
}

# VPS 自用直出 outbound：freedom 直连，不走任何代理
_DEFAULT_DIRECT_OUTBOUND = {
    "tag": "direct",
    "protocol": "freedom",
}


# ============================================================
# 纯函数：随机账密生成（给客户端 inbound 用）
# ============================================================

_AUTH_ALPHABET = string.ascii_letters + string.digits
_DEFAULT_AUTH_LEN = 16


def generate_random_auth(length: int = _DEFAULT_AUTH_LEN) -> tuple[str, str]:
    """生成随机 (user, pwd) 给客户端 inbound 账密用。

    用 secrets 模块（加密安全），字符集是大小写字母 + 数字（避免 shell / URL 转义麻烦）。
    默认长度 16，可调；同一次调用 user 和 pwd 是两条独立的随机串。

    返回 (user, pwd) 元组，业务层拿去：
        ① 入库（落到代理节点记录的 inbound_user / inbound_pwd_encrypted）
        ② 传给 build_proxy_relay_config(inbound_user=u, inbound_pwd=p)
        ③ 返回给用户作为客户端连接凭据
    """
    user = "".join(secrets.choice(_AUTH_ALPHABET) for _ in range(length))
    pwd = "".join(secrets.choice(_AUTH_ALPHABET) for _ in range(length))
    return user, pwd


# ============================================================
# 纯函数：构造 config dict 片段（outbound 原语）
# ============================================================

def build_proxy_outbound(
    host: str,
    port: int,
    user: str,
    pwd: str,
    protocol: str,
    tag: str = "proxy-out",
) -> dict:
    """把一条上游代理凭据翻成 xray outbound 字典片段。

    用法：把返回的 dict 加进完整 xray config 的 outbounds[] 里，
    再用 routing.rules 把对应 inbound 路由到这个 tag。

    参数：
        host / port: 上游代理入口（entry_host / entry_port）
        user / pwd : 上游代理账密（同时为空 = 免认证代理；任一非空则按账密认证装）
        protocol  : "socks5" 或 "http"（用 PROTOCOL_* 常量传更安全）
        tag       : outbound 在 xray 里的标识，路由规则用它指代本 outbound

    传入未注册的 protocol 抛 UnsupportedProtocolError。
    """
    if protocol not in SUPPORTED_PROTOCOLS:
        raise UnsupportedProtocolError(
            f"{UNSUPPORTED_PROTOCOL_MESSAGE} 传入的是: {protocol!r}"
        )

    server: dict = {"address": host, "port": port}
    # user 或 pwd 任一非空就带 users（有些代理只校验 user，pwd 可空）
    if user or pwd:
        server["users"] = [{"user": user, "pass": pwd}]

    return {
        "tag": tag,
        "protocol": _XRAY_PROTOCOL_NAME[protocol],
        "settings": {"servers": [server]},
    }


# ============================================================
# 纯函数：场景级完整 config 构造（顶层入口）
# ============================================================

def build_vps_direct_config() -> dict:
    """场景一：VPS 自用直出。

    inbounds:  [default-direct (18440 socks noauth)]
    outbounds: [direct (freedom)]
    routing:   default-direct → direct

    业务用途：装完 xray 默认写这个 config 让服务能起来（rgvps / install_xray 用）。
    """
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [_DEFAULT_DIRECT_INBOUND],
        "outbounds": [_DEFAULT_DIRECT_OUTBOUND],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["default-direct"],
                    "outboundTag": "direct",
                },
            ],
        },
    }


def build_proxy_relay_config(
    vps_port: int,
    proxy_outbound: dict,
    inbound_user: str,
    inbound_pwd: str,
) -> dict:
    """场景二：客户端走上游代理跳转。

    inbounds:
        ① default-direct       ← DEFAULT_PORT 上的免认证 socks5（保留 VPS 自用通路）
        ② client-{vps_port}    ← vps_port 上的账密 socks5（客户端连这个）
    outbounds:
        ① direct               ← freedom 直出（VPS 自用）
        ② <proxy_outbound>     ← 上游代理（caller 用 build_proxy_outbound 造好传入）
    routing:
        ① default-direct → direct
        ② client-{vps_port} → <proxy_outbound 的 tag>

    参数：
        vps_port       : 客户端要连的端口（必须 != DEFAULT_PORT）
        proxy_outbound : build_proxy_outbound 产出的 outbound dict
        inbound_user   : 客户端连本机的账号（业务层用 generate_random_auth() 生成）
        inbound_pwd    : 客户端连本机的密码（同上）

    失败：vps_port == DEFAULT_PORT → 抛 PortConflictError
    """
    if vps_port == DEFAULT_PORT:
        raise PortConflictError(
            f"{PORT_CONFLICTS_WITH_DEFAULT_MESSAGE} 传入 vps_port={vps_port}"
        )

    client_tag = f"client-{vps_port}"

    client_inbound = {
        "tag": client_tag,
        "port": vps_port,
        "listen": "0.0.0.0",
        "protocol": "socks",
        "settings": {
            "auth": "password",
            "udp": True,
            "accounts": [{"user": inbound_user, "pass": inbound_pwd}],
        },
    }

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [_DEFAULT_DIRECT_INBOUND, client_inbound],
        "outbounds": [_DEFAULT_DIRECT_OUTBOUND, proxy_outbound],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["default-direct"],
                    "outboundTag": "direct",
                },
                {
                    "type": "field",
                    "inboundTag": [client_tag],
                    "outboundTag": proxy_outbound["tag"],
                },
            ],
        },
    }


# ============================================================
# 纯函数：从 config dict 反向抠出端口绑定信息
# ============================================================

# xray-side protocol → 用户层 protocol 反查表（"socks" → "socks5"）
_USER_PROTOCOL_NAME = {v: k for k, v in _XRAY_PROTOCOL_NAME.items()}


def extract_port_bindings(config_dict: dict) -> list[dict]:
    """从 xray config dict 抠出"每条非 default-direct 客户端 inbound"对应的绑定信息。

    用途：rgvps 重装时，把已部署在 xray 里的代理出口信息抄录到 proxy 表，
    避免把已绑定的端口当成"空闲"重新分配。

    返回 list[dict]，每项含：
        protocol         ← 还原为对外名（"socks5"/"http"），未知协议原样保留
        listen_address   ← inbound 的 listen 字段（默认 "0.0.0.0"）
        port             ← inbound 端口
        inbound_user     ← settings.accounts[0].user（无认证时为 ""）
        inbound_pwd      ← settings.accounts[0].pass（无认证时为 ""）
        upstream_host    ← 对应 outbound 的 servers[0].address（上游入口）
        egress_ip        ← outbound["_meta"]["egress_ip"]（业务约定字段；无则为 ""）
        egress_country   ← outbound["_meta"]["egress_country"]（同上）

    跳过 tag="default-direct" 的 inbound（VPS 自用通路）。
    没有 routing 规则关联的 inbound 仍会出现在结果里，但 upstream/* 字段会留空。

    本函数是纯字典操作，xray 不在场也能跑（方便单测）。
    """
    inbounds = config_dict.get("inbounds", []) or []
    outbounds = config_dict.get("outbounds", []) or []
    routing = config_dict.get("routing", {}) or {}
    rules = routing.get("rules", []) or []

    # 建 inbound_tag → outbound_tag 映射
    routing_map: dict[str, str] = {}
    for rule in rules:
        in_tags = rule.get("inboundTag", []) or []
        out_tag = rule.get("outboundTag", "")
        for tag in in_tags:
            routing_map[tag] = out_tag

    # 按 tag 索引 outbound
    outbound_by_tag = {ob.get("tag", ""): ob for ob in outbounds}

    bindings: list[dict] = []
    for inb in inbounds:
        tag = inb.get("tag", "")
        if tag == "default-direct":
            continue  # 跳过 VPS 自用通路

        # ----- inbound 侧 -----
        xray_protocol = inb.get("protocol", "")
        protocol = _USER_PROTOCOL_NAME.get(xray_protocol, xray_protocol)
        port = inb.get("port", 0)
        listen = inb.get("listen", "")

        accounts = inb.get("settings", {}).get("accounts", []) or []
        if accounts:
            inbound_user = accounts[0].get("user", "")
            inbound_pwd = accounts[0].get("pass", "")
        else:
            inbound_user = ""
            inbound_pwd = ""

        # ----- outbound 侧（通过 routing 找）-----
        out_tag = routing_map.get(tag, "")
        outbound = outbound_by_tag.get(out_tag, {})
        servers = outbound.get("settings", {}).get("servers", []) or []
        upstream_host = servers[0].get("address", "") if servers else ""
        meta = outbound.get("_meta", {}) or {}
        egress_ip = meta.get("egress_ip", "")
        egress_country = meta.get("egress_country", "")

        bindings.append({
            "protocol": protocol,
            "listen_address": listen,
            "port": port,
            "inbound_user": inbound_user,
            "inbound_pwd": inbound_pwd,
            "upstream_host": upstream_host,
            "egress_ip": egress_ip,
            "egress_country": egress_country,
        })

    return bindings


# ============================================================
# 纯函数：往现有 config 增量加 / 删一组 binding（rgIP 业务用）
#
# 业务约定：一条 rgIP 部署 = 三件套（一组 inbound + 一个 outbound + 一条 routing 规则）
# 三件套靠 tag 互相挂钩：
#     client-{vps_port}      ← 新 inbound 的 tag
#     proxy_outbound["tag"]  ← 新 outbound 的 tag（caller 自己起，建议带 country_code）
#     routing.rules 一条 inboundTag=[client-{port}] → outboundTag=<proxy tag>
#
# 这两个函数都 deepcopy 入参，不 mutate；返回新 config dict。
# ============================================================

def add_proxy_binding(
    current: dict,
    vps_port: int,
    proxy_outbound: dict,
    inbound_user: str,
    inbound_pwd: str,
) -> dict:
    """往现有 xray config 里追加一组 rgIP binding（不破坏别的 client inbound）。

    参数：
        current        : 现有 config dict（read_config 拿到的；空 dict / None 也接受）
        vps_port       : 18441..18450 之一；== DEFAULT_PORT 抛 PortConflictError
        proxy_outbound : caller 用 build_proxy_outbound() 造好的 outbound dict，
                         应已设好 tag（建议 proxy-{country}-{port}）和 _meta
        inbound_user / inbound_pwd : 客户端连本机的账密（generate_random_auth 出的）

    冲突检查（任一命中抛错）：
        - vps_port == DEFAULT_PORT (18440) → PortConflictError
        - 已有 inbound 用 vps_port      → PortAlreadyBoundError
        - 已有 outbound tag 撞 proxy_outbound["tag"] → OutboundTagConflictError

    空 config (`{}` 或缺关键字段) → 先用 build_vps_direct_config 起 baseline 再追加，
    保证服务起步时 default-direct 在场。
    """
    if vps_port == DEFAULT_PORT:
        raise PortConflictError(
            f"{PORT_CONFLICTS_WITH_DEFAULT_MESSAGE} 传入 vps_port={vps_port}"
        )

    # 空 / 缺关键字段 → 起 baseline；否则深拷贝避免 mutate 入参
    if not current or "inbounds" not in current:
        new = build_vps_direct_config()
    else:
        new = copy.deepcopy(current)

    # 防御性补齐可能缺失的子结构（实战中遇到过 routing 字段缺失的脏 config）
    new.setdefault("inbounds", [])
    new.setdefault("outbounds", [])
    new.setdefault("routing", {})
    new["routing"].setdefault("rules", [])

    # 冲突：vps_port 已被某条 inbound 占用
    for inb in new["inbounds"]:
        if inb.get("port") == vps_port:
            raise PortAlreadyBoundError(
                f"{PORT_ALREADY_BOUND_MESSAGE} 冲突端口={vps_port} "
                f"已被 tag={inb.get('tag', '?')} 占用"
            )

    # 冲突：outbound tag 撞
    new_outbound_tag = proxy_outbound.get("tag", "")
    if new_outbound_tag:
        for ob in new["outbounds"]:
            if ob.get("tag") == new_outbound_tag:
                raise OutboundTagConflictError(
                    f"{OUTBOUND_TAG_CONFLICT_MESSAGE} 冲突 tag={new_outbound_tag!r}"
                )

    # 构造新 client inbound（结构和 build_proxy_relay_config 里一致）
    client_tag = f"client-{vps_port}"
    client_inbound = {
        "tag": client_tag,
        "port": vps_port,
        "listen": "0.0.0.0",
        "protocol": "socks",
        "settings": {
            "auth": "password",
            "udp": True,
            "accounts": [{"user": inbound_user, "pass": inbound_pwd}],
        },
    }

    # 三件套 append（用 deepcopy 把 caller 的 proxy_outbound 也隔离，
    # 否则后续 caller 修改它会反映到返回值里）
    new["inbounds"].append(client_inbound)
    new["outbounds"].append(copy.deepcopy(proxy_outbound))
    new["routing"]["rules"].append({
        "type": "field",
        "inboundTag": [client_tag],
        "outboundTag": new_outbound_tag,
    })

    return new


def remove_proxy_binding(current: dict, vps_port: int) -> dict:
    """从现有 xray config 里删除指定 vps_port 对应的 binding 三件套。

    用途：
        - rgIP 业务回滚（内 ping 不通 / egress 不匹配 时撤销刚追加的三件套）
        - 未来 IP 过期巡检模块按端口下线 binding

    幂等：vps_port 对应的 inbound 不在 config 里 → 直接返回 deepcopy(current)，不抛错。
    不会动 default-direct inbound / direct outbound / 别的 client-* binding。

    防御：删 outbound 前确认该 outboundTag 不再被剩余 routing 规则引用
    （正常 1 inbound 1 outbound 时直接删；坏数据时保留 outbound 避免误伤）。
    """
    new = copy.deepcopy(current) if current else {}
    if not new or "inbounds" not in new:
        return new

    client_tag = f"client-{vps_port}"

    # ① 删 inbound
    inbounds = new.get("inbounds", []) or []
    new_inbounds = [inb for inb in inbounds if inb.get("tag") != client_tag]
    inbound_removed = len(new_inbounds) < len(inbounds)
    new["inbounds"] = new_inbounds

    if not inbound_removed:
        # 幂等 noop：caller 重复 remove 不抛错
        return new

    # ② 删 routing 规则 + 记下被关联的 outbound tag
    routing = new.setdefault("routing", {})
    rules = routing.get("rules", []) or []
    outbound_tags_to_drop: set[str] = set()
    new_rules: list[dict] = []
    for rule in rules:
        in_tags = rule.get("inboundTag", []) or []
        if client_tag in in_tags:
            outbound_tags_to_drop.add(rule.get("outboundTag", ""))
        else:
            new_rules.append(rule)
    routing["rules"] = new_rules

    # ③ 删 outbound：只删不再被任何剩余规则引用的（防御坏数据）
    still_used: set[str] = {rule.get("outboundTag", "") for rule in new_rules}
    truly_drop = outbound_tags_to_drop - still_used - {""}  # 排除空 tag
    outbounds = new.get("outbounds", []) or []
    new["outbounds"] = [ob for ob in outbounds if ob.get("tag") not in truly_drop]

    return new


# ============================================================
# 兼容常量：默认 config 的 JSON 字符串形式
# （从 build_vps_direct_config 实时算，确保跟 dict 同源；外部代码若已依赖此名仍可用）
# ============================================================

DEFAULT_CONFIG_JSON = json.dumps(build_vps_direct_config(), indent=2)


# ============================================================
# SSH 操作：查 config 文件大小 / 是否空
# ============================================================

def get_config_size(client: paramiko.SSHClient) -> int:
    """获取 config.json 文件大小（字节）。不存在或读取失败返回 0。"""
    result = execute_command(
        client,
        f"stat -c %s {DEFAULT_CONFIG_PATH} 2>/dev/null || echo 0",
    )
    try:
        return int(result["stdout"].strip())
    except (ValueError, KeyError):
        return 0


def is_config_blank(client: paramiko.SSHClient) -> bool:
    """检查 config.json 是否空（缺失或 0 字节）。"""
    return get_config_size(client) == 0


def read_config(client: paramiko.SSHClient) -> dict:
    """从服务器读取 xray config.json，解析成 dict 返回。

    返回规则：
        - 文件不存在 / 空文件 → 返回 {}（语义"没有 config"）
        - 文件存在且能 parse → 返回 parse 后的 dict
        - 文件存在但 JSON 损坏 → 抛 ConfigReadError

    业务调用方一般先 is_config_blank() 短路；想拿现有 inbound/outbound
    再调 read_config。
    """
    # 直接 cat；用 2>/dev/null 把"找不到文件"屏蔽（按空内容处理）
    result = execute_command(client, f"cat {DEFAULT_CONFIG_PATH} 2>/dev/null")
    raw = result["stdout"]
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigReadError(
            f"{CONFIG_READ_FAILED_MESSAGE}: 文件存在但 JSON 解析失败 ({exc})"
        ) from exc


# ============================================================
# SSH 操作：写 config 文件
# ============================================================

def write_default_config(client: paramiko.SSHClient) -> None:
    """把 VPS 直出场景的默认 config 写到服务器。

    用途：xray 装完但 config 空（x-ui 类面板场景）→ 服务起不来。
    写默认 config 让服务能正常 active，业务后续往里加节点。

    内部调用 upload_config(build_vps_direct_config())，失败抛 ConfigWriteError。
    """
    upload_config(client, build_vps_direct_config())


def upload_config(client: paramiko.SSHClient, config_dict: dict) -> None:
    """把任意 config dict 序列化后写到 DEFAULT_CONFIG_PATH。

    用 heredoc 写入避免 JSON 中的 " 被 shell 解析。
    写入失败抛 ConfigWriteError，文案带 stderr 便于排查。
    """
    payload = json.dumps(config_dict, indent=2)
    cmd = (
        f"cat > {DEFAULT_CONFIG_PATH} << 'XRAY_CONFIG_EOF'\n"
        f"{payload}\n"
        f"XRAY_CONFIG_EOF"
    )
    result = execute_command(client, cmd)
    if result["exit_code"] != 0:
        raise ConfigWriteError(
            f"{CONFIG_WRITE_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


# ============================================================
# SSH 操作：校验 config 语法
# ============================================================

def validate_config(client: paramiko.SSHClient) -> None:
    """跑 `xray test -confdir /usr/local/etc/xray` 校验 config 语法。

    校验失败抛 ConfigValidationError，文案带 xray 的 stderr 便于定位。
    业务一般在 upload_config 后、reload 前调一次，避免推坏 config 上线。
    """
    # 用 confdir 而不是 -c <file>，让 xray 自己 glob 配置目录（更贴近 systemd 启动行为）
    result = execute_command(client, "xray test -confdir /usr/local/etc/xray")
    if result["exit_code"] != 0:
        # xray 把校验错误打在 stdout 或 stderr，取两者拼起来
        detail = (result["stderr"] or result["stdout"])[:300]
        raise ConfigValidationError(
            f"{CONFIG_VALIDATION_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"detail={detail}"
        )
