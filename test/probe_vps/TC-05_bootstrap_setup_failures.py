"""TC-19-05 bootstrap.ensure_ready 中途失败 → ProbeVPSSetupFailed (ADR-0009 §3 步③④⑤).

SSH 通了但 xray install / start / reload / config 操作挂了, 必须抛
ProbeVPSSetupFailed (而不是 ProbeVPSUnreachable), 让 IPProbeWorker 回 status
probe_vps_not_ready 区分上游 IP 问题.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from probe_vps import ProbeVPSSetupFailed, ProbeVPSUnreachable, bootstrap
from xray.config import ConfigReadError, ConfigValidationError, ConfigWriteError
from xray.service import InstallFailedError, ReloadFailedError, ServiceNotActiveError


_ENTRY = {
    "ip": "10.0.0.1",
    "port": 22,
    "username": "root",
    "password": "x",
}


class _FakeSess:
    def __init__(self, **_kw):
        self.client = MagicMock(name="fake_client")
        self.closed = False

    def connect(self):
        return self

    def close(self):
        self.closed = True


def _base_fake_xm(is_installed=False, is_running=False, is_blank=True):
    m = MagicMock(name="XrayManager")
    m.is_installed.return_value = is_installed
    m.is_running.return_value = is_running
    m.is_config_blank.return_value = is_blank
    m.version.return_value = "Xray 26.3.27"
    return m


def test_install_failure_to_setup_failed():
    """xray install 抛 → ProbeVPSSetupFailed (message 含 'install')."""
    fake_xm = _base_fake_xm(is_installed=False)
    fake_xm.install.side_effect = InstallFailedError("network down")

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        with pytest.raises(ProbeVPSSetupFailed) as exc:
            bootstrap.ensure_ready(_ENTRY)
        assert "install" in str(exc.value).lower()
        assert "network down" in str(exc.value)


def test_start_failure_to_setup_failed():
    """xray start 抛 → ProbeVPSSetupFailed (message 含 'start')."""
    fake_xm = _base_fake_xm(is_installed=True, is_running=False, is_blank=False)
    fake_xm.start.side_effect = ServiceNotActiveError("systemctl start failed")

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        with pytest.raises(ProbeVPSSetupFailed) as exc:
            bootstrap.ensure_ready(_ENTRY)
        assert "start" in str(exc.value).lower()


def test_write_default_config_failure_to_setup_failed():
    """xray 未装的预启动 write_default_config 抛 → setup_failed."""
    fake_xm = _base_fake_xm(is_installed=True, is_running=False, is_blank=True)
    fake_xm.write_default_config.side_effect = ConfigWriteError("disk full")

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        with pytest.raises(ProbeVPSSetupFailed) as exc:
            bootstrap.ensure_ready(_ENTRY)
        assert "config" in str(exc.value).lower()


def test_reload_failure_in_inbound_step_to_setup_failed():
    """add inbound 阶段 reload 抛 → setup_failed (message 含 'add inbound')."""
    fake_xm = _base_fake_xm(is_installed=True, is_running=True, is_blank=False)
    fake_xm.reload.side_effect = ReloadFailedError("systemctl reload failed")

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        with pytest.raises(ProbeVPSSetupFailed) as exc:
            bootstrap.ensure_ready(_ENTRY)
        assert "add inbound" in str(exc.value).lower() or "reload" in str(exc.value).lower()


def test_validate_config_failure_to_setup_failed():
    """upload 后 validate 抛 → setup_failed."""
    fake_xm = _base_fake_xm(is_installed=True, is_running=True, is_blank=False)
    fake_xm.validate_config.side_effect = ConfigValidationError("bad config")

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        with pytest.raises(ProbeVPSSetupFailed):
            bootstrap.ensure_ready(_ENTRY)


def test_read_config_failure_to_setup_failed():
    """读 config 抛 ConfigReadError → setup_failed (不是 unreachable)."""
    fake_xm = _base_fake_xm(is_installed=True, is_running=True, is_blank=False)

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(
             bootstrap.xc, "read_config",
             side_effect=ConfigReadError("json broken"),
         ):
        with pytest.raises(ProbeVPSSetupFailed):
            bootstrap.ensure_ready(_ENTRY)


def test_session_closed_in_finally_even_on_failure():
    """失败路径也必须关 SSH (finally 兜底, 防止连接泄漏)."""
    fake_xm = _base_fake_xm(is_installed=False)
    fake_xm.install.side_effect = InstallFailedError("err")

    captured: dict = {}

    class _CaptureSess(_FakeSess):
        def __init__(self, **kw):
            super().__init__(**kw)
            captured["sess"] = self

    with patch.object(bootstrap, "VPSSession", _CaptureSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        with pytest.raises(ProbeVPSSetupFailed):
            bootstrap.ensure_ready(_ENTRY)

    assert captured["sess"].closed is True


def test_setup_failure_is_not_unreachable():
    """边界确认: install 失败永远归 setup_failed, 不归 unreachable."""
    fake_xm = _base_fake_xm(is_installed=False)
    fake_xm.install.side_effect = InstallFailedError("err")

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        with pytest.raises(ProbeVPSSetupFailed):
            try:
                bootstrap.ensure_ready(_ENTRY)
            except ProbeVPSUnreachable:
                pytest.fail("setup 失败被误判 unreachable")
