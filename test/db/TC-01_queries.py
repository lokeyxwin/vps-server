"""TC-18-06 db/queries.py 3 函数签名 + 空 DB 返回形状.

db/queries.py 装 3 个函数 (从 services 搬), 签名跟原 services 一致.
本 TC 跑空 DB (in-memory) 时返回形状, 不真起业务流程.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import queries
from db.base import Base
from db.models import (
    IPRecord,
    IPTask,
    ProxyRecord,
    VPSRecord,
    VPSTask,
)


@pytest.fixture
def empty_db(monkeypatch):
    """in-memory SQLite + create_all 5 张表, monkeypatch queries.session_scope.

    每个 TC 拿到一个全新的空 DB.
    """
    engine = create_engine("sqlite:///:memory:")
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

    @contextmanager
    def fake_scope():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(queries, "session_scope", fake_scope)
    yield


# ============================================================
# TC-06-a 签名: query_vps_status(vps_id=None, task_id=None) -> dict
# ============================================================

def test_query_vps_status_signature():
    sig = inspect.signature(queries.query_vps_status)
    params = list(sig.parameters.keys())
    assert params == ["vps_id", "task_id"]
    assert sig.parameters["vps_id"].default is None
    assert sig.parameters["task_id"].default is None


# ============================================================
# TC-06-b 签名: query_ip_status(ip_id=None, task_id=None) -> dict
# ============================================================

def test_query_ip_status_signature():
    sig = inspect.signature(queries.query_ip_status)
    params = list(sig.parameters.keys())
    assert params == ["ip_id", "task_id"]
    assert sig.parameters["ip_id"].default is None
    assert sig.parameters["task_id"].default is None


# ============================================================
# TC-06-c 签名: list_available_proxies(country_code="") -> list[dict]
# ============================================================

def test_list_available_proxies_signature():
    sig = inspect.signature(queries.list_available_proxies)
    params = list(sig.parameters.keys())
    assert params == ["country_code"]
    assert sig.parameters["country_code"].default == ""


# ============================================================
# TC-06-d 空 DB 时返回形状: not_found / not_found / []
# ============================================================

def test_query_vps_status_no_args_returns_not_found(empty_db):
    """两个 id 都不传 → not_found (不需要 DB)."""
    result = queries.query_vps_status()
    assert result == {"status": "not_found"}


def test_query_vps_status_unknown_id_returns_not_found(empty_db):
    """vps_id 不存在 → not_found."""
    result = queries.query_vps_status(vps_id=999999)
    assert result == {"status": "not_found"}


def test_query_ip_status_no_args_returns_not_found(empty_db):
    result = queries.query_ip_status()
    assert result == {"status": "not_found"}


def test_query_ip_status_unknown_id_returns_not_found(empty_db):
    result = queries.query_ip_status(ip_id=999999)
    assert result == {"status": "not_found"}


def test_list_available_proxies_empty_db_returns_empty_list(empty_db):
    result = queries.list_available_proxies()
    assert result == []


def test_list_available_proxies_with_country_code(empty_db):
    """带 country_code 也是合法签名."""
    result = queries.list_available_proxies(country_code="SG")
    assert result == []
