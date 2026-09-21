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
