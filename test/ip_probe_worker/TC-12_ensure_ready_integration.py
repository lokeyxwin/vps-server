"""TC-19-09 IPProbeWorker 入口加 bootstrap.ensure_ready (ADR-0009 §决策 §5).

业务故事:
  IPProbeWorker.process 在 _pick_probe_vps 之后, _apply_test_outbound 之前,
  插一段 bootstrap.ensure_ready 调用. 失败按 ProbeVPSError 类型分流:
    - ProbeVPSUnreachable → status=probe_vps_unreachable
    - ProbeVPSSetupFailed → status=probe_vps_not_ready (新)

ensure_ready 成功不影响后续主流程 (透明经过).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from probe_vps import (
    ProbeVPSHandle,
    ProbeVPSSetupFailed,
    ProbeVPSUnreachable,
)
from workers.ip_probe_worker import IPProbeWorker


_PROBE_ENTRY = {
    "ip": "10.0.0.1",
    "port": 22,
    "username": "root",
    "password": "x",
}


def _call_process():
    return IPProbeWorker().process(
        entry_host="up.example.com",
        entry_port=1080,
        username="alice",
        password="p",
        protocol="socks5",
    )


def test_ensure_ready_unreachable_returns_status():
    """ensure_ready 抛 ProbeVPSUnreachable → process 短路 status=probe_vps_unreachable."""
    with patch.object(
        IPProbeWorker, "_pick_probe_vps", return_value=_PROBE_ENTRY,
    ), patch(
        "workers.ip_probe_worker.bootstrap.ensure_ready",
        side_effect=ProbeVPSUnreachable("ssh dead"),
    ):
        result = _call_process()

    assert result["status"] == "probe_vps_unreachable"
    assert "ssh dead" in result["message"]


def test_ensure_ready_setup_failed_returns_not_ready():
    """ensure_ready 抛 ProbeVPSSetupFailed → process 返 status=probe_vps_not_ready (新 status)."""
    with patch.object(
        IPProbeWorker, "_pick_probe_vps", return_value=_PROBE_ENTRY,
    ), patch(
        "workers.ip_probe_worker.bootstrap.ensure_ready",
        side_effect=ProbeVPSSetupFailed("install fail"),
    ):
        result = _call_process()

    assert result["status"] == "probe_vps_not_ready"
    assert "install fail" in result["message"]


def test_ensure_ready_success_lets_process_continue():
    """ensure_ready 成功不短路, 主流程继续 (后续 mock 拆掉 _apply_test_outbound 等)."""
    handle = ProbeVPSHandle(host="10.0.0.1", inbound_port=19000)

    # mock 主流程后续步骤都成功, 让 process 走到 queued
    captured = {"ensure_called": 0}

    def _ensure_ok(entry):
        captured["ensure_called"] += 1
        assert entry == _PROBE_ENTRY
        return handle

    # mock 全套后续: VPSSession + XrayManager + _apply_test_outbound +
    # _probe_and_resolve + 查重 + _persist_and_dispatch
    class _FakeSess:
        def __init__(self, **_kw):
            self.client = MagicMock()

        def connect(self):
            return self

        def close(self):
            pass

    fake_probe_result = {
        "ok": True,
        "actual_egress_ip": "1.2.3.4",
        "geo": {"country_code": "US"},
    }

    with patch.object(
        IPProbeWorker, "_pick_probe_vps", return_value=_PROBE_ENTRY,
    ), patch(
        "workers.ip_probe_worker.bootstrap.ensure_ready", side_effect=_ensure_ok,
    ), patch(
        "workers.ip_probe_worker.VPSSession", _FakeSess,
    ), patch(
        "workers.ip_probe_worker.XrayManager", MagicMock(),
    ), patch.object(
        IPProbeWorker, "_apply_test_outbound",
        return_value=({"inbounds": []}, "u", "p"),
    ), patch.object(
        IPProbeWorker, "_probe_and_resolve", return_value=fake_probe_result,
    ), patch.object(
        IPProbeWorker, "_lookup_by_actual", return_value=None,
    ), patch.object(
        IPProbeWorker, "_persist_and_dispatch",
        return_value={"ip_id": 99, "task_id": 100},
    ):
        result = _call_process()

    assert captured["ensure_called"] == 1
    assert result["status"] == "queued"
    assert result["ip_id"] == 99
    assert result["task_id"] == 100


def test_ensure_ready_called_with_probe_entry_dict():
    """ensure_ready 入参必须是 _pick_probe_vps 返回的 entry dict, 不是 slot int."""
    with patch.object(
        IPProbeWorker, "_pick_probe_vps", return_value=_PROBE_ENTRY,
    ), patch(
        "workers.ip_probe_worker.bootstrap.ensure_ready",
        side_effect=ProbeVPSSetupFailed("x"),
    ) as mock_ensure:
        _call_process()

    mock_ensure.assert_called_once_with(_PROBE_ENTRY)
