"""Burn measurement — counting what the explorer only totals.

`explorer.qubic.org` publishes one cumulative Burned Supply figure, and so does
the public RPC (`/v1/latest-stats` -> `burnedQus`). Measured 2026-09-19, that
counter **does not move between epoch boundaries**: six samples over 375 ticks
(~4.6 min) returned the identical 53,662,829,138,067 QU, while a Bob node
reported 913 burn transfers of exactly 1,000,000 QU each over 500 ticks in the
same window.

So the RPC figure is an epoch aggregate (one step per week). Storing it hourly
would yield a flat week and one jump; calling the flat stretch "0 QU burned
today" would be false, and interpolating the jump backwards would be an invented
curve presented as measurement. This module therefore *counts the events* from a
Bob node's tick logs, and the RPC total becomes the epoch-boundary anchor those
sums are reconciled against (CONCEPT_BURN §3).

Tick logs carry no timestamp, so a scan must be told which day it is counting.
Near the chain head that is simply today (`pipeline.scan_burns`). For older
ticks the day is resolved by measurement from `/v1/ticks/{t}/tick-data` — see
`qdr/dating.py` — which is what makes a historical backfill honest rather than
an interpolation.

Two event shapes carry burns, and they are not the same thing:

  * `QU_TRANSFER` to a burn sink — a transfer whose value does not come back.
    Most 1,000,000 QU transfers to the null address are refunded inside the same
    transaction and are NOT burns (see `burn_events`); what survives netting is
    small and sporadic — tens to low millions of QU per day, measured.
  * `BURNING` — a distinct log type carrying `contractIndexBurnedFor`, i.e. the
    only source that says *what* a burn was for.

This docstring used to call the second kind "hundreds of thousands of QU per
epoch: six orders of magnitude smaller". Backfilling four days of epoch 231
(2026-09-20) disproved that: contract burns ran to ~690 million QU over partial
coverage of those days, dominated by a single contract —

    QEarn    492,712,412 QU        QSwap      1,525,405 QU
    CCF        1,000,000 QU        QUtil/Random    <400 QU

The live scan had only ever seen QSwap's 33,402 QU, because it could not count
anything older than the day it was running. The two shapes are still counted and
reported separately, but not because one is negligible — they answer different
questions, and only `BURNING` says what a burn was for.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

from .bob import BobClient, BobError

BURNING = "BURNING"
QU_TRANSFER = "QU_TRANSFER"

# The one address value burns actually go to, measured from Bob's logs and
# confirmed against the ledger on 2026-09-19.
#
# A Qubic identity is 60 characters. Matching it on a shorter prefix silently
# matches nothing and the burn total comes out as a clean, plausible zero — this
# repo has paid for that mistake once already (CONCEPT §4.2) — so the full value
# is pinned here and compared exactly.
NULL_ADDRESS = "A" * 56 + "FXIB"

# Why an exact match and not `revenue.is_uninformative_identity()`, which is
# right there and looks like it fits: that helper answers a DIFFERENT question.
# It asks "does money moving through here reveal an owner?", and for that it is
# correctly generous — it treats the A/B/D-prefixed system addresses alike,
# because none of them indicate ownership.
#
# "Is this a burn?" is a narrower question, and reusing the looser test gets it
# wrong. Measured over ticks 80,820,000-80,820,500, the generous test matched a
# second sink, DAAA...NMIG, with 496 transfers of exactly 1 QU. The ledger says
# that address holds a balance of 4,210 QU against 16,328,271 incoming and
# 16,311,057 OUTGOING transfers: value flows straight back out of it, so it is an
# active system account, not a sink. The null address by contrast reports
# 0 balance and 0 transfers in either direction — value that reaches it leaves
# the supply, which is exactly what makes it the burn address.
#
# Counting that second address would have added 496 QU against 913,000,000 —
# harmless in magnitude, wrong in kind, and it would have grown silently the day
# some other system address started moving volume.
def is_burn_sink(identity: Optional[str]) -> bool:
    """True only for the protocol's burn address.

    Deliberately exact rather than prefix-based: see the note above for the
    address this distinction excludes and why.
    """
    return identity == NULL_ADDRESS

# Bob caps a log request at ~1000 ticks whatever range is asked for, and answers
# with ~2.6 MB per 500 ticks. A scan pass is therefore budgeted: it advances by
# at most MAX_CALLS chunks and stores what it got, rather than trying to catch up
# in one pass and monopolising the worker or the public node.
CHUNK_TICKS = 500
MAX_CALLS = 20


def _log_type(entry: dict) -> Optional[str]:
    """End-epoch logs spell it `logTypename`, tick logs `logTypeName`."""
    return entry.get("logTypename") or entry.get("logTypeName")


def _body(entry: dict) -> dict:
    b = entry.get("body")
    return b if isinstance(b, dict) else entry


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def event_day(entry: dict) -> Optional[str]:
    """UTC day carried BY the log entry itself, as 'YYYY-MM-DD', or None.

    End-epoch entries carry `"26-09-02 12:00:05"` — a two-digit year. **Ordinary
    tick-log entries carry no timestamp at all** (measured against a live node,
    2026-09-19: the entry has `tick`, `epoch`, `logId`, addresses and amount, and
    no time field of any kind). So this returns None for most events, and the
    caller supplies the day.

    This docstring used to add that no RPC endpoint exposes a tick's wall time
    either, having found `/v2/ticks/{t}` Not Found and `/v2/epochs/{e}/ticks`
    carrying only `{tickNumber, isEmpty}`. That conclusion was too broad: the
    **v1** route answers.

        GET /v1/ticks/80849178/tick-data -> timestamp 1789603200000
                                         =  2026-09-20 00:00:00 UTC

    Measured 2026-09-20 across epoch 231 and back into 230: present, in
    milliseconds, strictly increasing. A tick can therefore be dated by
    measurement after the fact, which is what `qdr/dating.py` does and what lets
    `pipeline.backfill_burns` fill days that predate the worker.

    Deriving a date from the tick NUMBER would still be wrong — that assumes a
    constant tick rate, and the measured rate (1.55/s over three days) is
    nothing like the ~2.7/s this repo once assumed. Reading the timestamp is not
    that: it is measurement, not inference.
    """
    raw = entry.get("timestamp")
    if not raw or not isinstance(raw, str):
        return None
    date_part = raw.strip().split(" ")[0]
    bits = date_part.split("-")
    if len(bits) != 3:
        return None
    y, m, d = bits
    if not (y.isdigit() and m.isdigit() and d.isdigit()):
        return None
    if len(y) == 2:
        y = f"20{y}"
    if len(y) != 4:
        return None
    return f"{y}-{int(m):02d}-{int(d):02d}"


def burn_events(logs: Iterable[dict]) -> list[dict]:
    """Extract burn events from a log range.

    Returns entries of {kind, amount, day, tick, contract}, where `kind` is
    'transfer' (QU that actually left the supply via a burn sink) or 'contract'
    (a BURNING event).

    A QU_TRANSFER only counts when its DESTINATION is a burn sink. Testing the
    source instead would count the protocol's emission — every computor payout is
    credited *from* the null address — and report the network's entire revenue as
    burned.

    **A transfer to the sink is only a burn if it stays there.** Measured
    2026-09-20 over 1,000 ticks: 1,696 transactions each sent exactly 1,000,000
    QU to the null address and had the identical amount sent straight back to the
    payer *within the same transaction* —

        QU_TRANSFER  1,000,000  payer -> AAAA…FXIB
        CUSTOM_MESSAGE
        QU_TRANSFER  1,000,000  AAAA…FXIB -> payer

    1,696,000,000 QU in, 1,696,000,000 QU out, net zero, every single one
    refunded. Counting only the inbound leg reported 17.6 Mrd QU burned in an
    hour while the protocol's own burnedQus counter did not move at all — which
    is exactly what it should do, because nothing was burned.

    So the legs are netted per transaction: what a transaction actually removed
    from the supply is (paid in) − (paid back out), floored at zero. A refunded
    round trip contributes nothing and is not an event.
    """
    out: list[dict] = []
    # Per transaction: how much reached the sink, how much came back, and where
    # to attribute the remainder. A transaction is the right unit because that is
    # the scope the refund happens in.
    flows: dict[str, dict] = {}
    order: list[str] = []

    for entry in logs:
        if not isinstance(entry, dict):
            continue
        kind = _log_type(entry)
        body = _body(entry)
        tick = _int(entry.get("tick"))
        day = event_day(entry)
        if kind == QU_TRANSFER:
            dest = body.get("to") or body.get("destination")
            src = body.get("from") or body.get("source")
            amount = _int(body.get("amount") or body.get("value"))
            if amount <= 0:
                continue
            to_sink = is_burn_sink(dest)
            from_sink = is_burn_sink(src)
            if not to_sink and not from_sink:
                continue
            # A transfer with no transaction hash cannot be paired with its
            # refund, so it is keyed on its own identity and nets against
            # nothing — the conservative reading for an unpairable leg.
            key = entry.get("transactionHash") or f"__{tick}:{len(order)}"
            if key not in flows:
                flows[key] = {"in": 0, "out": 0, "day": day, "tick": tick}
                order.append(key)
            if to_sink:
                flows[key]["in"] += amount
            if from_sink:
                flows[key]["out"] += amount
        elif kind == BURNING:
            amount = _int(body.get("amount"))
            if amount <= 0:
                continue   # a 0-QU burn event is a no-op, measured in epochs 225-228
            out.append({"kind": "contract", "amount": amount, "day": day,
                        "tick": tick,
                        "contract": body.get("contractIndexBurnedFor")})

    # Emit only what a transaction did NOT get back. A refund larger than the
    # payment is a protocol emission that happens to share the transaction (a
    # computor payout is credited from this same address), not a negative burn,
    # so the figure is floored at zero rather than allowed to subtract.
    for key in order:
        f = flows[key]
        net = f["in"] - f["out"]
        if net <= 0:
            continue
        out.append({"kind": "transfer", "amount": net, "day": f["day"],
                    "tick": f["tick"], "contract": None})
    return out


def observed_day(at: Optional[float] = None) -> str:
    """Today in UTC, as 'YYYY-MM-DD'."""
    return time.strftime("%Y-%m-%d", time.gmtime(at if at is not None else time.time()))


def aggregate_by_day(events: Iterable[dict],
                     default_day: Optional[str] = None) -> dict[str, dict]:
    """Fold burn events into per-day totals.

    A scan window is a unit of work, not a unit of reporting — a window that
    straddles midnight is split here, so a day's figure is never distorted by
    where the scanner happened to stop.

    `default_day` dates the events that carry no timestamp of their own, which is
    all of them in an ordinary tick log (see `event_day`). The worker passes the
    day it is scanning ON, which is accurate precisely because it scans close to
    the chain head: it counts burns shortly after they happen. That is an
    observation ("we counted this on this day"), not an inference from an assumed
    tick rate.

    With no `default_day`, undated events are dropped rather than guessed — a
    burn on the wrong day is worse than one we openly did not date. Backfilling
    old ticks therefore records nothing rather than misdating history, which is
    why the series begins where measurement began.
    """
    days: dict[str, dict] = {}
    for e in events:
        day = e.get("day") or default_day
        if not day:
            continue
        bucket = days.setdefault(day, {
            "burned": 0, "burn_events": 0,
            "contract_burned": 0, "contract_events": 0,
            "by_contract": {}, "from_tick": None, "to_tick": None,
        })
        amount = int(e.get("amount") or 0)
        if e.get("kind") == "contract":
            bucket["contract_burned"] += amount
            bucket["contract_events"] += 1
            idx = e.get("contract")
            if idx is not None:
                key = str(idx)
                bucket["by_contract"][key] = bucket["by_contract"].get(key, 0) + amount
        else:
            bucket["burned"] += amount
            bucket["burn_events"] += 1
        tick = int(e.get("tick") or 0)
        if tick:
            lo, hi = bucket["from_tick"], bucket["to_tick"]
            bucket["from_tick"] = tick if lo is None else min(lo, tick)
            bucket["to_tick"] = tick if hi is None else max(hi, tick)
    return days


def scan_range(
    bob: BobClient,
    from_tick: int,
    to_tick: int,
    epoch: int,
    chunk: int = CHUNK_TICKS,
    max_calls: int = MAX_CALLS,
    default_day: Optional[str] = None,
) -> dict:
    """Count burns across a tick range, paging around the node's request cap.

    Returns {days, scanned_to, calls, gaps}. `scanned_to` is the last tick
    actually counted — a chunk Bob could not serve ends the pass there rather
    than being skipped over, because advancing the pointer past an unread range
    would silently lose those burns forever.

    `default_day` dates events that carry no timestamp (all of them, in an
    ordinary tick log). Pass it only when scanning near the chain head, where
    "the day we counted it" and "the day it happened" are the same day. The
    caller enforces that; see `pipeline.scan_burns`.
    """
    days: dict[str, dict] = {}
    tick = int(from_tick)
    end = int(to_tick)
    calls = 0
    scanned_to = tick - 1
    gaps: list[list[int]] = []

    while tick <= end and calls < max_calls:
        chunk_end = min(tick + chunk - 1, end)
        try:
            logs = bob.tick_logs(tick, chunk_end)
        except BobError:
            gaps.append([tick, chunk_end])
            break        # stop at the gap; the next pass retries from here
        calls += 1
        for day, agg in aggregate_by_day(burn_events(logs), default_day).items():
            merged = days.setdefault(day, {
                "burned": 0, "burn_events": 0, "contract_burned": 0,
                "contract_events": 0, "by_contract": {},
                "from_tick": None, "to_tick": None,
            })
            merged["burned"] += agg["burned"]
            merged["burn_events"] += agg["burn_events"]
            merged["contract_burned"] += agg["contract_burned"]
            merged["contract_events"] += agg["contract_events"]
            for k, v in agg["by_contract"].items():
                merged["by_contract"][k] = merged["by_contract"].get(k, 0) + v
            for field, fn in (("from_tick", min), ("to_tick", max)):
                a, b = merged[field], agg[field]
                merged[field] = b if a is None else (a if b is None else fn(a, b))
        scanned_to = chunk_end
        tick = chunk_end + 1

    return {"days": days, "scanned_to": scanned_to, "calls": calls,
            "gaps": gaps, "epoch": epoch,
            "complete": scanned_to >= end}


def reconcile(measured_delta: int, official_delta: int) -> dict:
    """Compare our summed events against the official counter's step.

    Coverage below 1.0 means a burn category exists that the scan does not yet
    recognise. That is a finding to publish, not an error to hide — the same
    discipline `linkage_coverage: 0` already applies to operator attribution.
    An official delta of zero yields a null ratio rather than a division error:
    it means the anchor has not stepped yet, not that coverage is perfect.
    """
    ratio = None
    if official_delta > 0:
        ratio = round(measured_delta / official_delta, 6)
    return {
        "measured_delta": int(measured_delta),
        "official_delta": int(official_delta),
        "ratio": ratio,
        "missing": int(official_delta - measured_delta) if official_delta > 0 else None,
        "observed_at": int(time.time()),
    }
