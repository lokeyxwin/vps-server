"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-03 _prepare_stopped 分支 B 装了没跑 (spec v5.1 §3, ADR-0004 §1)

故事:
  分支 B 步骤:
    1. is_enabled() False 则 enable()    ⭐ v5 新增自启检查
    2. start()
    3. is_running() 验证

测试矩阵:
  TC-03-a is_enabled=False → enable 被调一次 + start + is_running 通过
  TC-03-b is_enabled=True  → enable 不被调, start + is_running 通过
  TC-03-c start 后 is_running=False → 抛 ServiceNotActiveError
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from workers.xray_worker import XrayWorker
from xray.service import ServiceNotActiveError


def _make_xray(enabled: bool, running_after: bool) -> MagicMock:
    xray = MagicMock()
    xray.is_enabled.return_value = enabled
    xray.is_running.return_value = running_after
    return xray


class TestPrepareStopped(unittest.TestCase):

    def test_tc03a_not_enabled_calls_enable_then_start(self):
        xray = _make_xray(enabled=False, running_after=True)
        XrayWorker._prepare_stopped(xray)
        xray.is_enabled.assert_called_once()
        xray.enable.assert_called_once()
        xray.start.assert_called_once()

    def test_tc03b_already_enabled_skips_enable(self):
        xray = _make_xray(enabled=True, running_after=True)
        XrayWorker._prepare_stopped(xray)
        xray.is_enabled.assert_called_once()
        xray.enable.assert_not_called()
        xray.start.assert_called_once()

    def test_tc03c_not_running_after_start_raises(self):
        xray = _make_xray(enabled=True, running_after=False)
        with self.assertRaises(ServiceNotActiveError):
            XrayWorker._prepare_stopped(xray)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
