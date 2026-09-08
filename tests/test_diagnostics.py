""""Is it building, or is it broken?" must be answerable from a browser.

Both states render the same "still building" notice on the dashboard: an ingest
mid-backfill and an ingest that cannot write a byte. That ambiguity cost a real
deployment days of waiting for a report that was never going to arrive, so the
distinction is pinned here.

The specific trap: /health used to catch every store exception and answer
`stored_reports: 0` — indistinguishable from a healthy empty store.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG_PAGE = ROOT / "dashboard" / "log.html"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.server as server
    from qdr.store import Store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_store", Store(str(tmp_path / "qdr.db")))
    monkeypatch.setattr(server, "_LOG_FILE", tmp_path / "worker.log")
    # Never touch the network from a test.
    monkeypatch.setattr(server, "_probe_rpc",
                        lambda: {"ok": True, "base": "test", "epoch": 1, "tick": 2})
    return TestClient(server.app)


def test_empty_store_with_a_running_worker_reads_as_building(client, tmp_path):
    """Empty + the worker is demonstrably alive = wait, not a fault."""
    (tmp_path / "worker.log").write_text("[worker] backfilling …", encoding="utf-8")

    d = client.get("/v1/diagnostics").json()

    assert d["store"]["ok"] and d["writable"]["ok"]
    assert d["healthy"] is False, "an empty store is not yet serving a report"
    assert any("backfill" in p for p in d["problems"]), (
        "an empty-but-healthy store must be explained as waiting, not as a fault"
    )


def test_empty_store_with_no_worker_log_names_the_missing_ingest(client):
    """The failure that kept a deployment empty for days.

    The image's default entrypoint only started the API; the ingest lived solely
    in docker-compose.yaml, so a host that just runs the image had a perfectly
    healthy API in front of a store nothing was ever going to fill. "Still
    building" was the wrong story — nothing was building.
    """
    d = client.get("/v1/diagnostics").json()

    assert d["healthy"] is False
    assert any("ingest worker has not run" in p for p in d["problems"]), (
        "an empty store with no worker log must be reported as a missing ingest, "
        "not as a backfill still in progress"
    )


def test_unreadable_store_is_reported_not_swallowed(tmp_path, monkeypatch):
    """The regression that made a broken volume look like a fresh deployment."""
    import api.server as server

    class Broken:
        path = tmp_path / "qdr.db"

        def stats(self):
            raise RuntimeError("unable to open database file")

        def list_epochs(self, limit=200):
            raise RuntimeError("unable to open database file")

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_store", Broken())
    monkeypatch.setattr(server, "_probe_rpc",
                        lambda: {"ok": True, "base": "test", "epoch": 1, "tick": 2})
    c = TestClient(server.app)

    health = c.get("/health").json()
    assert health["status"] == "degraded", "a broken store must not report 'ok'"
    assert "store_error" in health, (
        "/health hid the failure behind stored_reports: 0, which is exactly what "
        "a healthy fresh deployment also looks like"
    )

    d = c.get("/v1/diagnostics").json()
    assert d["store"]["ok"] is False
    assert any("volume" in p or "store" in p for p in d["problems"])


def test_unreachable_rpc_is_named(client, monkeypatch):
    import api.server as server

    monkeypatch.setattr(server, "_probe_rpc",
                        lambda: {"ok": False, "base": "x", "error": "Timeout"})
    d = client.get("/v1/diagnostics").json()

    assert d["healthy"] is False
    assert any("RPC" in p for p in d["problems"])


def test_log_endpoint_survives_a_missing_file(client):
    """A missing log is itself a diagnosis: the worker never started."""
    body = client.get("/v1/log").json()
    assert body["exists"] is False
    assert body["lines"] == []
    assert "note" in body


def test_log_endpoint_tails_the_file(client, tmp_path):
    (tmp_path / "worker.log").write_text(
        "\n".join(f"line {i}" for i in range(500)), encoding="utf-8")

    body = client.get("/v1/log?lines=10").json()
    assert body["exists"] is True
    assert len(body["lines"]) == 10
    assert body["lines"][-1] == "line 499", "must return the tail, not the head"


def test_log_page_is_served_and_self_contained():
    """The page must work from the container with no external assets."""
    assert LOG_PAGE.exists(), "dashboard/log.html is missing"
    html = LOG_PAGE.read_text(encoding="utf-8")

    assert "/v1/diagnostics" in html and "/v1/log" in html
    assert "<script src=" not in html and "@import" not in html, (
        "the diagnostics page must not depend on anything it may not be able to load"
    )


# ---------------------------------------------------------------------------
# Degraded-but-serving: the deployment we cannot administer
# ---------------------------------------------------------------------------

def test_store_falls_back_when_the_volume_is_unusable(tmp_path):
    """An unwritable mount must not take the whole report down.

    On a host we do not administer, "chown the volume" is not an available
    remedy, and a store that refuses to open turns every endpoint into a 500 —
    including the ones that would have explained why. Falling back to a path
    inside the container keeps the report serving and the failure visible.
    """
    from qdr.store import Store

    corrupt = tmp_path / "qdr.db"
    corrupt.write_bytes(b"not a database")

    store = Store(corrupt)

    assert store.is_degraded, "a store that had to move must say so"
    assert store.configured_path == corrupt
    assert store.path != corrupt, "it must actually have moved somewhere writable"
    assert store.stats()["reports"] == 0, "and it must work"


def test_fallback_can_be_refused(tmp_path):
    """The ingest's own tooling may prefer a hard failure over a silent move."""
    import pytest as _pytest
    from qdr.store import Store

    corrupt = tmp_path / "qdr.db"
    corrupt.write_bytes(b"not a database")

    with _pytest.raises(Exception):
        Store(corrupt, allow_fallback=False)


def test_degraded_store_is_never_reported_as_healthy(tmp_path, monkeypatch):
    import api.server as server
    from qdr.store import Store

    corrupt = tmp_path / "qdr.db"
    corrupt.write_bytes(b"not a database")

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_store", Store(corrupt))
    monkeypatch.setattr(server, "_probe_rpc",
                        lambda: {"ok": True, "base": "t", "epoch": 1, "tick": 2})
    c = TestClient(server.app)

    assert c.get("/health").json()["status"] == "degraded"

    d = c.get("/v1/diagnostics").json()
    assert d["store"]["degraded"] is True
    assert d["healthy"] is False
    assert any("history is lost" in p for p in d["problems"]), (
        "the cost of the fallback must be stated, not hidden behind a green check"
    )


def test_rpc_cache_survives_an_unwritable_data_dir(monkeypatch, tmp_path):
    """The ingest used to die here before making a single request."""
    from qdr.client import CachedClient

    blocked = tmp_path / "blocked"
    blocked.write_text("a file where a directory should be", encoding="utf-8")

    c = CachedClient(cache_dir=blocked / "raw")
    assert c.cache_dir.exists() and c.cache_dir.is_dir()
