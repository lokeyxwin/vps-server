"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-04 _prepare_running 分支 C 装着跑着 (spec v5.1 §3, ADR-0004 §1)

故事:
  分支 C 步骤:
    1. is_enabled() False 则 enable()   ⭐ v5 新增自启检查
    2. 其他啥都不做 (不 start, 不 stop, 不 reload)

测试矩阵:
  TC-04-a is_enabled=False → enable 被调一次, start/stop/reload 不被调
  TC-04-b is_enabled=True  → enable 不被调, 其他也不被调
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from workers.xray_worker import XrayWorker


def _make_xray(enabled: bool) -> MagicMock:
    xray = MagicMock()
    xray.is_enabled.return_value = enabled
    return xray


class TestPrepareRunning(unittest.TestCase):

    def test_tc04a_not_enabled_calls_enable_only(self):
        xray = _make_xray(enabled=False)
        XrayWorker._prepare_running(xray)
        xray.is_enabled.assert_called_once()
        xray.enable.assert_called_once()
        # 不应碰服务状态
        xray.start.assert_not_called()
        xray.stop.assert_not_called()
        xray.reload.assert_not_called()
        xray.install.assert_not_called()

    def test_tc04b_already_enabled_does_nothing(self):
        xray = _make_xray(enabled=True)
        XrayWorker._prepare_running(xray)
        xray.is_enabled.assert_called_once()
        xray.enable.assert_not_called()
        xray.start.assert_not_called()
        xray.stop.assert_not_called()
        xray.reload.assert_not_called()


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
