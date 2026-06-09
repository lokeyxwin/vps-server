"""TC-18-07 ⭐ 防回退 — services/registration_query.py / proxy_query.py 已 git rm."""

from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_services_registration_query_removed():
    assert not (ROOT / "services" / "registration_query.py").exists(), (
        "services/registration_query.py 应已 git rm (搬到 db/queries.py)"
    )


def test_services_proxy_query_removed():
    assert not (ROOT / "services" / "proxy_query.py").exists(), (
        "services/proxy_query.py 应已 git rm (搬到 db/queries.py)"
    )
