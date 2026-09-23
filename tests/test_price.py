"""The price endpoints' own rules, pinned so a later edit cannot quietly break them.

Price data here has a shape most market APIs do not: a row is stored per price
*change*, not per poll, so every row is an interval that says how long the price
held and how many readings confirm it. That shape carries obligations, and these
tests cover the ones that would be easy to lose in a refactor:

  * a series is steps, never a curve — and it says so in the payload, so a
    consumer cannot reasonably smooth it and call the result our data;
  * an unobserved moment answers 503 rather than the nearest reading, because
    substituting one would report a price nobody measured;
  * coverage travels with every figure, since a flat line means "steady" only if
    somebody was looking;
  * no endpoint ever grows a `volume` field — the RPC publishes none, and one
    sourced elsewhere would breach the terms this project stays inside.

No test here touches the network.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import pipeline  # noqa: E402
from qdr.store import Store  # noqa: E402


def _client(store_path: Path) -> TestClient:
    """A client bound to a specific store, with the price caches cleared.

    The caches matter here: they are keyed by query, not by store, so a client
    built after another test would otherwise serve that test's data.
    """
    import api.server as server
    server._store = Store(store_path)
    server._price_cache.update(at=0.0, data=None)
    server._price_series_cache.clear()
    server._price_avg_cache.clear()
    server._price_days_cache.clear()
    return TestClient(server.app)


def _store_with_prices(prefix: str = "qdr_price_") -> tuple[Path, int]:
    """A store holding two prices: one held over 5 polls, then a move held over 5.

    Returns the db path and the minute `now` was aligned to, so a test can ask
    about a moment it knows was observed.
    """
    d = Path(tempfile.mkdtemp(prefix=prefix))
    s = Store(d / "qdr.db")
    now = (int(time.time()) // 60) * 60
    for i in range(5):
        s.put_price_reading(at=now - 600 + i * 60, price=4.084e-7,
                            market_cap=612_000_000, circulating=1_500_000_000_000_000,
                            epoch=228)
    for i in range(5):
        s.put_price_reading(at=now - 300 + i * 60, price=4.103e-7,
                            market_cap=615_000_000, circulating=1_500_000_000_000_000,
                            epoch=228)
    s.close()
    return d / "qdr.db", now


# -- the store's interval shape ---------------------------------------------

def test_an_unchanged_reading_extends_the_interval_instead_of_adding_a_row():
    """The whole storage design rests on this. A row per poll would make a quiet
    market indistinguishable from a busy one once the repetitions were collapsed."""
    d = Path(tempfile.mkdtemp(prefix="qdr_price_"))
    s = Store(d / "qdr.db")
    now = (int(time.time()) // 60) * 60
    first = s.put_price_reading(at=now - 120, price=4.084e-7, market_cap=612_000_000)
    assert first["changed"] is True
    again = s.put_price_reading(at=now - 60, price=4.084e-7, market_cap=612_000_000)
    assert again["changed"] is False
    assert again["polls"] == 2
    assert again["at"] == first["at"]        # same interval, not a new one
    assert again["until"] == now - 60        # extended to the latest confirmation
    assert len(s.price_series()) == 1
    s.close()


def test_a_reading_arriving_out_of_order_is_ignored():
    """Clocks and retries can deliver one late. Rewriting an interval backwards
    would corrupt a record that was correct when written."""
    d = Path(tempfile.mkdtemp(prefix="qdr_price_"))
    s = Store(d / "qdr.db")
    now = (int(time.time()) // 60) * 60
    s.put_price_reading(at=now, price=4.1e-7)
    late = s.put_price_reading(at=now - 600, price=9.9e-7)
    assert late.get("ignored") == "out of order"
    assert s.latest_price()["price"] == 4.1e-7
    s.close()


def test_price_at_returns_none_for_an_unobserved_moment():
    """Never the nearest reading: the honest answer for a stretch nobody watched
    is that we do not know."""
    path, now = _store_with_prices()
    s = Store(path)
    assert s.price_at(now - 500)["price"] == 4.084e-7   # inside the first interval
    assert s.price_at(now - 100)["price"] == 4.103e-7   # inside the second
    assert s.price_at(now - 99_999) is None             # long before we ever looked
    s.close()


# -- the API contract --------------------------------------------------------

def test_an_empty_store_answers_503_rather_than_a_zero_price():
    """A zero price would draw as a crash to the floor of the chart — the most
    alarming thing this page could invent."""
    d = Path(tempfile.mkdtemp(prefix="qdr_price_"))
    Store(d / "qdr.db").close()
    c = _client(d / "qdr.db")
    for path in ("/v1/price/latest", "/v1/price/series",
                 "/v1/price/change?window=24h", "/v1/price/coverage",
                 "/v1/price/at?t=1700000000"):
        assert c.get(path).status_code == 503, path


def test_latest_reports_how_long_the_price_has_stood():
    """"Unchanged" alone cannot distinguish a quiet market from a dead sampler.
    held_for_s and polls together can."""
    path, _ = _store_with_prices()
    body = _client(path).get("/v1/price/latest").json()
    assert body["price"] == 4.103e-7
    assert body["held_for_s"] == 240        # four minutes of confirmations
    assert body["polls"] == 5
    assert body["measured"] is True


def test_latest_says_when_a_new_reading_can_exist():
    """A live page has to count down to real data, not to a cache expiry.

    The sampler writes minute-aligned readings, so the next one lands on the
    next minute boundary. `next_refresh_in` follows that, not this server's
    15-second response cache — a countdown on the cache would reach zero four
    times a minute, three of them with nothing new behind it.
    """
    path, _ = _store_with_prices()
    c = _client(path)

    # Start the pair of calls just after a minute boundary. The countdown
    # targets the NEXT boundary, so a first call landing in the last second of
    # a minute would see the second call roll over to a fresh ~65 and fail an
    # assertion about counting down -- a real property tested at the one moment
    # it legitimately does not hold. Waiting costs a fraction of a second and
    # removes the flake rather than papering over it with a retry.
    interval = pipeline.PRICE_SAMPLE_INTERVAL_S
    into_minute = time.time() % interval
    if into_minute > interval - 3:
        time.sleep(interval - into_minute + 0.2)

    body = c.get("/v1/price/latest").json()
    nxt = body["next_refresh_in"]
    assert 0 < nxt <= pipeline.PRICE_SAMPLE_INTERVAL_S + 5, nxt

    # The second call is served from the cache, and must still count DOWN --
    # returning the cached body verbatim would freeze the page's countdown.
    time.sleep(1.1)
    again = c.get("/v1/price/latest").json()
    assert again["next_refresh_in"] < nxt, (nxt, again["next_refresh_in"])
    assert again["price"] == body["price"], "the cached reading itself must not change"


def test_the_series_declares_itself_as_steps():
    """A consumer that smooths this into a curve invents motion between two
    measurements. The payload says outright how it must be drawn."""
    path, _ = _store_with_prices()
    body = _client(path).get("/v1/price/series").json()
    assert body["draw"] == "step"
    assert body["points"] == 2              # two prices, not ten polls
    for row in body["series"]:
        # Every row carries its own span: that is what makes it a step.
        assert row["until"] >= row["at"]
        assert row["polls"] >= 1


def test_a_window_keeps_an_interval_that_began_before_it():
    """Otherwise the chart has no value at its left edge — the price standing at
    the start of the window was set before it."""
    path, now = _store_with_prices()
    body = _client(path).get("/v1/price/series?window=120").json()
    starts = [r["at"] for r in body["series"]]
    assert min(starts) < now - 120


def test_change_carries_the_coverage_it_was_measured_over():
    """A move measured across a window we sampled a fraction of is a weaker claim
    than one we watched throughout. The number that says which is not optional."""
    path, _ = _store_with_prices()
    body = _client(path).get("/v1/price/change?window=24h").json()
    assert body["change"]["from"] == 4.084e-7
    assert body["change"]["to"] == 4.103e-7
    assert "coverage_pct" in body["change"]
    assert body["change"]["moves"] == 2


def test_change_refuses_a_window_holding_a_single_price():
    """Reporting 0% there would claim a stability nobody measured."""
    d = Path(tempfile.mkdtemp(prefix="qdr_price_"))
    s = Store(d / "qdr.db")
    s.put_price_reading(at=(int(time.time()) // 60) * 60, price=4.1e-7)
    s.close()
    assert _client(d / "qdr.db").get("/v1/price/change?window=24h").status_code == 503


def test_price_at_explains_a_gap_rather_than_claiming_an_empty_store():
    """"Still being filled" would be a false explanation for a store holding
    plenty of history either side of the moment asked about."""
    path, now = _store_with_prices()
    c = _client(path)
    ok = c.get(f"/v1/price/at?t={now - 500}")
    assert ok.status_code == 200
    assert ok.json()["price"] == 4.084e-7
    # The interval behind the answer travels with it, so a caller can see how
    # close the reading sits to the moment they asked about.
    assert ok.json()["interval"]["polls"] == 5

    gap = c.get("/v1/price/at?t=1")
    assert gap.status_code == 503
    assert "not observed" in gap.json()["detail"]
    assert "still being filled" not in gap.json()["detail"]


def test_coverage_publishes_the_poll_ratio():
    """A flat line means "steady" only if somebody was looking. poll_ratio is the
    only field that tells those apart."""
    path, _ = _store_with_prices()
    body = _client(path).get("/v1/price/coverage").json()
    assert body["point"]["points"] == 2
    assert body["point"]["polls"] == 10
    assert body["point"]["poll_ratio"] is not None


# -- the licensing boundary --------------------------------------------------

def _field_names(obj) -> set[str]:
    """Every key appearing anywhere in a response, at any depth."""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _field_names(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _field_names(v)
    return out


def test_no_price_endpoint_ever_publishes_trading_volume():
    """The RPC publishes none, and sourcing it from an exchange would breach the
    terms this project stays inside. A volume field appearing here would mean
    somebody wired one up — this test is the tripwire.

    Field names only: `source.note` says the words "no trading volume", and that
    sentence is the point rather than a violation of it.
    """
    path, now = _store_with_prices()
    c = _client(path)
    for route in ("/v1/price/latest", "/v1/price/series",
                  "/v1/price/change?window=24h", "/v1/price/coverage",
                  f"/v1/price/at?t={now - 500}"):
        names = _field_names(c.get(route).json())
        assert not [n for n in names if "volume" in n.lower()], route
        assert not [n for n in names if n.lower() in ("vol", "v", "quote_volume")], route


def test_every_price_response_names_its_source():
    """These figures get republished. Provenance has to travel with them rather
    than live only in the docs."""
    path, now = _store_with_prices()
    c = _client(path)
    for route in ("/v1/price/latest", "/v1/price/series",
                  "/v1/price/change?window=24h", "/v1/price/coverage",
                  f"/v1/price/at?t={now - 500}"):
        src = c.get(route).json()["source"]
        assert src["provider"] == "Qubic RPC"
        assert "CoinGecko" in src["upstream"]


def test_the_price_endpoints_are_indexed_and_tagged():
    """An endpoint missing from /api or from the OpenAPI tags is one nobody
    finds. The data is not secret, so it is documented like any other."""
    import api.server as server

    index = TestClient(server.app).get("/api").json()["endpoints"]
    paths = server.app.openapi()["paths"]
    for route in ("/v1/price/latest", "/v1/price/series", "/v1/price/change",
                  "/v1/price/at", "/v1/price/coverage"):
        assert route in index, route
        assert route in paths, route
        assert paths[route]["get"]["tags"] == ["price"], route
    assert any(t["name"] == "price" for t in server.app.openapi()["tags"])


# -- the epoch payout study --------------------------------------------------
#
# This is the page's reason to exist: every ~4.4 days the protocol pays 676
# computors at once, those payouts are emission rather than transfers (so the
# public RPC does not carry them), and this project derives them anyway. Lining
# them up against a measured price series is a question nobody else can ask.
#
# Which makes the honesty rules here sharper than elsewhere: an endpoint that
# invites a causal reading must not hand out a number it did not measure.

def _boundary_store(before_gap: bool = False, after_gap: bool = False,
                    epochs: int = 1) -> Store:
    """A store holding minute readings across `epochs` epoch boundaries."""
    d = Path(tempfile.mkdtemp(prefix="qdr-epoch-"))
    s = Store(d / "t.db", allow_fallback=False)
    now = (int(time.time()) // 60) * 60
    # Enough history for a 12h window either side of every boundary.
    span_min = (epochs + 1) * 26 * 60
    start = now - span_min * 60
    for i in range(span_min):
        t = start + i * 60
        # Boundaries evenly spaced; each epoch gets its own price level so a
        # change across the boundary is unambiguous.
        idx = i // (26 * 60)
        if before_gap and (i % (26 * 60)) < 13 * 60 - 30:
            continue        # nothing observed before the boundary
        if after_gap and (i % (26 * 60)) > 13 * 60 + 30:
            continue        # nothing observed after it
        s.put_price_reading(at=t, price=4.0e-7 + idx * 1e-8, epoch=230 + idx)
    return s


def test_boundaries_come_from_our_own_readings():
    """Not from a tick timestamp: a node's log retention is finite, and measured
    2026-09-21 the running epoch's first tick already answered "unknown"."""
    s = _boundary_store()
    b = s.price_epoch_boundaries()
    assert len(b) >= 1
    first = b[0]
    assert first["epoch"] == first["from_epoch"] + 1
    # The change is pinned to the gap between the last old and first new
    # reading -- at minute sampling that is 60s, and the page shows the window
    # rather than a false exact moment.
    assert first["within_s"] == 60
    assert first["after"] < first["at"]


def test_study_reports_change_and_how_well_it_was_watched():
    s = _boundary_store()
    out = pipeline.epoch_payout_study(s)
    case = out["cases"][0]
    assert case["change_pct"] is not None
    assert case["coverage_pct"] == 100.0, "a fully sampled window must say so"
    assert case["polls"] > 0


def test_study_refuses_a_figure_when_one_side_was_not_observed():
    """The whole point. Substituting the nearest reading would dress a guess as
    a measurement -- and here that guess would be read as evidence about a
    payout."""
    for kwargs in ({"before_gap": True}, {"after_gap": True}):
        s = _boundary_store(**kwargs)
        out = pipeline.epoch_payout_study(s)
        case = out["cases"][0]
        assert case["change_pct"] is None, f"invented a change for {kwargs}"
        assert "not observed" in case["note"]
        assert out["observed"] == 0


def test_study_says_when_it_has_seen_too_few_boundaries():
    """One epoch is an anecdote. The endpoint must not invite a reading from it."""
    s = _boundary_store(epochs=1)
    out = pipeline.epoch_payout_study(s)
    assert out["conclusive"] is False
    assert out["epochs_needed"] == pipeline.PAYOUT_STUDY_MIN_EPOCHS - out["observed"]

    s3 = _boundary_store(epochs=3)
    out3 = pipeline.epoch_payout_study(s3)
    assert out3["observed"] >= pipeline.PAYOUT_STUDY_MIN_EPOCHS
    assert out3["conclusive"] is True
    assert out3["epochs_needed"] == 0


def test_study_publishes_spread_not_just_an_average():
    """A mean across scattered cases says nothing; the page needs to be able to
    show that rather than print one reassuring number."""
    s = _boundary_store(epochs=3)
    summary = pipeline.epoch_payout_study(s)["summary"]
    for key in ("median_change_pct", "mean_change_pct", "min_change_pct",
                "max_change_pct", "up", "down"):
        assert key in summary, key


def test_study_never_claims_causation():
    """It reports what happened around the boundary. The wording is part of the
    contract, because the framing is what a reader takes away."""
    s = _boundary_store()
    text = pipeline.epoch_payout_study(s)["measures"].lower()
    assert "does not claim" in text or "not claim" in text


def test_study_answers_200_before_any_boundary_was_seen():
    """A young deployment has watched no epoch change.

    That is a normal state and it IS an answer: the study reports zero cases,
    says it is not conclusive, and counts how many boundaries it still needs.
    It used to raise 503, which threw that answer away and made every browser
    log a red console error against a deployment that was working correctly.

    What must never happen is the opposite: answering 200 with a change figure
    derived from no boundaries at all.
    """
    d = Path(tempfile.mkdtemp(prefix="qdr-epoch-api-"))
    s = Store(d / "qdr.db")
    now = (int(time.time()) // 60) * 60
    for i in range(10):                     # readings, but all one epoch
        s.put_price_reading(at=now - i * 60, price=4.0e-7, epoch=231)
    s.close()

    r = _client(d / "qdr.db").get("/v1/price/epochs")
    assert r.status_code == 200
    body = r.json()

    assert body["boundaries_seen"] == 0
    assert body["observed"] == 0
    assert body["cases"] == []
    assert body["conclusive"] is False
    assert body["epochs_needed"] > 0
    # No boundaries means no summary to draw -- an empty study must not carry
    # one, because the page renders it as a verdict.
    assert "summary" not in body


def test_study_endpoint_is_indexed_and_tagged():
    import api.server as server
    from fastapi.testclient import TestClient as _TC

    index = _TC(server.app).get("/api").json()["endpoints"]
    assert "/v1/price/epochs" in index
    paths = server.app.openapi()["paths"]
    assert paths["/v1/price/epochs"]["get"]["tags"] == ["price"]


def test_study_endpoint_carries_source_and_no_volume():
    d = Path(tempfile.mkdtemp(prefix="qdr-epoch-api-"))
    s = _boundary_store()
    # move the populated store where the client expects it
    import shutil
    shutil.copy(s.path, d / "qdr.db")
    s.close()

    body = _client(d / "qdr.db").get("/v1/price/epochs").json()
    assert body["source"]["provider"] == "Qubic RPC"
    names = _field_names(body)
    assert not [n for n in names if "volume" in n.lower()]


# -- the moving average ------------------------------------------------------
#
# The 50-day average is the one figure on this page that cannot be measured into
# existence on demand: it needs 50 days to have PASSED. Everything here pins the
# same rule from a different side -- the endpoint must never dress a shorter
# window in the longer name, because a chart cannot tell the difference and a
# reader trusting the label would be reading a number that does not exist yet.

def _store_with_hours(days: int, price: float = 4.0e-7,
                      hours_per_day: int = 24) -> Path:
    """A store holding `days` complete days of hourly summaries, ending yesterday.

    Hours rather than points on purpose: the average is built on the permanent
    hourly table, and a test seeded with points would pass even if the endpoint
    read the pruned one.
    """
    d = Path(tempfile.mkdtemp(prefix="qdr_price_avg_"))
    s = Store(d / "qdr.db")
    today = (int(time.time()) // 86400) * 86400
    start = today - days * 86400
    with s._tx() as c:
        for i in range(days):
            day = start + i * 86400
            for h in range(hours_per_day):
                at = day + h * 3600
                c.execute(
                    "INSERT OR REPLACE INTO price_hours (at,open,high,low,close,"
                    "avg_price,market_cap,moves,polls,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (at, price, price, price, price, price, 600_000_000, 1, 60, at))
    s.close()
    return d / "qdr.db"


def test_a_short_record_refuses_to_call_itself_a_fifty_day_average():
    """The failure this guards against is silent: with seven days on record, a
    mean of those seven is a perfectly computable number, and labelling it
    "50-day average" is the lie. `ready` is what a caller branches on."""
    db = _store_with_hours(days=7)
    r = _client(db).get("/v1/price/average?days=50")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is False
    assert body["days_on_record"] == 7
    assert body["days_needed"] == 43
    # Every point says how many days actually stand behind it.
    assert all(p["window_days"] <= 7 for p in body["series"])
    assert all(p["complete"] == 0 for p in body["series"])


def test_a_full_record_reports_ready_and_a_full_window():
    db = _store_with_hours(days=60)
    body = _client(db).get("/v1/price/average?days=50").json()
    assert body["ready"] is True
    assert body["days_on_record"] == 60
    assert body["days_needed"] == 0
    assert body["series"][-1]["window_days"] == 50
    assert body["series"][-1]["complete"] == 1


def test_the_average_is_the_mean_of_the_days_in_its_window():
    """A flat price must average to itself -- the arithmetic check that the
    window is neither short by one nor padded with days outside it."""
    db = _store_with_hours(days=60, price=4.0e-7)
    body = _client(db).get("/v1/price/average?days=50").json()
    assert abs(body["series"][-1]["sma"] - 4.0e-7) < 1e-18


def test_a_thinly_observed_day_does_not_count_as_a_day():
    """Six hours of readings is not a day's price. Counting it would let a
    deployment that was down most of the time reach "50 days" early, with a
    number built from whatever hours happened to be watched."""
    db = _store_with_hours(days=60, hours_per_day=6)
    body = _client(db).get("/v1/price/average?days=50").json()
    assert body["days_on_record"] == 0
    assert body["ready"] is False


def test_the_average_never_publishes_trading_volume():
    """Same rule as every other price endpoint: the RPC publishes no volume,
    and one sourced elsewhere would breach the terms this project stays inside."""
    db = _store_with_hours(days=60)
    body = _client(db).get("/v1/price/average?days=50").json()
    assert "volume" not in _field_names(body)


def test_the_average_names_its_source_and_carries_coverage():
    db = _store_with_hours(days=60)
    body = _client(db).get("/v1/price/average?days=50").json()
    assert body["source"]["provider"] == "Qubic RPC"
    assert "coverage" in body
    assert body["measured"] is True


def test_an_empty_store_answers_503_rather_than_a_flat_line():
    d = Path(tempfile.mkdtemp(prefix="qdr_price_avg_"))
    Store(d / "qdr.db").close()
    assert _client(d / "qdr.db").get("/v1/price/average?days=50").status_code == 503


# -- the compact reading -----------------------------------------------------

def test_price_now_answers_with_the_same_figure_as_latest():
    """Two views of one measurement. If these ever disagree, one of them is
    reading a different store or a different cache generation."""
    db, _ = _store_with_prices()
    c = _client(db)
    full = c.get("/v1/price/latest").json()
    slim = c.get("/v1/price/now").json()
    assert slim["price"] == full["price"]
    assert slim["at"] == full["at"]
    assert slim["status"] == full["status"]


def test_price_now_keeps_the_fields_that_make_a_price_readable():
    """A bare number is the one thing this endpoint must not become. Without a
    timestamp a stopped worker renders as a live quote, and without held_for_s
    a flat market cannot be told from an unobserved one."""
    db, _ = _store_with_prices()
    body = _client(db).get("/v1/price/now").json()
    for k in ("price", "at", "age_s", "held_for_s", "status", "quote"):
        assert k in body, k
    assert body["quote"] == "USD per QU"
    assert body["measured"] is True


def test_price_now_stays_smaller_than_the_full_reading():
    """The endpoint's whole reason for existing. If it grows back toward
    `latest`, a caller may as well use `latest`."""
    import json
    db, _ = _store_with_prices()
    c = _client(db)
    slim = len(json.dumps(c.get("/v1/price/now").json()))
    full = len(json.dumps(c.get("/v1/price/latest").json()))
    assert slim < full // 2, (slim, full)


def test_price_now_never_publishes_trading_volume():
    db, _ = _store_with_prices()
    body = _client(db).get("/v1/price/now").json()
    assert "volume" not in _field_names(body)


def test_price_now_on_an_empty_store_answers_503():
    d = Path(tempfile.mkdtemp(prefix="qdr_price_"))
    Store(d / "qdr.db").close()
    assert _client(d / "qdr.db").get("/v1/price/now").status_code == 503


# -- the calendar ------------------------------------------------------------
#
# A grid of 365 squares is the easiest place on this project to hide a lie: an
# unobserved day painted in the neutral colour looks exactly like a day that was
# measured and did not move, and nobody scanning a year of squares would catch
# it. Every test here defends that one distinction.

def _store_with_day_gap() -> Path:
    """25 days of hours with a 4-day hole and one 3-hour day inside them."""
    d = Path(tempfile.mkdtemp(prefix="qdr_price_cal_"))
    st = Store(d / "qdr.db")
    today = (int(time.time()) // 86400) * 86400
    start = today - 25 * 86400
    with st._tx() as c:
        for i in range(25):
            if 8 <= i < 12:          # nobody was watching
                continue
            hours = 3 if i == 15 else 24   # a day seen only briefly
            day = start + i * 86400
            for h in range(hours):
                at = day + h * 3600
                price = 4.0e-7 * (1 + i * 0.01)
                c.execute(
                    "INSERT OR REPLACE INTO price_hours (at,open,high,low,close,"
                    "avg_price,market_cap,moves,polls,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (at, price, price, price, price, price, 600_000_000, 1, 60, at))
    st.close()
    return d / "qdr.db"


def test_an_unobserved_day_is_never_reported_as_a_flat_day():
    """The whole point of the endpoint. `change_pct: 0` would render in the
    neutral colour and claim the price held on a day nobody measured."""
    body = _client(_store_with_day_gap()).get("/v1/price/days?days=40").json()
    gaps = [d for d in body["series"] if d["state"] == "unwatched"]
    assert gaps, "the seeded hole should appear"
    assert all(d["change_pct"] is None for d in gaps)
    assert all(d["close"] is None for d in gaps)
    # and it is never silently dropped either: a missing square would shift
    # every later day into the wrong weekday column.
    assert len(gaps) == 5      # the 4-day hole plus the 3-hour day


def test_a_briefly_watched_day_does_not_count_as_measured():
    """Three hours produce a real close, but not one worth comparing against a
    full day -- and a calendar cannot show that nuance in a coloured square."""
    body = _client(_store_with_day_gap()).get("/v1/price/days?days=40").json()
    thin = [d for d in body["series"] if 0 < d["hours"] < 12]
    assert thin and all(d["state"] == "unwatched" for d in thin)


def test_the_first_day_is_distinguished_from_an_unwatched_one():
    """Both have change_pct null, for completely different reasons."""
    body = _client(_store_with_day_gap()).get("/v1/price/days?days=40").json()
    first = [d for d in body["series"] if d["state"] == "first"]
    assert len(first) == 1
    assert first[0]["close"] is not None    # it WAS measured
    assert first[0]["change_pct"] is None   # there is just nothing before it


def test_days_are_contiguous_so_a_calendar_can_index_them():
    """One row per calendar day, no holes -- a grid lays these out by position,
    and a missing row would put every later day under the wrong weekday."""
    body = _client(_store_with_day_gap()).get("/v1/price/days?days=40").json()
    ats = [d["at"] for d in body["series"]]
    assert all(b - a == 86400 for a, b in zip(ats, ats[1:]))


def test_the_summary_counts_agree_with_the_rows():
    body = _client(_store_with_day_gap()).get("/v1/price/days?days=40").json()
    sm, rows = body["summary"], body["series"]
    for state in ("up", "down", "flat", "unwatched"):
        assert sm[state] == sum(1 for d in rows if d["state"] == state), state


def test_price_days_never_publishes_trading_volume():
    body = _client(_store_with_day_gap()).get("/v1/price/days?days=40").json()
    assert "volume" not in _field_names(body)


def test_price_days_on_an_empty_store_answers_503():
    d = Path(tempfile.mkdtemp(prefix="qdr_price_cal_"))
    Store(d / "qdr.db").close()
    assert _client(d / "qdr.db").get("/v1/price/days").status_code == 503
