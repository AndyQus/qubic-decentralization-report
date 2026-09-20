"""Dating ticks by measurement, so historical burns can be bucketed by day.

The burn scanner was built around a constraint that turned out to be only half
true. Tick logs carry no timestamp — that part still holds, and no amount of
reading a log entry will date it. From that the scanner concluded it could only
count burns it watched happen, dating them by the day it observed them, and that
anything older was undatable and had to be skipped (see `pipeline.scan_burns`
and the note in `burn.event_day`).

The missing piece was the RPC route. `/v2/ticks/{t}` is Not Found, which is what
the earlier measurement recorded; **`/v1/ticks/{t}/tick-data` answers**, and it
carries a real wall-clock timestamp for every tick Bob still retains. Measured
2026-09-20 over epoch 231 and back into 230, timestamps are present, in
milliseconds, and strictly increasing.

That changes what the project can honestly publish: a burn in a week-old tick
can be filed under the day it actually happened, measured, not inferred from an
assumed tick rate. (The assumed rate would have been wrong anyway — the code
reasoned from ~2.7 ticks/s, and the measured rate over three days is 1.55.)

The cost is latency, not correctness: one lookup is ~0.5 s against the public
RPC, so dating 500,000 ticks one by one would take days. It is never necessary.
Timestamps increase monotonically, so the tick where a day begins is found by
**bisection** — ~20 lookups per boundary instead of 130,000 — and every tick
between two boundaries belongs to that day by construction. A backfill of a
whole epoch needs a few dozen lookups, not a few hundred thousand.

Skipped ticks (`tickData: {}`) are normal and carry no timestamp. A probe that
lands on one walks outward to the nearest tick that has data rather than
treating the hole as an answer.
"""
from __future__ import annotations

import calendar
import time
from typing import Callable, Optional

# How far to walk away from a probe that landed on a skipped tick before giving
# up on that probe. Measured 2026-09-20: runs of empty ticks at the head of an
# epoch reach a few dozen; mid-epoch they are rare and short. 64 covers both
# without letting a bisection step degenerate into a linear scan.
MAX_PROBE_WALK = 64

SECONDS_PER_DAY = 86400


def day_of(ts: int) -> str:
    """UTC day of a unix second, as 'YYYY-MM-DD'."""
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def day_start(day: str) -> int:
    """Unix second of 00:00:00 UTC on 'YYYY-MM-DD'.

    `calendar.timegm`, not `time.mktime`: mktime reads the struct as LOCAL time.
    On this machine (UTC+2) that shifted every boundary two hours early, which
    put the whole first day of a backfill under the previous date — a silent
    off-by-one-day in published figures, and exactly the class of error this
    module exists to prevent. Caught by the boundary test, which is why it
    asserts the day of each range's own endpoints rather than trusting the walk.
    """
    return calendar.timegm(time.strptime(day, "%Y-%m-%d"))


def _nearest_dated(ts_of: Callable[[int], Optional[int]], tick: int,
                   lo: int, hi: int) -> tuple[Optional[int], Optional[int]]:
    """The closest tick to `tick` within [lo, hi] that carries a timestamp.

    Returns (tick, timestamp) or (None, None). A skipped tick is not an error
    and not a date — it is a hole, and the honest response is to look beside it.
    """
    for step in range(MAX_PROBE_WALK):
        for cand in ((tick + step), (tick - step)) if step else (tick,):
            if cand < lo or cand > hi:
                continue
            ts = ts_of(cand)
            if ts:
                return cand, ts
    return None, None


def find_first_tick_at_or_after(
    ts_of: Callable[[int], Optional[int]],
    target_ts: int,
    lo: int,
    hi: int,
) -> Optional[int]:
    """Lowest tick in [lo, hi] whose timestamp is >= target_ts, by bisection.

    `ts_of` maps a tick to its unix second or None (skipped / unknown). Relies
    only on timestamps being non-decreasing, which is measured, not assumed.

    Returns None when no tick in the range reaches `target_ts` — i.e. the whole
    range predates the target. A caller must not read that as tick `hi`.
    """
    lo, hi = int(lo), int(hi)
    if lo > hi:
        return None

    hi_tick, hi_ts = _nearest_dated(ts_of, hi, lo, hi)
    if hi_ts is None or hi_ts < target_ts:
        return None                       # even the end of the range is too early

    lo_tick, lo_ts = _nearest_dated(ts_of, lo, lo, hi)
    if lo_ts is not None and lo_ts >= target_ts:
        return lo_tick                    # the range starts already past target

    best = hi_tick
    left, right = lo, hi
    while left <= right:
        mid = (left + right) // 2
        probe, ts = _nearest_dated(ts_of, mid, left, right)
        if ts is None:
            break                         # no dated tick in the window; stop here
        if ts >= target_ts:
            best = probe
            right = probe - 1
        else:
            left = probe + 1
    return best


def day_boundaries(
    ts_of: Callable[[int], Optional[int]],
    lo: int,
    hi: int,
) -> list[dict]:
    """Tick ranges for each UTC day covered by [lo, hi].

    Returns [{day, from_tick, to_tick}], contiguous and in order. Each range is
    derived from measured boundaries, so every tick inside it provably belongs
    to that day — no tick-rate assumption anywhere.

    Returns [] when the range carries no dated tick at all, which is the honest
    answer for a range Bob no longer retains.
    """
    lo, hi = int(lo), int(hi)
    lo_tick, lo_ts = _nearest_dated(ts_of, lo, lo, hi)
    hi_tick, hi_ts = _nearest_dated(ts_of, hi, lo, hi)
    if lo_ts is None or hi_ts is None:
        return []

    out: list[dict] = []
    first_day = day_of(lo_ts)
    last_day = day_of(hi_ts)

    # Walk day by day. The first day starts wherever the range starts, not at
    # its midnight — the caller asked about [lo, hi], not about whole days.
    day = first_day
    start_tick = lo_tick
    while True:
        if day == last_day:
            out.append({"day": day, "from_tick": start_tick, "to_tick": hi_tick})
            break
        next_midnight = day_start(day) + SECONDS_PER_DAY
        nxt = find_first_tick_at_or_after(ts_of, next_midnight, start_tick, hi_tick)
        if nxt is None or nxt <= start_tick:
            # No tick reaches the next midnight inside this range: everything
            # left belongs to the current day.
            out.append({"day": day, "from_tick": start_tick, "to_tick": hi_tick})
            break
        out.append({"day": day, "from_tick": start_tick, "to_tick": nxt - 1})
        start_tick = nxt
        ts = ts_of(nxt)
        if not ts:
            probe, ts = _nearest_dated(ts_of, nxt, nxt, hi_tick)
            if ts is None:
                break
        day = day_of(ts)
    return out
