"""Contract names come from Qubic's own registry, or they do not come at all.

The burn panel used to print "Contract 13". It now prints "QSwap", resolved
against the file the official explorer itself loads
(static.qubic.org/v1/general/data/smart_contracts.json, confirmed 2026-09-20).

What is pinned here:

  * a new contract appears by NAME without anyone editing this repo — that is the
    whole point of reading a maintained registry instead of hardcoding a table;
  * an index the registry does not list gets NO name, so the page falls back to
    the bare index rather than showing an invented one;
  * an unreachable registry degrades to the cached copy, and a missing cache to
    no names at all — never to a wrong name, and never to an exception that takes
    the burn panel down with it.

No test here touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdr import contracts  # noqa: E402


REGISTRY_SAMPLE = {
    "smart_contracts": [
        {"contractIndex": 1, "name": "QX", "label": "Qx",
         "address": "BAAA" + "A" * 52 + "RMID"},
        {"contractIndex": 13, "name": "QSWAP", "label": "QSwap",
         "address": "NAAA" + "A" * 52 + "MAML"},
        {"contractIndex": 28, "name": "GGWP", "label": "GGWP",
         "address": "CBAA" + "A" * 52 + "XXXX"},
    ]
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test starts with an empty memo and its own cache directory."""
    monkeypatch.setattr(contracts, "DEFAULT_CACHE", tmp_path / "raw")
    monkeypatch.setattr(contracts, "_memo", {"at": 0.0, "data": None})
    yield


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _serve(monkeypatch, payload, calls=None):
    def fake_get(url, **kw):
        if calls is not None:
            calls.append(url)
        return _Resp(payload)

    monkeypatch.setattr(contracts.requests, "get", fake_get)


def test_an_index_resolves_to_its_published_name(monkeypatch):
    _serve(monkeypatch, REGISTRY_SAMPLE)

    assert contracts.name_for(13) == "QSwap"
    assert contracts.name_for(1) == "Qx"


def test_a_contract_added_upstream_appears_without_a_code_change(monkeypatch):
    """The reason this reads a registry instead of carrying a table."""
    _serve(monkeypatch, REGISTRY_SAMPLE)
    assert contracts.name_for(29) is None

    grown = json.loads(json.dumps(REGISTRY_SAMPLE))
    grown["smart_contracts"].append(
        {"contractIndex": 29, "name": "QNEW", "label": "QNew", "address": "D" * 60})
    _serve(monkeypatch, grown)

    # The memo still holds the old map until it is refreshed; after that the new
    # contract is simply there, with no code change anywhere in this repo.
    assert contracts.registry(force=True).get(29)["label"] == "QNew"
    assert contracts.name_for(29) == "QNew"


def test_an_unlisted_index_gets_no_name_rather_than_a_guess(monkeypatch):
    _serve(monkeypatch, REGISTRY_SAMPLE)

    assert contracts.name_for(999) is None
    d = contracts.describe(999)
    assert d["known"] is False and d["name"] is None and d["index"] == 999


def test_a_known_index_describes_itself_fully(monkeypatch):
    _serve(monkeypatch, REGISTRY_SAMPLE)

    d = contracts.describe(13)
    assert d["known"] is True
    assert d["name"] == "QSWAP" and d["label"] == "QSwap"
    assert d["address"].startswith("NAAA")


def test_the_registry_is_fetched_once_and_then_cached(monkeypatch):
    """The burn panel must not cost a third-party request per view."""
    calls: list[str] = []
    _serve(monkeypatch, REGISTRY_SAMPLE, calls)

    for _ in range(5):
        contracts.registry()

    assert len(calls) == 1


def test_an_unreachable_registry_falls_back_to_the_cached_copy(monkeypatch):
    _serve(monkeypatch, REGISTRY_SAMPLE)
    assert contracts.name_for(13) == "QSwap"        # warms the disk cache

    import requests

    def boom(url, **kw):
        raise requests.ConnectionError("upstream down")

    monkeypatch.setattr(contracts.requests, "get", boom)
    monkeypatch.setattr(contracts, "_memo", {"at": 0.0, "data": None})
    monkeypatch.setattr(contracts, "TTL_S", 0)      # force a refetch attempt

    assert contracts.name_for(13) == "QSwap", "a cached name was thrown away"


def test_no_registry_at_all_yields_no_names_and_no_exception(monkeypatch):
    """Degrading to bare indices is the pre-existing behaviour, and is fine.
    Raising here would take down a panel that has real numbers to show."""
    import requests

    def boom(url, **kw):
        raise requests.ConnectionError("upstream down")

    monkeypatch.setattr(contracts.requests, "get", boom)

    assert contracts.registry() == {}
    assert contracts.name_for(13) is None
    assert contracts.describe(13)["known"] is False


def test_malformed_registry_entries_are_skipped_not_fatal(monkeypatch):
    _serve(monkeypatch, {"smart_contracts": [
        {"contractIndex": 13, "name": "QSWAP", "label": "QSwap"},
        {"contractIndex": None, "name": "BROKEN"},
        {"name": "NO_INDEX"},
        {"contractIndex": "not-a-number", "name": "BAD"},
        {"contractIndex": 4},                      # no name at all
        "not even a dict",
    ]})

    reg = contracts.registry()
    assert set(reg) == {13}
    assert contracts.name_for(13) == "QSwap"


def test_garbage_indices_do_not_raise(monkeypatch):
    _serve(monkeypatch, REGISTRY_SAMPLE)

    assert contracts.name_for(None) is None
    assert contracts.name_for("abc") is None
    assert contracts.describe("abc")["known"] is False


# --- the endpoint and the page ---------------------------------------------

def test_the_burn_contracts_endpoint_carries_names(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api.server as server
    from qdr.store import Store

    _serve(monkeypatch, REGISTRY_SAMPLE)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    store = Store(str(tmp_path / "q.db"))
    monkeypatch.setattr(server, "_store", store)
    store.put_burn_bucket(from_tick=1, to_tick=500, epoch=231, day="2026-09-19",
                          contract_burned=50_801, contract_events=1,
                          by_contract={"13": 50_801})

    body = TestClient(server.app).get("/v1/burn/contracts").json()

    row = body["by_contract"][0]
    assert row["index"] == 13
    assert row["label"] == "QSwap" and row["known"] is True
    assert body["registry"]["source"].startswith("https://static.qubic.org/")


def test_the_page_prefers_the_name_and_keeps_the_index():
    """Both matter: the name is what a reader understands, the index is what the
    chain's raw data carries and what an explorer can be checked against."""
    html = (ROOT / "dashboard" / "burn.html").read_text(encoding="utf-8")

    assert "c.label || c.name" in html, "the page never reads the resolved name"
    assert "ct-sub" in html, "the index is dropped once a name exists"
    # and the old claim that no source exists must be gone, in both languages
    assert "no public index" not in html
    assert "keine öffentliche Index" not in html
