"""TC-18-03 _run_worker_loop 收到 _stop=True 后立即退出.

设 _stop=True (在循环之前), 进入 _run_worker_loop 应立刻返回 0,
不调 sleep.
"""

from __future__ import annotations

from unittest.mock import patch

import main


def test_loop_exits_immediately_when_stop_is_true(monkeypatch):
    """循环开始前 _stop=True → 立即返回 0, 不调 sleep."""
    monkeypatch.setattr(main, "_stop", True)

    fake_xray_run = lambda self: 0
    fake_proxy_run = lambda self: 0

    with patch("workers.xray_worker.XrayWorker.run_once", fake_xray_run), \
         patch("workers.proxy_deploy_worker.ProxyDeployWorker.run_once", fake_proxy_run), \
         patch("main.time.sleep") as mock_sleep:
        rc = main._run_worker_loop()

    assert rc == 0
    mock_sleep.assert_not_called()
