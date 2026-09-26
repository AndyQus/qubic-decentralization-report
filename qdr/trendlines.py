"""Trend lines — "join the two highest and the two lowest points", as a rule.

Taken literally that instruction fails: the two highest bars usually sit side by
side, and a line between neighbours says nothing. A hand drawing a trend line
does three things implicitly, and this module does them explicitly
(docs/CONCEPT_TRENDLINES.de.md §3):

  * **Lines lie ON the bars, not through them.** A resistance line is an edge of
    the upper convex hull of the bar highs. Every hull edge is a supporting
    line: no high anywhere in the range lies above it, which is exactly what the
    eye checks for. Support is the same on the lows, mirrored.
  * **The anchors are far apart.** An edge shorter than `min_sep` bars is two
    neighbours again, and is skipped.
  * **The best line is the one the price kept returning to.** Among the edges
    left, the one with the most *touches* wins -- swing points (pivots) within
    `tol` of the line, the arrows in a hand-drawn chart. Ties go to the line
    whose second anchor is more recent, then to the longer one.

Everything here is a pure function of the bars it is given, so the same data
always yields the same line. That matters because lines are stored with fixed
anchors (§5): a line that moved whenever it was recomputed could never be
checked afterwards.

Prices are compared on the chart's own linear axis. A straight line in log
space would bend on the page, and the page draws a linear axis.

Bars are dicts with `at` (unix second, start of the period), `high`, `low` and
`close` -- the shape of a `price_hours` row.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

HOUR = 3600
DAY = 86400


@dataclass(frozen=True)
class Scale:
    """How one family of lines is found. Changing a value later changes which
    lines exist, and with that the archive's comparability -- so these are
    calibrated once and then left alone (CONCEPT §9)."""
    name: str
    period_s: int      # bar length
    k: int             # pivot half-width, in bars
    min_sep: int       # minimum anchor distance, in bars
    tol: float         # relative distance that still counts as touching / not yet breaking
    lookback: int      # bars searched for anchors
    ready_bars: int    # bars on record before any line is drawn
    break_bars: int = 2  # consecutive closes beyond the line that break it


# One scale per chart window (CONCEPT §4): a single hourly scale cannot serve
# both 24 h and 7 d. Over a week the right resistance may join two highs four
# days apart, and in the 24 h window that line explains nothing about the last
# day's slide. Calibrated on the first 116 hours on record (21-26 Sep 2026).
SHORT_SCALE = Scale("short", HOUR, k=3, min_sep=5, tol=0.005,
                    lookback=48, ready_bars=24)          # 24 h window
HOUR_SCALE = Scale("hour", HOUR, k=6, min_sep=18, tol=0.005,
                   lookback=14 * 24, ready_bars=72)      # 7 d window
DAY_SCALE = Scale("day", DAY, k=3, min_sep=9, tol=0.01,
                  lookback=0, ready_bars=30)             # All
SCALES = {s.name: s for s in (SHORT_SCALE, HOUR_SCALE, DAY_SCALE)}


@dataclass(frozen=True)
class Line:
    kind: str          # 'resistance' | 'support' | 'parallel' | 'mid'
    t1: int
    p1: float
    t2: int
    p2: float
    touches: int
    touch_at: tuple = ()   # unix seconds of the touching bars, anchors included

    def at(self, t: float) -> float:
        """The line's price at time `t` (extended beyond its anchors)."""
        return self.p1 + (self.p2 - self.p1) * (t - self.t1) / (self.t2 - self.t1)

    def slope_pct_per_day(self) -> float:
        """Slope as a percentage of the second anchor's price, per day."""
        per_s = (self.p2 - self.p1) / (self.t2 - self.t1)
        return per_s * DAY / self.p2 * 100.0 if self.p2 else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["touch_at"] = list(self.touch_at)
        d["slope_pct_per_day"] = round(self.slope_pct_per_day(), 4)
        return d


# -- building blocks --------------------------------------------------------

def pivots(bars: Sequence[dict], k: int, side: str) -> list[int]:
    """Indices of confirmed swing highs (`side='high'`) or lows (`'low'`).

    A swing high tops the `k` bars on either side. It is strictly above the left
    ones and at least equal to the right ones, so a plateau -- common here,
    since the price often stands still for hours -- counts once, at its first
    bar. The last `k` bars cannot be pivots yet: their right side has not
    happened. That lag is inherent to every pivot method.
    """
    key = "high" if side == "high" else "low"
    sign = 1.0 if side == "high" else -1.0
    out = []
    for i in range(k, len(bars) - k):
        v = sign * bars[i][key]
        left = max(sign * bars[j][key] for j in range(i - k, i))
        right = max(sign * bars[j][key] for j in range(i + 1, i + k + 1))
        if v > left and v >= right:
            out.append(i)
    return out


def _hull(points: list[tuple[int, float]], upper: bool) -> list[int]:
    """Indices (into `points`) of the upper or lower convex hull, left to right.

    Monotone chain. `points` must be sorted by x. Collinear points are dropped,
    so a flat top of equal highs yields one edge from its first to its last bar
    -- the longest honest reading of it.
    """
    sign = 1.0 if upper else -1.0
    hull: list[int] = []
    for i, (x, y) in enumerate(points):
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = points[hull[-2]], points[hull[-1]]
            cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if sign * cross >= 0:   # hull[-1] lies on or under the chord: drop it
                hull.pop()
            else:
                break
        hull.append(i)
    return hull


def _rel_gap(line_p: float, p: float) -> float:
    return abs(line_p - p) / line_p if line_p else float("inf")


def _touches(line_t1: int, line_p1: float, line_t2: int, line_p2: float,
             bars: Sequence[dict], piv: list[int], key: str, tol: float,
             anchors: tuple[int, int]) -> tuple:
    def at(t):
        return line_p1 + (line_p2 - line_p1) * (t - line_t1) / (line_t2 - line_t1)
    hits = set(anchors)
    for i in piv:
        if bars[i]["at"] >= line_t1 and _rel_gap(at(bars[i]["at"]), bars[i][key]) <= tol:
            hits.add(i)
    return tuple(sorted(hits))


def best_line(bars: Sequence[dict], scale: Scale, side: str,
              start_at: Optional[int] = None) -> Optional[Line]:
    """The resistance (`side='high'`) or support (`'low'`) line, or None.

    Anchors are PIVOTS, never plain bars, and pivots are found on the whole
    series before `start_at` narrows the search. Replaying the first days on
    record hour by hour showed why both matter: with any confirmed bar allowed
    as an anchor, the newest one kept winning and the line moved every hour;
    with pivots found inside the lookback only, the window's sliding left edge
    dragged the first anchor along. A line should change when the market makes
    a new swing, and at no other time -- a stored line whose anchors drift is no
    record at all.

    The hull runs over the pivots; each edge is then checked against every bar
    it spans, since a bar between two pivots can still poke through. More than
    `tol` through the line between its anchors disqualifies it, and so does a
    confirmed break after its second anchor: a line found already broken is
    history, not a line to draw.

    None when no edge qualifies -- a one-way market has no resistance line yet,
    and inventing one would be worse.
    """
    key = "high" if side == "high" else "low"
    sign = 1.0 if side == "high" else -1.0
    kind = "resistance" if side == "high" else "support"
    piv = pivots(bars, scale.k, side)
    cand = [i for i in piv if start_at is None or bars[i]["at"] >= start_at]
    if len(cand) < 2:
        return None
    pts = [(bars[i]["at"], float(bars[i][key])) for i in cand]
    hull = [cand[j] for j in _hull(pts, upper=(side == "high"))]
    confirmed_end = len(bars) - scale.k

    best, best_rank = None, None
    for a, b in zip(hull, hull[1:]):
        if b - a < scale.min_sep:
            continue
        ln = Line(kind, bars[a]["at"], float(bars[a][key]),
                  bars[b]["at"], float(bars[b][key]), 0)
        if any(sign * (bars[i][key] - ln.at(bars[i]["at"])) > scale.tol * ln.at(bars[i]["at"])
               for i in range(a, b + 1)):
            continue
        if broken_at(ln, bars[:confirmed_end], scale) is not None:
            continue
        hits = _touches(ln.t1, ln.p1, ln.t2, ln.p2, bars, piv, key, scale.tol, (a, b))
        rank = (len(hits), ln.t2, ln.t2 - ln.t1)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = Line(kind, ln.t1, ln.p1, ln.t2, ln.p2, len(hits),
                        tuple(bars[i]["at"] for i in hits))
    return best


def channel(bars: Sequence[dict], res: Optional[Line], sup: Optional[Line],
            scale: Scale, max_slope_diff: float = 0.3,
            flat_pct_per_day: float = 0.5) -> list[Line]:
    """Parallel and mid line, when resistance and support run roughly parallel.

    Parallel means the slopes (in % per day) differ by at most `max_slope_diff`
    of the steeper one -- or by at most `flat_pct_per_day`, so two nearly flat
    lines in a sideways market still count, where a relative test would reject
    them over noise. A wedge or triangle gets no channel: a mid line between two
    converging lines means nothing.

    The parallel runs beside the STRONGER line (more touches; ties: resistance)
    through the bar that lies farthest from it on the other side, over the
    stronger line's own span up to now. It must be touched at least twice, like
    any other line: a parallel resting on one outlier -- a single spike, say --
    is a band drawn around that spike, not a channel the price moves in.

    When the parallel lands on the opposite line anyway (within `2·tol` at both
    ends of the span) the channel is already drawn by resistance and support;
    only the mid line is added, since two lines on top of each other read as
    one thick line.
    """
    if res is None or sup is None:
        return []
    sr, ss = res.slope_pct_per_day(), sup.slope_pct_per_day()
    diff = abs(sr - ss)
    if diff > max(max_slope_diff * max(abs(sr), abs(ss)), flat_pct_per_day):
        return []

    base = res if res.touches >= sup.touches else sup
    key = "low" if base is res else "high"
    span = [b for b in bars if b["at"] >= base.t1]
    if not span:
        return []
    # Farthest bar on the opposite side, measured as a price offset from the line.
    far = min(span, key=lambda b: b[key] - base.at(b["at"])) if base is res \
        else max(span, key=lambda b: b[key] - base.at(b["at"]))
    off = far[key] - base.at(far["at"])
    p1, p2 = base.p1 + off, base.p2 + off
    idx = [i for i, b in enumerate(bars) if b["at"] >= base.t1]
    piv = pivots(bars, scale.k, key)
    far_i = next(i for i in idx if bars[i]["at"] == far["at"])
    hits = _touches(base.t1, p1, base.t2, p2, bars, piv, key, scale.tol,
                    (far_i, far_i))
    if len(hits) < 2:
        return []
    par = Line("parallel", base.t1, p1, base.t2, p2, len(hits),
               tuple(bars[i]["at"] for i in hits))
    other = sup if base is res else res
    if all(_rel_gap(other.at(t), par.at(t)) <= 2 * scale.tol
           for t in (base.t1, bars[-1]["at"])):
        m1, m2 = (base.at(base.t1) + other.at(base.t1)) / 2,                  (base.at(base.t2) + other.at(base.t2)) / 2
        return [Line("mid", base.t1, m1, base.t2, m2, 0)]
    mid = Line("mid", base.t1, base.p1 + off / 2, base.t2, base.p2 + off / 2, 0)
    return [par, mid]


def detect(bars: Sequence[dict], scale: Scale) -> dict:
    """All lines for one scale, from every bar on record.

    `lookback` limits where anchors may lie, not which bars are read: pivots
    near the edge of the search range need their neighbours, and a break is
    judged on every bar since the second anchor.

    Returns `ready` like the moving average does: fewer than `ready_bars` bars
    means no lines and a count of how many are still missing, never a line
    drawn from too little.
    """
    bars = list(bars)
    out = {"scale": scale.name, "ready": len(bars) >= scale.ready_bars,
           "bars_on_record": len(bars), "bars_needed": scale.ready_bars,
           "lines": []}
    if not out["ready"]:
        return out
    start_at = bars[-scale.lookback]["at"]         if scale.lookback and len(bars) > scale.lookback else None
    res = best_line(bars, scale, "high", start_at)
    sup = best_line(bars, scale, "low", start_at)
    lines = [ln for ln in (res, sup) if ln] + channel(bars, res, sup, scale)
    out["lines"] = [dict(ln.as_dict(), broken_at=broken_at(ln, bars, scale))
                    for ln in lines]
    return out


def broken_at(line: Line, bars: Sequence[dict], scale: Scale) -> Optional[int]:
    """When the price broke through `line`, or None while it holds.

    A break is `break_bars` consecutive CLOSES more than `tol` beyond the line
    after its second anchor -- above a resistance, below a support. A wick
    through the line is not a break: hand-drawn channels are full of them. The
    returned time is the first bar of that run.
    """
    if line.kind not in ("resistance", "support"):
        return None
    run, first = 0, None
    for b in bars:
        if b["at"] <= line.t2:
            continue
        lp = line.at(b["at"])
        c = float(b["close"])
        beyond = (c > lp * (1 + scale.tol)) if line.kind == "resistance" \
            else (c < lp * (1 - scale.tol))
        if beyond:
            run += 1
            first = first if run > 1 else b["at"]
            if run >= scale.break_bars:
                return first
        else:
            run, first = 0, None
    return None


def daily_bars(hours: Sequence[dict]) -> list[dict]:
    """Hourly rows folded into UTC days: highest high, lowest low, last close.

    Built from `price_hours` because that table is permanent; price points are
    pruned after 90 days, and a stored line must never point at data that is
    gone. A day with a single hour on record is still a day -- coverage is the
    caller's to report, not this function's to hide.
    """
    days: dict[int, dict] = {}
    for h in hours:
        d = int(h["at"]) // DAY * DAY
        cur = days.get(d)
        if cur is None:
            days[d] = {"at": d, "high": float(h["high"]), "low": float(h["low"]),
                       "close": float(h["close"])}
        else:
            cur["high"] = max(cur["high"], float(h["high"]))
            cur["low"] = min(cur["low"], float(h["low"]))
            cur["close"] = float(h["close"])
    return [days[d] for d in sorted(days)]
