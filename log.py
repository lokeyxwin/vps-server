"""项目日志：标准库 logging，只输出到 stdout，不落盘。

分层格式（同一行格式，TAG 区分层）：
    原子层  →  HH:MM:SS [INFO] vps: 消息
    业务层  →  HH:MM:SS [BIZ]  services.xxx: 消息

业务层 = logger 名以 "services." 开头的 logger。
"""

import logging
import sys


LOG_DATEFMT = "%H:%M:%S"
LOG_LEVEL = logging.INFO
BUSINESS_LOGGER_PREFIX = "services."
DIVIDER = "─" * 78

_configured = False


class LayeredFormatter(logging.Formatter):
    """业务层日志在前面加分隔线，每个业务事件 = 控制台一个章节。

    格式：
        ────────────────────────────────────  ← 业务事件之前自动画分隔线
        15:26:54 [BIZ]  services.xxx: register_vps 开始 ip=...
        15:26:54 [INFO] vps: ...                                 ← 原子层无分隔线
        15:26:54 [INFO] paramiko.transport: ...
        ────────────────────────────────────
        15:27:00 [BIZ]  services.xxx: register_vps 成功 ip=...
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, LOG_DATEFMT)
        msg = record.getMessage()
        if record.name.startswith(BUSINESS_LOGGER_PREFIX):
            # 业务层：分隔线 + 三角标记，不用方括号（让方括号成为原子层独有的视觉语言）
            return f"{DIVIDER}\n{ts} ▶ {record.name}: {msg}"
        # 原子层：标准 [LEVEL] 方括号
        return f"{ts} [{record.levelname}] {record.name}: {msg}"


def _configure_once() -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(LOG_LEVEL)
    handler.setFormatter(LayeredFormatter())
    root.addHandler(handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取一个命名 logger。首次调用时自动配置根 logger。"""
    _configure_once()
    return logging.getLogger(name)
