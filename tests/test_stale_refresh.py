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
    store.put_report(10, _report(10), "sealed")          # the recompute
    assert store.stale_epochs("0.3.0") == []


def test_the_superseded_computation_is_kept(tmp_path, monkeypatch):
    """A changed number has to have a recorded before and after."""
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(qdr.store, "__version__", "0.2.1")
    store.upsert_epoch(10, "sealed", 1, 2, 676)
    store.put_report(10, _report(10), "sealed")

    monkeypatch.setattr(qdr.store, "__version__", "0.3.0")
    store.put_report(10, _report(10), "sealed")

    assert store.get_report(10, code_version="0.2.1") is not None
    assert store.get_report(10, code_version="0.3.0") is not None
    assert store.get_report(10)["code_version"] == "0.3.0"   # newest is served
