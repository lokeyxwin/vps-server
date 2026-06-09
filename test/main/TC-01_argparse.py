"""TC-18-01 main.py worker-loop 子命令存在 + argparse 行为.

规约金标准: task/done_18_*.md §测试用例 TC-18-01
- 无参数 → argparse 退码非 0 (subparsers required=True)
- worker-loop --help → 退码 0
"""

from __future__ import annotations

import pytest

import main


def test_no_args_exits_nonzero():
    """argv 为空 → argparse 报"the following arguments are required: ACTION", exit code 非 0."""
    with pytest.raises(SystemExit) as excinfo:
        main.main([])
    assert excinfo.value.code != 0


def test_worker_loop_help_exits_zero(capsys):
    """worker-loop --help → argparse 打 help, exit code 0."""
    with pytest.raises(SystemExit) as excinfo:
        main.main(["worker-loop", "--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "worker-loop" in captured.out


def test_top_level_help_exits_zero(capsys):
    """顶层 --help → 看到 worker-loop 子命令."""
    with pytest.raises(SystemExit) as excinfo:
        main.main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "worker-loop" in captured.out
