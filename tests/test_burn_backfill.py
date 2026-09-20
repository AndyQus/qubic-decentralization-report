"""Backfilling burns into days that predate the worker.

The live scan can only date what it watches happen, so the burn series used to
begin the day the worker did and every earlier day was permanently blank.
`backfill_burns` fills those days using measured tick timestamps, and these
tests pin the parts that would otherwise fail quietly:

  * a backfilled day must be stored under the day the ticks BELONG to, not the
    day the backfill ran;
  * a day scanned and found empty must be recorded as a measured zero, because
    "we counted nothing" and "we never looked" are different claims;
  * a bucket must record the window that was SCANNED, so it supersedes the
    narrow buckets a live scan left behind instead of double-counting beside
    them;
  * a range Bob cannot serve must be reported, never stored as zero burns.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr import dating, pipeline
from qdr.bob import BobError
from qdr.store import Store

NULL = "A" * 56 + "FXIB"
WALLET = "W1" + "Y" * 58

# A synthetic chain: 1.55 ticks/s (the measured rate), starting at midnight.
RATE = 1.55
LO = 1_000_000
DAY0 = "2026-09-17"
BASE_TS = dating.day_start(DAY0)


def _ts_of(tick):
    if tick < LO:
        return None
    return BASE_TS + int((tick - LO) / RATE)


def _tick_at(day, hour=0):
    """First tick of a given day/hour in the synthetic chain."""
    target = dating.day_start(day) + hour * 3600
    return LO + int((target - BASE_TS) * RATE)


class FakeClient:
    """Serves tick timestamps and tick-info, counting lookups."""

    def __init__(self, head):
        self.head = head
        self.lookups = 0

    def tick_info(self):
        return {"tick": self.head, "epoch": 231}

    def tick_timestamp(self, tick):
        self.lookups += 1
        return _ts_of(tick)


class FakeBob:
    """Tick logs with a burn at chosen ticks, and optionally a dead range."""

    def __init__(self, burn_ticks=(), indexed=None, initial=LO, fail_from=None):
        self.burn_ticks = set(burn_ticks)
        self.indexed = indexed
        self.initial = initial
        self.fail_from = fail_from
        self.calls = []

    def status(self):
        return {"currentIndexingTick": self.indexed, "initialTick": self.initial}

    def tick_logs(self, from_tick, to_tick):
        self.calls.append((from_tick, to_tick))
        if self.fail_from is not None and from_tick >= self.fail_from:
            raise BobError("no logs for that range")
        out = []
        for t in self.burn_ticks:
            if from_tick <= t <= to_tick:
                # no timestamp: the shape a live node really returns
                out.append({"logTypeName": "QU_TRANSFER", "tick": t,
                            "source": WALLET, "destination": NULL,
                            "amount": 7, "transactionHash": f"tx{t}"})
        return out


def fresh_store():
    d = tempfile.mkdtemp(prefix="qdr_backfill_test_")
    return Store(Path(d) / "qdr.db")


def _rows(store):
    return {r["key"]: r for r in store.burn_series(by="day")}


def test_burn_is_filed_under_the_day_its_tick_belongs_to():
    """The whole point: NOT the day the backfill happened to run."""
    burn_tick = _tick_at("2026-09-18", hour=13)
    head = _tick_at("2026-09-20", hour=1)
    store = fresh_store()
    bob = FakeBob(burn_ticks=[burn_tick], indexed=head)
    out = pipeline.backfill_burns(FakeClient(head), store, bob,
                                  from_tick=LO, to_tick=head, max_calls=2000)
    assert not out.get("reason"), out
    rows = _rows(store)
    assert rows["2026-09-18"]["burned"] == 7
    for other in ("2026-09-17", "2026-09-19", "2026-09-20"):
        assert rows.get(other, {"burned": 0})["burned"] == 0


def test_days_with_no_burns_are_recorded_as_measured_zeroes():
    """A measured zero and an unscanned day must not look identical."""
    head = _tick_at("2026-09-19", hour=2)
    store = fresh_store()
    out = pipeline.backfill_burns(FakeClient(head), store,
                                  FakeBob(indexed=head),
                                  from_tick=LO, to_tick=head, max_calls=2000)
    assert not out.get("reason"), out
    days = set(_rows(store))
    assert {"2026-09-17", "2026-09-18", "2026-09-19"} <= days


def test_bucket_records_the_scanned_window_not_the_event_span():
    """A window covering only the burn tick would fail to supersede the live
    scan's buckets, and the day would be counted twice."""
    burn_tick = _tick_at("2026-09-18", hour=8)
    head = _tick_at("2026-09-19", hour=1)
    store = fresh_store()
    pipeline.backfill_burns(FakeClient(head), store,
                            FakeBob(burn_ticks=[burn_tick], indexed=head),
                            from_tick=LO, to_tick=head, max_calls=2000)
    import sqlite3
    con = sqlite3.connect(str(store.path))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT from_tick,to_tick FROM burn_buckets WHERE day='2026-09-18'"
    ).fetchone()
    span = row["to_tick"] - row["from_tick"]
    assert span > 10_000, f"window {span} looks like an event span, not a scan"
    assert row["from_tick"] <= burn_tick <= row["to_tick"]


def test_backfill_supersedes_a_narrow_live_bucket_instead_of_doubling_it():
    """The live scan leaves small buckets behind; a wider rescan must replace
    them, not add to them."""
    burn_tick = _tick_at("2026-09-18", hour=8)
    head = _tick_at("2026-09-19", hour=1)
    store = fresh_store()
    # a live scan had already counted exactly that burn in a narrow window
    store.put_burn_bucket(from_tick=burn_tick - 5, to_tick=burn_tick + 5,
                          epoch=231, day="2026-09-18", burned=7, burn_events=1)
    assert _rows(store)["2026-09-18"]["burned"] == 7
    pipeline.backfill_burns(FakeClient(head), store,
                            FakeBob(burn_ticks=[burn_tick], indexed=head),
                            from_tick=LO, to_tick=head, max_calls=2000)
    assert _rows(store)["2026-09-18"]["burned"] == 7, "double counted"


def test_unreadable_range_is_reported_not_stored_as_zero():
    head = _tick_at("2026-09-19", hour=6)
    dead = _tick_at("2026-09-18", hour=0)
    store = fresh_store()
    out = pipeline.backfill_burns(FakeClient(head), store,
                                  FakeBob(indexed=head, fail_from=dead),
                                  from_tick=LO, to_tick=head, max_calls=2000)
    assert out["gaps"], "a range Bob refused must surface as a gap"
    assert "2026-09-19" not in _rows(store), "stopped short, so do not claim it"


def test_scan_never_passes_bobs_indexing_tick():
    """An unindexed tick answers empty, which reads as 'no burns'."""
    indexed = _tick_at("2026-09-18", hour=12)
    head = _tick_at("2026-09-20", hour=0)
    bob = FakeBob(indexed=indexed)
    pipeline.backfill_burns(FakeClient(head), fresh_store(), bob,
                            from_tick=LO, max_calls=2000)
    assert bob.calls, "nothing scanned"
    assert max(hi for _, hi in bob.calls) <= indexed


def test_dating_cost_stays_bounded():
    """Bisection, not a walk: a live lookup costs ~0.5 s."""
    head = _tick_at("2026-09-20", hour=0)
    client = FakeClient(head)
    pipeline.backfill_burns(client, fresh_store(), FakeBob(indexed=head),
                            from_tick=LO, to_tick=head, max_calls=2000)
    assert client.lookups < 500, f"{client.lookups} timestamp lookups"


# -- coverage: a measurement over a measurement -----------------------------

def test_a_complete_day_is_not_flagged_partial_at_its_own_length():
    """The bug this replaced: coverage divided by an assumed 2.7 ticks/s, so a
    day fully scanned at the real 1.58/s reported ~60% and warned 'partial'."""
    store = fresh_store()
    lo, hi = 1_000_000, 1_117_845            # 117,846 ticks: a real 09-19
    store.put_day_ticks("2026-09-19", lo, hi, complete=True)
    store.put_burn_bucket(from_tick=lo, to_tick=hi, epoch=231,
                          day="2026-09-19", burned=42, burn_events=1)
    point = pipeline.build_burn_series(store, by="day")["series"][0]
    assert point["period_measured"] is True
    assert point["coverage"] == 1.0
    assert point["partial"] is False


def test_a_partial_scan_of_a_known_day_still_reads_partial():
    """The measured denominator must not flatter an incomplete scan."""
    store = fresh_store()
    lo, hi = 1_000_000, 1_117_845
    store.put_day_ticks("2026-09-19", lo, hi, complete=True)
    store.put_burn_bucket(from_tick=lo, to_tick=lo + 9_999, epoch=231,
                          day="2026-09-19", burned=7, burn_events=1)
    point = pipeline.build_burn_series(store, by="day")["series"][0]
    assert point["partial"] is True
    assert point["coverage"] < 0.10


def test_an_unbounded_day_falls_back_and_says_so():
    """Today is still running: its length is not yet measurable."""
    store = fresh_store()
    store.put_day_ticks("2026-09-20", 1_000_000, 1_010_000, complete=False)
    store.put_burn_bucket(from_tick=1_000_000, to_tick=1_010_000, epoch=231,
                          day="2026-09-20", burned=3, burn_events=1)
    point = pipeline.build_burn_series(store, by="day")["series"][0]
    assert point["period_measured"] is False
    assert point["period_ticks"] == pipeline.TICKS_PER_DAY
