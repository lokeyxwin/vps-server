"""TC-18-08 ⭐ 防回退 — 没有任何活跃路径 import services/.

grep 整个项目 (排除 services/ 自身 + __pycache__ + .venv + .git + test/)
不应再有 `from services` 或 `import services` 出现.
"""

from __future__ import annotations

import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXCLUDE_DIRS = {"services", "__pycache__", ".venv", ".git", "test", "test.bak"}
IMPORT_RE = re.compile(
    r"^\s*from\s+services\b|^\s*import\s+services\b", re.MULTILINE
)


def test_no_active_path_imports_services():
    bad = []
    for p in ROOT.rglob("*.py"):
        rel_parts = p.relative_to(ROOT).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if IMPORT_RE.search(text):
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, (
        f"以下文件仍 import services/: {bad}\n"
        f"services/ 应彻底退出活跃路径 (ADR-0008 §决策 §2)"
    )
