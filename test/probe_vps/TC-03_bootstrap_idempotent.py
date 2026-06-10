"""TC-19-03 bootstrap.ensure_ready 幂等场景 (ADR-0009 §决策 §3 步③④⑤ 已就绪).

测试机已装 + 已跑 + 19000 已有 socks5/freedom inbound → 不调 install / start /
upload_config, 直接返回 handle. 跑 N 次结果一致.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from probe_vps import PROBE_TEST_PORT, ProbeVPSHandle, bootstrap


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


def _ready_config():
    """构造一份已含 19000 socks/freedom inbound 的 config dict."""
    return {
        "inbounds": [
            {
                "tag": "probe-direct",
                "port": PROBE_TEST_PORT,
                "protocol": "socks",
                "settings": {"auth": "noauth"},
            },
        ],
        "outbounds": [
            {"tag": "probe-freedom", "protocol": "freedom"},
        ],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["probe-direct"],
                    "outboundTag": "probe-freedom",
                },
            ],
        },
    }


def test_all_ready_skips_install_start_and_add_inbound():
    """完全就绪场景: install / start / upload_config / validate / reload 都不被调."""
    fake_xm = MagicMock(name="XrayManager")
    fake_xm.is_installed.return_value = True
    fake_xm.is_running.return_value = True
    fake_xm.is_config_blank.return_value = False
    fake_xm.version.return_value = "Xray 26.3.27"

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value=_ready_config()):
        handle = bootstrap.ensure_ready(_ENTRY)

    assert isinstance(handle, ProbeVPSHandle)
    assert handle.host == "10.0.0.1"
    assert handle.inbound_port == PROBE_TEST_PORT
    fake_xm.install.assert_not_called()
    fake_xm.start.assert_not_called()
    fake_xm.upload_config.assert_not_called()
    fake_xm.validate_config.assert_not_called()
    fake_xm.reload.assert_not_called()
    fake_xm.write_default_config.assert_not_called()


def test_idempotent_n_runs_same_result():
    """连跑 3 次, 每次都不动测试机, 都返回相同 handle."""
    fake_xm = MagicMock(name="XrayManager")
    fake_xm.is_installed.return_value = True
    fake_xm.is_running.return_value = True
    fake_xm.is_config_blank.return_value = False
    fake_xm.version.return_value = "Xray 26.3.27"

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value=_ready_config()):
        h1 = bootstrap.ensure_ready(_ENTRY)
        h2 = bootstrap.ensure_ready(_ENTRY)
        h3 = bootstrap.ensure_ready(_ENTRY)

    assert h1 == h2 == h3
    # 跑 3 次但都没动测试机
    fake_xm.install.assert_not_called()
    fake_xm.start.assert_not_called()
    fake_xm.upload_config.assert_not_called()


def test_session_close_called_in_finally_on_success():
    """成功路径也会关 SSH (finally 兜底释放连接)."""
    fake_xm = MagicMock(name="XrayManager")
    fake_xm.is_installed.return_value = True
    fake_xm.is_running.return_value = True
    fake_xm.is_config_blank.return_value = False

    captured: dict = {}

    class _CaptureSess(_FakeSess):
        def __init__(self, **kw):
            super().__init__(**kw)
            captured["sess"] = self

    with patch.object(bootstrap, "VPSSession", _CaptureSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value=_ready_config()):
        bootstrap.ensure_ready(_ENTRY)

    assert captured["sess"].closed is True
