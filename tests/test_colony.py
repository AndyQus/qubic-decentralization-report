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

def test_unsure_is_a_miss_and_an_unknown_hour_no_verdict():
    judged = colony.judge([{"hour": 1, "call": 1, "up": 1}, {"hour": 2, "call": 2, "up": 0},
                           {"hour": 3, "call": 0, "up": None}])
    assert [c["hit"] for c in judged] == [True, False, None]
    r = colony.rate(judged)
    assert r == {"calls": 2, "hits": 1, "unsure": 1, "rate": 0.5}


def test_baselines_are_scored_on_the_same_hours():
    hours = [{"hour": h, "up": u} for h, u in ((1, 1), (2, 1), (3, 0), (4, 0))]
    judged = colony.judge([{"hour": h, "call": 1, "up": u} for h, u in ((2, 1), (3, 0), (4, 0))])
    b = colony.baselines(hours, judged)
    assert b["always_up"] == round(1 / 3, 4)
    assert b["repeat_last"] == round(2 / 3, 4)      # 1->1 yes, 1->0 no, 0->0 yes


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
    store = Store(str(tmp_path / "q.db"))
    store.put_ant_solutions(240, [colony.parse_solution(e) for e in (
        entry(200, 0, anchor=120, score=4300), entry(300, 1, parent=(200, 0), anchor=260, score=4200))])
    store.put_colony_anchor(120, "11" * 32)
    store.put_colony_anchor(260, "22" * 32)
    return store


def test_a_verified_lineage_produces_one_call_for_the_unknown_hour(live):
    rep = FakeReplayer({"200:0": 4300, "300:1": 4200}, trits="011")
    tracker = colony.Tracker(live, rep)
    st = tracker.step(object(), ["203.0.113.7"])
    assert st["state"] == "tracking", st
    # Anchors 120 and 260: 0 and 2 live hours stored before them.
    assert [w[2] for w in rep.walked] == [0, 2]
    calls = live.btc_calls()
    assert len(calls) == 1 and calls[0]["hour"] == BASE + 3
    assert calls[0]["call"] == 1            # trits[j], j = hour - base - 1 = 2
    assert calls[0]["net_tick"] == 300 and calls[0]["net_error"] == 4200


def test_no_call_while_the_lineage_is_still_being_caught_up(live, monkeypatch):
    monkeypatch.setattr(colony, "WALKS_PER_STEP", 1)
    rep = FakeReplayer({"200:0": 4300, "300:1": 4200}, trits="011")
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
    assert set(s) == {"status", "epoch", "updated_at", "next", "call_status", "last", "rates", "coin_toss"}
    assert s["next"]["call"] == "down" and s["call_status"] == "available"
    assert s["last"][-1] == {"from": s["last"][-1]["to"] - 3600, "to": s["last"][-1]["to"],
                             "dir": "up", "call": "up", "hit": True}
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
