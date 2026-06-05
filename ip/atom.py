"""IP 领域原子层：纯函数 + 异常类。

这一层不接 SSH、不接 DB —— 只做"代理凭据 ↔ xray 配置片段"的翻译。
更高层的工具（reload xray / 上传 config / 测连通）走 xray.atom 和 core.* 。
"""

from __future__ import annotations

import config


# ============================================================
# 协议枚举（rgIP 入参约束）
# ============================================================

PROTOCOL_SOCKS5 = "socks5"
PROTOCOL_HTTP = "http"

# 业务层校验入参时用这个集合；扩展协议在此注册即可
SUPPORTED_PROTOCOLS = (PROTOCOL_SOCKS5, PROTOCOL_HTTP)

# xray config.json 里的 protocol 字段名跟我们用户层略不一样：
# 我们对外讲 "socks5"，xray 里写 "socks"。这里集中映射，避免业务层各处硬编码。
_XRAY_PROTOCOL_NAME = {
    PROTOCOL_SOCKS5: "socks",
    PROTOCOL_HTTP: "http",
}


# ============================================================
# 错误类 + 文案
# ============================================================

UNSUPPORTED_PROTOCOL_MESSAGE = (
    "不支持的代理协议。当前仅支持 socks5 / http；"
    "如需扩展，请在 ip.atom.SUPPORTED_PROTOCOLS 注册新值，"
    "并在 _XRAY_PROTOCOL_NAME 补充映射。"
)


PORT_CONFLICTS_WITH_DEFAULT_MESSAGE = (
    "客户端入站端口与 xray 默认直出端口冲突。"
    "rgIP 业务的端口应从 PROXY_PORT_RANGE_START..END 范围（默认 18441-18450）挑选；"
    "默认端口 18440 给 VPS 自用直出（default-direct），不能被客户端 inbound 占用。"
)


class UnsupportedProtocolError(ValueError):
    """传入了 SUPPORTED_PROTOCOLS 之外的协议字符串。"""


class PortConflictError(ValueError):
    """客户端入站端口跟 xray 默认直出端口（DEFAULT_PORT）撞了。"""


# ============================================================
# config 结构常量：跟 xray.atom.DEFAULT_CONFIG_JSON 的内容保持一致
# ----- 这里冗余维护一份 Python dict 形式，避免 ip.atom 反向依赖 xray.atom -----
# 等业务跑通后，可考虑把这两个常量抽到 config.py 或 db.constants
# ============================================================

# 默认直出 inbound：VPS 自用 socks5（免认证），监听 DEFAULT_PORT
_DEFAULT_DIRECT_INBOUND = {
    "tag": "default-direct",
    "port": config.XRAY_DEFAULT_PORT,
    "listen": "0.0.0.0",
    "protocol": "socks",
    "settings": {"auth": "noauth", "udp": True},
}

# 默认直出 outbound：freedom 协议（直连出去，不走任何代理）
_DEFAULT_DIRECT_OUTBOUND = {
    "tag": "direct",
    "protocol": "freedom",
}


# ============================================================
# 原子函数（纯函数）
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


def build_deploy_config(
    vps_port: int,
    proxy_outbound: dict,
    inbound_user: str,
    inbound_pwd: str,
) -> dict:
    """拼出一份完整的 xray config dict，包含「VPS 自用直出 + 客户端走代理」两套通路。

    输出结构（dict 形式，业务层 json.dumps 后写入 /usr/local/etc/xray/config.json）：

        inbounds:
            ① default-direct       ← DEFAULT_PORT 上的免认证 socks5（VPS 自用直出）
            ② client-{vps_port}    ← vps_port 上的账密 socks5（客户端连这个）
        outbounds:
            ① direct               ← freedom 直出
            ② <proxy_outbound>     ← 走上游代理（caller 传入）
        routing.rules:
            ① default-direct → direct
            ② client-{vps_port} → <proxy_outbound 的 tag>

    参数：
        vps_port       : 客户端要连的端口（必须 != config.XRAY_DEFAULT_PORT）
        proxy_outbound : build_proxy_outbound 产出的 outbound dict
        inbound_user   : 客户端连本机的账号（业务层生成 / 用户指定）
        inbound_pwd    : 客户端连本机的密码（同上）

    失败：
        vps_port == DEFAULT_PORT → 抛 PortConflictError（会破坏 default-direct inbound）

    备注：
        本函数不校验 inbound_user / inbound_pwd 是否为空字符串——
        业务层应保证客户端 inbound 一定有认证，但这里不强行约束（也许测试场景需要 noauth）。
    """
    if vps_port == config.XRAY_DEFAULT_PORT:
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
