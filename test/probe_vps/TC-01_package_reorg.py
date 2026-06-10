"""TC-19-01 probe_vps 包重组 + 兼容性 (ADR-0009 §2 §决策 §6).

业务故事:
  原 probe_vps.py 单文件改组成 probe_vps/ 包 (config.py + bootstrap.py +
  __init__.py). 所有原有 `from probe_vps import ...` 必须仍能用 (re-export 兜底),
  同时新 bootstrap 公开符号必须可 import.

合并自原 TC-01_probe_vps_pool.py: pool / _build_pool / PROBE_TEST_PORT
+ get_probe_vps_pool helper, 但 monkeypatch 路径改成 probe_vps.config
(原代码用 probe_vps 模块, 改包后 PROBE_VPS_POOL 真正住 probe_vps.config).
"""

from __future__ import annotations

import pytest

import probe_vps
from probe_vps import (
    NO_PROBE_VPS_MESSAGE,
    PROBE_TEST_PORT,
    ProbeVPSError,
    ProbeVPSHandle,
    ProbeVPSSetupFailed,
    ProbeVPSUnreachable,
    _MAX_PROBE_SLOTS,
    _build_pool,
    bootstrap,
    ensure_ready,
    get_probe_vps_pool,
)
from probe_vps import config as pvc
from ssh.session import VPSSession


def _clear_all_probe_env(monkeypatch):
    for n in range(1, _MAX_PROBE_SLOTS + 1):
        for key in ("IP", "PORT", "USER", "PWD"):
            monkeypatch.delenv(f"PROBE_VPS_{n}_{key}", raising=False)


# ============ 包重组兼容性 (新加, TC-19-01 核心) ============

def test_reexport_config_symbols_via_root_package():
    """旧 import 路径 `from probe_vps import <config 符号>` 仍可用."""
    assert PROBE_TEST_PORT == 19000
    assert isinstance(NO_PROBE_VPS_MESSAGE, str)
    assert callable(get_probe_vps_pool)


def test_reexport_bootstrap_symbols_via_root_package():
    """新 bootstrap 入口可以 from probe_vps import 拿到."""
    assert callable(ensure_ready)
    assert ProbeVPSHandle is not None
    # 异常类继承层级 (ADR-0009 §决策 §4)
    assert issubclass(ProbeVPSUnreachable, ProbeVPSError)
    assert issubclass(ProbeVPSSetupFailed, ProbeVPSError)
    assert issubclass(ProbeVPSError, Exception)


def test_bootstrap_module_accessible_via_root_package():
    """`from probe_vps import bootstrap` 能拿到 bootstrap 子模块本身."""
    assert hasattr(bootstrap, "ensure_ready")
    assert hasattr(bootstrap, "ProbeVPSHandle")


def test_handle_is_immutable_dataclass():
    """ProbeVPSHandle 是 frozen dataclass, host + inbound_port 不可变."""
    h = ProbeVPSHandle(host="1.2.3.4", inbound_port=19000)
    assert h.host == "1.2.3.4"
    assert h.inbound_port == 19000
    with pytest.raises(Exception):  # frozen → FrozenInstanceError
        h.host = "5.6.7.8"


# ============ _build_pool 行为 (沉自原 TC-01) ============

def test_build_pool_empty_when_no_env(monkeypatch):
    _clear_all_probe_env(monkeypatch)
    assert _build_pool() == ()


def test_build_pool_reads_single_entry(monkeypatch):
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
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv("PROBE_VPS_1_IP", "5.6.7.8")
    monkeypatch.setenv("PROBE_VPS_1_PWD", "x")
    pool = _build_pool()
    assert pool[0]["port"] == 22
    assert pool[0]["username"] == "root"


def test_build_pool_skips_blank_ip_slot(monkeypatch):
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv("PROBE_VPS_1_IP", "1.1.1.1")
    monkeypatch.setenv("PROBE_VPS_1_PWD", "a")
    monkeypatch.setenv("PROBE_VPS_3_IP", "3.3.3.3")
    monkeypatch.setenv("PROBE_VPS_3_PWD", "c")
    pool = _build_pool()
    assert len(pool) == 2
    assert pool[0]["ip"] == "1.1.1.1"
    assert pool[1]["ip"] == "3.3.3.3"


def test_build_pool_respects_max_slots(monkeypatch):
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv(f"PROBE_VPS_{_MAX_PROBE_SLOTS + 1}_IP", "9.9.9.9")
    monkeypatch.setenv(f"PROBE_VPS_{_MAX_PROBE_SLOTS + 1}_PWD", "z")
    assert _build_pool() == ()


def test_build_pool_entry_can_construct_vps_session(monkeypatch):
    _clear_all_probe_env(monkeypatch)
    monkeypatch.setenv("PROBE_VPS_1_IP", "10.0.0.1")
    monkeypatch.setenv("PROBE_VPS_1_PORT", "22")
    monkeypatch.setenv("PROBE_VPS_1_USER", "ubuntu")
    monkeypatch.setenv("PROBE_VPS_1_PWD", "pw")
    sess = VPSSession(**_build_pool()[0])
    assert sess.ip == "10.0.0.1"
    assert sess.port == 22
    assert not sess.is_connected


# ============ get_probe_vps_pool helper (沉自原 TC-01) ============
# 注意: 包重组后 PROBE_VPS_POOL 真正住 probe_vps.config, 必须 patch 它
# (旧版 patch probe_vps 模块, 因为旧版 PROBE_VPS_POOL 是模块全局, 现在不是).

def test_get_probe_vps_pool_returns_module_constant_when_non_empty(monkeypatch):
    fake_pool = (
        {"ip": "1.2.3.4", "port": 22, "username": "u", "password": "p"},
    )
    monkeypatch.setattr(pvc, "PROBE_VPS_POOL", fake_pool)
    assert get_probe_vps_pool() is fake_pool


def test_get_probe_vps_pool_raises_with_guidance_when_empty(monkeypatch):
    monkeypatch.setattr(pvc, "PROBE_VPS_POOL", ())
    with pytest.raises(RuntimeError) as exc_info:
        get_probe_vps_pool()
    msg = str(exc_info.value)
    assert msg == NO_PROBE_VPS_MESSAGE
    assert "zshrc.local" in msg
    assert "PROBE_VPS_1_IP" in msg


# ============ PROBE_TEST_PORT (沉自原 TC-01) ============

def test_probe_test_port_is_valid_high_port_not_default_inbound():
    assert isinstance(PROBE_TEST_PORT, int)
    assert 1024 <= PROBE_TEST_PORT <= 65535
    assert PROBE_TEST_PORT != 18440


# 防御: probe_vps re-export 的 probe_vps.config 是 sub-module (而非 ROM symbol)
def test_probe_vps_config_submodule_is_module():
    """`probe_vps.config` 是子模块对象 (跟 patch 路径一致)."""
    import types
    assert isinstance(pvc, types.ModuleType)
    assert pvc.__name__ == "probe_vps.config"
