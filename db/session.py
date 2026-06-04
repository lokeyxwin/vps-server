"""SQLAlchemy 会话工厂。

业务代码用法（推荐）：
    from db import session_scope

    with session_scope() as session:
        session.add(record)
        # 退出 with 自动 commit；异常自动 rollback；总会 close。
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session, sessionmaker

from db.engine import engine


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务级会话上下文：成功 commit，失败 rollback，总会关闭。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
