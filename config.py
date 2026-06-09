"""项目全局配置。

切换数据库：修改 DB_TYPE 为 "sqlite" 或 "mysql" 即可，
db/engine.py 会自动根据这个开关选择对应的连接 URL。
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# 自动加载根目录的 .env 文件到环境变量
load_dotenv()


# ============================================================
# 项目根路径
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent


# ============================================================
# 加密配置
# ============================================================

# 密码加密密钥（必须在 .env 中配置）
# 缺失时不在 import 时报错（避免影响测试导入），由 security 模块在首次使用时检查
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")


# ============================================================
# 数据库配置
# ============================================================

# 切换开关：开发/测试改 "sqlite"，生产改 "mysql"
DB_TYPE = "sqlite"

# SQLite（开发/测试）
# 跑 pytest 时设环境变量 VPS_SERVER_TESTING=1 切独立测试 DB，不污染 dev 数据
_TESTING = os.environ.get("VPS_SERVER_TESTING", "").lower() in {"1", "true", "yes"}
_DB_FILENAME = "vps_server_test.db" if _TESTING else "vps_server.db"
SQLITE_PATH = PROJECT_ROOT / "db" / _DB_FILENAME
SQLITE_URL = f"sqlite:///{SQLITE_PATH}"

# MySQL（生产）—— 仅密码留 env，避免明文进 git
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = "vps_server"
MYSQL_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)

# 引擎参数
DB_ECHO = False
DB_POOL_SIZE = 5
DB_POOL_RECYCLE = 3600  # MySQL 默认 8 小时断连，提前 1 小时回收


# ============================================================
# SSH 连接相关常量
# ============================================================

SSH_CONNECT_TIMEOUT = 10
SSH_EXECUTE_TIMEOUT = 30
SSH_DEFAULT_PORT = 22

# 老服务器（CentOS 7 + OpenSSH 7.4 + fail2ban）对频繁开 channel 不友好，
# execute_command 失败时退避重试。3 次尝试，间隔 0.25 / 1 / 2 秒。
SSH_EXECUTE_RETRY_ATTEMPTS = 3
SSH_EXECUTE_RETRY_BACKOFF = (0.25, 1.0, 2.0)

# connect_server 同款：老服务器对频繁建连也会拒。3 次尝试，间隔 2 / 5 秒（比 channel
# 间隔大，因为这是 TCP/SSH 握手层；服务器端 fail2ban 一般 sleep 几秒就放过）
SSH_CONNECT_RETRY_ATTEMPTS = 3
SSH_CONNECT_RETRY_BACKOFF = (2.0, 5.0)


# ============================================================
# xray
# ============================================================

# VPS 自身默认 xray 入站端口（socks5 → freedom 直出，被 ADR-0004 让步算法保护）
XRAY_DEFAULT_PORT = 18440


# ============================================================
# 连通性测试 / GeoIP（API URL 已 inline 到调用处，这里只留可调超时 + token）
# ============================================================

# socks5 over 跨境慢链路单 RTT 就要 3-5 秒，20 秒覆盖典型慢链路 + 留点裕量
CONNECTIVITY_TEST_TIMEOUT = 20

# ipinfo.io 免费 50k 次/月（注册 token 后），无 token 也能查但限额很小
IPINFO_TOKEN = os.environ.get("IPINFO_TOKEN", "")  # 没设时填:<请放入你的凭据>
IPINFO_TIMEOUT = 8
