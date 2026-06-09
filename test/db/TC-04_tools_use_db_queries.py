"""TC-18-09 ⭐ 防回退 — 3 个 tools 文件 import 路径已改 from db.queries."""

from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"


def test_get_vps_registration_status_uses_db_queries():
    text = (TOOLS / "get_vps_registration_status.py").read_text(encoding="utf-8")
    assert "from db.queries import query_vps_status" in text
    assert "from services" not in text


def test_get_ip_registration_status_uses_db_queries():
    text = (TOOLS / "get_ip_registration_status.py").read_text(encoding="utf-8")
    assert "from db.queries import query_ip_status" in text
    assert "from services" not in text


def test_get_available_proxy_nodes_uses_db_queries():
    text = (TOOLS / "get_available_proxy_nodes.py").read_text(encoding="utf-8")
    assert "from db.queries import list_available_proxies" in text
    assert "from services" not in text
