"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-01 XrayWorker._classify 现状判断 3 分支 (spec v5.1 §3)

故事:
  XrayWorker 抢到 task 后看 xray 现状, 决定走哪个前置分支:
    - 分支 A: xray 没装       (vps.xray_version 空 或 is_installed() False)
    - 分支 B: 装了没跑          (xray_version 非空 + is_installed True + is_running False)
    - 分支 C: 装着且跑着        (xray_version 非空 + is_installed True + is_running True)

测试矩阵:
  TC-01-a vps.xray_version="" + is_installed=anything       → 'A'
  TC-01-b vps.xray_version="X" + is_installed=False         → 'A' (OR 关系兜底)
  TC-01-c vps.xray_version="X" + is_installed=True  + running=False → 'B'
  TC-01-d vps.xray_version="X" + is_installed=True  + running=True  → 'C'
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from workers.xray_worker import XrayWorker


def _make_xray(installed: bool, running: bool) -> MagicMock:
    xray = MagicMock()
    xray.is_installed.return_value = installed
    xray.is_running.return_value = running
    return xray


class TestClassify(unittest.TestCase):

    def test_tc01a_empty_version_returns_A(self):
        xray = _make_xray(installed=True, running=True)
        self.assertEqual(XrayWorker._classify(xray, ""), "A")
        # 短路: 拿到 'A' 就不需要再问 is_running
        xray.is_running.assert_not_called()

    def test_tc01b_version_set_but_not_installed_returns_A(self):
        xray = _make_xray(installed=False, running=False)
        self.assertEqual(XrayWorker._classify(xray, "Xray 26.3.27"), "A")

    def test_tc01c_installed_not_running_returns_B(self):
        xray = _make_xray(installed=True, running=False)
        self.assertEqual(XrayWorker._classify(xray, "Xray 26.3.27"), "B")

    def test_tc01d_installed_and_running_returns_C(self):
        xray = _make_xray(installed=True, running=True)
        self.assertEqual(XrayWorker._classify(xray, "Xray 26.3.27"), "C")


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
