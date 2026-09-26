"""Trend-line rules, pinned (docs/CONCEPT_TRENDLINES.de.md §3-§5).

The lines are stored with fixed anchors and archived as held or broken, so the
rule that finds them has to be deterministic, and it has to behave like a hand
drawing one. These tests cover the properties that make both true:

  * a line lies ON the bars -- no high above a resistance, no low below a
    support, between its anchors;
  * its anchors are swing points far apart, never two neighbouring bars and
    never the bar still being written;
  * a bar that makes no new swing does not move the line (the first replay on
    real data moved it almost every hour, which made storing it pointless);
  * a wick through the line is not a break, two closes beyond it are;
  * converging lines get no channel, and too little data gets no lines at all.

No test here touches the network.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import trendlines as T  # noqa: E402

H = 3600
T0 = 1_790_000_000 // H * H


def bars_from(highs, lows=None, closes=None):
    lows = lows or [h * 0.99 for h in highs]
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    return [{"at": T0 + i * H, "high": h, "low": l, "close": c}
            for i, (h, l, c) in enumerate(zip(highs, lows, closes))]


def channel_series(n=120, top=1.0, slope=-0.001, width=0.1, period=24):
    """A falling channel: price swings between two parallel lines."""
    highs, lows = [], []
    for i in range(n):
        upper = top + slope * i
        phase = (math.cos(2 * math.pi * i / period) + 1) / 2   # 1 at the top
        mid = upper - width * (1 - phase)
        highs.append(mid)
        lows.append(mid - 0.01)
    return bars_from(highs, lows)


def lines_by_kind(res):
    return {d["kind"]: d for d in res["lines"]}


# -- the shape of a line ----------------------------------------------------

def test_falling_channel_yields_both_lines_and_a_mid():
    res = T.detect(channel_series(), T.HOUR_SCALE)
    got = lines_by_kind(res)
    assert res["ready"]
    assert got["resistance"]["slope_pct_per_day"] < 0
    assert got["support"]["slope_pct_per_day"] < 0
    assert got["resistance"]["touches"] >= 3
    assert got["support"]["touches"] >= 3
    assert "mid" in got
    assert got["resistance"]["broken_at"] is None


def test_no_bar_crosses_a_line_between_its_anchors():
    rng = random.Random(7)
    p, highs, lows = 1.0, [], []
    for _ in range(300):
        p *= math.exp(rng.gauss(0, 0.01))
        highs.append(p * (1 + abs(rng.gauss(0, 0.003))))
        lows.append(p * (1 - abs(rng.gauss(0, 0.003))))
    bars = bars_from(highs, lows)
    for scale in (T.SHORT_SCALE, T.HOUR_SCALE):
        for d in T.detect(bars, scale)["lines"]:
            if d["kind"] not in ("resistance", "support"):
                continue
            ln = T.Line(d["kind"], d["t1"], d["p1"], d["t2"], d["p2"], d["touches"])
            for b in bars:
                if not d["t1"] <= b["at"] <= d["t2"]:
                    continue
                lp = ln.at(b["at"])
                if d["kind"] == "resistance":
                    assert b["high"] <= lp * (1 + scale.tol) + 1e-15
                else:
                    assert b["low"] >= lp * (1 - scale.tol) - 1e-15


def test_the_two_highest_neighbours_are_not_a_line():
    # Flat market with two adjacent spikes: joining them is what "connect the
    # two highest points" means literally, and it must not happen.
    highs = [1.0] * 100
    highs[50], highs[51] = 1.2, 1.19
    res = T.detect(bars_from(highs), T.HOUR_SCALE)
    for d in res["lines"]:
        assert (d["t2"] - d["t1"]) // H >= T.HOUR_SCALE.min_sep


def test_anchors_are_never_the_newest_unconfirmed_bars():
    bars = channel_series(n=100)
    cutoff = bars[-T.HOUR_SCALE.k]["at"]
    for d in T.detect(bars, T.HOUR_SCALE)["lines"]:
        assert d["t2"] < cutoff


def test_a_bar_without_a_new_swing_does_not_move_the_line():
    bars = channel_series(n=110)
    before = lines_by_kind(T.detect(bars[:100], T.HOUR_SCALE))
    after = lines_by_kind(T.detect(bars[:101], T.HOUR_SCALE))
    for kind in ("resistance", "support"):
        assert (before[kind]["t1"], before[kind]["t2"]) == \
               (after[kind]["t1"], after[kind]["t2"])


def test_detection_is_deterministic():
    bars = channel_series()
    assert T.detect(bars, T.HOUR_SCALE) == T.detect(list(bars), T.HOUR_SCALE)


# -- channel or not ---------------------------------------------------------

def test_converging_lines_get_no_channel():
    # Symmetric triangle: highs fall, lows rise.
    highs, lows = [], []
    for i in range(150):
        amp = 0.2 * (1 - i / 180)
        phase = math.cos(2 * math.pi * i / 24)
        highs.append(1.0 + amp * max(phase, 0) + 0.001)
        lows.append(1.0 - amp * max(-phase, 0) - 0.001)
    got = lines_by_kind(T.detect(bars_from(highs, lows), T.HOUR_SCALE))
    assert got["resistance"]["slope_pct_per_day"] < 0
    assert got["support"]["slope_pct_per_day"] > 0
    assert "mid" not in got and "parallel" not in got


# -- breaks -----------------------------------------------------------------

def test_a_wick_through_the_line_is_not_a_break_two_closes_are():
    ln = T.Line("resistance", T0, 1.0, T0 + 10 * H, 1.0, 2)
    wick = [{"at": T0 + 11 * H, "high": 1.2, "low": 0.95, "close": 0.99},
            {"at": T0 + 12 * H, "high": 0.99, "low": 0.95, "close": 0.98}]
    assert T.broken_at(ln, wick, T.HOUR_SCALE) is None
    one = [{"at": T0 + 11 * H, "high": 1.1, "low": 1.0, "close": 1.05},
           {"at": T0 + 12 * H, "high": 1.0, "low": 0.95, "close": 0.97}]
    assert T.broken_at(ln, one, T.HOUR_SCALE) is None
    two = [{"at": T0 + 11 * H, "high": 1.1, "low": 1.0, "close": 1.05},
           {"at": T0 + 12 * H, "high": 1.1, "low": 1.0, "close": 1.06}]
    assert T.broken_at(ln, two, T.HOUR_SCALE) == T0 + 11 * H


def test_support_breaks_downwards():
    ln = T.Line("support", T0, 1.0, T0 + 10 * H, 1.0, 2)
    two = [{"at": T0 + 11 * H, "high": 1.0, "low": 0.9, "close": 0.95},
           {"at": T0 + 12 * H, "high": 1.0, "low": 0.9, "close": 0.94}]
    assert T.broken_at(ln, two, T.HOUR_SCALE) == T0 + 11 * H


# -- honesty about too little data -----------------------------------------

def test_too_few_bars_draws_nothing_and_says_how_many_are_missing():
    res = T.detect(channel_series(n=50), T.HOUR_SCALE)
    assert res["ready"] is False
    assert res["lines"] == []
    assert res["bars_on_record"] == 50
    assert res["bars_needed"] == T.HOUR_SCALE.ready_bars


# -- building blocks --------------------------------------------------------

def test_a_plateau_is_one_pivot_at_its_first_bar():
    highs = [1.0, 1.0, 1.0, 1.5, 1.5, 1.5, 1.0, 1.0, 1.0, 1.0]
    assert T.pivots(bars_from(highs), 3, "high") == [3]


def test_daily_bars_fold_hours_into_utc_days():
    day = 86400
    start = 1_790_000_000 // day * day
    hours = [{"at": start + i * H, "high": 1 + i, "low": 1 - i / 100,
              "close": 10 + i} for i in range(30)]
    days = T.daily_bars(hours)
    assert [d["at"] for d in days] == [start, start + day]
    assert days[0]["high"] == 24 and days[0]["close"] == 33
    assert days[0]["low"] == 1 - 23 / 100
    assert days[1]["high"] == 30 and days[1]["close"] == 39
