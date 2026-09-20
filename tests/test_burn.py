"""Burn measurement: the traps live data set, pinned as tests.

The RPC's `burnedQus` is an epoch aggregate — measured 2026-09-19, it did not
move across 375 ticks — so the daily series has to be counted from a Bob node's
events (CONCEPT_BURN §2). These tests pin the three things that make that count
right or silently wrong:

  * a burn sink is matched on the FULL 60-character identity, not a short prefix;
  * only the DESTINATION of a transfer marks a burn, never the source (every
    computor payout is credited *from* the null address);
  * a scan window that hits a gap stops there instead of advancing past ticks it
    never read.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr.burn import (
    aggregate_by_day,
    burn_events,
    event_day,
    reconcile,
    scan_range,
)
from qdr.store import Store

import tempfile

# The real null address, measured from Bob's logs: 56 'A' then FXIB, 60 chars.
NULL = "A" * 56 + "FXIB"
COMPUTOR = "C1" + "X" * 58
WALLET = "W1" + "Y" * 58


def fresh_store() -> Store:
    d = tempfile.mkdtemp(prefix="qdr_burn_test_")
    return Store(Path(d) / "qdr.db")


def transfer(frm, to, amount, tick=100, ts="26-09-02 12:00:05"):
    """A tick-log transfer. `ts=None` is the shape a live node really returns."""
    e = {"logTypeName": "QU_TRANSFER", "tick": tick,
         "source": frm, "destination": to, "amount": amount}
    if ts is not None:
        e["timestamp"] = ts
    return e


def burning(amount, contract=19, tick=100, ts="26-09-02 12:00:05"):
    return {"logTypename": "BURNING", "tick": tick, "timestamp": ts,
            "body": {"amount": amount, "contractIndexBurnedFor": contract,
                     "publicKey": "T" + "A" * 55 + "XRFC"}}


# -- the address trap ------------------------------------------------------

def test_full_60_char_null_address_is_recognised_as_a_burn():
    """The exact address Bob reports must count. A 40-char prefix match would
    miss it entirely and total a plausible-looking zero."""
    assert len(NULL) == 60
    events = burn_events([transfer(COMPUTOR, NULL, 1_000_000)])
    assert len(events) == 1
    assert events[0]["amount"] == 1_000_000
    assert events[0]["kind"] == "transfer"


def test_ordinary_wallet_destination_is_not_a_burn():
    assert burn_events([transfer(COMPUTOR, WALLET, 1_000_000)]) == []


def test_source_being_the_null_address_is_not_a_burn():
    """Every computor payout is credited FROM the null address (protocol
    emission). Counting those would report the network's whole revenue as
    burned — the single most expensive way to get this wrong."""
    assert burn_events([transfer(NULL, COMPUTOR, 1_000_000_000)]) == []


def test_an_active_system_address_is_not_a_burn_sink():
    """DAAA...NMIG looks like a burn sink to a prefix test and is not one.

    Measured 2026-09-19: it received 496 transfers of exactly 1 QU over the same
    500 ticks, but the ledger reports 16,311,057 OUTGOING transfers against a
    balance of 4,210 QU — value flows straight back out, so nothing is destroyed.
    The null address reports 0 balance and 0 transfers in either direction.

    `revenue.is_uninformative_identity()` matches both (correctly, for its own
    question about ownership); burn detection must not reuse it.
    """
    active_system_addr = "D" + "A" * 55 + "NMIG"
    assert len(active_system_addr) == 60
    assert burn_events([transfer(WALLET, active_system_addr, 1)]) == []


def test_zero_amount_transfers_are_ignored():
    """Computors emit amount=0 solution submissions to the null address
    constantly; they move no value and must not inflate the event count."""
    assert burn_events([transfer(COMPUTOR, NULL, 0)]) == []


# -- contract burns --------------------------------------------------------

def test_burning_events_are_counted_separately_with_their_contract():
    events = burn_events([burning(450_000, contract=19)])
    assert len(events) == 1
    assert events[0]["kind"] == "contract"
    assert events[0]["contract"] == 19
    assert events[0]["amount"] == 450_000


def test_zero_amount_burning_events_are_dropped():
    """Epochs 225-228 each carried BURNING entries with amount 0."""
    assert burn_events([burning(0, contract=10)]) == []


def test_transfer_and_contract_burns_are_never_summed_into_one_figure():
    """They differ by six orders of magnitude; folding them into one number
    makes the attributable burns vanish."""
    agg = aggregate_by_day(burn_events([
        transfer(COMPUTOR, NULL, 1_000_000),
        burning(450_000, contract=19),
    ]))
    day = agg["2026-09-02"]
    assert day["burned"] == 1_000_000
    assert day["contract_burned"] == 450_000
    assert day["by_contract"] == {"19": 450_000}


# -- dating ----------------------------------------------------------------

def test_two_digit_year_timestamps_resolve_to_the_right_century():
    assert event_day({"timestamp": "26-09-02 12:00:05"}) == "2026-09-02"


def test_live_tick_log_entries_carry_no_timestamp():
    """Measured against a live node 2026-09-19: an ordinary tick-log entry has
    tick/epoch/logId/addresses/amount and NO time field, and no RPC endpoint
    exposes a tick's wall time either. The daily series depends on knowing this,
    so it is pinned rather than assumed."""
    live_shape = {"logTypeName": "QU_TRANSFER", "tick": 80822570, "epoch": 231,
                  "logId": 5104955, "source": COMPUTOR, "destination": NULL,
                  "amount": 1_000_000}
    assert event_day(live_shape) is None
    events = burn_events([live_shape])
    assert len(events) == 1 and events[0]["day"] is None


def test_undated_events_are_dropped_when_no_observation_day_is_given():
    """A burn on the wrong day is worse than one we openly did not date. This is
    what makes backfilling old ticks record nothing instead of misdating them."""
    agg = aggregate_by_day(burn_events([transfer(COMPUTOR, NULL, 5_000, ts="")]))
    assert agg == {}


def test_undated_events_take_the_observation_day_when_scanning_the_head():
    """The worker scans minutes behind the head, so 'the day we counted it' is
    the day it happened. That is an observation, not an assumed tick rate."""
    agg = aggregate_by_day(burn_events([transfer(COMPUTOR, NULL, 5_000, ts="")]),
                           default_day="2026-09-19")
    assert agg["2026-09-19"]["burned"] == 5_000


def test_an_events_own_timestamp_wins_over_the_observation_day():
    """End-epoch entries do carry a stamp; it must not be overridden."""
    agg = aggregate_by_day(burn_events([burning(450_000, ts="26-09-02 12:00:05")]),
                           default_day="2026-09-19")
    assert "2026-09-02" in agg and "2026-09-19" not in agg


def test_a_window_spanning_midnight_splits_into_two_days():
    """A scan window is a unit of work, not of reporting."""
    agg = aggregate_by_day(burn_events([
        transfer(COMPUTOR, NULL, 1_000_000, tick=100, ts="26-09-02 23:59:58"),
        transfer(COMPUTOR, NULL, 2_000_000, tick=101, ts="26-09-03 00:00:02"),
    ]))
    assert agg["2026-09-02"]["burned"] == 1_000_000
    assert agg["2026-09-03"]["burned"] == 2_000_000


# -- scanning --------------------------------------------------------------

class StubBob:
    """Serves canned logs per chunk; `fail_from` makes a chunk unavailable.

    Entries carry NO timestamp, matching what a live node actually returns for a
    tick range — the dating rules only mean anything against that shape.
    """

    def __init__(self, per_chunk=2, fail_from=None, indexed_tick=None):
        self.per_chunk = per_chunk
        self.fail_from = fail_from
        self.indexed_tick = indexed_tick
        self.calls = []

    def status(self):
        return {"currentIndexingTick": self.indexed_tick} if self.indexed_tick else {}

    def tick_logs(self, from_tick, to_tick):
        self.calls.append((from_tick, to_tick))
        if self.fail_from is not None and from_tick >= self.fail_from:
            from qdr.bob import BobError
            raise BobError("node has no logs for that range")
        # A node returns an EMPTY log for ticks it has not indexed yet — not an
        # error, which is exactly what makes those ticks dangerous to skip.
        if self.indexed_tick is not None and from_tick > self.indexed_tick:
            return []
        return [transfer(COMPUTOR, NULL, 1_000_000, tick=from_tick, ts=None)
                for _ in range(self.per_chunk)]


def test_scan_counts_every_chunk_in_the_range():
    bob = StubBob(per_chunk=3)
    out = scan_range(bob, 1000, 1999, epoch=231, chunk=500,
                     default_day="2026-09-19")
    assert out["complete"]
    assert out["scanned_to"] == 1999
    assert out["days"]["2026-09-19"]["burned"] == 6_000_000   # 2 chunks x 3
    assert out["days"]["2026-09-19"]["burn_events"] == 6


def test_scan_without_an_observation_day_counts_ticks_but_dates_nothing():
    """Tick logs carry no timestamp, so a scan that may not date its events
    reads them and records no day — never today by default."""
    bob = StubBob(per_chunk=3)
    out = scan_range(bob, 1000, 1999, epoch=231, chunk=500)
    assert out["complete"] and out["scanned_to"] == 1999
    assert out["days"] == {}


def test_scan_stops_at_a_gap_rather_than_advancing_past_unread_ticks():
    """Advancing the pointer over a range Bob could not serve would lose those
    burns permanently — the next pass must retry from the gap."""
    bob = StubBob(per_chunk=1, fail_from=1500)
    out = scan_range(bob, 1000, 1999, epoch=231, chunk=500)
    assert not out["complete"]
    assert out["scanned_to"] == 1499          # not 1999
    assert out["gaps"] == [[1500, 1999]]


def test_scan_respects_its_call_budget():
    """One pass must not monopolise the worker or the public node."""
    bob = StubBob(per_chunk=1)
    out = scan_range(bob, 0, 99_999, epoch=231, chunk=500, max_calls=3)
    assert out["calls"] == 3
    assert not out["complete"]
    assert out["scanned_to"] == 1499


# -- store round-trip ------------------------------------------------------

def test_rescanning_a_window_overwrites_instead_of_doubling():
    """A retry must be safe: scanning the same ticks twice must not double the
    day's burn."""
    s = fresh_store()
    for _ in range(2):
        s.put_burn_bucket(1000, 1499, epoch=231, day="2026-09-02",
                          burned=5_000_000, burn_events=5)
    series = s.burn_series(by="day")
    assert len(series) == 1
    assert series[0]["burned"] == 5_000_000


def test_a_wider_rescan_supersedes_the_buckets_it_covers():
    """Overlapping windows double-count, and the primary key does not stop them:
    it only dedupes an identical window.

    Measured live: a catch-up pass re-read ticks 80,812,215-80,829,173, landed
    beside two earlier buckets sitting inside that range, and the day's total
    came out 13.1% high. The wider measurement counted exactly the same events,
    so it supersedes them.
    """
    s = fresh_store()
    s.put_burn_bucket(1000, 1499, 231, "2026-09-19", burned=1_000_000, burn_events=1)
    s.put_burn_bucket(1500, 1999, 231, "2026-09-19", burned=2_000_000, burn_events=2)
    # a catch-up pass re-reads the whole span in one window
    s.put_burn_bucket(500, 2499, 231, "2026-09-19", burned=9_000_000, burn_events=9)

    series = s.burn_series(by="day")
    assert len(series) == 1
    assert series[0]["burned"] == 9_000_000, "contained buckets were counted twice"
    assert series[0]["events"] == 9


def test_a_bucket_outside_the_rescanned_window_survives():
    """Superseding must not reach beyond what the new window actually covered."""
    s = fresh_store()
    s.put_burn_bucket(1000, 1499, 231, "2026-09-19", burned=1_000_000, burn_events=1)
    s.put_burn_bucket(5000, 5499, 231, "2026-09-19", burned=4_000_000, burn_events=4)
    s.put_burn_bucket(900, 2000, 231, "2026-09-19", burned=3_000_000, burn_events=3)
    assert s.burn_series(by="day")[0]["burned"] == 7_000_000   # 3M + untouched 4M


def test_superseding_is_scoped_to_the_day():
    """A window is written per day; it must not delete another day's buckets."""
    s = fresh_store()
    s.put_burn_bucket(1000, 1499, 231, "2026-09-18", burned=1_000_000, burn_events=1)
    s.put_burn_bucket(900, 2000, 231, "2026-09-19", burned=3_000_000, burn_events=3)
    days = {r["key"]: r["burned"] for r in s.burn_series(by="day")}
    assert days == {"2026-09-18": 1_000_000, "2026-09-19": 3_000_000}


def test_series_groups_by_day_epoch_and_year_from_one_table():
    s = fresh_store()
    s.put_burn_bucket(1000, 1499, 231, "2026-09-02", burned=1_000_000, burn_events=1)
    s.put_burn_bucket(1500, 1999, 231, "2026-09-03", burned=2_000_000, burn_events=2)
    s.put_burn_bucket(2000, 2499, 232, "2027-01-04", burned=4_000_000, burn_events=4)

    days = s.burn_series(by="day")
    assert [d["key"] for d in days] == ["2026-09-02", "2026-09-03", "2027-01-04"]

    epochs = s.burn_series(by="epoch")
    assert {e["key"]: e["burned"] for e in epochs} == {"231": 3_000_000, "232": 4_000_000}

    years = s.burn_series(by="year")
    assert {y["key"]: y["burned"] for y in years} == {"2026": 3_000_000, "2027": 4_000_000}


def test_contract_burns_fold_across_buckets():
    s = fresh_store()
    s.put_burn_bucket(1000, 1499, 231, "2026-09-02", contract_burned=450_000,
                      contract_events=1, by_contract={"19": 450_000})
    s.put_burn_bucket(1500, 1999, 231, "2026-09-02", contract_burned=28_850,
                      contract_events=1, by_contract={"9": 28_850})
    assert s.burn_by_contract(231) == {"19": 450_000, "9": 28_850}


def test_recorded_epoch_total_is_not_walked_back_by_a_stale_reading():
    s = fresh_store()
    s.put_burn_total(231, 53_662_829_138_067, circulating=177_337_170_861_933)
    s.put_burn_total(231, 1)          # a stale RPC read must not overwrite it
    assert s.latest_burn_total()["burned_total"] == 53_662_829_138_067


def test_first_measured_tick_is_recorded_once_and_never_moves():
    """The series is labelled against where our own measurement begins; if that
    pointer drifted forward, measured history would silently become 'derived'."""
    s = fresh_store()
    s.set_burn_scan_state(last_tick=1499, first_tick=1000)
    s.set_burn_scan_state(last_tick=2999)
    state = s.burn_scan_state()
    assert state["first_tick"] == 1000
    assert state["last_tick"] == 2999


# -- the dating lag rule ---------------------------------------------------

class StubRpc:
    def __init__(self, tick, epoch=231):
        self._tick, self._epoch = tick, epoch

    def tick_info(self):
        return {"tick": self._tick, "epoch": self._epoch}


def test_a_scan_near_the_head_dates_its_burns():
    from qdr import pipeline
    s = fresh_store()
    head = 1_000_000
    s.set_burn_scan_state(last_tick=head - 600, first_tick=head - 600)
    out = pipeline.scan_burns(StubRpc(head), s, StubBob(per_chunk=2), max_calls=2)
    assert out["days_written"] == 1
    assert s.burn_series(by="day")[0]["burned"] > 0


def test_a_pass_that_is_behind_buys_the_calls_it_needs_to_catch_up():
    """The watch loop does not run at its nominal interval — the balance
    snapshot ahead of it takes ~20 min (676 sequential RPC calls at ~1.75 s,
    measured), so the burn scan comes round on that cadence instead. A fixed
    call budget left almost no margin once the gap stretched, so a pass sizes
    its budget to the backlog."""
    from qdr import pipeline
    s = fresh_store()
    head = 1_000_000
    gap = 20_000                       # ~2 h of chain, well past a 20-call budget
    s.set_burn_scan_state(last_tick=head - gap, first_tick=head - gap)
    bob = StubBob(per_chunk=1)
    out = pipeline.scan_burns(StubRpc(head), s, bob, max_calls=20)

    assert out["complete"], "a catch-up pass must reach the head"
    assert out["behind"] <= 1
    assert out["calls"] > 20, "budget did not grow with the backlog"


def test_a_catch_up_pass_is_still_bounded():
    """Unbounded catch-up would let one pass monopolise the worker and hammer a
    public node."""
    from qdr import pipeline
    s = fresh_store()
    head = 10_000_000
    s.set_burn_scan_state(last_tick=head - 90_000, first_tick=head - 90_000)
    bob = StubBob(per_chunk=1)
    out = pipeline.scan_burns(StubRpc(head), s, bob, max_calls=20)
    assert out["calls"] <= pipeline.CATCHUP_MAX_CALLS


def test_a_scan_far_behind_the_head_skips_to_the_head_and_reports_the_hole():
    """After an outage the old ticks cannot be dated (tick logs carry no time).

    Grinding through them would spend every pass on ticks it must discard while
    today's burns scroll past uncounted — the scan would never catch up. So it
    jumps to the head, keeps measuring the present, and reports the gap instead
    of quietly presenting the series as continuous.
    """
    from qdr import pipeline
    s = fresh_store()
    head = 1_000_000
    stale = head - 500_000
    s.set_burn_scan_state(last_tick=stale, first_tick=stale)
    bob = StubBob(per_chunk=2)
    out = pipeline.scan_burns(StubRpc(head), s, bob, max_calls=2)

    assert out["skipped_undatable"][0] == stale + 1
    assert out["from_tick"] > stale + 1          # jumped forward
    assert out["behind"] <= 1                    # and caught up to the head
    assert out["days_written"] == 1              # today is being measured again
    assert bob.calls[0][0] == out["from_tick"]   # no wasted calls on old ticks


def test_the_scan_stops_at_bobs_indexing_tick_not_the_chain_head():
    """Measured 2026-09-19: Bob indexes ~36 ticks behind the chain head, and
    returns an EMPTY log for ticks it has not reached — indistinguishable from
    "no burns happened". Advancing the pointer over those would lose their burns
    permanently, so the scan stops where the node has actually indexed."""
    from qdr import pipeline
    s = fresh_store()
    head, indexed = 1_000_000, 999_900
    s.set_burn_scan_state(last_tick=head - 1_000, first_tick=head - 1_000)
    bob = StubBob(per_chunk=2, indexed_tick=indexed)
    out = pipeline.scan_burns(StubRpc(head), s, bob, max_calls=5)

    assert out["to_tick"] <= indexed
    assert s.burn_scan_state()["last_tick"] <= indexed
    assert all(f <= indexed for f, _ in bob.calls)


# -- reconciliation --------------------------------------------------------

def test_coverage_reports_a_shortfall_rather_than_hiding_it():
    out = reconcile(measured_delta=900, official_delta=1000)
    assert out["ratio"] == 0.9
    assert out["missing"] == 100


def test_coverage_is_null_when_the_anchor_has_not_stepped_yet():
    """An official delta of 0 means the epoch counter has not published, not
    that coverage is perfect."""
    out = reconcile(measured_delta=5_000, official_delta=0)
    assert out["ratio"] is None


# --- reconciliation against the protocol's own counter ----------------------
# Added 2026-09-20 after the page published 17.6 Mrd QU for "19.09." while the
# official burnedQus counter moved by exactly 0 over the following 66,666 ticks.
# Every counted transfer was EXACTLY 1,000,000 QU at ~2 per tick: a fixed
# protocol fee, which the official counter does not treat as burned supply.
# The measurement was also an hour of ticks labelled as a day.

def test_an_unchecked_figure_is_labelled_unreconciled(tmp_path):
    """Without two epoch anchors nothing has been verified, and the payload must
    say so rather than presenting the count as confirmed."""
    from qdr.store import Store
    from qdr import pipeline

    store = Store(str(tmp_path / "q.db"))
    store.put_burn_bucket(from_tick=1000, to_tick=11000, epoch=231,
                          day="2026-09-19", burned=17_636_000_020, burn_events=17_638)
    store.put_burn_total(231, 53_662_829_138_067)

    rec = pipeline.build_burn_series(store, by="day")["reconciliation"]

    assert rec["status"] == "unreconciled"
    assert rec["official_total"] == 53_662_829_138_067
    assert rec["steps"] == []


def test_a_count_the_official_counter_contradicts_is_flagged(tmp_path):
    """The real failure: we count billions while the counter's step says otherwise."""
    from qdr.store import Store
    from qdr import pipeline

    store = Store(str(tmp_path / "q.db"))
    store.put_burn_total(230, 53_000_000_000_000)
    store.put_burn_total(231, 53_000_000_050_000)      # the epoch really burned 50,000
    store.put_burn_bucket(from_tick=1, to_tick=100_000, epoch=231,
                          day="2026-09-19", burned=17_636_000_020, burn_events=17_638)

    rec = pipeline.build_burn_series(store, by="day")["reconciliation"]

    assert rec["status"] == "diverging", "a 350,000x overstatement passed as consistent"
    assert rec["reason"]


def test_a_measurement_matching_the_counter_is_consistent(tmp_path):
    from qdr.store import Store
    from qdr import pipeline

    store = Store(str(tmp_path / "q.db"))
    store.put_burn_total(230, 53_000_000_000_000)
    store.put_burn_total(231, 53_000_000_100_000)
    store.put_burn_bucket(from_tick=1, to_tick=100_000, epoch=231,
                          day="2026-09-19", burned=98_000, burn_events=98)

    rec = pipeline.build_burn_series(store, by="day")["reconciliation"]
    assert rec["status"] == "consistent"
    assert abs(rec["steps"][0]["ratio"] - 0.98) < 0.01


def test_an_hour_of_ticks_is_not_labelled_a_whole_day(tmp_path):
    """10,000 ticks is ~1 hour. Presented as a day's bar it reads as 24x what was
    measured, which is exactly how the 17.6 Mrd figure reached the page."""
    from qdr.store import Store
    from qdr import pipeline

    store = Store(str(tmp_path / "q.db"))
    store.put_burn_bucket(from_tick=80_820_402, to_tick=80_830_402, epoch=231,
                          day="2026-09-19", burned=17_636_000_020, burn_events=17_638)

    point = pipeline.build_burn_series(store, by="day")["series"][0]

    assert point["partial"] is True
    # ~10,000 ticks is about 1.8 hours of a real day (measured 1.58 ticks/s ->
    # ~136,500 ticks/day). The bound was 0.05 when the code divided by an
    # assumed 2.7 ticks/s; that denominator was 71% too large, so it flattered
    # every coverage figure. 0.10 is the same claim against the measured day.
    assert point["coverage"] < 0.10, "an hour must not look like a full day"
    assert point["scanned_ticks"] == 10_001


def test_a_full_day_of_ticks_is_not_flagged_partial(tmp_path):
    from qdr.store import Store
    from qdr import pipeline

    store = Store(str(tmp_path / "q.db"))
    store.put_burn_bucket(from_tick=1, to_tick=pipeline.TICKS_PER_DAY, epoch=231,
                          day="2026-09-19", burned=5_000, burn_events=5)

    point = pipeline.build_burn_series(store, by="day")["series"][0]
    assert point["partial"] is False
    assert point["coverage"] == 1.0


def test_the_headline_endpoint_carries_the_same_verdict(tmp_path):
    """The KPI tiles are the largest numbers on the page; the caveat has to reach
    them, not only the chart below."""
    from qdr.store import Store
    from qdr import pipeline

    store = Store(str(tmp_path / "q.db"))
    store.put_burn_total(231, 53_662_829_138_067)
    store.put_burn_bucket(from_tick=1, to_tick=10_000, epoch=231,
                          day="2026-09-19", burned=17_636_000_020, burn_events=17_638)

    latest = pipeline.build_burn_latest(store)
    assert latest["reconciliation"]["status"] == "unreconciled"


def test_the_burn_page_shows_the_caveat_in_both_languages():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parents[1]
            / "dashboard" / "burn.html").read_text(encoding="utf-8")

    assert "burn-warn" in html and "drawWarning" in html
    for key in ("warnDiverging:", "warnUnreconciled:", "warnPartial:"):
        assert html.count(key) == 2, f"{key} missing from one language"


# --- refunded round trips are not burns -------------------------------------
# Measured 2026-09-20 over 1,000 ticks: 1,696 transactions each sent exactly
# 1,000,000 QU to the null address and had the identical amount returned to the
# payer WITHIN THE SAME TRANSACTION. 1,696,000,000 QU in, 1,696,000,000 out, net
# zero. Counting only the inbound leg reported 17.6 Mrd QU burned in an hour
# while burnedQus did not move at all.

def _tx(hash_: str, *legs) -> list[dict]:
    """Log entries for one transaction; each leg is (source, destination, amount)."""
    return [{"logTypeName": "QU_TRANSFER", "tick": 80_820_402,
             "transactionHash": hash_, "source": src,
             "destination": dst, "amount": amt}
            for src, dst, amt in legs]


PAYER = "CDQVMLIROQKVRCVEZBVZSDEJEQOCALENVYYMBNODOFKKBIUFESUFTTOBKGEM"


def test_a_refunded_round_trip_is_not_a_burn():
    """The exact shape seen on chain, and the whole cause of the 17.6 Mrd figure."""
    from qdr.burn import burn_events, NULL_ADDRESS

    logs = _tx("abc",
               (PAYER, NULL_ADDRESS, 1_000_000),
               (NULL_ADDRESS, PAYER, 1_000_000))

    assert burn_events(logs) == [], "a round trip that nets to zero was counted"


def test_only_the_unrefunded_remainder_counts():
    from qdr.burn import burn_events, NULL_ADDRESS

    logs = _tx("abc",
               (PAYER, NULL_ADDRESS, 1_000_000),
               (NULL_ADDRESS, PAYER, 600_000))

    events = burn_events(logs)
    assert len(events) == 1
    assert events[0]["amount"] == 400_000


def test_a_transfer_that_stays_in_the_sink_is_still_a_burn():
    """The fix must not silence real burns — only refunded ones."""
    from qdr.burn import burn_events, NULL_ADDRESS

    events = burn_events(_tx("abc", (PAYER, NULL_ADDRESS, 250_000)))

    assert len(events) == 1 and events[0]["amount"] == 250_000


def test_a_refund_in_a_different_transaction_does_not_cancel_a_burn():
    """Netting is per transaction. A payout that merely shares a tick with someone
    else's burn must not erase it."""
    from qdr.burn import burn_events, NULL_ADDRESS

    logs = (_tx("burn-tx", (PAYER, NULL_ADDRESS, 500_000))
            + _tx("payout-tx", (NULL_ADDRESS, "SOMEONE" + "A" * 53, 9_000_000)))

    events = burn_events(logs)
    assert len(events) == 1 and events[0]["amount"] == 500_000


def test_a_payout_alone_never_becomes_a_negative_burn():
    """Computor payouts are credited FROM the null address. Netting must floor at
    zero rather than subtract the network's emission from the burn total."""
    from qdr.burn import burn_events, NULL_ADDRESS

    logs = _tx("payout", (NULL_ADDRESS, PAYER, 178_477_462_349))

    assert burn_events(logs) == []


def test_many_round_trips_sum_to_nothing():
    """1,696 of these produced a 17.6 Mrd/hour headline. They must produce zero."""
    from qdr.burn import burn_events, NULL_ADDRESS

    logs = []
    for i in range(200):
        logs += _tx(f"tx{i}",
                    (PAYER, NULL_ADDRESS, 1_000_000),
                    (NULL_ADDRESS, PAYER, 1_000_000))

    events = burn_events(logs)
    assert sum(e["amount"] for e in events) == 0
    assert events == []


def test_a_leg_without_a_transaction_hash_still_counts():
    """An unpairable leg cannot be shown to be refunded, so it is kept — the
    conservative reading, and it keeps older log shapes working."""
    from qdr.burn import burn_events, NULL_ADDRESS

    logs = [{"logTypeName": "QU_TRANSFER", "tick": 1,
             "source": PAYER, "destination": NULL_ADDRESS, "amount": 700_000}]

    events = burn_events(logs)
    assert len(events) == 1 and events[0]["amount"] == 700_000


def test_contract_burns_are_untouched_by_the_netting():
    """BURNING events are a different mechanism (qpi.burn) and have no refund leg."""
    from qdr.burn import burn_events

    logs = [{"logTypeName": "BURNING", "tick": 80_820_893, "amount": 50_801,
             "contractIndexBurnedFor": 13, "transactionHash": "zzz"}]

    events = burn_events(logs)
    assert len(events) == 1
    assert events[0]["kind"] == "contract" and events[0]["contract"] == 13
