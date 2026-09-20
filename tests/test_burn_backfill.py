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


# ── the once-per-deployment guard ──────────────────────────────────────────
#
# `backfill_burns_days` is what the worker runs on every container start, on a
# host whose watcher restarts the image on every push. Its cost is hundreds of
# heavy calls against a Bob node that is usually someone else's, so "runs once"
# is not a nicety — it is the property that makes it safe to leave switched on.


def test_a_completed_backfill_is_not_repeated_on_the_next_start():
    """The second start must not touch the node at all."""
    head = _tick_at("2026-09-20", hour=1)
    store = fresh_store()
    bob = FakeBob(burn_ticks=[_tick_at("2026-09-18", hour=13)], indexed=head)

    first = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3,
                                         max_calls=2000)
    assert first.get("reason") is None
    assert first["recorded"] is True
    calls_after_first = len(bob.calls)
    assert calls_after_first > 0

    second = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3,
                                          max_calls=2000)
    assert second["reason"] == "already done"
    assert len(bob.calls) == calls_after_first, "the node was scanned twice"


def test_a_backfill_that_hit_a_gap_is_retried_on_the_next_start():
    """A hole is not a finished job: not recorded, so the next start resumes."""
    head = _tick_at("2026-09-20", hour=1)
    store = fresh_store()
    # Bob refuses everything from midway on, so the run stops at a gap.
    bob = FakeBob(indexed=head, fail_from=_tick_at("2026-09-19"))

    out = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3)
    assert out["recorded"] is False
    assert not store.burn_backfill_done(out["from_tick"], out["to_tick"])

    again = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3)
    assert again.get("reason") != "already done"


def test_force_reruns_a_window_already_recorded():
    head = _tick_at("2026-09-20", hour=1)
    store = fresh_store()
    bob = FakeBob(burn_ticks=[_tick_at("2026-09-18", hour=13)], indexed=head)

    pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3)
    before = len(bob.calls)
    out = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3,
                                       force=True)
    assert out.get("reason") is None
    assert len(bob.calls) > before


def test_zero_days_is_off_and_never_calls_the_node():
    """The default. An unset env var must cost nothing."""
    head = _tick_at("2026-09-20", hour=1)
    bob = FakeBob(indexed=head)
    client = FakeClient(head)
    out = pipeline.backfill_burns_days(client, fresh_store(), bob, days=0)
    assert out["reason"] == "disabled"
    assert not bob.calls and client.lookups == 0


def test_the_window_starts_at_a_measured_day_boundary_not_an_assumed_rate():
    """`days=2` must reach back to yesterday's midnight, by measurement.

    Deriving the start from a tick rate is the error this module exists to
    retire: the rate the old code assumed is nearly double the measured one, so
    arithmetic would land on the wrong day.
    """
    head = _tick_at("2026-09-20", hour=6)
    store = fresh_store()
    bob = FakeBob(indexed=head)
    out = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=2)
    # Day 2 of 2 counting back from 2026-09-20 is 2026-09-19.
    expected = _tick_at("2026-09-19")
    assert abs(out["from_tick"] - expected) <= 2, (out["from_tick"], expected)


def test_a_window_older_than_bobs_history_starts_at_bobs_floor():
    """Asking for more history than the node retains must not ask below it."""
    head = _tick_at("2026-09-20", hour=1)
    floor = _tick_at("2026-09-19", hour=12)
    store = fresh_store()
    bob = FakeBob(indexed=head, initial=floor)
    out = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=30)
    assert out["from_tick"] >= floor
    for frm, _to in bob.calls:
        assert frm >= floor


def test_a_run_that_ran_out_of_budget_is_not_recorded_as_done():
    """Found by a live run, not by reasoning: a budget-limited pass stops
    partway with NO gap recorded, so testing `gaps` alone marked a window done
    that had 90% of its ticks uncounted — and the next start would have skipped
    exactly those."""
    head = _tick_at("2026-09-20", hour=1)
    store = fresh_store()
    bob = FakeBob(indexed=head)
    out = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3,
                                       max_calls=2)          # far too few
    assert not out["gaps"], "this test is only meaningful without a gap"
    assert out["complete"] is False
    assert out["reached_tick"] < out["to_tick"]
    assert out["recorded"] is False
    assert not store.burn_backfill_done(out["from_tick"], out["to_tick"])

    # and the next start must therefore still do the work
    again = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3,
                                         max_calls=2)
    assert again.get("reason") != "already done"


def test_a_full_run_reports_complete_and_is_recorded():
    """The other side of the same rule, so it cannot be satisfied by never
    recording anything."""
    head = _tick_at("2026-09-20", hour=1)
    store = fresh_store()
    bob = FakeBob(burn_ticks=[_tick_at("2026-09-19", hour=4)], indexed=head)
    out = pipeline.backfill_burns_days(FakeClient(head), store, bob, days=3,
                                       max_calls=2000)
    assert out["complete"] is True
    assert out["reached_tick"] >= out["to_tick"]
    assert out["recorded"] is True


# ── running beside the live scan ───────────────────────────────────────────
#
# The worker now starts the backfill in the BACKGROUND, because it takes ~9 min
# per day against the public node and the watch loop must not wait that long
# (a missed epoch boundary is not replayable). That puts two writers on one
# SQLite store, so the isolation between them is load-bearing.


def test_a_backfilled_day_does_not_delete_the_live_days_buckets():
    """The backfill writes one wide bucket per past day; the live scan writes
    narrow ones for today. `put_burn_bucket` supersedes by containment, so a
    wide window MUST NOT swallow another day's rows just by overlapping their
    tick range — the delete is scoped to the day, and this pins that."""
    store = fresh_store()
    for i in range(5):
        store.put_burn_bucket(from_tick=600_000 + i * 500, to_tick=600_499 + i * 500,
                              epoch=231, day="2026-09-20", burned=1, burn_events=1,
                              contract_burned=0, contract_events=0, by_contract={})
    # a past day whose measured span overlaps those ticks
    store.put_burn_bucket(from_tick=536_000, to_tick=669_999, epoch=231,
                          day="2026-09-19", burned=1003, burn_events=10,
                          contract_burned=500, contract_events=2,
                          by_contract={"9": 500})
    rows = {r["key"]: r["burned"] for r in store.burn_series(by="day")}
    assert rows == {"2026-09-19": 1003, "2026-09-20": 5}


def test_two_writers_on_one_store_do_not_error():
    """WAL mode plus the store's own lock: concurrent backfill and live-scan
    writes must not raise 'database is locked'."""
    import threading
    store_path = fresh_store().path
    errs = []

    def backfill():
        try:
            s = Store(store_path)
            for i, day in enumerate(["2026-09-16", "2026-09-17", "2026-09-18"]):
                for _ in range(20):
                    s.put_burn_bucket(from_tick=i * 134_000, to_tick=(i + 1) * 134_000 - 1,
                                      epoch=231, day=day, burned=1000 + i, burn_events=10,
                                      contract_burned=0, contract_events=0, by_contract={})
        except Exception as e:                     # noqa: BLE001 - the point is to catch any
            errs.append(repr(e))

    def live():
        try:
            s = Store(store_path)
            for i in range(60):
                s.put_burn_bucket(from_tick=600_000 + i * 500, to_tick=600_499 + i * 500,
                                  epoch=231, day="2026-09-20", burned=1, burn_events=1,
                                  contract_burned=0, contract_events=0, by_contract={})
                s.set_burn_scan_state(last_tick=600_499 + i * 500, first_tick=600_000)
                s.burn_series(by="day")
        except Exception as e:                     # noqa: BLE001
            errs.append(repr(e))

    ts = [threading.Thread(target=backfill), threading.Thread(target=live)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, errs
    days = {r["key"] for r in Store(store_path).burn_series(by="day")}
    assert "2026-09-20" in days and "2026-09-16" in days
