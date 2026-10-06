"""The live-ticks relay: what a tick is condensed to, and how the hub fans it out.

The fixture holds five real tickStream events recorded from bob.qubic.li on
2026-10-05 (input data shortened, signatures removed): a skipped tick, a Qx
order, a 184,754,756 QU transfer, a tick with contract reserve deductions and an
ordinary one. Every one of them is full of mining solutions — transactions that
send 1,000,000 QU to the null address and get it straight back — which is the
case the burn figure must not be fooled by.

No test here touches the network.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import tickstream  # noqa: E402
from qdr.burn import NULL_ADDRESS  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "tickstream_sample.jsonl"
QX = "BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAARMID"
RANDOM = "DAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAANMIG"

# The two registry entries the fixture's ticks call, in by_address() shape.
CONTRACTS = {
    QX: {"index": 1, "name": "QX", "label": "Qx", "address": QX,
         "procedures": {5: "Add to Ask Order", 6: "Add to Bid Order"}},
    RANDOM: {"index": 3, "name": "RANDOM", "label": "Random", "address": RANDOM,
             "procedures": {1: "Reveal and Commit"}},
}


def ticks() -> dict[int, dict]:
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    return {r["tick"]: r for r in rows}


def summary(tick: int) -> dict:
    return tickstream.condense(ticks()[tick], CONTRACTS)[0]


# -- condensing ----------------------------------------------------------------

def test_a_skipped_tick_says_so_and_claims_no_time():
    s = summary(83193283)
    assert s["state"] == "skipped"
    # Bob sends "2000-00-00T00:00:00Z" for it; that is a placeholder, not a time.
    assert s["ts"] is None
    assert s["tx"] == 0 and s["top"] == []


def test_mining_solutions_are_counted_and_never_burned():
    """Each solution pays 1,000,000 QU to the null address and is refunded in the
    same transaction. Counting the inbound leg would report billions burned."""
    for tick in ticks():
        s = summary(tick)
        assert s["burned"] == 0, f"tick {tick}: refunded round trips counted as a burn"
    s = summary(83193355)
    assert s["solutions"] == 13
    assert all(t["kind"] not in ("solution", "system") for t in s["top"])


def test_a_contract_call_is_named_with_its_procedure():
    s = summary(83193296)
    names = {c["name"]: c["calls"] for c in s["contracts"]}
    assert names == {"Qx": 1, "Random": 1}
    qx = [t for t in s["top"] if (t.get("contract") or {}).get("name") == "Qx"]
    assert qx and qx[0]["contract"]["proc"] == "Add to Ask Order"


def test_a_plain_transfer_is_volume_and_listed():
    s = summary(83193355)
    assert s["qu_moved"] >= 184_754_756
    assert any(t["kind"] == "transfer" and t["qu"] == 184_754_756 for t in s["top"])


def test_the_summary_carries_no_full_identity():
    """It streams twice a second to every viewer; the detail view has them whole."""
    for tick in ticks():
        s, d = tickstream.condense(ticks()[tick], CONTRACTS)
        blob = json.dumps(s)
        for t in d["transactions"]:
            for ident in (t.get("from"), t.get("to")):
                if ident:
                    assert ident not in blob
        assert len(blob) < 2000


def test_reserve_deductions_appear_as_events_in_the_detail():
    d = tickstream.condense(ticks()[83193293], CONTRACTS)[1]
    kinds = {e["type"] for e in d["events"]}
    assert "CONTRACT_RESERVE_DEDUCTION" in kinds


def test_an_unrefunded_payment_to_the_null_address_is_a_burn():
    raw = {
        "tick": 5, "epoch": 233, "computorIndex": 7, "timestamp": "2026-10-05T12:00:00Z",
        "transactions": [{"hash": "h1", "from": "X" * 60, "to": NULL_ADDRESS,
                          "amount": 5000, "inputType": 0, "status": "success",
                          "executed": True}],
        "logs": [{"logTypename": "QU_TRANSFER", "type": 0, "tick": 5, "txHash": "h1",
                  "body": {"from": "X" * 60, "to": NULL_ADDRESS, "amount": 5000}}],
    }
    s = tickstream.condense(raw)[0]
    assert s["burned"] == 5000
    assert s["top"][0]["kind"] == "burn" and s["top"][0]["burned"] == 5000
    assert s["ts"] == 1791201600


def test_the_poll_shape_condenses_to_the_same_counts():
    """qubic_getLogs is flat (source/destination/logTypeName/rawData) and the
    tick names its transactions by hash only."""
    sol_hex = "0x" + tickstream.ANT_SOLU_HEX + "00" * 8
    logs = [
        {"tick": 9, "logType": 0, "logTypeName": "QU_TRANSFER", "transactionHash": "a",
         "source": "Y" * 60, "destination": NULL_ADDRESS, "amount": 1_000_000},
        {"tick": 9, "logType": 255, "logTypeName": "CUSTOM_MESSAGE",
         "transactionHash": "a", "rawData": sol_hex},
        {"tick": 9, "logType": 0, "logTypeName": "QU_TRANSFER", "transactionHash": "a",
         "source": NULL_ADDRESS, "destination": "Y" * 60, "amount": 1_000_000},
    ]
    tick_data = {"tickNumber": 9, "epoch": 233, "computorIndex": 1,
                 "timestamp": 1791203305, "transactions": ["a", "b"],
                 "hasNoTickData": False, "isSkipped": False}
    s = tickstream.condense_polled(tick_data, logs)[0]
    assert (s["tick"], s["tx"], s["solutions"], s["burned"]) == (9, 2, 1, 0)


@pytest.mark.parametrize("bob_url, ws", [
    ("https://bob.qubic.li/qubic", "wss://bob.qubic.li/ws/qubic"),
    ("http://10.0.0.5:40420", "ws://10.0.0.5:40420/ws/qubic"),
    ("http://10.0.0.5:40420/", "ws://10.0.0.5:40420/ws/qubic"),
])
def test_the_socket_url_follows_the_rpc_url(bob_url, ws, monkeypatch):
    monkeypatch.delenv("QDR_BOB_WS", raising=False)
    assert tickstream.ws_url_for(bob_url) == ws


def test_bob_computor_identities_lose_their_trailing_bytes(monkeypatch):
    """Measured: a U+00F7 after every identity, and sometimes more than that."""
    a, b = "A" * 56 + "BCDE", "POKORF" + "Q" * 50 + "SEVK"
    rpc = tickstream.BobRPC("http://example.invalid")
    monkeypatch.setattr(rpc, "call", lambda m, p: {"computors": [
        a + "÷", b + "c", "too-short÷"]})
    assert rpc.computors(233) == [a, b, None]


# -- the hub -------------------------------------------------------------------

def _hub(**kw) -> tickstream.TickHub:
    return tickstream.TickHub("http://example.invalid", buffer_ticks=kw.pop("buffer", 10), **kw)


def _s(tick: int, epoch: int = 233, leader: int = 0) -> tuple[dict, dict]:
    s = {"tick": tick, "epoch": epoch, "leader": {"index": leader}, "state": "ok"}
    return s, dict(s)


def test_the_hub_drops_duplicates_and_keeps_order():
    hub = _hub()
    for t in (100, 101, 103, 102, 101):
        hub.ingest(*_s(t))
    assert [s["tick"] for s in hub.recent()] == [100, 101, 102, 103]
    assert [s["tick"] for s in hub.recent(after=101)] == [102, 103]
    # More waiting than asked for: the newest, because a late viewer wants now.
    assert [s["tick"] for s in hub.recent(limit=2)] == [102, 103]


def test_the_buffer_is_bounded():
    hub = _hub(buffer=5)
    for t in range(1000, 1020):
        hub.ingest(*_s(t))
    assert len(hub.buffer) == 5 and len(hub.details) == 5
    assert hub.detail(1019) is not None and hub.detail(1000) is None


def test_subscribers_get_each_tick_and_a_slow_one_is_dropped():
    async def scenario():
        hub = _hub()
        fast = hub.subscribe()
        slow = hub.subscribe()
        for _ in range(slow.maxsize):
            slow.put_nowait({"tick": 0})            # already full
        hub.ingest(*_s(500))
        assert (await fast.get())["tick"] == 500
        assert slow not in hub.subscribers
    asyncio.run(scenario())


def test_the_leader_is_named_short_in_the_summary_and_full_in_the_detail():
    hub = _hub()
    full = "Q" * 56 + "ABCD"
    hub._leaders[233] = [{"id": "Z" * 60, "cluster": None},
                         {"id": full, "cluster": "Pool A"}]
    s, d = _s(700, leader=1)
    hub.ingest(s, d)
    assert hub.recent()[-1]["leader"] == {"index": 1, "id": "QQQQQQ…ABCD",
                                          "cluster": "Pool A"}
    assert hub.detail(700)["leader"]["id"] == full


def test_an_unwatched_hub_is_idle():
    hub = _hub(idle_s=0.0)
    hub._last_seen -= 1
    assert hub._idle()
    q = hub.subscribe()
    assert not hub._idle()
    hub.unsubscribe(q)


# -- the API -------------------------------------------------------------------

@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api.server as server

    hub = _hub(buffer=50)

    async def no_upstream():
        hub.touch()
    monkeypatch.setattr(hub, "ensure_running", no_upstream)
    monkeypatch.setattr(server, "_tick_hub", hub)
    # A stream ends by itself after its lifetime; keep that short so a test that
    # stops reading early is not left waiting on a heartbeat.
    monkeypatch.setattr(server, "TICKS_STREAM_MAX_S", 0.5)
    monkeypatch.setattr(server, "TICKS_HEARTBEAT_S", 0.1)
    return TestClient(server.app), hub


def test_recent_serves_the_buffer(api):
    client, hub = api
    for t in (10, 11, 12):
        hub.ingest(*_s(t))
    body = client.get("/v1/ticks/recent", params={"after": 10}).json()
    assert [t["tick"] for t in body["ticks"]] == [11, 12]
    assert body["status"]["buffered"] == 3


def test_a_buffered_tick_is_served_without_the_node(api):
    client, hub = api
    hub.ingest(*_s(42))
    assert client.get("/v1/ticks/42").json()["tick"] == 42


def test_the_stream_replays_after_the_last_event_id(api):
    client, hub = api
    for t in (20, 21, 22):
        hub.ingest(*_s(t))
    with client.stream("GET", "/v1/ticks/stream",
                       headers={"Last-Event-ID": "20"}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers.get("x-accel-buffering") == "no"
        ids = []
        for line in resp.iter_lines():
            if line.startswith("id: "):
                ids.append(int(line[4:]))
            if len(ids) == 2:
                break
    assert ids == [21, 22]


def test_the_ticks_endpoints_are_indexed(api):
    client, _ = api
    eps = client.get("/api").json()["endpoints"]
    for path in ("/v1/ticks/stream", "/v1/ticks/recent", "/v1/ticks/{tick}"):
        assert path in eps


def test_a_restart_after_idle_drops_ticks_from_before_the_gap(monkeypatch):
    """Otherwise a new viewer is served minutes-old ticks as "recent"."""
    hub = _hub()
    hub.ingest(*_s(900))
    hub.last_event_at -= 120

    async def no_upstream():
        return None
    monkeypatch.setattr(hub, "_run", no_upstream)

    async def scenario():
        await hub.ensure_running()
    asyncio.run(scenario())
    assert hub.recent() == [] and hub.detail(900) is None


# -- node failover -------------------------------------------------------------
# 2026-10-06: bob.qubic.li froze at tick 83,288,297 for hours while answering
# every call normally; the page said "node silent" and had no other node to ask.

A, B = "https://bob-a.invalid/qubic", "https://bob-b.invalid/qubic"


class _FakeRPC:
    def __init__(self, head):
        self.head = head

    def tick_number(self):
        if isinstance(self.head, Exception):
            raise self.head
        return self.head


def _failover_hub(heads: dict, network: int | None = 1000, **kw) -> tickstream.TickHub:
    hub = tickstream.TickHub(urls=list(heads), buffer_ticks=50,
                             network_fn=(lambda: network), **kw)
    rpcs = {u: _FakeRPC(h) for u, h in heads.items()}
    hub._rpc = lambda url: rpcs[url]
    return hub


def test_the_node_list_puts_your_own_node_first_then_the_public_ones(monkeypatch):
    monkeypatch.delenv("QDR_BOB_URLS", raising=False)
    monkeypatch.setattr(tickstream, "BOB_URL", "http://10.0.0.5:40420")
    assert tickstream.bob_urls() == ["http://10.0.0.5:40420", *tickstream.PUBLIC_BOB_URLS]
    # The public default is not listed twice.
    monkeypatch.setattr(tickstream, "BOB_URL", tickstream.PUBLIC_BOB_URLS[0] + "/")
    assert len(tickstream.bob_urls()) == len(tickstream.PUBLIC_BOB_URLS)


def test_qdr_bob_urls_is_the_whole_list(monkeypatch):
    monkeypatch.setenv("QDR_BOB_URLS", f" {A} ,{B},,")
    assert tickstream.bob_urls() == [A, B]


def test_qdr_bob_ws_belongs_to_qdr_bob_url_only(monkeypatch):
    monkeypatch.setenv("QDR_BOB_WS", "ws://own-socket/ws")
    monkeypatch.setattr(tickstream, "BOB_URL", A)
    assert tickstream.TickHub._ws_url(A) == "ws://own-socket/ws"
    assert tickstream.TickHub._ws_url(B) == "wss://bob-b.invalid/ws/qubic"


def test_a_node_far_behind_the_network_is_skipped_for_the_next():
    hub = _failover_hub({A: 1000 - tickstream.LAG_TICKS - 1, B: 999})
    assert asyncio.run(hub._pick_node()) == B
    st = hub.status()
    down = st["nodes"][0]
    assert down["state"] == "down" and "behind the network" in down["reason"]


def test_an_unreachable_node_is_skipped_and_none_left_means_none():
    hub = _failover_hub({A: ConnectionError("refused"), B: 1})
    assert asyncio.run(hub._pick_node()) is None
    assert all(n["state"] == "down" for n in hub.status()["nodes"])


def test_without_a_network_reading_a_reachable_node_is_used():
    """The yardstick is a nicety; its absence must not take every node out."""
    hub = _failover_hub({A: 5}, network=None)
    assert asyncio.run(hub._pick_node()) == A


def _quiet_since(hub, node_head: int, seconds: float) -> None:
    """The current node last made progress, at node_head, `seconds` ago."""
    hub._node_head = node_head
    hub._node_alive_at = hub._node_since = time.time() - seconds


def test_a_node_frozen_at_one_tick_is_down_although_it_answers():
    hub = _failover_hub({A: 500})
    _quiet_since(hub, 500, tickstream.STALL_S + 1)
    with pytest.raises(tickstream.NodeDown, match="stuck at tick 500"):
        asyncio.run(hub._check_health(A))
    # The same in poll mode: answering every poll with one tick is not health.
    with pytest.raises(tickstream.NodeDown, match="stuck at tick 500"):
        asyncio.run(hub._check_health(A, socket=False))


def test_a_silent_socket_on_an_advancing_node_retries_the_socket_not_the_node():
    hub = _failover_hub({A: 520})
    _quiet_since(hub, 500, tickstream.STALL_S + 1)
    with pytest.raises(RuntimeError) as err:
        asyncio.run(hub._check_health(A))
    assert not isinstance(err.value, tickstream.NodeDown)


def test_an_advancing_node_in_poll_mode_is_healthy():
    """Review finding: this raised out of the poll loop and ended the relay."""
    hub = _failover_hub({A: 520}, network=530)
    _quiet_since(hub, 500, tickstream.STALL_S + 1)
    asyncio.run(hub._check_health(A, socket=False))
    assert hub._node_head == 520


def test_ticks_the_buffer_already_holds_still_count_as_progress():
    """After a switch the new node may replay ticks the buffer has (from the old
    node or the bare feed). That is a working node, not a stuck one."""
    hub = _failover_hub({A: 600}, network=610)
    for t in range(600, 610):
        hub.ingest(*tickstream.condense_bare(t))
    _quiet_since(hub, 590, tickstream.STALL_S + 1)
    raw = ticks()[min(ticks())]
    asyncio.run(hub._ingest_raw({**raw, "tick": 605}))
    asyncio.run(hub._check_health(A))            # no stall: it delivered 605


def test_a_full_tick_replaces_its_bare_twin_without_resending_it():
    hub = _hub()
    hub.ingest(*tickstream.condense_bare(700))
    q = hub.subscribe()
    s, d = _s(700)
    assert hub.ingest(s, d) is False             # viewers already have the cube
    assert q.empty()
    assert hub.recent()[-1].get("bare") is None
    assert hub.detail(700) is not None
    assert hub.ingest(*_s(700)) is False         # and a full tick is never replaced


def test_a_node_delivering_old_ticks_is_down():
    hub = _failover_hub({A: 500}, network=500 + tickstream.LAG_TICKS + 1)
    _quiet_since(hub, 500, 0)
    with pytest.raises(tickstream.NodeDown, match="behind the network"):
        asyncio.run(hub._check_health(A))


def test_a_bare_tick_claims_no_content_and_is_read_from_a_node_on_click():
    s, d = tickstream.condense_bare(83333606)
    assert s["bare"] and s["tick"] == 83333606
    for k in ("tx", "burned", "solutions", "qu_moved", "ts", "epoch"):
        assert s[k] is None, k          # unknown, not zero
    hub = _hub()
    hub.ingest(s, d)
    assert [r["tick"] for r in hub.recent()] == [83333606]
    assert hub.detail(83333606) is None


def test_a_detail_read_falls_through_to_the_next_node(monkeypatch):
    calls = []

    def fetch(self, tick, contracts=None):
        calls.append(self.url)
        if self.url == A:
            return {"tick": 0}           # frozen node: does not know the tick
        return {"tick": tick, "from": self.url}
    monkeypatch.setattr(tickstream.BobRPC, "fetch_tick", fetch)
    hub = _failover_hub({A: 1, B: 1})
    assert hub.fetch_detail(42)["from"] == B
    assert calls == [A, B]


def test_the_status_names_the_bob_node_in_use():
    hub = _failover_hub({A: 1, B: 1})
    hub.source, hub.active_url = "bob-ws", B
    st = hub.status()
    assert st["node"] == {"kind": "bob", "host": "bob-b.invalid"}
    assert [n["state"] for n in st["nodes"]] == ["standby", "active"]
    hub.source, hub.active_url = "bare", None
    assert hub.status()["node"]["kind"] == "bare"
