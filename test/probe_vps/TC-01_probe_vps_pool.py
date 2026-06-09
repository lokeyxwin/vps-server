"""TC-10-a 测试 VPS 凭据清单结构断言。

凭据从 ~/.zshrc.local 的 PROBE_VPS_N_* env 读, 由 _build_pool() 拼成 tuple。
TC 用 monkeypatch.setenv / delenv 注入测试值, 不依赖系统真实凭据,
也不连真服务器。
"""

import pytest

import probe_vps
from probe_vps import (
    NO_PROBE_VPS_MESSAGE,
    PROBE_TEST_PORT,
    _MAX_PROBE_SLOTS,
    _build_pool,
    get_probe_vps_pool,
)
from ssh.session import VPSSession


def _clear_all_probe_env(monkeypatch):
    """把所有 PROBE_VPS_N_* env 清空, 确保 _build_pool 输出可控。"""
    for n in range(1, _MAX_PROBE_SLOTS + 1):
        for key in ("IP", "PORT", "USER", "PWD"):
            monkeypatch.delenv(f"PROBE_VPS_{n}_{key}", raising=False)


# ============ _build_pool 行为 ============

def test_build_pool_empty_when_no_env(monkeypatch):
    """所有 PROBE_VPS_N_* env 都没设 → 空 tuple。"""
    _clear_all_probe_env(monkeypatch)
    assert _build_pool() == ()


def test_build_pool_reads_single_entry(monkeypatch):
    """设了 PROBE_VPS_1_* 4 条 → pool 长度 1, 字段对齐。"""
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv("PROBE_VPS_1_IP", "1.2.3.4")
    monkeypatch.setenv("PROBE_VPS_1_PORT", "2222")
    monkeypatch.setenv("PROBE_VPS_1_USER", "alice")
    monkeypatch.setenv("PROBE_VPS_1_PWD", "secret!@")

    pool = _build_pool()
    assert pool == (
        {"ip": "1.2.3.4", "port": 2222, "username": "alice", "password": "secret!@"},
    )


def test_build_pool_defaults_port_22_and_user_root(monkeypatch):
    """只设 IP + PWD, PORT / USER 走默认值 22 / root。"""
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv("PROBE_VPS_1_IP", "5.6.7.8")
    monkeypatch.setenv("PROBE_VPS_1_PWD", "x")

    pool = _build_pool()
    assert pool[0]["port"] == 22
    assert pool[0]["username"] == "root"


def test_build_pool_skips_blank_ip_slot(monkeypatch):
    """中间编号 IP 空跳过, 后续编号仍生效(不要求连续)。"""
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv("PROBE_VPS_1_IP", "1.1.1.1")
    monkeypatch.setenv("PROBE_VPS_1_PWD", "a")
    # PROBE_VPS_2_IP 留空
    monkeypatch.setenv("PROBE_VPS_3_IP", "3.3.3.3")
    monkeypatch.setenv("PROBE_VPS_3_PWD", "c")

    pool = _build_pool()
    assert len(pool) == 2
    assert pool[0]["ip"] == "1.1.1.1"
    assert pool[1]["ip"] == "3.3.3.3"


def test_build_pool_respects_max_slots(monkeypatch):
    """超过 _MAX_PROBE_SLOTS 的编号 env 不被读取。"""
    _clear_all_probe_env(monkeypatch)
    # 设一个超出上限的编号
    monkeypatch.setenv(f"PROBE_VPS_{_MAX_PROBE_SLOTS + 1}_IP", "9.9.9.9")
    monkeypatch.setenv(f"PROBE_VPS_{_MAX_PROBE_SLOTS + 1}_PWD", "z")

    pool = _build_pool()
    assert pool == ()


def test_build_pool_entry_can_construct_vps_session(monkeypatch):
    """字段键名跟 VPSSession.__init__ 形参对齐, 展开实例化不抛错。"""
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv("PROBE_VPS_1_IP", "10.0.0.1")
    monkeypatch.setenv("PROBE_VPS_1_PORT", "22")
    monkeypatch.setenv("PROBE_VPS_1_USER", "ubuntu")
    monkeypatch.setenv("PROBE_VPS_1_PWD", "pw")

    pool = _build_pool()
    sess = VPSSession(**pool[0])
    assert sess.ip == "10.0.0.1"
    assert sess.port == 22
    assert not sess.is_connected  # 只构造不连接


# ============ get_probe_vps_pool helper ============

def test_get_probe_vps_pool_returns_module_constant_when_non_empty(monkeypatch):
    """非空 pool 时直接透传模块级 PROBE_VPS_POOL 常量。"""
    fake_pool = (
        {"ip": "1.2.3.4", "port": 22, "username": "u", "password": "p"},
    )
    monkeypatch.setattr(probe_vps, "PROBE_VPS_POOL", fake_pool)
    assert get_probe_vps_pool() is fake_pool


def test_get_probe_vps_pool_raises_with_guidance_when_empty(monkeypatch):
    """空 pool 时抛 RuntimeError, 消息里指明去 ~/.zshrc.local 加 env。"""
    monkeypatch.setattr(probe_vps, "PROBE_VPS_POOL", ())
    with pytest.raises(RuntimeError) as exc_info:
        probe_vps.get_probe_vps_pool()
    assert str(exc_info.value) == NO_PROBE_VPS_MESSAGE
    msg = str(exc_info.value)
    assert "zshrc.local" in msg
    assert "PROBE_VPS_1_IP" in msg


# ============ PROBE_TEST_PORT ============

def test_probe_test_port_is_valid_high_port_not_default_inbound():
    """PROBE_TEST_PORT (T-13 加) 是 int, 1024-65535 高位段, != 18440 默认入口。"""
    assert isinstance(PROBE_TEST_PORT, int)
    assert 1024 <= PROBE_TEST_PORT <= 65535
    assert PROBE_TEST_PORT != 18440
