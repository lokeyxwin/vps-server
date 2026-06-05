"""IP 领域包：代理 IP 注册与管理。

子模块：
    atom.py     ← 原子函数（如代理凭据→xray outbound 翻译）+ 错误类
    manager.py  ← IPManager 类（占位，未来封装代理 IP 的生命周期管理）

业务函数将在 services/ip_*.py 中编排上述工具。
"""

from ip.atom import (
    PROTOCOL_SOCKS5,
    PROTOCOL_HTTP,
    SUPPORTED_PROTOCOLS,
    UnsupportedProtocolError,
    UNSUPPORTED_PROTOCOL_MESSAGE,
    PortConflictError,
    PORT_CONFLICTS_WITH_DEFAULT_MESSAGE,
    build_proxy_outbound,
    build_deploy_config,
)


__all__ = [
    "PROTOCOL_SOCKS5",
    "PROTOCOL_HTTP",
    "SUPPORTED_PROTOCOLS",
    "UnsupportedProtocolError",
    "UNSUPPORTED_PROTOCOL_MESSAGE",
    "PortConflictError",
    "PORT_CONFLICTS_WITH_DEFAULT_MESSAGE",
    "build_proxy_outbound",
    "build_deploy_config",
]
