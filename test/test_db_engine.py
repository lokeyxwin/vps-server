import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

import config
from db import Base, get_engine, session_scope
from db.engine import UNSUPPORTED_DB_MESSAGE


class TestEngineFactory(unittest.TestCase):
    def test_sqlite_engine_creates(self):
        engine = get_engine("sqlite")
        self.assertIsInstance(engine, Engine)
        self.assertEqual(engine.dialect.name, "sqlite")

    def test_mysql_url_is_constructed(self):
        """MySQL 驱动 pymysql 默认不装，只验证 URL 拼装正确，不真的建引擎。"""
        self.assertTrue(config.MYSQL_URL.startswith("mysql+pymysql://"))
        self.assertIn(config.MYSQL_DATABASE, config.MYSQL_URL)

    @unittest.skipUnless(
        __import__("importlib").util.find_spec("pymysql"),
        "未安装 pymysql 驱动，跳过 MySQL 引擎创建测试（生产环境需 pip install pymysql）",
    )
    def test_mysql_engine_creates_when_driver_available(self):
        engine = get_engine("mysql")
        self.assertEqual(engine.dialect.name, "mysql")

    def test_unsupported_db_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_engine("postgres")
        self.assertEqual(str(ctx.exception), UNSUPPORTED_DB_MESSAGE)

    def test_default_engine_matches_config(self):
        from db import engine
        self.assertEqual(engine.dialect.name, config.DB_TYPE)


class _SmokeRecord(Base):
    """仅用于测试 ORM/会话工作的临时表，不进业务。"""
    __tablename__ = "_smoke_test"
    id: Mapped[int] = mapped_column(primary_key=True)
    note: Mapped[str] = mapped_column()


class TestSqliteConnectivity(unittest.TestCase):
    """真正打通 SQLite：建表 → 插入 → 查询 → 删表。"""

    @classmethod
    def setUpClass(cls):
        cls.engine = get_engine("sqlite")
        Base.metadata.create_all(cls.engine, tables=[_SmokeRecord.__table__])

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(cls.engine, tables=[_SmokeRecord.__table__])

    def test_sqlite_file_exists(self):
        self.assertTrue(Path(config.SQLITE_PATH).exists())

    def test_session_scope_commits(self):
        with session_scope() as s:
            s.add(_SmokeRecord(note="hello"))

        with session_scope() as s:
            rows = s.query(_SmokeRecord).all()
            self.assertTrue(any(r.note == "hello" for r in rows))

    def test_session_scope_rollbacks_on_exception(self):
        with session_scope() as s:
            count_before = s.query(_SmokeRecord).count()

        with self.assertRaises(RuntimeError):
            with session_scope() as s:
                s.add(_SmokeRecord(note="should_rollback"))
                raise RuntimeError("业务异常")

        with session_scope() as s:
            count_after = s.query(_SmokeRecord).count()
            self.assertEqual(count_before, count_after)

    def test_raw_sql_works(self):
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
