"""core 基础设施包：通用工具（加密 / SSH / 防火墙 / 连通性 ...）。

任何领域（xray / ip / proxy / VPS 业务）都通过这里拿基础能力。
"""

from core.ssh import (
    # 原子函数
    connect_server,
    close_server,
    execute_command,
    upload_file,
    download_file,
    get_system_info,
    # 错误类
    AuthFailedError,
    ConnectTimeoutError,
    ConnectRefusedError,
    # 错误文案
    CONNECTION_ERROR_MESSAGE,
    AUTH_FAILED_MESSAGE,
    CONNECT_TIMEOUT_MESSAGE,
    CONNECT_REFUSED_MESSAGE,
    EXECUTE_ERROR_MESSAGE,
    FILE_TRANSFER_ERROR_MESSAGE,
)
from core.session import VPSSession, NOT_CONNECTED_MESSAGE
from core.firewall import (
    open_tcp_port_range,
    detect_firewall,
    FirewallOpenError,
    FIREWALL_OPEN_FAILED_MESSAGE,
)
from core.proxy_check import test_socks_proxy, EXTERNAL_UNREACHABLE_MESSAGE


__all__ = [
    "VPSSession",
    "connect_server",
    "close_server",
    "execute_command",
    "upload_file",
    "download_file",
    "get_system_info",
    "AuthFailedError",
    "ConnectTimeoutError",
    "ConnectRefusedError",
    "CONNECTION_ERROR_MESSAGE",
    "AUTH_FAILED_MESSAGE",
    "CONNECT_TIMEOUT_MESSAGE",
    "CONNECT_REFUSED_MESSAGE",
    "EXECUTE_ERROR_MESSAGE",
    "FILE_TRANSFER_ERROR_MESSAGE",
    "NOT_CONNECTED_MESSAGE",
    "open_tcp_port_range",
    "detect_firewall",
    "FirewallOpenError",
    "FIREWALL_OPEN_FAILED_MESSAGE",
    "test_socks_proxy",
    "EXTERNAL_UNREACHABLE_MESSAGE",
]
