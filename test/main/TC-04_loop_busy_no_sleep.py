"""TC-18-04 _run_worker_loop busy=1 时不 sleep, idle=0 时 sleep.

mock XrayWorker.run_once 返 1 (有活), ProxyDeployWorker 返 0
→ 那一轮 busy>=1 不调 sleep, 然后置 _stop=True 退出.
"""

from __future__ import annotations

from unittest.mock import patch

import main


def test_busy_round_does_not_sleep(monkeypatch):
    """一轮 busy>=1 + 下一轮置 _stop → sleep 永远没被调."""
    monkeypatch.setattr(main, "_stop", False)

    # 第一轮: xray 返 1, proxy 返 0 → busy=1 → 不该 sleep
    # 第一轮结束后立刻置 _stop=True (用 side_effect 实现)
    call_count = {"n": 0}

    def fake_xray_run(self):
        call_count["n"] += 1
        return 1

    def fake_proxy_run(self):
        # 第一轮结束 → 置 _stop, 下轮直接退出
        main._stop = True
        return 0

    with patch("workers.xray_worker.XrayWorker.run_once", fake_xray_run), \
         patch("workers.proxy_deploy_worker.ProxyDeployWorker.run_once", fake_proxy_run), \
         patch("main.time.sleep") as mock_sleep:
        rc = main._run_worker_loop()

    assert rc == 0
    # busy=1 那轮不 sleep, _stop=True 也提前跳出, 整个 loop sleep 0 次
    mock_sleep.assert_not_called()
    # xray.run_once 至少被调一次
    assert call_count["n"] >= 1


def test_idle_round_sleeps_then_exits(monkeypatch):
    """一轮 busy=0 → 应 sleep, 期间触发 _stop → 下一轮退出."""
    monkeypatch.setattr(main, "_stop", False)

    sleep_calls = {"n": 0}

    def fake_xray_run(self):
        return 0

    def fake_proxy_run(self):
        return 0

    def fake_sleep(_sec):
        sleep_calls["n"] += 1
        main._stop = True

    with patch("workers.xray_worker.XrayWorker.run_once", fake_xray_run), \
         patch("workers.proxy_deploy_worker.ProxyDeployWorker.run_once", fake_proxy_run), \
         patch("main.time.sleep", side_effect=fake_sleep):
        rc = main._run_worker_loop()

    assert rc == 0
    assert sleep_calls["n"] == 1
