"""xray 领域包。

内部分两层：
- xray.service ← 服务运行时操作（install / start / stop / enable / disable / is_* / version / test_internal_socks）
- xray.config  ← 配置层（纯函数 build_* + SSH 操作 upload/validate/write_default_config）

外部业务通过 `from xray import ...` 直接拿，不用关心是哪一层。
"""

from xray.service import (
    # 命令模板
    INSTALL_COMMAND,
    UNINSTALL_COMMAND,
    INSTALL_TIMEOUT,
    # 错误类（服务运行时）
    XrayError,
    InstallFailedError,
    UninstallFailedError,
    VerifyFailedError,
    ServiceNotActiveError,
    EnableFailedError,
    StopFailedError,
    DisableFailedError,
    # 错误文案（服务运行时）
    XRAY_INSTALL_FAILED_MESSAGE,
    XRAY_UNINSTALL_FAILED_MESSAGE,
    XRAY_VERIFY_FAILED_MESSAGE,
    XRAY_SERVICE_START_FAILED_MESSAGE,
    XRAY_SERVICE_NOT_ACTIVE_MESSAGE,
    XRAY_ENABLE_FAILED_MESSAGE,
    XRAY_SERVICE_STOP_FAILED_MESSAGE,
    XRAY_DISABLE_FAILED_MESSAGE,
    # 原子函数：通用软件管理契约
    install,
    uninstall,
    start,
    stop,
    enable,
    disable,
    is_installed,
    is_running,
    is_enabled,
    version,
    # 服务自检
    test_internal_socks,
)
from xray.config import (
    # 协议常量
    PROTOCOL_SOCKS5,
    PROTOCOL_HTTP,
    SUPPORTED_PROTOCOLS,
    # 路径 / 端口常量
    DEFAULT_CONFIG_PATH,
    DEFAULT_PORT,
    DEFAULT_CONFIG_JSON,
    # 错误类（配置相关）
    UnsupportedProtocolError,
    PortConflictError,
    ConfigWriteError,
    ConfigValidationError,
    # 错误文案（配置相关）
    UNSUPPORTED_PROTOCOL_MESSAGE,
    PORT_CONFLICTS_WITH_DEFAULT_MESSAGE,
    CONFIG_WRITE_FAILED_MESSAGE,
    CONFIG_VALIDATION_FAILED_MESSAGE,
    # 纯函数
    generate_random_auth,
    build_proxy_outbound,
    build_vps_direct_config,
    build_proxy_relay_config,
    # SSH 操作
    get_config_size,
    is_config_blank,
    write_default_config,
    upload_config,
    validate_config,
)
from xray.manager import XrayManager


__all__ = [
    "XrayManager",
    # ----- 服务运行时 -----
    "XrayError",
    "InstallFailedError",
    "UninstallFailedError",
    "VerifyFailedError",
    "ServiceNotActiveError",
    "EnableFailedError",
    "StopFailedError",
    "DisableFailedError",
    "INSTALL_COMMAND",
    "UNINSTALL_COMMAND",
    "INSTALL_TIMEOUT",
    "XRAY_INSTALL_FAILED_MESSAGE",
    "XRAY_UNINSTALL_FAILED_MESSAGE",
    "XRAY_VERIFY_FAILED_MESSAGE",
    "XRAY_SERVICE_START_FAILED_MESSAGE",
    "XRAY_SERVICE_NOT_ACTIVE_MESSAGE",
    "XRAY_ENABLE_FAILED_MESSAGE",
    "XRAY_SERVICE_STOP_FAILED_MESSAGE",
    "XRAY_DISABLE_FAILED_MESSAGE",
    "install",
    "uninstall",
    "start",
    "stop",
    "enable",
    "disable",
    "is_installed",
    "is_running",
    "is_enabled",
    "version",
    "test_internal_socks",
    # ----- 配置层 -----
    "PROTOCOL_SOCKS5",
    "PROTOCOL_HTTP",
    "SUPPORTED_PROTOCOLS",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_PORT",
    "DEFAULT_CONFIG_JSON",
    "UnsupportedProtocolError",
    "PortConflictError",
    "ConfigWriteError",
    "ConfigValidationError",
    "UNSUPPORTED_PROTOCOL_MESSAGE",
    "PORT_CONFLICTS_WITH_DEFAULT_MESSAGE",
    "CONFIG_WRITE_FAILED_MESSAGE",
    "CONFIG_VALIDATION_FAILED_MESSAGE",
    "generate_random_auth",
    "build_proxy_outbound",
    "build_vps_direct_config",
    "build_proxy_relay_config",
    "get_config_size",
    "is_config_blank",
    "write_default_config",
    "upload_config",
    "validate_config",
]
