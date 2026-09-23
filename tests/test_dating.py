"""Dating ticks by measurement — the arithmetic that decides a burn's day.

The burn scanner used to believe no tick could be dated after the fact, so it
skipped anything it had not watched live. `/v1/ticks/{t}/tick-data` disproves
that, and these tests pin the three ways the replacement could go wrong quietly:

  * a day boundary computed in LOCAL time instead of UTC (this bug was real:
    mktime put a whole backfilled day under the previous date);
  * a skipped tick read as a date rather than as a hole;
  * a bisection that walks ticks instead of halving them, which would turn a
    backfill from ~90 lookups into ~500,000 and never finish.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr import dating


def _chain(start_day="2026-09-17", rate=1.55, lo=1000, days=3.5, skip_every=97):
    """A synthetic chain: monotonic timestamps, with periodic skipped ticks.

    Mirrors what the live node actually returns (measured 2026-09-20): dense,
    strictly increasing, occasional empty ticks.
    """
    base = dating.day_start(start_day)
    hi = lo + int(days * 86400 * rate)
    calls = {"n": 0}

    def ts_of(t):
        calls["n"] += 1
        if t < lo or t > hi:
            return None
        if skip_every and t % skip_every == 0:
            return None
        return base + int((t - lo) / rate)

    return ts_of, lo, hi, calls


def test_day_start_is_utc_not_local():
    """The bug that shifted a whole day. mktime reads local time; timegm is UTC."""
    for day in ("2026-09-17", "2026-01-01", "2026-06-30"):
        assert dating.day_of(dating.day_start(day)) == day
    # midnight exactly, and one second before, must be different days
    ms = dating.day_start("2026-09-18")
    assert dating.day_of(ms) == "2026-09-18"
    assert dating.day_of(ms - 1) == "2026-09-17"


def test_boundaries_are_contiguous_and_correctly_dated():
    ts_of, lo, hi, _ = _chain()
    ranges = dating.day_boundaries(ts_of, lo, hi)
    assert [r["day"] for r in ranges] == [
        "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]
    # no gaps, no overlaps: every tick belongs to exactly one day
    for a, b in zip(ranges, ranges[1:]):
        assert b["from_tick"] == a["to_tick"] + 1
    # and each range's own endpoints really carry that day
    for r in ranges:
        for probe in (r["from_tick"], r["to_tick"]):
            ts = ts_of(probe)
            if ts:
                assert dating.day_of(ts) == r["day"]


def test_boundary_tick_is_the_first_of_its_day():
    """Off by one tick here misfiles every burn in that tick."""
    ts_of, lo, hi, _ = _chain()
    for r in dating.day_boundaries(ts_of, lo, hi)[1:]:
        first = r["from_tick"]
        assert dating.day_of(ts_of(first)) == r["day"]
        prev = ts_of(first - 1)
        assert prev is None or dating.day_of(prev) < r["day"]


def test_bisection_stays_logarithmic():
    """A linear walk would 'work' and be unusable: ~0.5 s per live lookup."""
    ts_of, lo, hi, calls = _chain()
    dating.day_boundaries(ts_of, lo, hi)
    span = hi - lo
    assert calls["n"] < 400, f"{calls['n']} lookups over {span:,} ticks"


def test_skipped_ticks_are_holes_not_dates():
    """An empty tick carries no timestamp; the probe must look beside it."""
    ts_of, lo, hi, _ = _chain(skip_every=3)      # two thirds of ticks empty
    ranges = dating.day_boundaries(ts_of, lo, hi)
    assert [r["day"] for r in ranges][0] == "2026-09-17"
    for a, b in zip(ranges, ranges[1:]):
        assert b["from_tick"] == a["to_tick"] + 1


def test_undated_range_returns_empty_not_a_guess():
    """Bob no longer retains it -> say nothing, rather than invent a day."""
    assert dating.day_boundaries(lambda t: None, 1, 5000) == []


def test_find_first_tick_returns_none_when_range_is_too_early():
    """None means 'no tick reaches the target', never 'the last tick'."""
    ts_of, lo, hi, _ = _chain()
    far_future = dating.day_start("2027-01-01")
    assert dating.find_first_tick_at_or_after(ts_of, far_future, lo, hi) is None


def test_single_day_range_is_one_bucket():
    ts_of, lo, hi, _ = _chain(days=0.4)
    ranges = dating.day_boundaries(ts_of, lo, hi)
    assert len(ranges) == 1
    assert ranges[0]["from_tick"] == lo and ranges[0]["to_tick"] == hi


# -- the epoch clock ----------------------------------------------------------
#
# An epoch is a week, Wednesday 12:00 UTC to Wednesday 12:00 UTC. The repo once
# called it "~4.4 days", reasoned from tick counts at an assumed tick rate.

def _utc(*t):
    import calendar
    return calendar.timegm(t + (0,) * (6 - len(t)))


def test_epoch_starts_on_wednesday_noon_utc():
    import time as _time
    for e in (220, 231, 232, 240):
        tm = _time.gmtime(dating.epoch_start(e))
        assert (tm.tm_wday, tm.tm_hour, tm.tm_min) == (2, 12, 0)


def test_epoch_start_matches_measured_first_ticks():
    # first ticks measured on the live network: 231 at 12:10, 232 at 12:17 UTC
    assert dating.epoch_start(231) == _utc(2026, 9, 16, 12)
    assert dating.epoch_start(232) == _utc(2026, 9, 23, 12)
    assert dating.epoch_start(233) - dating.epoch_start(232) == 7 * 86400


def test_epoch_progress_runs_on_the_clock_and_clamps():
    start = dating.epoch_start(232)
    assert dating.epoch_progress(232, start) == 0.0
    assert dating.epoch_progress(232, start + 3.5 * 86400) == 0.5
    # the switch takes minutes: an overrunning epoch reads full, not past it
    assert dating.epoch_progress(231, start + 600) == 1.0
    assert dating.epoch_progress(232, start - 600) == 0.0
