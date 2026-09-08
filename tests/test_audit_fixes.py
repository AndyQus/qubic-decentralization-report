"""Regressions for defects found auditing the published figures.

Each of these put a wrong number, or a wrongly-labelled number, in front of a
reader — the failure mode this report can least afford.
"""
import pytest

from qdr import pipeline
from qdr.metrics import nakamoto_coefficient
from qdr.store import Store


# --- metrics ---------------------------------------------------------------

def test_nakamoto_is_exact_at_a_boundary():
    """676 equal weights at ½ is exactly 339, and float accumulation said 338.

    Accumulating x/s drifted to 0.5000000000000017 after 338 terms and crossed
    the threshold one slot early, publishing Nakamoto-½ off by one.
    """
    assert nakamoto_coefficient([1.0] * 676, 0.5) == 339
    assert nakamoto_coefficient([1.0] * 676, 1 / 3) == 226


def test_nakamoto_edges():
    assert nakamoto_coefficient([], 0.5) == 0
    assert nakamoto_coefficient([10.0, 0.0, 0.0], 0.5) == 1
    assert nakamoto_coefficient([5.0, 5.0], 0.5) == 2


# --- slot distribution -----------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def _seal(store, epoch, revenues, status="sealed"):
    ids = [f"ID{i:03d}" for i in range(len(revenues))]
    store.upsert_epoch(epoch, status, 1, 2, len(ids))
    store.put_revenue(epoch, dict(zip(ids, revenues)), 1, 2,
                      complete=(status == "sealed"))


def test_bands_never_overlap_for_small_n(store):
    """Bands must tile the ranking. Overlapping ones counted revenue twice."""
    for n in (1, 2, 5, 10, 37, 50):
        _seal(store, 100 + n, list(range(n, 0, -1)))
        d = pipeline.slot_distribution(store, 100 + n)
        assert d is not None
        assert sum(b["slots"] for b in d["bands"]) == n, f"n={n} slots double-counted"
        assert sum(b["revenue_share"] for b in d["bands"]) == pytest.approx(1.0, abs=0.01)


def test_a_live_epoch_is_refused_even_if_rows_say_complete(store):
    """Stale complete=1 rows from an older code version must not leak through.

    put_revenue only rewrites rows when the epoch is recomputed, so an upgrade
    leaves the old flags in place; the epoch's own status has to be the authority.
    """
    # The real shape of this: the epoch is live, and an older code version wrote
    # its revenue rows as complete (Bob's log lists every computor long before
    # the epoch settles). A sealed epoch is never walked back, so the live row
    # comes first and the stale complete flags land on top of it.
    store.upsert_epoch(200, "live", 1, 2, 10)
    ids = [f"ID{i:03d}" for i in range(10)]
    store.put_revenue(200, {i: 100 for i in ids}, 1, 2, complete=True)
    assert store.revenue_is_complete(200) is True      # the stale flag is there
    assert pipeline.slot_distribution(store, 200) is None


# --- bundle consistency ----------------------------------------------------

def test_headline_report_is_an_epoch_the_slider_covers(store):
    """The header labelled one epoch while every panel showed another."""
    _seal(store, 10, [100] * 8, status="sealed")
    pipeline.compute_and_store_stub = None             # (no-op marker for clarity)
    store.put_report(10, {"epoch": 10, "status": "sealed",
                          "totals": {"computors": 8, "operators": 8,
                                     "declared_operators": 0, "unattributed_slots": 8,
                                     "total_revenue": 800},
                          "concentration_by_revenue": {
                              "gini": 0.0, "hhi_normalized": 0.0, "top1_share": 0.125,
                              "nakamoto_one_third": 3, "nakamoto_half": 5},
                          "concentration_by_slots": {"nakamoto_one_third": 3},
                          "clusters": [], "linkage_coverage": {}}, "sealed")
    bundle = pipeline.build_dashboard_bundle(store)
    charted = {p["epoch"] for p in bundle["timeseries"]["series"]}
    assert bundle["report"]["epoch"] in charted


def test_a_store_holding_only_a_running_epoch_publishes_nothing(store):
    """A fresh deployment mid-backfill must not publish the running epoch.

    Found running the real container against an empty volume: the API served
    epoch 229 at zero revenue, Nakamoto 0, as though that described the network.
    """
    store.upsert_epoch(229, "live", 1, 2, 676)
    store.put_report(229, {
        "epoch": 229, "status": "live",
        "totals": {"computors": 676, "operators": 676, "declared_operators": 0,
                   "unattributed_slots": 676, "total_revenue": 0},
        "concentration_by_revenue": {"gini": 0.0, "hhi_normalized": 0.0,
                                     "top1_share": 0.0, "nakamoto_one_third": 0,
                                     "nakamoto_half": 0},
        "concentration_by_slots": {"nakamoto_one_third": 226},
        "clusters": [], "linkage_coverage": {}}, "live")
    bundle = pipeline.build_dashboard_bundle(store)
    assert bundle["report"] is None
    assert bundle["timeseries"]["series"] == []
