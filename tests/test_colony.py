"""The colony's call: rebuilt from public data, written down once, judged honestly.

Pinned here:

  * ANT_SOLU log entries parse to the solution and its address (tick, tx index),
    and a lineage walks back to the root -- or is reported incomplete;
  * a solution is graded on the live hours stored in a tick BEFORE its anchor;
  * a call is recorded only from a network that reproduced every logged error,
    only while its hour is still unknown, and never rewritten;
  * verdicts and rates: "unsure" is a miss, an hour not yet stored is no verdict,
    and the naive baselines are computed on the same hours;
  * the API carries calls and rates, and /v1/btc/summary stays small and wordy.

The replayer is a fake here (the real one is C++, tools/replayer, checked
bit-exact against qubic/core's ground truth when it is built). No network.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import antask, antnode, colony  # noqa: E402
from qdr.store import Store  # noqa: E402

BASE = 40316
W = 8760


def sol_hex(parent=(0, 0xFFFFFFFF), anchor=100, score=4300, result=0, pk=b"\x01" * 32, nonce=b"\x02" * 32):
    raw = b"ANT_SOLU" + pk + nonce + struct.pack("<IIIII", parent[0], parent[1], anchor, score, result)
    return raw.hex()


def entry(tick, idx, **kw):
    return {"tick": tick, "transactionIndex": idx, "logType": 255, "rawData": "0x" + sol_hex(**kw)}


# -- logs and lineage -------------------------------------------------------------

def test_a_solution_log_parses_to_its_address_and_parent():
    s = colony.parse_solution(entry(500, 7, parent=(400, 3), anchor=490, score=4100))
    assert (s["tick"], s["tx_index"], s["parent_tick"], s["parent_index"]) == (500, 7, 400, 3)
    assert s["anchor_tick"] == 490 and s["score"] == 4100 and s["result"] == 0
    assert s["pubkey"] == "01" * 32
    assert colony.parse_solution({"tick": 1, "transactionIndex": 0, "rawData": "0x" + "00" * 92}) is None


def test_lineage_walks_to_the_root_or_reports_a_gap(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    rows = [colony.parse_solution(e) for e in (
        entry(100, 1), entry(200, 2, parent=(100, 1)), entry(300, 0, parent=(200, 2)))]
    store.put_ant_solutions(240, rows)
    chain = colony.lineage(store, 300, 0)
    assert [(r["tick"], r["tx_index"]) for r in chain] == [(100, 1), (200, 2), (300, 0)]
    store.put_ant_solutions(240, [colony.parse_solution(entry(400, 0, parent=(999, 9)))])
    assert colony.lineage(store, 400, 0) is None


def test_scan_keeps_only_accepted_solutions_and_resumes(tmp_path):
    store = Store(str(tmp_path / "q.db"))

    class Bob:
        def __init__(self):
            self.asked = []

        def _call(self, method, params):
            self.asked.append(params[0])
            assert params[0]["logType"] == 255
            return [entry(101, 0), entry(102, 1, result=4), {"tick": 103, "transactionIndex": 0, "rawData": "0xabcd"}]

    bob = Bob()
    out = colony.scan(store, bob, 240, initial_tick=100, head=120, chunk=50)
    assert out["new"] == 1 and store.ant_solution_count(240) == 1
    assert store.colony_scan_tick(240) == 120
    colony.scan(store, bob, 240, initial_tick=100, head=130, chunk=50)
    assert bob.asked[-1]["fromTick"] == 121


def test_shift_limit_counts_hours_stored_before_the_anchor():
    ticks = [1000, 1010, 1020]
    assert colony.shift_limit_at(1010, ticks, 168) == 1       # stored IN 1010 does not count
    assert colony.shift_limit_at(1011, ticks, 168) == 2
    assert colony.shift_limit_at(5000, ticks, 2) == 2


# -- verdicts ----------------------------------------------------------------------

def test_unsure_is_an_abstention_and_an_unknown_hour_no_verdict():
    # CFB, 2026-11-24: "?" is an abstention -- neither a hit nor a miss.
    judged = colony.judge([{"hour": 1, "call": 1, "up": 1}, {"hour": 2, "call": 2, "up": 0},
                           {"hour": 3, "call": 0, "up": None}, {"hour": 4, "call": 0, "up": 1}])
    assert [c["hit"] for c in judged] == [True, None, None, False]
    assert [c["abstained"] for c in judged] == [False, True, False, False]
    r = colony.rate(judged)
    assert r == {"calls": 2, "hits": 1, "unsure": 1, "rate": 0.5}


def test_baselines_are_scored_on_the_same_hours():
    hours = [{"hour": h, "up": u} for h, u in ((1, 1), (2, 1), (3, 0), (4, 0))]
    judged = colony.judge([{"hour": h, "call": 1, "up": u} for h, u in ((2, 1), (3, 0), (4, 0))])
    b = colony.baselines(hours, judged)
    assert b["always_up"] == round(1 / 3, 4)
    assert b["always_down"] == round(2 / 3, 4)
    assert b["repeat_last"] == round(2 / 3, 4)      # 1->1 yes, 1->0 no, 0->0 yes


def test_a_streak_needs_hits_in_consecutive_hours():
    judged = colony.judge([{"hour": h, "call": 1, "up": u} for h, u in
                           ((1, 1), (2, 1), (3, 0), (4, 1), (5, 1), (6, 1), (8, 1), (9, 1))])
    # 4-6 is the longest run; hour 7 has no call, so 8-9 starts a new one.
    assert colony.streak(judged) == {"longest": 3, "current": 2}
    assert colony.streak([]) == {"longest": 0, "current": 0}


def test_run_chance_is_what_luck_alone_gives():
    assert colony.run_chance(1, 1) == 0.5
    assert colony.run_chance(2, 2) == 0.25
    assert colony.run_chance(3, 2) == 0.375          # HHx, xHH minus HHH counted twice
    assert colony.run_chance(4, 5) == 0.0
    assert colony.run_chance(168, 5) == 0.9409       # a streak of 5 is near certain per epoch
    assert 0.44 < colony.run_chance(38, 5) < 0.46


def test_a_call_is_never_rewritten(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    row = {"hour": BASE + 3, "epoch": 240, "call": 1, "net_tick": 5, "net_index": 0, "pubkey": "aa",
           "net_error": 3500, "net_shift": 2, "made_at": 100}
    assert store.put_btc_call(row) is True
    assert store.put_btc_call(dict(row, call=0, made_at=200)) is False
    assert store.btc_calls()[0]["call"] == 1


# -- the tracker with a fake replayer ------------------------------------------------

class FakeReplayer:
    def __init__(self, errors: dict, trits: str = "0110"):
        self.path = "fake"
        self.errors, self.trits = errors, trits
        self.seed, self.known, self.walked = None, {}, []

    def start(self, seed):
        self.seed, self.known = seed, {}

    def close(self):
        self.seed, self.known = None, {}

    def targets(self, bits):
        self.bits = bits

    def load(self, ref, shift, ann, error):
        self.known[ref] = (error, shift)

    def walk(self, ref, parent, pubkey, nonce, anchor, limit):
        self.walked.append((ref, parent, limit))
        self.known[ref] = (self.errors[ref], 0)
        return self.errors[ref], 0

    def dump(self, ref):
        return 0, "00"

    def calls(self, ref, n):
        return self.trits[:n]


@pytest.fixture()
def live(monkeypatch, tmp_path):
    """A node answering for epoch 240 with two live hours; next hour BASE + 3."""
    targets = bytes([i % 2 for i in range(W)] + [0, 1])
    data_hash = antask.k12_hex(targets[:W])

    class Ctx:
        spectrum_digest = "ab" * 32
    Ctx.data_hash = data_hash

    class Info:
        tick, initial_tick, source_ip = 2000, 100, "203.0.113.7"

    task = {"epoch": 240, "base_hour": BASE, "shift_limit": 2, "targets": targets}
    series = {"next_hour": BASE + 3, "points": [{"hour": BASE + 1, "append_tick": 150, "flags": 0},
                                                {"hour": BASE + 2, "append_tick": 250, "flags": 0}]}
    monkeypatch.setattr(antnode, "best_epoch_context", lambda peers: Ctx)
    monkeypatch.setattr(antnode, "query_system_info", lambda ip: Info)
    monkeypatch.setattr(antnode, "read_feed", lambda peers, info: (task, series))
    monkeypatch.setattr(antnode, "fetch_peers", lambda: ["203.0.113.7"])
    monkeypatch.setattr(antnode, "query_price_series", lambda ip, h, n: {"next_hour": BASE + 3})
    monkeypatch.setattr(colony.Replayer, "available", staticmethod(lambda path="": True))
    monkeypatch.setattr(colony, "scan", lambda *a, **k: {"calls": 0, "new": 0})
    monkeypatch.setattr(colony, "indexed_head", lambda bob, tick: tick)
    # Half way through the running hour BASE+3; the coming hour BASE+4 starts
    # when BASE+3 ends.
    monkeypatch.setattr(colony.time, "time", lambda: antask.hour_at(BASE + 3) - 1800)
    store = Store(str(tmp_path / "q.db"))
    store.put_ant_solutions(240, [colony.parse_solution(e) for e in (
        entry(200, 0, anchor=120, score=4300), entry(300, 1, parent=(200, 0), anchor=260, score=4200))])
    store.put_colony_anchor(120, "11" * 32)
    store.put_colony_anchor(260, "22" * 32)
    return store


def test_a_verified_lineage_calls_the_coming_hour_before_it_begins(live):
    rep = FakeReplayer({"200:0": 4300, "300:1": 4200}, trits="0001")
    tracker = colony.Tracker(live, rep)
    st = tracker.step(object(), ["203.0.113.7"])
    assert st["state"] == "tracking", st
    # Anchors 120 and 260: 0 and 2 live hours stored before them.
    assert [w[2] for w in rep.walked] == [0, 2]
    calls = live.btc_calls()
    assert len(calls) == 1 and calls[0]["hour"] == BASE + 4
    assert calls[0]["call"] == 1            # trits[j], j = hour - base - 1 = 3
    assert calls[0]["made_at"] < antask.hour_at(BASE + 4) - 3600
    assert calls[0]["net_tick"] == 300 and calls[0]["net_error"] == 4200


def test_no_call_while_the_lineage_is_still_being_caught_up(live, monkeypatch):
    monkeypatch.setattr(colony, "WALKS_PER_STEP", 1)
    rep = FakeReplayer({"200:0": 4300, "300:1": 4200}, trits="0001")
    tracker = colony.Tracker(live, rep)
    st = tracker.step(object(), ["x"])
    assert st["state"] == "catching-up" and st["call"] is None
    assert live.btc_calls() == []
    st = tracker.step(object(), ["x"])          # the leaf is reached: now it calls
    assert st["state"] == "tracking" and live.btc_calls()[0]["net_tick"] == 300


def test_a_rebuild_that_misses_the_logged_error_records_nothing(live):
    rep = FakeReplayer({"200:0": 4300, "300:1": 4199})
    st = colony.Tracker(live, rep).step(object(), ["203.0.113.7"])
    assert st["state"] == "mismatch"
    assert live.btc_calls() == []


def test_no_call_once_the_hour_has_arrived(live, monkeypatch):
    monkeypatch.setattr(antnode, "query_price_series", lambda ip, h, n: {"next_hour": BASE + 4})
    colony.Tracker(live, FakeReplayer({"200:0": 4300, "300:1": 4200})).step(object(), ["x"])
    assert live.btc_calls() == []


def test_no_call_once_the_coming_hour_has_begun(live, monkeypatch):
    monkeypatch.setattr(colony.time, "time", lambda: antask.hour_at(BASE + 3) + 10)
    colony.Tracker(live, FakeReplayer({"200:0": 4300, "300:1": 4200}, trits="0001")).step(object(), ["x"])
    assert live.btc_calls() == []


def test_without_the_replayer_it_says_so(live, monkeypatch):
    monkeypatch.setattr(colony.Replayer, "available", staticmethod(lambda path="": False))
    st = colony.Tracker(live, FakeReplayer({})).step(object(), ["x"])
    assert st["state"] == "no-replayer"
    assert live.colony_status()["state"] == "no-replayer"


# -- API -------------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.server as server
    from tests.test_btc import _bundle

    store = Store(str(tmp_path / "qdr.db"))
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_mining_cache", {"at": 0.0, "data": None, "prev_count": None, "prev_at": 0.0})
    monkeypatch.setattr(server, "get_client", lambda: None)
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _bundle())
    c = TestClient(server.app)
    c.store = store
    return c


def _call(hour, call, made=1):
    return {"hour": hour, "epoch": 240, "call": call, "net_tick": 900, "net_index": 1, "pubkey": "cd" * 32,
            "net_error": 3490, "net_shift": 3, "made_at": made}


def test_calls_and_verdicts_ride_on_the_hours(client):
    from tests.test_btc import _hours
    client.store.put_btc_hours(240, _hours(), seen_at=1)        # BASE+1 down, BASE+2 up
    client.store.put_btc_call(_call(BASE + 1, 1))              # miss
    client.store.put_btc_call(_call(BASE + 2, 1))              # hit
    client.store.put_btc_call(_call(BASE + 3, 0))              # the next hour, open
    body = client.get("/v1/btc").json()
    live = {h["hour"]: h for h in body["hours"] if h["live"]}
    assert live[BASE + 1]["hit"] is False and live[BASE + 2]["hit"] is True
    assert body["call"]["available"] is True and body["call"]["hour"] == BASE + 3 and body["call"]["call"] == 0
    assert body["stats"]["epoch"]["calls"] == 2 and body["stats"]["epoch"]["hits"] == 1
    assert [h.get("hit") for h in body["record"]["hours"]] == [False, True]


def test_the_summary_is_small_and_wordy(client):
    from tests.test_btc import _hours
    client.store.put_btc_hours(240, _hours(), seen_at=1)
    client.store.put_btc_call(_call(BASE + 2, 1))
    client.store.put_btc_call(_call(BASE + 3, 0))
    s = client.get("/v1/btc/summary").json()
    assert set(s) == {"status", "epoch", "updated_at", "next", "call_status", "last", "rates", "coin_toss",
                      "price_source"}
    assert s["next"]["call"] == "down" and s["call_status"] == "available"
    assert s["last"][-1] == {"from": s["last"][-1]["to"] - 3600, "to": s["last"][-1]["to"],
                             "dir": "up", "price": None, "change_pct": None, "call": "up", "hit": True}
    assert len(client.get("/v1/btc/summary").content) < 3000


def test_a_dying_replayer_is_reported_with_its_reasons(live):
    class Dying(FakeReplayer):
        def walk(self, *a, **k):
            raise colony.ReplayerError("replayer exited (code -4)")

    st = colony.Tracker(live, Dying({})).step(object(), ["x"])
    assert st["state"] == "replayer-failed" and "code -4" in st["detail"]
    assert "cpu_avx2" in st and "memory_limit_mb" in st
    assert live.btc_calls() == []


def test_the_scan_stops_where_bob_has_indexed():
    class Bob:
        def status(self):
            return {"currentIndexingTick": 950}
    assert colony.indexed_head(Bob(), 1000) == 949

    class Mute:
        def status(self):
            raise OSError("down")
    assert colony.indexed_head(Mute(), 1000) == 900


def test_a_missing_ancestor_is_read_again_from_its_tick(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    store.put_ant_solutions(240, [colony.parse_solution(entry(300, 0, parent=(200, 2)))])

    class Bob:
        def _call(self, method, params):
            assert params[0]["fromTick"] == params[0]["toTick"] == 200
            return [entry(200, 2), entry(200, 3)]

    assert colony.lineage(store, 300, 0) is None
    assert colony.heal(store, Bob(), 240, 300, 0) == 2
    assert [r["tick"] for r in colony.lineage(store, 300, 0)] == [200, 300]


def test_the_overview_sums_prices_moves_and_calls(client):
    hours = [{"hour": BASE + 1, "up": False, "live": True, "append_tick": 1, "price": 100.0},
             {"hour": BASE + 2, "up": True, "live": True, "append_tick": 2, "price": 110.0},
             {"hour": BASE + 3, "up": False, "live": True, "append_tick": 3, "price": 99.0}]
    client.store.put_btc_hours(240, hours, seen_at=1)
    client.store.put_btc_call(_call(BASE + 2, 1))      # hit
    client.store.put_btc_call(_call(BASE + 3, 1))      # miss
    by_hour = client.get("/v1/btc/overview?group=hour").json()
    rows = {r["hour"]: r for r in by_hour["rows"]}
    assert rows[BASE + 2]["change_pct"] == 10.0 and rows[BASE + 2]["hit"] is True
    assert rows[BASE + 3]["change_pct"] == -10.0 and rows[BASE + 3]["hit"] is False
    assert rows[BASE + 2]["prev_price"] == 100.0 and rows[BASE + 3]["prev_price"] == 110.0
    assert rows[BASE + 1]["change_pct"] is None              # nothing stored before it
    assert rows[BASE + 1]["prev_price"] is None
    ep = client.get("/v1/btc/overview?group=epoch").json()["rows"][0]
    assert (ep["epoch"], ep["hours"], ep["open"], ep["close"], ep["calls"], ep["hits"]) == (240, 3, 100.0, 99.0, 2, 1)
    assert ep["change_pct"] == -1.0 and ep["rate"] == 0.5
    assert client.get("/v1/btc/overview?group=month").json()["rows"][0]["month"] == "2026-11"


def test_an_old_store_gains_the_price_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE btc_hours (hour INTEGER PRIMARY KEY, epoch INTEGER NOT NULL, up INTEGER NOT NULL,"
                " filled INTEGER NOT NULL DEFAULT 0, append_tick INTEGER, first_seen_at INTEGER NOT NULL)")
    con.execute("INSERT INTO btc_hours VALUES (1, 240, 1, 0, 5, 9)")
    con.commit(); con.close()
    store = Store(str(path))
    store.put_btc_hours(240, [{"hour": 1, "up": True, "live": True, "price": 123.0}], seen_at=10)
    assert store.btc_hours()[0]["price"] == 123.0 and store.btc_hours()[0]["first_seen_at"] == 9


# -- the accuracy rule (CFB, 2026-11-24; docs/CONCEPT_BTC_RANKING.de.md) -------------

def _hours_pts(ups, first_tick=1000, step=10):
    """Live hours BASE+1.. with targets `ups`, each stored `step` ticks after the last."""
    return [{"hour": BASE + 1 + j, "j": j, "up": u, "append_tick": first_tick + j * step}
            for j, u in enumerate(ups)]


def test_hour_points_map_emits_targets_and_append_ticks():
    targets = bytes([0] * W + [1, 0])
    pts = colony.hour_points({"targets": targets, "shift_limit": 2, "base_hour": BASE},
                             {"points": [{"hour": BASE + 1, "append_tick": 150}, {"hour": BASE + 2, "append_tick": 250}]})
    assert pts == [{"hour": BASE + 1, "j": 0, "up": 1, "append_tick": 150},
                   {"hour": BASE + 2, "j": 1, "up": 0, "append_tick": 250}]


def test_a_network_is_judged_only_on_hours_it_could_not_have_known():
    hours = _hours_pts([1, 1, 1, 1, 1, 1])           # stored in ticks 1000, 1010, ... 1050
    # Born in tick 1015: after hour j=1 was stored (1010), before j=2 (1020).
    # Hour h counts if h-2 was stored after its birth: h-2 = j=2 -> h = j=4, j=5.
    rec = colony.net_record("111111", 1015, hours, window_h=48)
    assert rec == {"hits": 2, "misses": 0, "abstained": 0, "decided": 2}
    # A network born before every hour still never counts the first two live
    # hours: the hour two before them is not a live hour.
    assert colony.net_record("111111", 1, hours, window_h=48)["decided"] == 4


def test_unsure_and_missing_outputs_are_abstentions_and_the_window_rolls():
    hours = _hours_pts([1, 0, 1, 0, 1, 0])
    rec = colony.net_record("1012", 1, hours, window_h=48)   # j2 "1" hit, j3 "2" unsure, j4/j5 missing
    assert rec == {"hits": 1, "misses": 0, "abstained": 3, "decided": 1}
    # Only the last 2 hours (j4, j5) are in a 2-hour window.
    assert colony.net_record("101010", 1, hours, window_h=2) == {"hits": 2, "misses": 0, "abstained": 0,
                                                                "decided": 2}


def test_wilson_punishes_small_samples():
    assert colony.wilson_low(0, 0) == 0.0
    assert round(colony.wilson_low(5, 5), 2) == 0.57
    assert colony.wilson_low(60, 100) > colony.wilson_low(5, 5) - 0.1
    assert colony.wilson_low(30, 40) > colony.wilson_low(6, 7)


def _cand(tick, hits, decided, error=4000, shift=0):
    return {"tick": tick, "tx_index": 0, "hits": hits, "decided": decided, "error": error, "shift": shift,
            "wilson": colony.wilson_low(hits, decided)}


def test_selection_needs_enough_hours_then_takes_the_best_lower_bound():
    few = [_cand(1, 5, 5), _cand(2, 3, 4)]
    assert colony.select_network(few, min_decided=12) is None        # fallback to the score
    cands = [_cand(1, 12, 12, error=3400), _cand(2, 30, 40, error=4300), _cand(3, 20, 40, error=3300)]
    assert colony.select_network(cands, min_decided=12)["tick"] == 1
    # Fewer errors (the score) does not buy a place: 20/40 loses to 30/40.
    assert colony.select_network(cands[1:], min_decided=12)["tick"] == 2


def test_without_records_the_accuracy_rule_falls_back_to_the_score_network(live):
    rep = FakeReplayer({"200:0": 4300, "300:1": 4200}, trits="0001")
    st = colony.Tracker(live, rep).step(object(), ["x"])
    assert st["selection"]["rule"] == "score-fallback"
    acc = live.btc_calls_accuracy()
    assert len(acc) == 1 and acc[0]["fallback"] == 1 and acc[0]["call"] == 1
    assert acc[0]["net_tick"] == live.btc_calls()[0]["net_tick"] == 300
    # Every rebuilt network keeps its outputs.
    assert {(r["tick"], r["depth"]) for r in live.colony_outputs(240)} == {(200, 1), (300, 2)}


def test_a_second_identity_is_rebuilt_and_the_best_record_calls(live, monkeypatch):
    # A second tree with a worse score: its lineage is rebuilt beside the best one.
    live.put_ant_solutions(240, [colony.parse_solution(entry(210, 4, anchor=120, score=4340, pk=b"\x03" * 32))])
    monkeypatch.setattr(colony, "RECORD_MIN_DECIDED", 0)

    class Rep(FakeReplayer):
        def calls(self, ref, n):
            return ("0000" if ref == "210:4" else "0001")[:n]

    rep = Rep({"200:0": 4300, "300:1": 4200, "210:4": 4340})
    st = colony.Tracker(live, rep).step(object(), ["x"])
    assert "210:4" in rep.known
    assert [l["pubkey"][:2] for l in st["lines"]] == ["01", "03"]
    assert st["selection"]["rule"] == "accuracy" and st["ranking"]
    acc = live.btc_calls_accuracy()
    assert acc[0]["fallback"] == 0 and acc[0]["net_decided"] is not None
    assert live.btc_calls()[0]["net_tick"] == 300           # the score rule is untouched


def test_a_side_line_that_misses_its_error_is_set_aside_not_the_best_one(live):
    live.put_ant_solutions(240, [colony.parse_solution(entry(210, 4, anchor=120, score=4340, pk=b"\x03" * 32))])
    rep = FakeReplayer({"200:0": 4300, "300:1": 4200, "210:4": 4999}, trits="0001")
    tracker = colony.Tracker(live, rep)
    st = tracker.step(object(), ["x"])
    assert st["state"] == "tracking" and st["lines"][1]["state"] == "mismatch"
    assert "210:4" in tracker.bad and not live.has_colony_output(210, 4)
    assert len(live.btc_calls()) == 1


def test_the_api_shows_the_accuracy_call_and_both_rules(client):
    from tests.test_btc import _hours
    client.store.put_btc_hours(240, _hours(), seen_at=1)        # BASE+1 down, BASE+2 up
    client.store.put_btc_call(_call(BASE + 1, 1))              # score: miss
    client.store.put_btc_call(_call(BASE + 2, 0))              # score: miss
    acc = dict(_call(BASE + 2, 1), net_hits=9, net_decided=12, net_abstained=1, fallback=0)
    client.store.put_btc_call_accuracy(acc)                    # accuracy: hit
    client.store.put_btc_call_accuracy(dict(_call(BASE + 3, 2), net_hits=None, net_decided=None,
                                            net_abstained=None, fallback=1))
    body = client.get("/v1/btc").json()
    live = {h["hour"]: h for h in body["hours"] if h["live"]}
    assert live[BASE + 1]["rule"] == "score" and live[BASE + 1]["hit"] is False
    assert live[BASE + 2]["rule"] == "accuracy" and live[BASE + 2]["hit"] is True
    rules = body["stats"]["rules"]
    assert rules["hours"] == 1 and rules["score"]["hits"] == 0 and rules["accuracy"]["hits"] == 1
    assert rules["measured"]["calls"] == 1
    call = body["call"]
    assert call["hour"] == BASE + 3 and call["call"] == 2 and call["rule"] == "accuracy" and call["fallback"]
    assert call["rules"]["score"]["upcoming"] is None


def test_an_unsure_call_is_shown_as_an_abstention(client):
    from tests.test_btc import _hours
    client.store.put_btc_hours(240, _hours(), seen_at=1)
    client.store.put_btc_call(_call(BASE + 2, 2))
    body = client.get("/v1/btc").json()
    h = [h for h in body["hours"] if h["hour"] == BASE + 2][0]
    assert h["hit"] is None and h["abstained"] is True
    assert body["stats"]["epoch"]["calls"] == 0 and body["stats"]["epoch"]["unsure"] == 1
