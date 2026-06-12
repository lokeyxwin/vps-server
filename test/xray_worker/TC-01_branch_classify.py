"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-01 XrayWorker._classify 现状判断 3 分支 (spec v5.1 §3)

故事:
  XrayWorker 抢到 task 后**实时探测** xray 现状, 决定走哪个前置分支:
    - 分支 A: 没装           (实时 xray version 空)
    - 分支 B: 装了没跑        (version 非空 + is_running False)
    - 分支 C: 装着且跑着      (version 非空 + is_running True)
  判据是实时 `xray version`, 不看 DB 的 vps.xray_version ——
  那是 XrayWorker 完工时自己写的产出, 第一次进来必为空, 拿它当判据会误判 A 去重装。

测试矩阵:
  TC-01-a version="" + running=anything   → 'A'
  TC-01-b version="X" + running=False      → 'B'
  TC-01-c version="X" + running=True       → 'C'
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from workers.xray_worker import XrayWorker


def _make_xray(version: str, running: bool) -> MagicMock:
    xray = MagicMock()
    xray.version.return_value = version
    xray.is_running.return_value = running
    return xray


class TestClassify(unittest.TestCase):

    def test_tc01a_empty_version_returns_A(self):
        xray = _make_xray(version="", running=True)
        self.assertEqual(XrayWorker._classify(xray), "A")
        # 短路: 没版本就是没装, 不需要再问 is_running
        xray.is_running.assert_not_called()

    def test_tc01b_installed_not_running_returns_B(self):
        xray = _make_xray(version="Xray 26.3.27", running=False)
        self.assertEqual(XrayWorker._classify(xray), "B")

    def test_tc01c_installed_and_running_returns_C(self):
        xray = _make_xray(version="Xray 26.3.27", running=True)
        self.assertEqual(XrayWorker._classify(xray), "C")


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
