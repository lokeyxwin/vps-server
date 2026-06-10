"""TC-19-04 bootstrap.ensure_ready 全新空白 → 装 + 起 + add inbound (ADR-0009 §3).

测试机啥都没装 / config 空 → ensure_ready 走全路径: install → write_default
→ start → upload_config → validate → reload → 返回 handle.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from config import XRAY_DEFAULT_PORT
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


def test_fresh_install_start_add_inbound():
    """完全空白: 装 + 写默认 config + 起 + add 18440 inbound → handle 返回."""
    fake_xm = MagicMock(name="XrayManager")
    fake_xm.is_installed.return_value = False
    # 装完后 is_running=False 触发 start; start 完后假设不再问 is_running
    fake_xm.is_running.return_value = False
    fake_xm.is_config_blank.return_value = True
    fake_xm.version.return_value = "Xray 26.3.27"

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        handle = bootstrap.ensure_ready(_ENTRY)

    assert isinstance(handle, ProbeVPSHandle)
    assert handle.inbound_port == PROBE_TEST_PORT
    # 全路径都被调
    fake_xm.install.assert_called_once()
    fake_xm.write_default_config.assert_called_once()
    fake_xm.start.assert_called_once()
    fake_xm.upload_config.assert_called_once()
    fake_xm.validate_config.assert_called_once()
    fake_xm.reload.assert_called_once()


def test_fresh_uploaded_config_contains_18440_inbound():
    """upload_config 收到的 dict 必须含 18440 socks/freedom inbound."""
    fake_xm = MagicMock(name="XrayManager")
    fake_xm.is_installed.return_value = False
    fake_xm.is_running.return_value = False
    fake_xm.is_config_blank.return_value = True

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        bootstrap.ensure_ready(_ENTRY)

    uploaded = fake_xm.upload_config.call_args.args[0]
    inbounds = uploaded.get("inbounds", [])
    probe = [i for i in inbounds if i.get("port") == XRAY_DEFAULT_PORT]
    assert len(probe) == 1, f"应有 1 条 {XRAY_DEFAULT_PORT} inbound, got: {inbounds}"
    assert probe[0]["protocol"] == "socks"
    assert probe[0]["settings"]["auth"] == "noauth"


def test_already_installed_but_not_running_starts():
    """xray 已装但没跑 → 跳 install, 跑 start + add inbound."""
    fake_xm = MagicMock(name="XrayManager")
    fake_xm.is_installed.return_value = True
    fake_xm.is_running.return_value = False
    fake_xm.is_config_blank.return_value = False
    fake_xm.version.return_value = "Xray 26.3.27"

    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value={}):
        bootstrap.ensure_ready(_ENTRY)

    fake_xm.install.assert_not_called()  # 已装跳过
    fake_xm.start.assert_called_once()   # 没跑就起
    fake_xm.upload_config.assert_called_once()
    fake_xm.reload.assert_called_once()


def test_already_running_but_no_inbound_only_adds_inbound():
    """xray 装好跑着但无 19000 inbound → 只 add + reload, 不动 install/start."""
    fake_xm = MagicMock(name="XrayManager")
    fake_xm.is_installed.return_value = True
    fake_xm.is_running.return_value = True
    fake_xm.is_config_blank.return_value = False
    fake_xm.version.return_value = "Xray 26.3.27"

    other_cfg = {
        "inbounds": [{"tag": "other", "port": 8080, "protocol": "http"}],
        "outbounds": [],
        "routing": {"rules": []},
    }
    with patch.object(bootstrap, "VPSSession", _FakeSess), \
         patch.object(bootstrap, "XrayManager", return_value=fake_xm), \
         patch.object(bootstrap.xc, "read_config", return_value=other_cfg):
        bootstrap.ensure_ready(_ENTRY)

    fake_xm.install.assert_not_called()
    fake_xm.start.assert_not_called()
    fake_xm.upload_config.assert_called_once()
    fake_xm.reload.assert_called_once()
