"""Find readings a frozen upstream backend echoed into the price series.

What happened (2026-09-29, ~09:40 to 2026-09-30, ~01:30 UTC): `/v1/latest-stats`
is served by more than one backend (the response carries `x-server-name`), and
for sixteen hours one of them stood still at 4.58e-7 while the other kept
following the market up to 4.88e-7. The load balancer alternated between them,
so the worker read the real price one minute and the frozen one the next. Every
reading was stored faithfully, and the chart drew a sixteen-hour comb between
the two.

Nothing about a single reading gives that away -- the frozen answer is well
formed, carries the right epoch and supply, and is a price the market really
stood at earlier. What gives it away is the SHAPE:

  * the price jumps back to the same value again and again, each time from
    somewhere clearly different (a return), several times an hour;
  * of the two values taking turns, the frozen one is the OLDER: it has been
    on record, unchanged, since before the other appeared. A backend that
    stops updating keeps serving the last price it had, so the value that was
    there first is the one that stopped.

Both conditions are required. Market noise flips between neighbouring prices
constantly (4.57/4.58 all morning), but a 1e-9 step is under JUMP, so it never
counts as a return. A real spike and recovery returns once, not four times in
an hour.

Pure functions only: the store decides what to do with the answer.
"""
from __future__ import annotations

# A move smaller than this is a neighbouring price, not a return. The smallest
# published step is 1e-9 on a price of ~5e-7, i.e. 0.2%; the frozen value sat
# 1.3% to 6.5% away from the real one during the incident.
JUMP = 0.005

# Two appearances of a value further apart than this are not one run of it.
CHAIN_GAP_S = 1800

# How many returns to the same value inside WINDOW_S make a pattern. The
# incident produced 10 to 17 per hour; a genuine spike and recovery makes one.
MIN_RETURNS = 4
WINDOW_S = 3600


def _jump(a: float, b: float) -> bool:
    lo = min(a, b)
    return lo > 0 and abs(a - b) / lo >= JUMP


def frozen_rows(rows: list[dict]) -> list[int]:
    """Indices of `rows` that a frozen backend echoed.

    `rows` are price intervals (`at`, `until`, `price`) sorted by `at`. Rows
    already set aside may be included; they still count as evidence, which is
    what keeps an ongoing incident recognised after its first readings have
    been removed from the series.
    """
    n = len(rows)
    if n < 2:
        return []

    # Where the current run of each row's value began: the first appearance
    # not separated from the next by more than CHAIN_GAP_S.
    run_start = [0] * n
    seen: dict[float, tuple[int, int]] = {}      # price -> (run start, last until)
    for i, r in enumerate(rows):
        v = float(r["price"])
        prev = seen.get(v)
        if prev is not None and int(r["at"]) - prev[1] <= CHAIN_GAP_S:
            start, last = prev[0], max(prev[1], int(r["until"]))
        else:
            start, last = int(r["at"]), int(r["until"])
        run_start[i] = start
        seen[v] = (start, last)

    # Returns: a jump INTO a value. `older` records whether the value jumped
    # back to had been on record longer than the one it came from.
    returns: dict[float, list[tuple[int, bool]]] = {}
    for i in range(1, n):
        a, b = float(rows[i - 1]["price"]), float(rows[i]["price"])
        if _jump(a, b):
            returns.setdefault(b, []).append((i, run_start[i] < run_start[i - 1]))

    out: set[int] = set()
    for value, rets in returns.items():
        # Episodes: returns to this value no further apart than CHAIN_GAP_S --
        # or further, while the value's run never broke in between. On
        # 2026-09-29 the real price sat one step above the frozen one for two
        # hours (4.59 against 4.58); those flips are too small to be returns,
        # yet the frozen value was there throughout and is the same echo.
        episodes: list[list[tuple[int, bool]]] = [[rets[0]]]
        for ret in rets[1:]:
            last = episodes[-1][-1][0]
            if (int(rows[ret[0]]["at"]) - int(rows[last]["at"]) > CHAIN_GAP_S
                    and run_start[ret[0]] != run_start[last]):
                episodes.append([])
            episodes[-1].append(ret)

        for ep in episodes:
            if len(ep) < MIN_RETURNS:
                continue
            times = [int(rows[i]["at"]) for i, _ in ep]
            dense = any(
                sum(1 for t in times[k:] if t - times[k] < WINDOW_S) >= MIN_RETURNS
                for k in range(len(times)))
            if not dense:
                continue
            # The frozen side is the older one, at most of its returns.
            if sum(1 for _, older in ep if older) * 2 <= len(ep):
                continue
            first, last = ep[0][0], ep[-1][0]
            out.update(i for i in range(first, last + 1)
                       if float(rows[i]["price"]) == value)
    return sorted(out)
