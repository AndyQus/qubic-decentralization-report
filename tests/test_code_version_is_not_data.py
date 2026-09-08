"""The build's version must show before the first epoch is sealed.

A fresh deployment starts with an empty store: /v1/dashboard-data answers 503
for the minutes the ingest needs to backfill. That is fine for the numbers —
they are measurements and must wait until they are measured. The code version
is not a measurement. It is a property of the running build, known at startup,
and a footer reading "v.–" on a freshly deployed image reads as a broken
deploy rather than as a report still filling up. So the dashboard reads it
from /health, which does not touch the store, and these tests keep it that way.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INDEX = ROOT / "dashboard" / "index.html"


def _client(tmp_path, monkeypatch):
    """An API over an empty store — a deployment in its first minutes.

    The store is swapped in place rather than by reimporting api/qdr: reloading
    those modules rebinds qdr.__version__ to a fresh object, which makes other
    tests in the same run compare versions across two different modules.
    """
    from qdr.store import Store

    import api.server as server

    store = Store(str(tmp_path / "qdr.db"))
    monkeypatch.setattr(server, "_store", store)
    return TestClient(server.app)


def test_health_carries_the_version_with_an_empty_store(tmp_path, monkeypatch):
    """The version is available exactly when the report is not."""
    from qdr import __version__

    c = _client(tmp_path, monkeypatch)

    assert c.get("/v1/dashboard-data").status_code == 503, (
        "expected an empty store to withhold the report"
    )
    body = c.get("/health").json()
    assert body["stored_reports"] == 0
    assert body["version"] == __version__


def test_dashboard_asks_health_for_the_version():
    """The footer must not depend on a report that may not exist yet."""
    html = INDEX.read_text(encoding="utf-8")

    assert re.search(r'fetch\(.*\+\s*"/health"\)', html), (
        "the dashboard no longer fetches /health; the footer would sit on "
        '"–" for the whole backfill of a fresh deployment'
    )
    assert "fetchCodeVersion();" in html, "startUI must request the version"


def test_a_missing_version_never_overwrites_a_known_one():
    """Late/failed responses must not blank a version already on screen."""
    html = INDEX.read_text(encoding="utf-8")
    fn = re.search(r"function renderCodeVersion\(v\)\{(.*?)\n  \}", html, re.S)
    assert fn, "renderCodeVersion is gone"
    assert "if(!v) return;" in fn.group(1), (
        "renderCodeVersion must ignore an empty value rather than render a dash"
    )
