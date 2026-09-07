"""Store + pipeline tests: the sealed-vs-live rule.

The point of the store (CONCEPT §5.1) is that history is the product: the running
epoch is always recomputed, a closed and completely derived epoch is sealed and
served from disk forever after, and a recompute under new logic never silently
erases the number it replaces.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr import pipeline
from qdr.clustering import cluster_epoch
from qdr.store import STATUS_LIVE, STATUS_PARTIAL, STATUS_SEALED, Store


def fresh_store() -> Store:
    d = tempfile.mkdtemp(prefix="qdr_test_")
    return Store(Path(d) / "qdr.db")


def ids(n: int) -> list[str]:
    return [f"ID{i:02d}" + "A" * 57 for i in range(n)]


class StubClient:
    """Deterministic stand-in for the RPC: fixed computors, fixed current epoch."""

    def __init__(self, computors, current=228):
        self._c = computors
        self._current = current

    def computors(self, epoch):
        return list(self._c)

    def current_epoch(self):
        return self._current


def test_sealed_epoch_is_not_downgraded():
    s = fresh_store()
    s.upsert_epoch(220, STATUS_LIVE)
    s.seal_epoch(220)
    s.upsert_epoch(220, STATUS_LIVE)  # a later poll must not walk it back
    assert s.epoch_status(220) == STATUS_SEALED


def test_revenue_roundtrip_with_tick_window():
    s = fresh_store()
    s.put_revenue(228, {"A": 10, "B": 0}, first_tick=100, last_tick=199, complete=True)
    assert s.get_revenue(228) == {"A": 10, "B": 0}
    assert s.revenue_is_complete(228)
    # an incomplete pull must be visible as such
    s.put_revenue(229, {"A": 5}, complete=False)
    assert not s.revenue_is_complete(229)


def test_transfers_are_deduped():
    s = fresh_store()
    tx = {"txId": "t1", "tick": 5, "sourceId": "ARB", "destId": "A", "amount": 10}
    s.put_transfers(228, [tx])
    s.put_transfers(228, [tx])  # re-poll of the same window
    assert len(s.get_transfers(228)) == 1


def test_linkage_accumulates_across_epochs():
    """A link proven in an earlier epoch does not stop being true later."""
    s = fresh_store()
    s.put_linkage(220, {"A": "W1"})
    s.put_linkage(228, {"B": "W1"})
    assert s.get_linkage(228) == {"A": "W1", "B": "W1"}
    assert s.get_linkage(220) == {"A": "W1"}  # as known at that time


def test_report_versions_are_kept_side_by_side():
    """A recompute under new logic must not erase the number it replaces."""
    s = fresh_store()
    s.put_report(228, {"epoch": 228, "n": 1}, STATUS_SEALED)
    import qdr.store as store_mod
    original = store_mod.__version__
    try:
        store_mod.__version__ = "9.9.9"  # simulate a logic change
        s.put_report(228, {"epoch": 228, "n": 2}, STATUS_SEALED)
    finally:
        store_mod.__version__ = original
    versions = s.report_versions(228)
    assert len(versions) == 2
    assert {v["code_version"] for v in versions} == {original, "9.9.9"}


def test_clusters_roundtrip():
    s = fresh_store()
    computors = ids(4)
    clusters = cluster_epoch(computors, {c: 100 for c in computors},
                             registry=None, linkage={computors[0]: "W", computors[1]: "W"})
    s.put_clusters(228, clusters)
    got = s.get_clusters(228)
    assert len(got) == len(clusters)
    linked = [c for c in got if c["confidence"] == "linked"]
    assert linked and len(linked[0]["computors"]) == 2


def test_get_report_serves_sealed_from_store_without_client():
    """A sealed epoch never needs the network again."""
    s = fresh_store()
    s.upsert_epoch(220, STATUS_LIVE)
    s.put_report(220, {"epoch": 220, "totals": {}}, STATUS_SEALED)
    s.seal_epoch(220)
    rep = pipeline.get_report(s, 220, client=None)
    assert rep is not None and rep["epoch"] == 220
    assert rep["status"] == STATUS_SEALED


def test_incomplete_revenue_keeps_epoch_partial():
    """A closed epoch whose payout pull was not provably exhaustive must not seal.

    Publishing an under-counted epoch as settled is exactly the failure mode that
    makes two implementations disagree.
    """
    s = fresh_store()
    computors = ids(4)
    # current epoch is 230, so 228 is closed; the stub RPC yields no transfers,
    # so derivation cannot be proven complete -> partial, not sealed.
    client = StubClient(computors, current=230)
    rep = pipeline.compute_epoch(client, s, 228, registry={"pools": []})
    assert rep["status"] == STATUS_PARTIAL
    assert s.epoch_status(228) == STATUS_PARTIAL
    assert rep["warnings"]  # says why


def test_partial_epochs_stay_out_of_the_timeseries():
    s = fresh_store()
    s.put_report(226, {"epoch": 226, "status": STATUS_SEALED, "totals":
                       {"operators": 3, "declared_operators": 1, "unattributed_slots": 0},
                       "concentration_by_revenue": {"gini": 0.1, "hhi_normalized": 0.1,
                                                    "nakamoto_one_third": 2, "nakamoto_half": 3,
                                                    "top1_share": 0.4},
                       "concentration_by_slots": {"nakamoto_one_third": 2},
                       "linkage_coverage": {}}, STATUS_SEALED)
    s.put_report(227, {"epoch": 227, "status": STATUS_PARTIAL, "totals":
                       {"operators": 99, "declared_operators": 0, "unattributed_slots": 99},
                       "concentration_by_revenue": {"gini": 0.0, "hhi_normalized": 0.0,
                                                    "nakamoto_one_third": 40, "nakamoto_half": 60,
                                                    "top1_share": 0.01},
                       "concentration_by_slots": {"nakamoto_one_third": 40},
                       "linkage_coverage": {}}, STATUS_PARTIAL)
    ts = pipeline.build_timeseries(s)
    assert [p["epoch"] for p in ts["series"]] == [226]


def test_backfill_skips_sealed_unless_forced():
    s = fresh_store()
    computors = ids(4)
    client = StubClient(computors, current=230)
    s.upsert_epoch(228, STATUS_LIVE)
    s.seal_epoch(228)
    out = pipeline.backfill(client, s, [228], registry={"pools": []})
    assert out["skipped_sealed"] == [228]
    forced = pipeline.backfill(client, s, [228], registry={"pools": []}, force=True)
    assert [d["epoch"] for d in forced["computed"]] == [228]


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} store tests passed")
