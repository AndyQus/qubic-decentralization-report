"""The BTC feed behind /v1/btc and the BTC page (docs/CONCEPT_BTC_PREDICTION.de.md).

Pinned here:

  * no price leaves this server -- the hourly prices originate at Binance, and
    this project republishes no exchange market data (decision 2026-11-18:
    direction only). Same idea as the volume tripwire in test_price.py;
  * the hour view rides on /v1/btc only, so /v1/mining, which every live page
    polls, does not grow by it;
  * the colony's call is reported missing, with the reason, and never filled;
  * the worker records each live hour once, and the record survives the node;
  * the tick stream tells accepted from rejected solutions (since core v1.309
    every solution transaction is logged with its outcome);
  * the page itself: linked from every sibling, both languages complete, every
    API path it calls exists, no third-party code.

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

from qdr import antnode, pipeline, tickstream  # noqa: E402
from qdr.store import Store  # noqa: E402

DASH = ROOT / "dashboard"
PAGE = DASH / "btc.html"
BASE = 40316


def _hours() -> list[dict]:
    frame = [{"hour": BASE - 1 + i, "at": 0, "up": bool(i % 2), "live": False} for i in range(2)]
    live = [{"hour": BASE + 1, "at": 0, "up": False, "live": True, "append_tick": 500, "filled": False},
            {"hour": BASE + 2, "at": 0, "up": True, "live": True, "append_tick": 600, "filled": True}]
    return frame + live


def _bundle() -> dict:
    return {
        "status": "live", "fetched_at": 1795005342,
        "node": {"ip": "203.0.113.7", "latency_ms": 42, "version": 313,
                 "peers_total": 10, "peers_responding": 8},
        "colony": {"solution_count": 12, "threshold": 4350, "free_ann_slots": 8388608,
                   "max_children_per_parent": 0},
        "task": {"source": "live-feed", "scoring": "rolling-frame", "data_hash": "67" * 32,
                 "hash_verified": True, "source_ref": "v1.313.0", "window_width": 8760,
                 "score_max": 8760, "shift_cap": 168, "shift_limit": 2, "advance_threshold": 3504,
                 "base_hour": BASE, "base_at": 1794988800, "anchor_tick": 91911000,
                 "target_zeros": 4394, "target_ones": 4370, "target_unknown": 0,
                 "oracle": {"synced": True, "querying": False, "next_hour": BASE + 3,
                            "next_at": 1795000000, "next_due_at": 1795000300,
                            "failed_attempts": 0, "filled": 1}},
        "btc": {"epoch": 240, "hours": _hours()},
        "epoch": {"epoch": 240, "tick": 91911000, "initial_tick": 91910000, "ticks_into_epoch": 1000},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.server as server

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    store = Store(str(tmp_path / "qdr.db"))
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_mining_cache",
                        {"at": 0.0, "data": None, "prev_count": None, "prev_at": 0.0})
    monkeypatch.setattr(server, "get_client", lambda: None)
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _bundle())
    c = TestClient(server.app)
    c.store = store
    return c


def _keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}", k
            yield from _keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _keys(v, f"{path}[{i}]")


def test_prices_leave_only_the_btc_endpoints_and_always_with_their_source(client):
    """Since 2026-11-18 (the user's decision) the BTC page shows the hourly
    BTC/USDT price Qubic's Price oracle stored on-chain. It is Binance data,
    so it never appears without its attribution -- and /v1/mining, which every
    live page polls, carries none."""
    for where, key in _keys(client.get("/v1/mining").json()):
        assert "price" not in key.lower(), f"/v1/mining carries a price field at {where}"
    for url in ("/v1/btc", "/v1/btc/summary", "/v1/btc/overview"):
        body = client.get(url).json()
        assert "Binance" in body["price_source"] and "oracle" in body["price_source"], url
        for where, key in _keys(body):
            assert "volume" not in key.lower(), f"{url} carries a volume field at {where}"


def test_the_hours_ride_on_btc_only(client):
    mining = client.get("/v1/mining").json()
    assert "btc" not in mining and "hours" not in mining
    btc = client.get("/v1/btc").json()
    assert btc["status"] == "live"
    assert [h["hour"] for h in btc["hours"] if h["live"]] == [BASE + 1, BASE + 2]
    assert btc["task"]["shift_limit"] == 2 and btc["oracle"]["next_hour"] == BASE + 3
    assert btc["node"]["ip"] == "203.0.113.7"


def test_compact_carries_only_the_live_hours_and_no_record(client):
    body = client.get("/v1/btc?compact=1").json()
    assert body["record"] is None
    assert body["hours"] and all(h["live"] for h in body["hours"])


def test_without_a_recorded_call_the_reason_is_given_not_a_guess(client):
    call = client.get("/v1/btc").json()["call"]
    assert call["available"] is False and call["reason"] == "no-tracker"
    assert "call" not in call and call["detail"]


def test_no_node_still_serves_the_record(client, monkeypatch):
    client.store.put_btc_hours(240, _hours(), seen_at=1)
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: {"status": "unavailable", "reason": "x"})
    body = client.get("/v1/btc").json()
    assert body["status"] == "unavailable"
    assert [h["hour"] for h in body["record"]["hours"]] == [BASE + 1, BASE + 2]


def test_live_hours_are_recorded_once_and_never_rewritten(tmp_path):
    store = Store(str(tmp_path / "qdr.db"))
    assert store.put_btc_hours(240, _hours(), seen_at=100) == 2      # frame hours skipped
    assert store.put_btc_hours(240, _hours(), seen_at=200) == 0
    rows = store.btc_hours(epoch=240)
    assert [(r["hour"], r["up"], r["filled"], r["first_seen_at"]) for r in rows] == [
        (BASE + 1, 0, 0, 100), (BASE + 2, 1, 1, 100)]
    assert store.btc_epochs() == [{"epoch": 240, "hours": 2, "ups": 1, "filled": 1,
                                   "first_hour": BASE + 1, "last_hour": BASE + 2}]


def test_the_mining_sample_records_the_hours(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "qdr.db"))
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _bundle())
    assert pipeline.sample_mining(store) is not None
    assert [r["hour"] for r in store.btc_hours()] == [BASE + 1, BASE + 2]


# -- tick stream: accepted vs rejected ----------------------------------------

# Real ANT_SOLU payload from epoch 239 (tick 91,800,000): anchor 91,799,992,
# score 3574, result 0 (Valid).
ACCEPTED_HEX = ("414e545f534f4c55688c6b8c44e28e60984797e2770d9e0b8ee9fbd1caf217beedb59ce30fea1e60"
                "01210007a5962d9f66489c6676636b752b9a5c74a4b7be5b5dc1d1f3550ad02ebcc1780501000000"
                "b8c17805f60d000000000000")


def _sol_log(hexdata: str, tx: str) -> dict:
    return {"logTypename": "CUSTOM_MESSAGE", "type": 255, "txHash": tx, "body": {"hex": hexdata}}


def test_solution_outcome_reads_score_and_result():
    assert tickstream.solution_outcome({"body": {"hex": ACCEPTED_HEX}}) == (3574, 0)
    assert tickstream.solution_outcome({"body": {"hex": ACCEPTED_HEX[:40]}}) is None


def test_a_tick_counts_accepted_and_rejected_solutions():
    rejected = ACCEPTED_HEX[:-16] + "00000000" + "06000000"   # score 0, result 6 (RejectLeParent)
    summary, _ = tickstream.condense({"tick": 1, "logs": [
        _sol_log(ACCEPTED_HEX, "a"), _sol_log(rejected, "b")]})
    assert summary["solutions"] == 2 and summary["sol_ok"] == 1 and summary["sol_best"] == 3574


# -- the page -------------------------------------------------------------------

def html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_is_linked_from_every_sibling():
    for name in ("index.html", "burn.html", "price.html", "mining.html", "ticks.html"):
        src = (DASH / name).read_text(encoding="utf-8")
        assert 'href="./btc.html"' in src, f"{name} does not link the BTC page"
    # The label: in the markup and in both dictionaries (ticks keeps its
    # dictionaries in ticks.js).
    for n in ("index.html", "burn.html", "price.html", "mining.html"):
        assert (DASH / n).read_text(encoding="utf-8").count("btcLive") >= 3, f"{n}: label missing"
    assert (DASH / "ticks.js").read_text(encoding="utf-8").count("btcLive") >= 2


def test_page_links_back_to_the_others():
    src = html()
    for href in ("./", "./burn.html", "./price.html", "./mining.html", "./ticks.html", "./how-it-works.html"):
        assert f'href="{href}"' in src, f"nav is missing {href}"
    assert 'href="./btc.html" aria-current="page"' in src


def _dict_keys(block: str) -> set[str]:
    return set(re.findall(r"^\s*(\w+):", block, re.M)) | set(re.findall(r",\s*(\w+):", block))


def test_both_languages_are_complete_and_cover_the_markup():
    src = html()
    en = _dict_keys(src[src.index("en: {") + 5:src.index("de: {")])
    de = _dict_keys(src[src.index("de: {") + 5:src.index("var lang =")])
    assert en - de == set(), f"missing in DE: {sorted(en - de)}"
    assert de - en == set(), f"missing in EN: {sorted(de - en)}"
    used = set(re.findall(r'data-i18n="(\w+)"', src))
    assert used - en == set(), f"untranslated: {sorted(used - en)}"


def test_every_api_path_the_page_calls_exists():
    import api.server as server

    routes = {getattr(r, "path", "") for r in server.app.routes}
    called = set(re.findall(r'apiUrl\("(/[^"?]+)', html()))
    called |= set(re.findall(r'"(/v1/[a-z/]+)\?', html()))
    assert {"/v1/btc", "/v1/ticks/recent"} <= called
    for path in called:
        assert path in routes, f"btc.html calls {path}, which the server does not serve"


def test_no_third_party_code_or_fonts():
    for tag in re.findall(r"<(?:script|link)[^>]+(?:src|href)=\"([^\"]+)\"", html()):
        assert not tag.startswith(("http://", "https://", "//")), f"external resource: {tag}"


def test_the_page_names_the_source_of_its_prices():
    src = html()
    assert "Binance" in src and "price_source" in src
