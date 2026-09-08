"""A deploy must retire the figures its own code has since corrected.

Sealed epochs are immutable by design — but that rule assumes the computation was
right. When a fix changes what the right answer is, an epoch sealed by the older
version is not "already done"; it is wrong and still on the page. This is what
happened in practice: a container running the pre-fix image re-sealed epochs, and
the corrected numbers silently reverted.
"""
import qdr
from qdr.store import Store


def _report(epoch, status="sealed"):
    return {"epoch": epoch, "status": status,
            "totals": {"computors": 676, "operators": 676, "declared_operators": 0,
                       "unattributed_slots": 676, "total_revenue": 100},
            "concentration_by_revenue": {"gini": 0.0, "hhi_normalized": 0.0,
                                         "top1_share": 0.0, "nakamoto_one_third": 1,
                                         "nakamoto_half": 1},
            "concentration_by_slots": {"nakamoto_one_third": 1},
            "clusters": [], "linkage_coverage": {}}


def test_an_epoch_from_an_older_version_is_stale(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(qdr.store, "__version__", "0.2.1")
    store.upsert_epoch(10, "sealed", 1, 2, 676)
    store.put_report(10, _report(10), "sealed")

    monkeypatch.setattr(qdr.store, "__version__", "0.3.0")
    assert store.stale_epochs("0.3.0") == [10]


def test_nothing_is_stale_once_recomputed(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(qdr.store, "__version__", "0.2.1")
    store.upsert_epoch(10, "sealed", 1, 2, 676)
    store.put_report(10, _report(10), "sealed")

    monkeypatch.setattr(qdr.store, "__version__", "0.3.0")
    store.upsert_epoch(10, "sealed", 1, 2, 676)          # the recompute writes both
    store.put_report(10, _report(10), "sealed")
    assert store.stale_epochs("0.3.0") == []


def test_the_superseded_computation_is_kept(tmp_path, monkeypatch):
    """A changed number has to have a recorded before and after."""
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(qdr.store, "__version__", "0.2.1")
    store.upsert_epoch(10, "sealed", 1, 2, 676)
    store.put_report(10, _report(10), "sealed")

    monkeypatch.setattr(qdr.store, "__version__", "0.3.0")
    store.upsert_epoch(10, "sealed", 1, 2, 676)
    store.put_report(10, _report(10), "sealed")

    assert store.get_report(10, code_version="0.2.1") is not None
    assert store.get_report(10, code_version="0.3.0") is not None
    assert store.get_report(10)["code_version"] == "0.3.0"   # newest is served


def test_an_older_build_writing_to_the_same_store_is_detected(tmp_path, monkeypatch):
    """The failure that actually reached the page.

    reports are keyed by (epoch, code_version), so a 0.3.0 recompute leaves its
    row in place forever. But computor_revenue is NOT versioned: an older build
    touching the same volume afterwards rewrote those rows — flipping a running
    epoch back to complete — while the 0.3.0 report row still said "current".
    The epoch row records who wrote last, so staleness has to consult it too.
    """
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(qdr.store, "__version__", "0.3.0")
    store.upsert_epoch(10, "live", 1, 2, 676)
    store.put_report(10, _report(10, "live"), "live")
    assert store.stale_epochs("0.3.0") == []

    # an older build runs against the same store
    monkeypatch.setattr(qdr.store, "__version__", "0.2.1")
    store.upsert_epoch(10, "live", 1, 2, 676)
    store.put_revenue(10, {"ID0": 5}, 1, 2, complete=True)   # the old bug

    monkeypatch.setattr(qdr.store, "__version__", "0.3.0")
    assert store.stale_epochs("0.3.0") == [10]
    # and independently: a live epoch is never complete, whatever the rows say
    assert store.revenue_is_complete(10) is False
