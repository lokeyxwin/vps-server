"""
========================================================================
TC-09 + TC-10 防回退 (spec §2 + §8 不变量 #1)

故事:
  - 没有 rgip / rgvps 残留: ALL_TOOLS 不含旧名, tools/ 目录下不存在旧文件
  - 文件 stem == TOOL.name (三处对齐: 文件名 / TOOL.name / __init__.py import)

子测:
  TC-09-a ALL_TOOLS names 不含 'rgip' 'rgvps'
  TC-09-b tools/ 目录下不存在 rgip.py / rgvps.py 文件
  TC-10-a 每个 tools/<name>.py 的 stem 跟 TOOL.name 一致
========================================================================
"""

from __future__ import annotations

import importlib
import pathlib
import unittest

from tools import ALL_TOOLS


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[2] / "tools"


class TestAntiRegression(unittest.TestCase):

    def test_tc09a_no_legacy_names_in_all_tools(self):
        names = {t.name for t, _ in ALL_TOOLS}
        self.assertNotIn("rgip", names)
        self.assertNotIn("rgvps", names)

    def test_tc09b_no_legacy_files_in_tools_dir(self):
        rgip_path = TOOLS_DIR / "rgip.py"
        rgvps_path = TOOLS_DIR / "rgvps.py"
        self.assertFalse(rgip_path.exists(),
                         "tools/rgip.py 应已 git mv 为 register_ip.py")
        self.assertFalse(rgvps_path.exists(),
                         "tools/rgvps.py 应已 git rm (ADR-0007 §2)")

    def test_tc10a_file_stem_matches_tool_name(self):
        """每个 tools/<name>.py 的 stem 跟 TOOL.name 三处对齐."""
        for tool, _handler in ALL_TOOLS:
            # 推算对应模块路径: tools/<TOOL.name>.py
            expected_path = TOOLS_DIR / f"{tool.name}.py"
            self.assertTrue(
                expected_path.exists(),
                f"找不到 tools/{tool.name}.py, "
                f"违反 spec §8 不变量 #1 文件名 == TOOL.name",
            )
            # 反向 import 模块, 拿 TOOL.name 比对
            mod = importlib.import_module(f"tools.{tool.name}")
            self.assertEqual(
                mod.TOOL.name, tool.name,
                f"tools/{tool.name}.py 里 TOOL.name 跟文件名不一致",
            )


if __name__ == "__main__":
    unittest.main()
