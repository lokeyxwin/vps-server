"""TC 共享 fixture: in-memory DB + session_scope patch + mock factories.

每个 TC 文件 import 这里的工具, 避免 boilerplate 重复.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import (
    IPRecord,
    IPTask,
    ProxyRecord,
    TaskStatus,
    VPSRecord,
    VPSStage,
    VPSTask,
)


def make_in_memory_engine():
    """建一个 in-memory SQLite engine + sessionmaker, 含 5 张相关表."""
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
            VPSTask.__table__,
            IPRecord.__table__,
            IPTask.__table__,
            ProxyRecord.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def make_fake_session_scope(Session):
    """返回一个 contextmanager, 行为同 db.session.session_scope 但绑给定 Session."""

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
    """返回伪 VPSSession 类, 模拟 with 上下文 + client 属性."""
    fake_client = client or MagicMock(name="fake_paramiko_client")

    class _FakeVPSSession:
        def __init__(self, ip=None, username=None, password=None, port=22, **kw):
            self.ip = ip
            self.username = username
            self.password = password
            self.port = port
            self.client = fake_client
            self._connected = False
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


def insert_vps(
    s,
    *,
    ip: str = "10.0.0.1",
    stage: str = VPSStage.CONNECTABLE,
    xray_version: str = "Xray 1.8.0",
    is_active: int = 1,
    used_port_count: int = 0,
    port: int = 22,
    username: str = "root",
    password: str = "vps-pwd",
    expire_date: date | None = None,
) -> VPSRecord:
    """插一行 VPSRecord, 返回(已 flush 拿到 id)."""
    vps = VPSRecord.from_form(
        ip=ip, username=username, password=password, port=port,
        expire_date=expire_date,
    )
    vps.stage = stage
    vps.xray_version = xray_version
    vps.is_active = is_active
    vps.used_port_count = used_port_count
    s.add(vps)
    s.flush()
    return vps


def insert_ip(
    s,
    *,
    egress_ip: str = "2.2.2.2",
    country_code: str = "SG",
    entry_host: str = "up.example.com",
    entry_port: int = 8080,
    username: str = "upuser",
    password: str = "uppwd",
    protocol: str = "socks5",
) -> IPRecord:
    """插一行 IPRecord(ProxyDeployWorker 期望的入口形态)."""
    ip = IPRecord.from_form(
        entry_host=entry_host,
        entry_port=entry_port,
        username=username,
        password=password,
        protocol=protocol,
        egress_ip=egress_ip,
        geo={"country_code": country_code, "country_name": "", "city": "", "region_name": ""},
    )
    s.add(ip)
    s.flush()
    return ip


def insert_ip_task(
    s,
    ip_id: int,
    *,
    status: str = TaskStatus.PENDING,
    vps_id: int | None = None,
) -> IPTask:
    """插一行 IPTask(默认 pending + vps_id=NULL, IPProbeWorker 派出来的形态)."""
    task = IPTask(ip_id=ip_id, status=status, vps_id=vps_id)
    s.add(task)
    s.flush()
    return task
