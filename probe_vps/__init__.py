"""probe_vps — 测试 VPS 自举模块 (ADR-0009).

包内分工:
  config.py    — PROBE_VPS_POOL / PROBE_TEST_PORT / NO_PROBE_VPS_MESSAGE
                 / get_probe_vps_pool / _build_pool / _MAX_PROBE_SLOTS
                 (旧单文件 probe_vps.py 内容, 一字不改搬过来)
  bootstrap.py — ensure_ready() / ProbeVPSHandle / ProbeVPSError 系列
                 (新建, ADR-0009 §3 决策落地)

re-export 现有 config 公开 + 私有符号 (保持 `from probe_vps import ...` 兼容,
含 _MAX_PROBE_SLOTS / _build_pool 给 TC 用), 同时暴露新 bootstrap 入口.
"""

from probe_vps.config import (
    NO_PROBE_VPS_MESSAGE,
    PROBE_TEST_PORT,
    PROBE_VPS_POOL,
    _MAX_PROBE_SLOTS,
    _build_pool,
    get_probe_vps_pool,
)
from probe_vps.bootstrap import (
    ProbeVPSError,
    ProbeVPSHandle,
    ProbeVPSSetupFailed,
    ProbeVPSUnreachable,
    ensure_ready,
)

__all__ = [
    # config
    "NO_PROBE_VPS_MESSAGE",
    "PROBE_TEST_PORT",
    "PROBE_VPS_POOL",
    "get_probe_vps_pool",
    # bootstrap
    "ensure_ready",
    "ProbeVPSHandle",
    "ProbeVPSError",
    "ProbeVPSUnreachable",
    "ProbeVPSSetupFailed",
]
