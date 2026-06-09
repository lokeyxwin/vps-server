"""TC-18bis-06 main.py init-db 子命令: 建好所有表 + 幂等.

业务故事:
  - main.py init-db --help → exit 0, 含 "建好所有表"
  - 跑 init-db (用 in-memory engine 替身) → Base.metadata.tables 含 5 张业务表
  - 跑两次 init-db → 第二次不抛错 (幂等)
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import create_engine, inspect

import db.engine  # 触发注册到 sys.modules['db.engine']
import main


EXPECTED_TABLES = {
    "vps_record",
    "vps_task",
    "ip_record",
    "ip_task",
    "proxy_record",
}


def test_init_db_subcommand_in_help(capsys):
    """顶层 --help 应看到 init-db 子命令."""
    with pytest.raises(SystemExit) as excinfo:
        main.main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "init-db" in captured.out


def test_init_db_creates_all_tables(monkeypatch, tmp_path):
    """跑 main init-db → SQLite 文件落地 + 5 张业务表都建出来."""
    db_file = tmp_path / "test_init.db"
    test_engine = create_engine(f"sqlite:///{db_file}")

    # db/__init__.py 把 engine 名字注入 db 包命名空间, 导致 `import db.engine as X`
    # 拿到的是 Engine 实例不是子模块. 走 sys.modules 拿真正的模块对象再 patch.
    db_engine_module = sys.modules["db.engine"]
    monkeypatch.setattr(db_engine_module, "engine", test_engine)

    rc = main.main(["init-db"])
    assert rc == 0

    # 验证 5 张业务表全建出来
    insp = inspect(test_engine)
    actual = set(insp.get_table_names())
    missing = EXPECTED_TABLES - actual
    assert not missing, f"缺以下表: {missing}; 实际建出: {actual}"


def test_init_db_idempotent(monkeypatch, tmp_path):
    """连跑两次 init-db, 第二次不抛错 (CREATE TABLE IF NOT EXISTS)."""
    db_file = tmp_path / "test_idem.db"
    test_engine = create_engine(f"sqlite:///{db_file}")

    db_engine_module = sys.modules["db.engine"]
    monkeypatch.setattr(db_engine_module, "engine", test_engine)

    rc1 = main.main(["init-db"])
    rc2 = main.main(["init-db"])
    assert rc1 == 0
    assert rc2 == 0
