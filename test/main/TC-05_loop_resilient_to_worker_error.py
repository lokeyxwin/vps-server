"""TC-18-05 worker run_once 抛异常不杀死 loop.

mock XrayWorker.run_once side_effect=Exception, ProxyDeployWorker 正常.
循环应继续, 不退出, 第二轮置 _stop=True 退出.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import main


def test_xray_worker_exception_logged_loop_continues(monkeypatch, caplog):
    """XrayWorker 抛错 → 应被 catch + log warning, ProxyDeployWorker 仍跑, 下一轮退出."""
    monkeypatch.setattr(main, "_stop", False)

    proxy_call_count = {"n": 0}

    def fake_xray_run(self):
        raise RuntimeError("xray boom")

    def fake_proxy_run(self):
        proxy_call_count["n"] += 1
        # 第一轮 proxy 跑完 → 触发退出
        main._stop = True
        return 0

    with caplog.at_level(logging.WARNING, logger="main.worker_loop"), \
         patch("workers.xray_worker.XrayWorker.run_once", fake_xray_run), \
         patch("workers.proxy_deploy_worker.ProxyDeployWorker.run_once", fake_proxy_run), \
         patch("main.time.sleep"):
        rc = main._run_worker_loop()

    assert rc == 0
    # ProxyDeployWorker 在 XrayWorker 抛错后仍被调
    assert proxy_call_count["n"] >= 1
    # 日志含 warning + xray 错信息
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("XrayWorker" in r.getMessage() and "boom" in r.getMessage() for r in warnings)


def test_proxy_worker_exception_logged_loop_continues(monkeypatch, caplog):
    """ProxyDeployWorker 抛错也走兜底."""
    monkeypatch.setattr(main, "_stop", False)

    xray_call_count = {"n": 0}

    def fake_xray_run(self):
        xray_call_count["n"] += 1
        return 0

    def fake_proxy_run(self):
        main._stop = True  # 异常前置 stop 让下轮退出
        raise RuntimeError("proxy boom")

    with caplog.at_level(logging.WARNING, logger="main.worker_loop"), \
         patch("workers.xray_worker.XrayWorker.run_once", fake_xray_run), \
         patch("workers.proxy_deploy_worker.ProxyDeployWorker.run_once", fake_proxy_run), \
         patch("main.time.sleep"):
        rc = main._run_worker_loop()

    assert rc == 0
    assert xray_call_count["n"] >= 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ProxyDeployWorker" in r.getMessage() and "boom" in r.getMessage() for r in warnings)
