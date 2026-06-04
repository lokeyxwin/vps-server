"""IP 领域原子层：纯函数 + 异常类。

这一层不接 SSH、不接 DB —— 只做"代理凭据 ↔ xray 配置片段"的翻译。
更高层的工具（reload xray / 上传 config / 测连通）走 xray.atom 和 core.* 。
"""

from __future__ import annotations


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


class UnsupportedProtocolError(ValueError):
    """传入了 SUPPORTED_PROTOCOLS 之外的协议字符串。"""


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
