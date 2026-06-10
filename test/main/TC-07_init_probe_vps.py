"""TC-19-06 main.py init-probe-vps 子命令 (ADR-0009 §决策 §6.1).

业务故事:
  - main --help / init-probe-vps --help 看到子命令 + --slot 参数
  - pool 空 → rc=1
  - slot 越界 → rc=1
  - ensure_ready 抛 ProbeVPSError → rc=1
  - 成功 → rc=0 + 日志"完成"
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import main
from probe_vps import ProbeVPSHandle, ProbeVPSSetupFailed, ProbeVPSUnreachable


_FAKE_ENTRY = {
    "ip": "10.0.0.1",
    "port": 22,
    "username": "root",
    "password": "x",
}


def test_init_probe_vps_in_help(capsys):
    """顶层 --help 应看到 init-probe-vps 子命令."""
    with pytest.raises(SystemExit) as excinfo:
        main.main(["--help"])
    assert excinfo.value.code == 0
    assert "init-probe-vps" in capsys.readouterr().out


def test_init_probe_vps_sub_help_shows_slot(capsys):
    """init-probe-vps --help 退 0 + 看到 --slot."""
    with pytest.raises(SystemExit) as excinfo:
        main.main(["init-probe-vps", "--help"])
    assert excinfo.value.code == 0
    assert "--slot" in capsys.readouterr().out


def test_empty_pool_returns_rc1():
    """pool 空 → get_probe_vps_pool 抛 RuntimeError → rc=1."""
    with patch("probe_vps.bootstrap.ensure_ready") as mock_ensure, \
         patch("probe_vps.get_probe_vps_pool", side_effect=RuntimeError("no pool")):
        rc = main.main(["init-probe-vps"])
    assert rc == 1
    mock_ensure.assert_not_called()


def test_slot_out_of_range_returns_rc1():
    """slot 超出 pool 长度 → rc=1, 不调 ensure_ready."""
    with patch("probe_vps.get_probe_vps_pool", return_value=(_FAKE_ENTRY,)), \
         patch("probe_vps.bootstrap.ensure_ready") as mock_ensure:
        rc = main.main(["init-probe-vps", "--slot", "5"])
    assert rc == 1
    mock_ensure.assert_not_called()


def test_slot_negative_returns_rc1():
    with patch("probe_vps.get_probe_vps_pool", return_value=(_FAKE_ENTRY,)), \
         patch("probe_vps.bootstrap.ensure_ready") as mock_ensure:
        rc = main.main(["init-probe-vps", "--slot", "-1"])
    assert rc == 1
    mock_ensure.assert_not_called()


def test_ensure_ready_unreachable_returns_rc1():
    """ensure_ready 抛 ProbeVPSUnreachable → rc=1."""
    with patch("probe_vps.get_probe_vps_pool", return_value=(_FAKE_ENTRY,)), \
         patch(
             "probe_vps.bootstrap.ensure_ready",
             side_effect=ProbeVPSUnreachable("ssh down"),
         ):
        rc = main.main(["init-probe-vps"])
    assert rc == 1


def test_ensure_ready_setup_failed_returns_rc1():
    """ensure_ready 抛 ProbeVPSSetupFailed → rc=1."""
    with patch("probe_vps.get_probe_vps_pool", return_value=(_FAKE_ENTRY,)), \
         patch(
             "probe_vps.bootstrap.ensure_ready",
             side_effect=ProbeVPSSetupFailed("install fail"),
         ):
        rc = main.main(["init-probe-vps"])
    assert rc == 1


def test_ensure_ready_success_returns_rc0(caplog):
    """ensure_ready 成功 → rc=0, 日志含 '完成'."""
    handle = ProbeVPSHandle(host="1.2.3.4", inbound_port=19000)
    with patch("probe_vps.get_probe_vps_pool", return_value=(_FAKE_ENTRY,)), \
         patch("probe_vps.bootstrap.ensure_ready", return_value=handle) as mock_ensure:
        with caplog.at_level("INFO"):
            rc = main.main(["init-probe-vps"])
    assert rc == 0
    mock_ensure.assert_called_once_with(_FAKE_ENTRY)
    assert any("完成" in r.message for r in caplog.records)


def test_slot_arg_passes_correct_entry():
    """--slot 1 → ensure_ready 收到 pool[1]."""
    pool = (
        {"ip": "1.1.1.1", "port": 22, "username": "u", "password": "p"},
        {"ip": "2.2.2.2", "port": 22, "username": "u", "password": "p"},
    )
    handle = ProbeVPSHandle(host="2.2.2.2", inbound_port=19000)
    with patch("probe_vps.get_probe_vps_pool", return_value=pool), \
         patch("probe_vps.bootstrap.ensure_ready", return_value=handle) as mock_ensure:
        rc = main.main(["init-probe-vps", "--slot", "1"])
    assert rc == 0
    mock_ensure.assert_called_once_with(pool[1])
