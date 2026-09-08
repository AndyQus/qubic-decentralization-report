"""The slot distribution is what the page shows while attribution is empty.

It stands in for the operator map, so the ways it can lie matter: a running
epoch's partial payouts would render as extreme concentration, and a cluster
list that reaches further than the timeseries would open the dashboard on an
epoch the slider cannot address.
"""
import pytest

from qdr import pipeline
from qdr.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def _seal(store, epoch, revenues, status="sealed"):
    ids = [f"ID{i:03d}" for i in range(len(revenues))]
    store.upsert_epoch(epoch, status, 1, 2, len(ids))
    store.put_revenue(epoch, dict(zip(ids, revenues)), 1, 2,
                      complete=(status == "sealed"))


def test_distribution_describes_the_spread(store):
    _seal(store, 10, [100] * 60 + [50] * 40)
    d = pipeline.slot_distribution(store, 10)
    assert d["slots"] == 100
    assert d["max"] == 100 and d["min"] == 50
    assert d["spread"] == 2.0
    assert d["flat_slots"] == 60          # the modal payout
    assert d["flat_share"] == 0.6
    assert sum(b["revenue_share"] for b in d["bands"]) == pytest.approx(1.0, abs=0.02)


def test_a_running_epoch_has_no_distribution(store):
    """Payouts land progressively: a half-paid epoch is not a distribution.

    Epoch 229 mid-flight held 859M QU of the ~178,000M it settles at, which as a
    distribution reads as "the top 1% take 80%" — an artefact of the clock, not
    a fact about the network.
    """
    _seal(store, 11, [100] * 5 + [0] * 95, status="live")
    assert pipeline.slot_distribution(store, 11) is None


def test_an_epoch_with_no_revenue_has_no_distribution(store):
    _seal(store, 12, [0] * 10, status="live")
    assert pipeline.slot_distribution(store, 12) is None
