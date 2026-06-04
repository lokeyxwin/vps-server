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

# 切换开关：开发/测试用 "sqlite"，生产改为 "mysql"
DB_TYPE = os.environ.get("DB_TYPE", "sqlite")

# SQLite 配置（开发/测试）
SQLITE_PATH = PROJECT_ROOT / "db" / "vps_server.db"
SQLITE_URL = f"sqlite:///{SQLITE_PATH}"

# MySQL 配置（生产）—— 通过环境变量注入凭证，避免硬编码
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "vps_server")
MYSQL_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)

# 引擎额外参数
DB_ECHO = os.environ.get("DB_ECHO", "false").lower() == "true"  # 是否打印 SQL
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "5"))
DB_POOL_RECYCLE = 3600  # 连接 1 小时后回收（MySQL 默认 8 小时断连，提前回收）


# ============================================================
# SSH 连接相关常量
# ============================================================

SSH_CONNECT_TIMEOUT = 10
SSH_EXECUTE_TIMEOUT = 30
SSH_DEFAULT_PORT = 22


# ============================================================
# xray 相关常量
# ============================================================

# VPS 自身代理端口（默认 xray 入站，freedom 直出）
XRAY_DEFAULT_PORT = 18440

# xray 安装脚本超时（GitHub 拉取，国内可能慢）
XRAY_INSTALL_TIMEOUT = 120


# ============================================================
# Proxy 业务端口范围
# ============================================================

# Proxy 业务部署的代理出口端口范围（每台 VPS 最多 10 个出口）
PROXY_PORT_RANGE_START = 18441
PROXY_PORT_RANGE_END = 18450

# 服务器本地防火墙开放范围：覆盖 xray 默认端口 + Proxy 业务范围
FIREWALL_OPEN_START = 18440
FIREWALL_OPEN_END = 18450


# ============================================================
# 连通性测试
# ============================================================

# 返回出口 IP 的探测 URL（用于内部 / 外部 socks5 ping）
CONNECTIVITY_TEST_URL = "https://api.ipify.org"
CONNECTIVITY_TEST_TIMEOUT = 8
