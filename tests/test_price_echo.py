"""A frozen upstream backend, and the hourly record -- both pinned.

On 2026-09-29 one of the backends behind `/v1/latest-stats` stood still at
4.58e-7 for sixteen hours while the other followed the market; the worker read
them in turn and the chart drew a comb. These tests hold the rules that take
such echoes out (qdr/price_echo.py) without touching real moves, and the
rollup fix found on the way: hours older than the worker's 48 h rollup window
had been left summarising only their last minutes.

No test here touches the network.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import pipeline, price_echo  # noqa: E402
from qdr.store import Store  # noqa: E402

FROZEN = 4.58e-7
T0 = 1_790_640_000          # 2026-09-29 00:00 UTC, hour-aligned


def _store() -> Store:
    return Store(Path(tempfile.mkdtemp(prefix="qdr_echo_")) / "qdr.db")


def _rows(seq: list[tuple[int, float]]) -> list[dict]:
    return [{"at": at, "until": at, "price": p} for at, p in seq]


def _incident(minutes: int = 120, start: int = T0 + 3600) -> list[tuple[int, float]]:
    """The real price climbing slowly, the frozen one answering every other minute."""
    out = [(start - 60 * k, FROZEN) for k in range(30, 0, -1)]   # before: real == frozen
    for m in range(minutes):
        real = 4.64e-7 + 1e-9 * (m // 30)
        out.append((start + 60 * m, FROZEN if m % 2 else real))
    return out


# -- the detector -----------------------------------------------------------

def test_the_frozen_side_of_an_alternation_is_found_and_only_it():
    seq = _incident()
    rows = _rows(seq)
    bad = price_echo.frozen_rows(rows)
    assert bad, "a sixteen-hour comb must be recognised"
    assert {rows[i]["price"] for i in bad} == {FROZEN}
    # The readings from before the incident, when the market really stood at
    # the value that later froze, are not echoes.
    assert min(rows[i]["at"] for i in bad) >= T0 + 3600


def test_flipping_between_neighbouring_prices_is_market_noise():
    seq = [(T0 + 60 * m, 4.57e-7 if m % 2 else 4.58e-7) for m in range(240)]
    assert price_echo.frozen_rows(_rows(seq)) == []


def test_a_single_spike_and_recovery_is_a_real_move():
    seq = [(T0 + 60 * m, 5.0e-7) for m in range(30)]
    seq += [(T0 + 60 * 30, 5.4e-7), (T0 + 60 * 31, 5.0e-7)]
    seq += [(T0 + 60 * m, 5.0e-7) for m in range(32, 60)]
    assert price_echo.frozen_rows(_rows(seq)) == []


def test_a_new_price_taking_turns_with_an_old_one_is_not_the_echo():
    """Of the two values, the one on record longer is the one that stopped."""
    rows = _rows(_incident())
    bad = price_echo.frozen_rows(rows)
    assert all(rows[i]["price"] == FROZEN for i in bad)


# -- the store: set aside, heal ---------------------------------------------

def test_rejecting_an_echo_merges_the_real_intervals_around_it():
    s = _store()
    for at, p in [(T0, 5e-7), (T0 + 60, 5e-7), (T0 + 120, FROZEN),
                  (T0 + 180, 5e-7), (T0 + 240, 5e-7)]:
        with mock.patch("time.time", return_value=at + 5):
            s.put_price_reading(at=at, price=p, market_cap=1)
    s.reject_prices([T0 + 120], "test")
    pts = s.price_series()
    assert [(p["at"], p["until"], p["polls"], p["price"]) for p in pts] == \
        [(T0, T0 + 240, 4, 5e-7)]
    assert s.price_rejected_count() == 1
    assert s.price_coverage()["rejected"] == 1


def test_rejecting_an_echo_between_two_prices_bridges_the_minutes_it_held():
    s = _store()
    for at, p in [(T0, 5e-7), (T0 + 60, FROZEN), (T0 + 120, FROZEN),
                  (T0 + 180, 5.1e-7)]:
        with mock.patch("time.time", return_value=at + 5):
            s.put_price_reading(at=at, price=p, market_cap=1)
    s.reject_prices([T0 + 60], "test")
    pts = s.price_series()
    # No gap for the page to hatch, and the poll count stays the real one.
    assert [(p["at"], p["until"], p["polls"]) for p in pts] == \
        [(T0, T0 + 120, 1), (T0 + 180, T0 + 180, 1)]


# -- the worker, minute by minute -------------------------------------------

def test_the_worker_takes_an_ongoing_incident_out_as_it_happens():
    s = _store()
    seq = _incident(minutes=180)
    shown_frozen = 0
    for at, p in seq:
        with mock.patch("time.time", return_value=at + 5):
            s.put_price_reading(at=at, price=p, market_cap=1)
            pipeline.reject_price_echoes(s, since=at - 86400)
            if at >= T0 + 3600 and s.latest_price()["price"] == FROZEN:
                shown_frozen += 1
    left = [p for p in s.price_series() if p["at"] >= T0 + 3600 and p["price"] == FROZEN]
    assert left == []
    # It takes a few returns to be sure; after that no echo stands as "now".
    assert shown_frozen <= 5


# -- the hourly record ------------------------------------------------------

def test_a_rolling_rollup_never_cuts_an_hour_down_to_its_tail():
    """Regression: `since` inside an hour used to refold that hour from the
    rows after `since` only, so every hour aged out of the worker's 48 h
    window kept only its final minutes."""
    s = _store()
    prices = [4.0e-7, 4.4e-7, 4.2e-7, 4.1e-7]      # high early in the hour
    for m in range(60):
        with mock.patch("time.time", return_value=T0 + 60 * m + 5):
            s.put_price_reading(at=T0 + 60 * m, price=prices[m // 15], market_cap=1)
    with mock.patch("time.time", return_value=T0 + 7200):
        s.rollup_price_hours()
        whole = s.price_series(resolution="hour")[0]
        s.rollup_price_hours(since=T0 + 50 * 60)
        again = s.price_series(resolution="hour")[0]
    assert whole["high"] == 4.4e-7 and whole["open"] == 4.0e-7
    assert again == whole


def test_repair_is_idempotent_and_replays_lines_only_when_hours_changed():
    s = _store()
    for at, p in _incident(minutes=180):
        with mock.patch("time.time", return_value=at + 5):
            s.put_price_reading(at=at, price=p, market_cap=1)
    with mock.patch("time.time", return_value=T0 + 6 * 3600):
        s.rollup_price_hours()
        first = pipeline.repair_price_history(s)
        second = pipeline.repair_price_history(s)
    assert first["echo_from"] is not None and first["hours_changed"] > 0
    assert second == {**second, "echo_from": None, "hours_changed": 0,
                      "trendlines": None}
    lows = [h["low"] for h in s.price_series(resolution="hour") if h["at"] >= T0 + 3600]
    assert FROZEN not in lows
