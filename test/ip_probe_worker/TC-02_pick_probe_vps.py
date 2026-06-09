"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-02 ② 测试 VPS 顺序挑 _pick_probe_vps (spec v2 §3 ② + §4)

故事:
  从 PROBE_VPS_POOL 顺序遍历挑测试 VPS, 第一台 SSH 通就用它;
  全连不上抛 _ProbeVPSAllDownError, process() 转 probe_vps_unreachable status。
  空 pool 时 get_probe_vps_pool() 自抛 RuntimeError, 工人转 _ProbeVPSAllDownError 透传指引。

测试矩阵 (5 TC):
  TC-02-a 第 1 台连通 → 返回该 entry, 不试第 2 台
  TC-02-b 第 1 台挂, 第 2 台通 → 返回第 2 台
  TC-02-c 所有台都挂 → 抛 _ProbeVPSAllDownError
  TC-02-d 空 pool → get_probe_vps_pool 抛 RuntimeError → 转 _ProbeVPSAllDownError 带指引
  TC-02-e process() 集成: 全连不上 → status=probe_vps_unreachable
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import probe_vps
from workers import ip_probe_worker as ipw
from workers.ip_probe_worker import IPProbeWorker, _ProbeVPSAllDownError

from ._helpers import make_fake_session_scope, make_in_memory_engine


_POOL_2 = (
    {"ip": "10.0.0.1", "port": 22, "username": "root", "password": "p1"},
    {"ip": "10.0.0.2", "port": 22, "username": "root", "password": "p2"},
)


class TestPickProbeVPS(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_in_memory_engine()
        self._scope_patcher = patch(
            "workers.ip_probe_worker.session_scope",
            make_fake_session_scope(self.Session),
        )
        self._scope_patcher.start()
        self.worker = IPProbeWorker()

    def tearDown(self):
        self._scope_patcher.stop()
        self.engine.dispose()

    # ---------- TC-02-a ----------
    def test_tc02a_first_alive_returned_no_second_attempt(self):
        # 第一台通, 第二台被替换成"会抛错"以验证从未被试
        attempts = []

        class _FakeVPSSession:
            def __init__(self, **kwargs):
                attempts.append(kwargs["ip"])
                self.ip = kwargs["ip"]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("workers.ip_probe_worker.get_probe_vps_pool", return_value=_POOL_2), \
             patch("workers.ip_probe_worker.VPSSession", _FakeVPSSession):
            entry = self.worker._pick_probe_vps()

        self.assertEqual(entry["ip"], "10.0.0.1")
        self.assertEqual(attempts, ["10.0.0.1"], "第 1 台通了就不该试第 2 台")

    # ---------- TC-02-b ----------
    def test_tc02b_first_fails_second_succeeds(self):
        attempts = []

        class _FakeVPSSession:
            def __init__(self, **kwargs):
                attempts.append(kwargs["ip"])
                self.ip = kwargs["ip"]
                self._should_fail = kwargs["ip"] == "10.0.0.1"

            def __enter__(self):
                if self._should_fail:
                    raise ConnectionError("test: 第 1 台挂")
                return self

            def __exit__(self, *a):
                return False

        with patch("workers.ip_probe_worker.get_probe_vps_pool", return_value=_POOL_2), \
             patch("workers.ip_probe_worker.VPSSession", _FakeVPSSession):
            entry = self.worker._pick_probe_vps()

        self.assertEqual(entry["ip"], "10.0.0.2")
        self.assertEqual(attempts, ["10.0.0.1", "10.0.0.2"])

    # ---------- TC-02-c ----------
    def test_tc02c_all_down_raises(self):
        class _FakeVPSSession:
            def __init__(self, **kwargs):
                self.ip = kwargs["ip"]

            def __enter__(self):
                raise ConnectionError(f"test: {self.ip} 挂")

            def __exit__(self, *a):
                return False

        with patch("workers.ip_probe_worker.get_probe_vps_pool", return_value=_POOL_2), \
             patch("workers.ip_probe_worker.VPSSession", _FakeVPSSession):
            with self.assertRaises(_ProbeVPSAllDownError) as cm:
                self.worker._pick_probe_vps()
        self.assertIn("测试 VPS 都连不上", str(cm.exception))

    # ---------- TC-02-d ----------
    def test_tc02d_empty_pool_raises_with_guidance(self):
        def _empty_pool():
            raise RuntimeError(probe_vps.NO_PROBE_VPS_MESSAGE)

        with patch("workers.ip_probe_worker.get_probe_vps_pool", side_effect=_empty_pool):
            with self.assertRaises(_ProbeVPSAllDownError) as cm:
                self.worker._pick_probe_vps()
        # 指引文案应该透传(凭据 env 化后, 指向 ~/.zshrc.local + PROBE_VPS_1_IP)
        msg = str(cm.exception)
        self.assertIn("zshrc.local", msg)
        self.assertIn("PROBE_VPS_1_IP", msg)

    # ---------- TC-02-e ----------
    def test_tc02e_process_returns_probe_vps_unreachable_status(self):
        with patch.object(
            self.worker, "_pick_probe_vps",
            side_effect=_ProbeVPSAllDownError("test: 全挂"),
        ):
            result = self.worker.process(
                entry_host="proxy.example.com",
                entry_port=1080,
                username="u",
                password="p",
                protocol="socks5",
            )
        self.assertEqual(result["status"], "probe_vps_unreachable")
        self.assertIn("test: 全挂", result["message"])


if __name__ == "__main__":
    unittest.main()
