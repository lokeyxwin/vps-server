"""TC-19-08 tools/init_probe_vps.py (admin) — 注册 + handler.

业务故事:
  - TOOL.name='init_probe_vps', description 含 ok/probe_vps_unreachable/
    probe_vps_not_ready 3 种 status
  - TOOL 注册到 ALL_TOOLS
  - handler 4 个分支:
    - ensure_ready 成功 → {"status":"ok","host","inbound_port"}
    - ProbeVPSUnreachable → {"status":"probe_vps_unreachable","message"}
    - ProbeVPSSetupFailed → {"status":"probe_vps_not_ready","message"}
    - pool 空 (RuntimeError) → {"status":"probe_vps_unreachable","message":NO_PROBE_VPS_MESSAGE}
  - slot 参数能透传到 pool[slot]
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from mcp.types import TextContent

from probe_vps import (
    NO_PROBE_VPS_MESSAGE,
    ProbeVPSHandle,
    ProbeVPSSetupFailed,
    ProbeVPSUnreachable,
)
from tools import ALL_TOOLS
from tools.init_probe_vps import TOOL, handler


_FAKE_ENTRY = {
    "ip": "10.0.0.1",
    "port": 22,
    "username": "root",
    "password": "x",
}


def test_tool_meta():
    assert TOOL.name == "init_probe_vps"
    assert TOOL.title
    # description 含 3 种 status (映射表)
    assert "ok" in TOOL.description
    assert "probe_vps_unreachable" in TOOL.description
    assert "probe_vps_not_ready" in TOOL.description
    # slot 是可选, 默认 0
    schema = TOOL.inputSchema
    assert schema["required"] == []
    assert schema["properties"]["slot"]["default"] == 0


def test_registered_in_all_tools():
    names = [t.name for t, _ in ALL_TOOLS]
    assert "init_probe_vps" in names


def test_handler_success_returns_ok_with_host_and_port():
    handle = ProbeVPSHandle(host="1.2.3.4", inbound_port=19000)
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool",
        return_value=(_FAKE_ENTRY,),
    ), patch(
        "tools.init_probe_vps.bootstrap.ensure_ready",
        return_value=handle,
    ):
        out = asyncio.run(handler({}))

    assert len(out) == 1
    assert isinstance(out[0], TextContent)
    result = json.loads(out[0].text)
    assert result["status"] == "ok"
    assert result["host"] == "1.2.3.4"
    assert result["inbound_port"] == 19000


def test_handler_unreachable_returns_probe_vps_unreachable():
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool",
        return_value=(_FAKE_ENTRY,),
    ), patch(
        "tools.init_probe_vps.bootstrap.ensure_ready",
        side_effect=ProbeVPSUnreachable("ssh down"),
    ):
        out = asyncio.run(handler({}))
    result = json.loads(out[0].text)
    assert result["status"] == "probe_vps_unreachable"
    assert "ssh down" in result["message"]


def test_handler_setup_failed_returns_probe_vps_not_ready():
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool",
        return_value=(_FAKE_ENTRY,),
    ), patch(
        "tools.init_probe_vps.bootstrap.ensure_ready",
        side_effect=ProbeVPSSetupFailed("install fail"),
    ):
        out = asyncio.run(handler({}))
    result = json.loads(out[0].text)
    assert result["status"] == "probe_vps_not_ready"
    assert "install fail" in result["message"]


def test_handler_empty_pool_returns_unreachable_with_guidance():
    """pool 空 (RuntimeError) → probe_vps_unreachable + NO_PROBE_VPS_MESSAGE."""
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool",
        side_effect=RuntimeError("no pool"),
    ):
        out = asyncio.run(handler({}))
    result = json.loads(out[0].text)
    assert result["status"] == "probe_vps_unreachable"
    assert result["message"] == NO_PROBE_VPS_MESSAGE


def test_handler_slot_out_of_range_returns_unreachable():
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool",
        return_value=(_FAKE_ENTRY,),
    ), patch(
        "tools.init_probe_vps.bootstrap.ensure_ready",
    ) as mock_ensure:
        out = asyncio.run(handler({"slot": 5}))
    result = json.loads(out[0].text)
    assert result["status"] == "probe_vps_unreachable"
    assert "越界" in result["message"]
    mock_ensure.assert_not_called()


def test_handler_slot_arg_passes_correct_entry():
    pool = (
        {"ip": "1.1.1.1", "port": 22, "username": "u", "password": "p"},
        {"ip": "2.2.2.2", "port": 22, "username": "u", "password": "p"},
    )
    handle = ProbeVPSHandle(host="2.2.2.2", inbound_port=19000)
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool", return_value=pool,
    ), patch(
        "tools.init_probe_vps.bootstrap.ensure_ready", return_value=handle,
    ) as mock_ensure:
        out = asyncio.run(handler({"slot": 1}))
    result = json.loads(out[0].text)
    assert result["status"] == "ok"
    mock_ensure.assert_called_once_with(pool[1])


def test_handler_accepts_none_arguments():
    """arguments=None → 默认 slot=0."""
    handle = ProbeVPSHandle(host="1.2.3.4", inbound_port=19000)
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool",
        return_value=(_FAKE_ENTRY,),
    ), patch(
        "tools.init_probe_vps.bootstrap.ensure_ready", return_value=handle,
    ) as mock_ensure:
        out = asyncio.run(handler(None))
    result = json.loads(out[0].text)
    assert result["status"] == "ok"
    mock_ensure.assert_called_once_with(_FAKE_ENTRY)


def test_handler_unexpected_exception_bottom_to_not_ready():
    """非 ProbeVPSError 兜底也归 probe_vps_not_ready (不裸抛)."""
    with patch(
        "tools.init_probe_vps.get_probe_vps_pool",
        return_value=(_FAKE_ENTRY,),
    ), patch(
        "tools.init_probe_vps.bootstrap.ensure_ready",
        side_effect=ValueError("oops"),
    ):
        out = asyncio.run(handler({}))
    result = json.loads(out[0].text)
    assert result["status"] == "probe_vps_not_ready"
    assert "oops" in result["message"]
