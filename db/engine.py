"""根据 config.DB_TYPE 创建对应的 SQLAlchemy 引擎。

切换 DB 类型只需修改 config.py 的 DB_TYPE，业务代码无需变动。
"""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

import config


UNSUPPORTED_DB_MESSAGE = "不支持的数据库类型，请在 config.py 中将 DB_TYPE 设为 sqlite 或 mysql"


def _set_sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    """每个新连接生效: WAL 让读写并行, busy_timeout 让写锁冲突时等待而非立刻报错。

    两个进程 (mcp_server.py / main.py worker-loop) 并发写同一个 SQLite 文件,
    缺这两个 PRAGMA 时锁冲突会直接抛 'database is locked'。
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _build_engine(db_type: str) -> Engine:
    if db_type == "sqlite":
        # 确保 SQLite 文件所在目录存在
        config.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        sqlite_engine = create_engine(
            config.SQLITE_URL,
            echo=config.DB_ECHO,
            # SQLite 默认禁止多线程共享连接，开发环境放宽
            connect_args={"check_same_thread": False},
        )
        event.listen(sqlite_engine, "connect", _set_sqlite_pragmas)
        return sqlite_engine

    if db_type == "mysql":
        return create_engine(
            config.MYSQL_URL,
            echo=config.DB_ECHO,
            pool_size=config.DB_POOL_SIZE,
            pool_recycle=config.DB_POOL_RECYCLE,
            pool_pre_ping=True,  # 避免使用已断开的连接
        )

    raise ValueError(UNSUPPORTED_DB_MESSAGE)


def get_engine(db_type: str | None = None) -> Engine:
    """按需创建引擎。不传 db_type 则用 config.DB_TYPE。

    主要给测试或需要临时切换的场景使用。
    业务代码请直接 import engine。
    """
    return _build_engine(db_type or config.DB_TYPE)


# 默认引擎实例：业务代码 import 这个即可
engine: Engine = _build_engine(config.DB_TYPE)
