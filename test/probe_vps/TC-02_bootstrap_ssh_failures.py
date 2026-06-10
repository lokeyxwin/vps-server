"""TC-19-02 bootstrap.ensure_ready SSH 失败 → ProbeVPSUnreachable (ADR-0009 §决策 §3 步②).

SSH 连不上的 3 类 ConnectionError 子类全转 ProbeVPSUnreachable, 不进 setup_failed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from probe_vps import ProbeVPSSetupFailed, ProbeVPSUnreachable, bootstrap
from ssh.ops import (
    AuthFailedError,
    ConnectRefusedError,
    ConnectTimeoutError,
)


_ENTRY = {
    "ip": "10.0.0.1",
    "port": 22,
    "username": "root",
    "password": "x",
}


def _patch_vpssession_raising(exc_cls, msg="boom"):
    """patch bootstrap 模块里 VPSSession, 调 connect() 时抛指定异常."""
    class _FakeSess:
        def __init__(self, **_kw):
            pass

        def connect(self):
            raise exc_cls(msg)

        def close(self):
            pass

    return patch.object(bootstrap, "VPSSession", _FakeSess)


def test_auth_failed_to_unreachable():
    with _patch_vpssession_raising(AuthFailedError):
        with pytest.raises(ProbeVPSUnreachable) as exc:
            bootstrap.ensure_ready(_ENTRY)
        assert "SSH 连不上" in str(exc.value)
        assert "10.0.0.1" in str(exc.value)


def test_timeout_to_unreachable():
    with _patch_vpssession_raising(ConnectTimeoutError):
        with pytest.raises(ProbeVPSUnreachable):
            bootstrap.ensure_ready(_ENTRY)


def test_refused_to_unreachable():
    with _patch_vpssession_raising(ConnectRefusedError):
        with pytest.raises(ProbeVPSUnreachable):
            bootstrap.ensure_ready(_ENTRY)


def test_generic_connection_error_to_unreachable():
    """通用 ConnectionError 也归 unreachable, 不是 setup_failed."""
    with _patch_vpssession_raising(ConnectionError):
        with pytest.raises(ProbeVPSUnreachable):
            bootstrap.ensure_ready(_ENTRY)


def test_ssh_failure_does_not_raise_setup_failed():
    """边界确认: SSH 失败永远不会被误判成 setup_failed."""
    with _patch_vpssession_raising(AuthFailedError):
        with pytest.raises(ProbeVPSUnreachable):
            try:
                bootstrap.ensure_ready(_ENTRY)
            except ProbeVPSSetupFailed:  # pragma: no cover — 出现就 fail
                pytest.fail("SSH 失败被误判 setup_failed")
