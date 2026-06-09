"""TC-10-a 测试 VPS 凭据清单结构断言。

只测静态结构, 不连真服务器。
"""

import pytest

import probe_vps
from probe_vps import (
    NO_PROBE_VPS_MESSAGE,
    PROBE_TEST_PORT,
    PROBE_VPS_POOL,
    get_probe_vps_pool,
)
from ssh.session import VPSSession


def test_pool_is_tuple_with_valid_size():
    assert isinstance(PROBE_VPS_POOL, tuple)
    assert 1 <= len(PROBE_VPS_POOL) <= 3


@pytest.mark.parametrize("entry", PROBE_VPS_POOL)
def test_entry_has_required_keys(entry):
    assert set(entry.keys()) >= {"ip", "port", "username", "password"}
    assert isinstance(entry["ip"], str) and entry["ip"]
    assert isinstance(entry["port"], int) and entry["port"] > 0
    assert isinstance(entry["username"], str) and entry["username"]
    assert isinstance(entry["password"], str) and entry["password"]


def test_first_entry_can_construct_vps_session():
    """字段键名跟 VPSSession.__init__ 形参对齐, 展开实例化不抛错。"""
    sess = VPSSession(**PROBE_VPS_POOL[0])
    assert sess.ip == PROBE_VPS_POOL[0]["ip"]
    assert sess.port == PROBE_VPS_POOL[0]["port"]
    assert not sess.is_connected  # 只构造不连接


def test_get_probe_vps_pool_returns_pool_when_non_empty():
    assert get_probe_vps_pool() is PROBE_VPS_POOL


def test_get_probe_vps_pool_raises_with_guidance_when_empty(monkeypatch):
    """空 pool 时抛 RuntimeError, 消息里指明去 probe_vps.py 加凭据。"""
    monkeypatch.setattr(probe_vps, "PROBE_VPS_POOL", ())
    with pytest.raises(RuntimeError) as exc_info:
        probe_vps.get_probe_vps_pool()
    assert str(exc_info.value) == NO_PROBE_VPS_MESSAGE
    assert "probe_vps.py" in str(exc_info.value)


def test_probe_test_port_is_valid_high_port_not_default_inbound():
    """PROBE_TEST_PORT (T-13 加) 是 int, 1024-65535 高位段, != 18440 默认入口。"""
    assert isinstance(PROBE_TEST_PORT, int)
    assert 1024 <= PROBE_TEST_PORT <= 65535
    assert PROBE_TEST_PORT != 18440
