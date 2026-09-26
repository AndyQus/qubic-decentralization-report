"""Stored trend lines: the lifecycle and the endpoint (CONCEPT_TRENDLINES §5).

A line the worker sets keeps its anchors for good and can only END -- broken by
two closes beyond it, or replaced when a new swing made a better one. That is
what makes the table an archive, and these tests pin it:

  * running the update twice on the same bars changes nothing;
  * a break ends the line at the first bar of the break, and never revives it;
  * a better line replaces the old one and says which replaced it;
  * the channel is derived when served, never stored;
  * the endpoint only reads, and says `ready: false` rather than drawing a line
    from too little.

No test here touches the network.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import pipeline  # noqa: E402
from qdr.store import Store  # noqa: E402

H = 3600
# Hours end well before now: price_hours holds finished hours only, and the
# day scale drops the running day.
T0 = (int(time.time()) // 86400 - 20) * 86400


def channel_bars(n, top=1e-6, slope=-1e-9, width=1e-7, period=24, start=0):
    """A falling channel in USD/QU, the shape the first real week showed."""
    out = []
    for i in range(start, start + n):
        upper = top + slope * i
        phase = (math.cos(2 * math.pi * i / period) + 1) / 2
        mid = upper - width * (1 - phase)
        out.append({"at": T0 + i * H, "high": mid, "low": mid - 1e-8,
                    "close": mid - 5e-9})
    return out


def seed_hours(store: Store, bars) -> None:
    with store._tx() as c:
        for b in bars:
            c.execute(
                "INSERT OR REPLACE INTO price_hours (at,open,high,low,close,"
                "avg_price,market_cap,moves,polls,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (b["at"], b["close"], b["high"], b["low"], b["close"],
                 b["close"], 0, 1, 60, b["at"] + H))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db", allow_fallback=False)
    yield s
    s.close()


def active(store, scale="hour"):
    return {r["kind"]: r for r in store.trendlines(scale=scale, status="active")}


# -- lifecycle --------------------------------------------------------------

def test_first_run_sets_resistance_and_support(store):
    seed_hours(store, channel_bars(120))
    out = pipeline.update_trendlines(store, now=T0 + 130 * H)
    assert out["hour"]["found"] == 2
    got = active(store)
    assert set(got) == {"resistance", "support"}
    assert got["resistance"]["touches"] >= 3
    assert got["resistance"]["t1"] < got["resistance"]["t2"]


def test_running_twice_changes_nothing(store):
    seed_hours(store, channel_bars(120))
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    before = store.trendlines()
    out = pipeline.update_trendlines(store, now=T0 + 131 * H)
    assert store.trendlines() == before
    assert all(not any(c.values()) for c in out.values())


def test_a_break_ends_the_line_at_its_first_bar_and_it_stays_broken(store):
    bars = channel_bars(120)
    seed_hours(store, bars)
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    res = active(store)["resistance"]

    # Two closes well above the resistance.
    line = pipeline._as_line(res)
    brk = []
    for j, i in enumerate((120, 121)):
        p = line.at(T0 + i * H) * 1.05
        brk.append({"at": T0 + i * H, "high": p * 1.01, "low": p * 0.99, "close": p})
    seed_hours(store, brk)
    out = pipeline.update_trendlines(store, now=T0 + 131 * H)

    assert out["hour"]["broken"] >= 1
    row = store.trendline_by_anchors("hour", "resistance", res["t1"], res["t2"])
    assert row["status"] == "broken"
    assert row["ended_at"] == T0 + 120 * H
    # And the same anchors are never active again.
    pipeline.update_trendlines(store, now=T0 + 132 * H)
    again = store.trendline_by_anchors("hour", "resistance", res["t1"], res["t2"])
    assert again["status"] == "broken"


def test_a_better_line_replaces_the_old_one_and_names_it(store):
    seed_hours(store, channel_bars(80))
    pipeline.update_trendlines(store, now=T0 + 90 * H)
    old = active(store)["resistance"]
    # More of the same channel: new swings confirm and may yield a line with
    # more touches. Whatever is chosen, a change of anchors is a replacement.
    seed_hours(store, channel_bars(80, start=80))
    pipeline.update_trendlines(store, now=T0 + 170 * H)
    new = active(store)["resistance"]
    if (new["t1"], new["t2"]) == (old["t1"], old["t2"]):
        assert new["touches"] >= old["touches"]
    else:
        ended = store.trendline_by_anchors("hour", "resistance", old["t1"], old["t2"])
        assert ended["status"] == "replaced"
        assert ended["replaced_by"] == new["id"]
        assert ended["ended_at"] == T0 + 170 * H


def test_anchors_are_never_rewritten(store):
    seed_hours(store, channel_bars(120))
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    first = {r["id"]: (r["t1"], r["p1"], r["t2"], r["p2"]) for r in store.trendlines()}
    seed_hours(store, channel_bars(60, start=120))
    pipeline.update_trendlines(store, now=T0 + 190 * H)
    for r in store.trendlines():
        if r["id"] in first:
            assert (r["t1"], r["p1"], r["t2"], r["p2"]) == first[r["id"]]


def test_channel_lines_are_never_stored(store):
    seed_hours(store, channel_bars(120))
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    kinds = {r["kind"] for r in store.trendlines()}
    assert kinds <= {"resistance", "support"}


def test_too_few_bars_stores_nothing(store):
    seed_hours(store, channel_bars(30))
    pipeline.update_trendlines(store, now=T0 + 40 * H)
    assert store.trendlines(scale="hour") == []


def test_replaced_line_found_again_is_revived(store):
    seed_hours(store, channel_bars(120))
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    res = active(store)["resistance"]
    store.end_trendline(res["id"], "replaced", T0 + 130 * H)
    out = pipeline.update_trendlines(store, now=T0 + 131 * H)
    assert out["hour"]["revived"] == 1
    assert active(store)["resistance"]["id"] == res["id"]


# -- endpoint ---------------------------------------------------------------

def _client(store: Store) -> TestClient:
    import api.server as server
    server._store = store
    server._price_trend_cache.clear()
    return TestClient(server.app)


def test_endpoint_serves_lines_and_a_derived_channel(store):
    seed_hours(store, channel_bars(120))
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    r = _client(store).get("/v1/price/trendlines?scale=hour")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert {ln["kind"] for ln in body["lines"]} == {"resistance", "support"}
    assert body["shape"] == "channel"
    assert "mid" in {ln["kind"] for ln in body["channel"]}
    assert body["recording_since"] == T0 + 130 * H
    assert "forecast" in body["note"]


def test_endpoint_archive_includes_ended_lines(store):
    seed_hours(store, channel_bars(120))
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    res = active(store)["resistance"]
    store.end_trendline(res["id"], "broken", T0 + 125 * H)
    c = _client(store)
    act = c.get("/v1/price/trendlines?scale=hour").json()
    assert all(ln["status"] == "active" for ln in act["lines"])
    everything = c.get("/v1/price/trendlines?scale=hour&status=all").json()
    broken = [ln for ln in everything["lines"] if ln["status"] == "broken"]
    assert broken and broken[0]["broke"] == "up"


def test_endpoint_says_not_ready_instead_of_guessing(store):
    seed_hours(store, channel_bars(120))   # 5 days: too few for the day scale
    pipeline.update_trendlines(store, now=T0 + 130 * H)
    body = _client(store).get("/v1/price/trendlines?scale=day").json()
    assert body["ready"] is False
    assert body["lines"] == []
    assert body["bars_needed"] == 30


def test_endpoint_without_price_history_is_503(store):
    r = _client(store).get("/v1/price/trendlines?scale=hour")
    assert r.status_code == 503


def test_endpoint_only_reads(store):
    seed_hours(store, channel_bars(120))
    c = _client(store)
    c.get("/v1/price/trendlines?scale=hour")
    c.get("/v1/price/trendlines?scale=short&status=all")
    assert store.trendlines() == []


def test_endpoint_rejects_unknown_scale(store):
    seed_hours(store, channel_bars(120))
    assert _client(store).get("/v1/price/trendlines?scale=minute").status_code == 422
