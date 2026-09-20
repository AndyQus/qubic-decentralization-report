"""Mining state is sampled into the store, because it cannot be recovered later.

The live endpoint reads a node and forgets the answer; everything here exists so
that a reading, once taken, survives. What is pinned:

  * a sample is stored per second, so a re-read of the same cached reading does
    not inflate the epoch's sample count into a fake measurement density;
  * pruning keeps the rolling window of RAW samples but never the summaries --
    losing resolution is the cost, losing history is not;
  * the window is two epochs, not one, because `solution_count` resets at an
    epoch boundary and a one-epoch window would delete the previous peak at
    exactly the moment the drop becomes visible;
  * the growth rate comes from the stored series, so it is right immediately
    after a restart rather than only once the process has polled twice;
  * the series endpoint thins server-side and never invents a point.

No test here touches the network.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import antnode, pipeline  # noqa: E402
from qdr.store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "qdr.db"))


def _fill(store: Store, epoch: int, n: int, start_at: int, base: int = 1000,
          step: int = 20, every: int = 30) -> None:
    for i in range(n):
        store.put_mining_sample(
            at=start_at + i * every, epoch=epoch, tick=100000 + i,
            solution_count=base + i * step, threshold=4000,
            free_ann_slots=7_000_000 - i, peers_responding=5,
            node_ip="203.0.113.7", latency_ms=41,
        )


# --- storage ---------------------------------------------------------------

def test_a_repeated_reading_does_not_inflate_the_sample_count(store):
    """The API caches for 30s, so the same reading can arrive twice. It is one sample."""
    for _ in range(4):
        store.put_mining_sample(at=1700000000, epoch=231, solution_count=5000)

    assert len(store.mining_series(epoch=231)) == 1
    assert store.mining_epochs()[0]["samples"] == 1


def test_samples_come_back_oldest_first_so_a_chart_can_draw_them(store):
    _fill(store, 231, 5, 1700000000)
    rows = store.mining_series(epoch=231)

    ats = [r["at"] for r in rows]
    assert ats == sorted(ats), "a chart would draw this series backwards"


def test_a_windowed_read_returns_the_newest_samples(store):
    """A limit must show now, not the start of history -- same rule as burn_series."""
    _fill(store, 231, 10, 1700000000, base=1000, step=20)
    rows = store.mining_series(epoch=231, limit=3)

    assert [r["solution_count"] for r in rows] == [1140, 1160, 1180]


# --- pruning ---------------------------------------------------------------

def test_pruning_drops_old_samples_but_keeps_every_epoch_summary(store):
    """Resolution is the thing we can afford to lose. History is not."""
    _fill(store, 229, 5, 1700000000)
    _fill(store, 230, 5, 1700100000)
    _fill(store, 231, 5, 1700200000)

    dropped = store.prune_mining(keep_epochs=2)

    assert dropped == 5
    assert sorted({r["epoch"] for r in store.mining_series()}) == [230, 231]
    assert [e["epoch"] for e in store.mining_epochs()] == [229, 230, 231]


def test_a_pruned_epochs_peak_survives_in_the_summary(store):
    """The peak was observed; a later prune must not silently lower it."""
    _fill(store, 229, 5, 1700000000, base=1000, step=100)   # peak 1400
    _fill(store, 230, 3, 1700100000, base=10, step=1)
    _fill(store, 231, 3, 1700200000, base=10, step=1)
    peak_before = [e for e in store.mining_epochs() if e["epoch"] == 229][0]["peak_count"]

    store.prune_mining(keep_epochs=2)
    # touching epoch 231 rewrites summaries; 229's must not drift
    store.put_mining_sample(at=1700200100, epoch=231, solution_count=99)

    after = [e for e in store.mining_epochs() if e["epoch"] == 229][0]
    assert after["peak_count"] == peak_before == 1400


def test_the_window_is_two_epochs_so_a_reset_stays_explicable(store):
    """solution_count drops to zero at a boundary. Keeping one epoch would delete
    the previous peak at exactly the moment the drop appears, leaving a curve that
    starts near zero with nothing on the page explaining why."""
    assert Store.MINING_KEEP_EPOCHS >= 2

    _fill(store, 230, 4, 1700000000, base=900_000, step=50)   # an epoch at full height
    _fill(store, 231, 4, 1700100000, base=10, step=50)        # the one after the reset
    store.prune_mining()

    epochs_present = sorted({r["epoch"] for r in store.mining_series()})
    assert epochs_present == [230, 231], "the drop has nothing to be measured against"


def test_pruning_an_empty_or_short_history_is_a_no_op(store):
    assert store.prune_mining() == 0

    _fill(store, 231, 3, 1700000000)
    assert store.prune_mining() == 0
    assert len(store.mining_series()) == 3


def test_keeping_zero_epochs_is_refused(store):
    """A window of nothing would delete the data the moment it was written."""
    _fill(store, 231, 3, 1700000000)
    with pytest.raises(ValueError):
        store.prune_mining(keep_epochs=0)


# --- growth ----------------------------------------------------------------

def test_growth_is_measured_across_the_stored_window(store):
    now = int(time.time())
    # 20 solutions per 30s sample == 40/min
    _fill(store, 231, 11, now - 300, base=1000, step=20, every=30)

    assert pipeline.mining_growth(store, 231) == pytest.approx(40.0, abs=0.5)


def test_growth_is_unreadable_across_an_epoch_reset(store):
    """The counter only rises inside an epoch; a fall means the window spans a
    boundary, and no rate can be read across it."""
    now = int(time.time())
    _fill(store, 231, 5, now - 200, base=1_000_000, step=10)
    store.put_mining_sample(at=now, epoch=231, solution_count=3)

    assert pipeline.mining_growth(store, 231) is None


def test_growth_needs_more_than_one_point(store):
    store.put_mining_sample(at=int(time.time()), epoch=231, solution_count=5000)
    assert pipeline.mining_growth(store, 231) is None
    assert pipeline.mining_growth(store, 999) is None


# --- sampling --------------------------------------------------------------

def _live_bundle(solutions: int = 5000, epoch: int = 231) -> dict:
    return {
        "status": "live",
        "fetched_at": 1788958516,
        "node": {"ip": "203.0.113.7", "latency_ms": 42, "version": 304,
                 "peers_total": 10, "peers_responding": 8},
        "colony": {"solution_count": solutions, "threshold": 4000,
                   "free_ann_slots": 8382831, "max_children_per_parent": 0},
        "epoch": {"epoch": epoch, "tick": 79304436, "initial_tick": 79300000,
                  "ticks_into_epoch": 4436},
    }


def test_a_sample_records_what_the_node_answered(store, monkeypatch):
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _live_bundle(5000))

    row = pipeline.sample_mining(store)

    assert row["epoch"] == 231 and row["solution_count"] == 5000
    assert store.latest_mining_sample()["node_ip"] == "203.0.113.7"


def test_an_unreachable_node_stores_nothing_and_does_not_raise(store, monkeypatch):
    """These are other people's machines; an outage must not stop the worker."""
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: {
        "status": "unavailable", "reason": "no node answered",
        "fetched_at": 1788958516, "node": {"peers_total": 10, "peers_responding": 0},
    })
    assert pipeline.sample_mining(store) is None

    def boom(*a, **k):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(antnode, "collect", boom)
    assert pipeline.sample_mining(store) is None
    assert store.mining_series() == []


def test_a_sample_without_an_epoch_is_refused(store, monkeypatch):
    """Epoch is what places a row in the window that gets pruned; a mis-filed row
    would be deleted on the wrong boundary."""
    bundle = _live_bundle()
    bundle["epoch"] = {}
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: bundle)

    assert pipeline.sample_mining(store) is None
    assert store.mining_series() == []


def test_the_sampler_subtracts_the_fetch_from_its_wait(monkeypatch):
    """A collect() takes 10-17s because it probes several peers. Sleeping the
    whole interval on top of that stretched a 30s cadence to a measured 47s, so
    the wait has to be what REMAINS of the interval."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ingest_mod", ROOT / "scripts" / "ingest.py")
    ingest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ingest)

    slept: list[float] = []
    clock = {"t": 1000.0}
    stop = threading.Event()

    def fake_sample(store):
        clock["t"] += 4.0       # the fetch itself costs four seconds
        return {"epoch": 231}

    def fake_sleep(sec):
        slept.append(sec)
        clock["t"] += sec
        if len(slept) >= 3:
            stop.set()
            # Park the daemon thread here instead of raising: an exception out of
            # a thread is reported by pytest as an unhandled one, and this stop
            # is deliberate.
            threading.Event().wait()

    monkeypatch.setattr(ingest.pipeline, "sample_mining", fake_sample)
    monkeypatch.setattr(ingest.time, "sleep", fake_sleep)
    monkeypatch.setattr(ingest.time, "time", lambda: clock["t"])

    ingest.start_mining_sampler(object(), interval=30)
    assert stop.wait(timeout=5), "the sampler never completed three passes"

    assert slept and all(s == pytest.approx(26.0) for s in slept), (
        f"cadence drifts: waited {slept} on top of a 4s fetch"
    )


def test_a_pass_slower_than_the_interval_does_not_spin(monkeypatch):
    """The floor keeps this a loop rather than a busy wait."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ingest_mod2", ROOT / "scripts" / "ingest.py")
    ingest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ingest)

    slept: list[float] = []
    clock = {"t": 1000.0}
    stop = threading.Event()

    def slow_sample(store):
        clock["t"] += 90.0      # three times the interval
        return None

    def fake_sleep(sec):
        slept.append(sec)
        clock["t"] += sec
        stop.set()
        threading.Event().wait()        # park; see the note above

    monkeypatch.setattr(ingest.pipeline, "sample_mining", slow_sample)
    monkeypatch.setattr(ingest.time, "sleep", fake_sleep)
    monkeypatch.setattr(ingest.time, "time", lambda: clock["t"])

    ingest.start_mining_sampler(object(), interval=30)
    assert stop.wait(timeout=5), "the sampler never slept"

    assert slept == [1.0], f"a slow pass must still pause, got {slept}"


# --- the API ---------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.server as server

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    st = Store(str(tmp_path / "qdr.db"))
    monkeypatch.setattr(server, "_store", st)
    monkeypatch.setattr(server, "_mining_cache",
                        {"at": 0.0, "data": None, "prev_count": None, "prev_at": 0.0})
    monkeypatch.setattr(server, "_mining_series_cache", {})
    monkeypatch.setattr(server, "get_client", lambda: None)
    return TestClient(server.app), st


def test_an_empty_history_says_so_rather_than_drawing_nothing(client):
    api, _ = client
    resp = api.get("/v1/mining/series")

    assert resp.status_code == 503
    assert "mining history" in resp.json()["detail"]


def test_the_series_serves_what_was_sampled(client):
    api, st = client
    _fill(st, 231, 6, 1700000000, base=1000, step=25)

    body = api.get("/v1/mining/series").json()

    assert body["epoch"] == 231
    assert body["points"] == 6 and body["sampled_total"] == 6
    assert body["measured"] is True
    assert [p["solution_count"] for p in body["series"]] == [1000, 1025, 1050, 1075, 1100, 1125]


def test_a_long_series_is_thinned_but_keeps_its_newest_point(client):
    """An epoch holds ~12,700 samples; sending them all costs a megabyte to draw a
    line a few hundred pixels wide. The last point must survive the thinning, or
    the live poll would append to a gap."""
    api, st = client
    _fill(st, 231, 3000, 1700000000, base=0, step=7)

    body = api.get("/v1/mining/series?points=200").json()

    assert body["points"] == 200 and body["sampled_total"] == 3000
    assert body["series"][-1]["solution_count"] == 2999 * 7
    counts = [p["solution_count"] for p in body["series"]]
    assert counts == sorted(counts), "thinning reordered the series"


def test_the_series_never_returns_more_points_than_it_sampled(client):
    api, st = client
    _fill(st, 231, 4, 1700000000)

    body = api.get("/v1/mining/series?points=400").json()
    assert body["points"] == 4, "an interpolated point is an invented measurement"


def test_a_window_narrows_the_series_to_recent_samples(client):
    api, st = client
    now = int(time.time())
    _fill(st, 231, 20, now - 20 * 60, every=60)

    body = api.get("/v1/mining/series?window=300").json()
    assert body["points"] <= 6
    assert body["first_at"] >= now - 400


def test_the_series_carries_the_permanent_per_epoch_summaries(client):
    """Raw samples are windowed; these outlive them, so the page can still say
    what earlier epochs did."""
    api, st = client
    _fill(st, 230, 3, 1700000000, base=500)
    _fill(st, 231, 3, 1700100000, base=900)

    body = api.get("/v1/mining/series").json()
    assert [e["epoch"] for e in body["epochs"]] == [230, 231]


def test_the_series_does_not_call_a_node(client, monkeypatch):
    """Reading history is a store query. Sampling is the worker's job."""
    api, st = client
    _fill(st, 231, 5, 1700000000)

    def boom(*a, **k):
        raise AssertionError("the series endpoint went to the network")

    monkeypatch.setattr(antnode, "collect", boom)
    assert api.get("/v1/mining/series").status_code == 200


def test_the_live_endpoint_reads_its_growth_from_the_store(client, monkeypatch):
    """The in-process pair starts empty on every deploy; the stored series does not,
    so a restarted API still reports a rate on its very first response."""
    api, st = client
    now = int(time.time())
    _fill(st, 231, 11, now - 300, base=1000, step=20, every=30)
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _live_bundle(1200))

    colony = api.get("/v1/mining").json()["colony"]

    assert colony["delta_source"] == "stored"
    assert colony["delta_per_min"] == pytest.approx(40.0, abs=0.5)


def test_without_stored_samples_the_live_pair_still_works(client, monkeypatch):
    """A plain `docker run` of the API alone has no ingest worker writing samples;
    the old two-fetch measurement stays as the fallback."""
    import api.server as server
    api, _ = client

    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _live_bundle(5000))
    first = api.get("/v1/mining").json()
    assert "delta_per_min" not in first["colony"]

    server._mining_cache["at"] = 0.0
    server._mining_cache["prev_at"] -= 60.0
    monkeypatch.setattr(antnode, "collect", lambda *a, **k: _live_bundle(5100))

    colony = api.get("/v1/mining").json()["colony"]
    assert colony["delta_source"] == "live"
    assert colony["delta_per_min"] == pytest.approx(100, abs=5)


def test_the_series_is_cached_between_requests(client, monkeypatch):
    api, st = client
    _fill(st, 231, 5, 1700000000)
    api.get("/v1/mining/series")

    calls = {"n": 0}
    real = Store.mining_series

    def counting(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(Store, "mining_series", counting)
    for _ in range(4):
        api.get("/v1/mining/series")

    assert calls["n"] == 0, "every viewer re-queried the store"


def test_the_endpoint_is_indexed(client):
    api, _ = client
    assert "/v1/mining/series" in api.get("/api").json()["endpoints"]


# --- the page --------------------------------------------------------------

def test_the_mining_page_draws_the_history_in_both_languages():
    html = (ROOT / "dashboard" / "mining.html").read_text(encoding="utf-8")

    assert "/v1/mining/series" in html, "the page never asks for the stored history"
    assert "hist-chart" in html and "drawHistory" in html
    # both dictionaries carry the new strings, or one language renders blanks
    assert html.count("histTitle:") == 2
    assert html.count("histEmpty:") == 2
    assert html.count("histDesc:") == 2


def test_the_page_appends_live_points_to_the_stored_curve():
    """Otherwise the curve would freeze at page load while the counter above it moves."""
    html = (ROOT / "dashboard" / "mining.html").read_text(encoding="utf-8")
    assert "appendHistoryPoint(data)" in html
