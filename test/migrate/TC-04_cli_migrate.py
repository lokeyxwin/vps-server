"""TC-30-04 main.py migrate 子命令 CLI 行为 (验收矩阵⑥).

矩阵⑥ CLI: main.py --help 出现 migrate; migrate 返回清晰 applied/skipped。
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import create_engine, inspect, text

import db.engine  # 触发注册到 sys.modules['db.engine']
import main


def _columns(engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


def test_migrate_subcommand_in_help(capsys):
    """顶层 --help 应看到 migrate 子命令。"""
    with pytest.raises(SystemExit) as excinfo:
        main.main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "migrate" in captured.out


def test_migrate_help_exits_zero(capsys):
    """migrate --help → exit 0。"""
    with pytest.raises(SystemExit) as excinfo:
        main.main(["migrate", "--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "migrate" in captured.out


def test_cli_migrate_old_db_prints_applied(monkeypatch, tmp_path, capsys):
    """CLI migrate 在缺 method 的旧库上 → 打印 applied: ['0001'] + 加列。"""
    db_file = tmp_path / "cli_old.db"
    test_engine = create_engine(f"sqlite:///{db_file}")
    with test_engine.begin() as conn:
        conn.execute(text("CREATE TABLE proxy_record (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(sys.modules["db.engine"], "engine", test_engine)

    rc = main.main(["migrate"])
    assert rc == 0

    captured = capsys.readouterr()
    assert "applied: ['0001']" in captured.out
    assert "skipped: []" in captured.out
    assert "method" in _columns(test_engine, "proxy_record")


def test_cli_migrate_idempotent_prints_empty_applied(monkeypatch, tmp_path, capsys):
    """CLI migrate 连跑两次, 第二次打印 applied: [] (no-op)。"""
    db_file = tmp_path / "cli_idem.db"
    test_engine = create_engine(f"sqlite:///{db_file}")
    with test_engine.begin() as conn:
        conn.execute(text("CREATE TABLE proxy_record (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(sys.modules["db.engine"], "engine", test_engine)

    main.main(["migrate"])
    capsys.readouterr()  # 清掉第一次输出

    rc = main.main(["migrate"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "applied: []" in captured.out
    assert "skipped: ['0001']" in captured.out


def test_cli_migrate_failure_returns_1(capsys):
    """apply_pending 抛错(DB 锁/权限) → _migrate 兜底返回 1 + 友好提示(非 raw stacktrace)。"""
    from unittest.mock import patch

    with patch("db.migrate.apply_pending", side_effect=RuntimeError("db locked")):
        rc = main.main(["migrate"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "migrate failed" in captured.out
    assert "db locked" in captured.out
