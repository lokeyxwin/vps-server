"""TC 共享 fixture: in-memory DB + session_scope patch + mock factories。

每个 TC 文件 import 这里的工具, 避免 boilerplate 重复。
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import IPRecord, IPTask, VPSRecord


def make_in_memory_engine():
    """建一个 in-memory SQLite engine + sessionmaker, 含 ip_record / ip_task / vps_record 三表。"""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(
        engine,
        tables=[
            VPSRecord.__table__,
            IPRecord.__table__,
            IPTask.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def make_fake_session_scope(Session):
    """返回一个 contextmanager, 行为同 db.session.session_scope 但绑给定 Session。"""

    @contextmanager
    def _fake_scope():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return _fake_scope


def make_fake_vps_session_cls(client=None):
    """返回一个伪 VPSSession 类(可调用), 模拟连接成功 + with 上下文 + client 属性。

    用法:
        FakeSess = make_fake_vps_session_cls()
        with patch("workers.ip_probe_worker.VPSSession", FakeSess):
            ...

    client 属性默认 MagicMock(), 上层用 it 给 XrayManager(client) 用。
    """
    fake_client = client or MagicMock(name="fake_paramiko_client")

    class _FakeVPSSession:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.ip = kwargs.get("ip")
            self.port = kwargs.get("port")
            self.username = kwargs.get("username")
            self.password = kwargs.get("password")
            self._connected = False
            self.client = fake_client
            self.close_called = 0

        def connect(self):
            self._connected = True
            return self

        def close(self):
            self.close_called += 1
            self._connected = False

        @property
        def is_connected(self):
            return self._connected

        def __enter__(self):
            return self.connect()

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    return _FakeVPSSession


def make_internal_socks_result(
    ok: bool = True,
    http_code: int | None = 200,
    body: str = "1.2.3.4",
    exit_code: int = 0,
    stderr: str = "",
    error: str | None = None,
) -> dict:
    """构造 xray.service.test_internal_socks 的返回 dict (T-12 6 键)。"""
    return {
        "ok": ok,
        "http_code": http_code,
        "body": body,
        "error": error if error is not None else (None if ok else f"http_code={http_code}"),
        "exit_code": exit_code,
        "stderr": stderr,
    }


def make_geo(country_code: str = "US") -> dict:
    """构造 toolbox.geoip.lookup_egress 的返回 dict (5 字段)。"""
    return {
        "country_code": country_code,
        "country_name": "United States" if country_code == "US" else "",
        "city": "Los Angeles" if country_code == "US" else "",
        "region_name": "California" if country_code == "US" else "",
        "raw": None,
    }
