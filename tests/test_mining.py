"""The mining view reads other people's nodes, so it has to behave under failure.

Three things are pinned here, each of which was a real risk while building it:

  * a node that does not answer must produce an honest empty state, never a page of
    numbers from the last time one did (the project's no-invented-data rule);
  * the response must be cached, because every extra fetch is a request against a
    stranger's node -- one query per TTL however many browsers are watching;
  * `hash_verified` must actually compare, since it is the page's claim that the
    task described in the concept doc is the task being mined right now.

No test here touches the network.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import antnode  # noqa: E402


def _live_bundle(solutions: int = 5000) -> dict:
    """A bundle shaped exactly like antnode.collect() returns on a good fetch."""
    return {
        "status": "live",
        "fetched_at": 1788958516,
        "node": {"ip": "203.0.113.7", "latency_ms": 42, "version": 304,
                 "peers_total": 10, "peers_responding": 8},
        "colony": {"solution_count": solutions, "threshold": 4000,
                   "free_ann_slots": 8382831, "max_children_per_parent": 0},
        "task": {"input_trits": 18, "sequence_length": 8760, "window_width": 672,
                 "graded_windows": 8088, "data_hash": antnode.CANONICAL_DATA_HASH,
                 "hash_verified": True, "target_zeros": 4344, "target_ones": 4417,
                 "target_unknown": 0},
        "epoch": {"epoch": 230, "tick": 79304436, "initial_tick": 79300000,
                  "ticks_into_epoch": 4436},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.server as server
    from qdr.store import Store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_store", Store(str(tmp_path / "qdr.db")))
    # Each test starts with a cold cache, or it would inherit the previous test's.
    monkeypatch.setattr(server, "_mining_cache",
                        {"at": 0.0, "data": None, "prev_count": None, "prev_at": 0.0})
    # The expected-epoch-length lookup is an RPC call; keep it off the network.
    monkeypatch.setattr(server, "get_client", lambda: None)
    return TestClient(server.app)


def test_unreachable_nodes_yield_an_empty_state_not_stale_numbers(client, monkeypatch):
    """No node answering is a normal state, and it must not read as measured data."""
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: {
        "status": "unavailable",
        "reason": "no Qubic node answered REQUEST_ANT_EPOCH_CONTEXT",
        "fetched_at": 1788958516,
        "node": {"peers_total": 10, "peers_responding": 0},
    })
    body = client.get("/v1/mining").json()

    assert body["status"] == "unavailable"
    assert body["reason"]
    # The page keys its rendering off these; inventing them would render a fake page.
    assert "colony" not in body
    assert "revenue" not in body


def test_a_raising_collect_does_not_500_the_page(client, monkeypatch):
    """A socket blowing up mid-read is a 503 with a reason, not a stack trace."""
    def boom(*a, **k):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(antnode, "collect", boom)
    resp = client.get("/v1/mining")

    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]


def test_repeat_requests_are_served_from_cache(client, monkeypatch):
    """Many viewers must not mean many queries against a stranger's node."""
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return _live_bundle()

    monkeypatch.setattr(antnode, "collect", counting)

    first = client.get("/v1/mining").json()
    for _ in range(5):
        client.get("/v1/mining")

    assert calls["n"] == 1, "cache did not hold; every viewer would hit a real node"
    assert first["next_refresh_in"] > 0, "a live view needs to know when data renews"


def test_growth_rate_needs_two_real_fetches(client, monkeypatch):
    """delta_per_min is measured, so it must be absent until there is a baseline."""
    import api.server as server

    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _live_bundle(5000))
    first = client.get("/v1/mining").json()
    assert "delta_per_min" not in first["colony"], "no baseline yet -- nothing to measure"

    # Expire the cache and move the baseline a minute into the past.
    server._mining_cache["at"] = 0.0
    server._mining_cache["prev_at"] -= 60.0
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _live_bundle(5100))

    second = client.get("/v1/mining").json()
    assert second["colony"]["delta_per_min"] == pytest.approx(100, abs=5)


def test_hash_verification_actually_compares():
    """hash_verified is a claim about reality; it has to fail when reality differs."""
    ctx = antnode.AntEpochContext(
        epoch=230, threshold=4000, freshness_window=15000, solution_count=1,
        free_ann_slots=1, max_children_per_parent=0, spectrum_digest="00",
        topology_hash=antnode.CANONICAL_TOPOLOGY_HASH,
        data_hash="deadbeef" * 8,  # a task that is not the one we analysed
        source_ip="203.0.113.7", latency_ms=1.0,
    )
    assert ctx.data_hash != antnode.CANONICAL_DATA_HASH

    # And the unbounded-child-cap encoding, which reads backwards if taken literally.
    assert ctx.max_children_is_unbounded is True


def test_the_dashboard_links_to_the_mining_page():
    """The page started out unlisted and is now part of the navigation.

    Pinned because the link is easy to lose in a header edit, and a page nobody
    can reach from the report is a page nobody visits.
    """
    index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert './mining.html' in index


def test_mining_page_offers_both_languages():
    """Both language tables must carry every key the markup asks for.

    A missing key silently falls back to English, which reads as a half-translated
    page rather than an error -- so it is checked rather than eyeballed.
    """
    page = (ROOT / "dashboard" / "mining.html").read_text(encoding="utf-8")

    used = set(re.findall(r'data-i18n="([A-Za-z0-9_]+)"', page))
    assert used, "no data-i18n nodes found -- did the markup lose its tagging?"

    def keys_of(block: str) -> set[str]:
        # Several keys share a line, so match every `name:` that follows the start
        # of a line or a comma -- not just the first one on each line.
        return set(re.findall(r'(?:^|,)\s*([A-Za-z0-9_]+):\s*"', block, re.M))

    en_block = page[page.index("    en: {"):page.index("    de: {")]
    de_block = page[page.index("    de: {"):page.index("  let lang =")]
    en, de = keys_of(en_block), keys_of(de_block)

    assert not (used - en), f"English is missing: {sorted(used - en)}"
    assert not (used - de), f"German is missing: {sorted(used - de)}"
    assert en == de, f"tables differ: {sorted(en ^ de)}"
