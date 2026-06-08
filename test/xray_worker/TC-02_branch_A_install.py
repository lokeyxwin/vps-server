"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-02 _prepare_fresh 分支 A 装机前置 (spec v5.1 §3)

故事:
  分支 A 步骤(v5.1 已补 write_default_config):
    1. install()
    2. is_config_blank() → True 则 write_default_config()
    3. start()
    4. enable()
    5. version() 非空, 否则抛 VerifyFailedError
    6. is_running() True, 否则抛 ServiceNotActiveError

测试矩阵:
  TC-02-a config 空 → install + write_default_config + start + enable + version + is_running 全部按序调
  TC-02-b config 已有内容 → write_default_config 不调, 其他全调
  TC-02-c version() 返空 → 抛 VerifyFailedError
  TC-02-d 启动后 is_running=False → 抛 ServiceNotActiveError
========================================================================
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from workers.xray_worker import XrayWorker
from xray.service import ServiceNotActiveError, VerifyFailedError


def _make_xray(
    config_blank: bool = True,
    running_after: bool = True,
    version: str = "Xray 26.3.27",
) -> MagicMock:
    xray = MagicMock()
    xray.is_config_blank.return_value = config_blank
    xray.is_running.return_value = running_after
    xray.version.return_value = version
    return xray


class TestPrepareFresh(unittest.TestCase):

    def test_tc02a_blank_config_full_sequence(self):
        xray = _make_xray(config_blank=True)
        XrayWorker._prepare_fresh(xray)
        xray.install.assert_called_once()
        xray.write_default_config.assert_called_once()
        xray.start.assert_called_once()
        xray.enable.assert_called_once()
        xray.version.assert_called_once()
        xray.is_running.assert_called_once()

    def test_tc02b_nonblank_config_skips_write_default(self):
        xray = _make_xray(config_blank=False)
        XrayWorker._prepare_fresh(xray)
        xray.install.assert_called_once()
        xray.write_default_config.assert_not_called()
        xray.start.assert_called_once()
        xray.enable.assert_called_once()

    def test_tc02c_empty_version_raises_verify(self):
        xray = _make_xray(config_blank=True, version="")
        with self.assertRaises(VerifyFailedError):
            XrayWorker._prepare_fresh(xray)

    def test_tc02d_not_running_raises_service_not_active(self):
        xray = _make_xray(config_blank=True, running_after=False)
        with self.assertRaises(ServiceNotActiveError):
            XrayWorker._prepare_fresh(xray)


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
