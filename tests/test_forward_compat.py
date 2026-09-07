"""Forward-compatibility: the pipeline must keep working as the network changes.

New epochs arrive continuously, pools appear and grow, the analysis layer may add
confidence levels, and even network constants (the slot count) are not guaranteed
forever. Nothing in the analysis may be pinned to today's shape.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr.clustering import (
    CONF_LINKED,
    CONF_UNATTRIBUTED,
    cluster_epoch,
    declared_vs_detected,
    linkage_coverage,
)
from qdr.metrics import summarize


def ids(n, prefix="F"):
    return [f"{prefix}{i:05d}" + "Z" * 54 for i in range(n)]


def test_slot_count_is_not_assumed_to_be_676():
    """The 676 is a current network constant, not an invariant of this code."""
    for n in (100, 676, 1000, 2048):
        c = ids(n)
        clusters = cluster_epoch(c, {x: 1 for x in c}, registry=None, linkage=None)
        cov = linkage_coverage(c, clusters)
        assert cov["computors"] == n
        assert len(clusters) == n           # all unattributed -> one each
        assert cov["unattributed_slots"] == n


def test_new_pools_appear_without_code_changes():
    """A pool added to the registry later is picked up on its next epoch."""
    c = ids(50)
    rev = {x: 10 for x in c}
    before = cluster_epoch(c, rev, {"pools": []}, {})
    after = cluster_epoch(
        c, rev, {"pools": [{"id": "brandnew", "label": "Brand New Pool",
                            "computors": c[:12]}]}, {})
    assert len(before) == 50
    assert len(after) == 39                       # 12 slots folded into one operator
    assert any(cl.cluster_id == "brandnew" and cl.computor_count == 12 for cl in after)


def test_growing_epoch_range_is_handled():
    """Metrics must stay well-defined as the epoch count grows without bound."""
    c = ids(200)
    for epoch in range(400, 460, 10):
        size = 20 + (epoch - 400) // 10
        linkage = {x: "WALLET" for x in c[:size]}
        clusters = cluster_epoch(c, {x: 5 for x in c}, registry=None, linkage=linkage)
        cov = linkage_coverage(c, clusters)
        assert cov["onchain_linked_slots"] == size
        assert 0.0 <= cov["attributed_share"] <= 1.0
        s = summarize([cl.revenue for cl in clusters])
        assert s.nakamoto_one_third >= 1


def test_unknown_confidence_level_does_not_break_aggregation():
    """A level added by a future analysis layer must not corrupt the summaries.

    linkage_coverage counts by confidence, so an unseen value has to fall through
    as its own bucket rather than being miscounted as attributed or unattributed.
    """
    c = ids(10)
    clusters = cluster_epoch(c, {x: 1 for x in c}, registry=None,
                             linkage={x: "W" for x in c[:4]})
    # simulate a future level arriving on an existing cluster
    for cl in clusters:
        if cl.confidence == CONF_LINKED:
            cl.confidence = "quorum-correlated"
            break
    cov = linkage_coverage(c, clusters)
    assert cov["computors"] == 10
    assert cov["slots_by_confidence"]["quorum-correlated"] == 4
    # unattributed is still counted exactly; the unknown level is simply not
    # claimed as on-chain-linked, which is the safe direction to be wrong in
    assert cov["unattributed_slots"] == 6
    assert cov["onchain_linked_slots"] == 0
    delta = declared_vs_detected(clusters)
    assert delta["undeclared_linked_slots"] == 0


def test_empty_and_degenerate_epochs_do_not_raise():
    """Edge cases the live chain can actually produce (a boundary, an outage)."""
    assert cluster_epoch([], {}, None, None) == []
    cov = linkage_coverage([], [])
    assert cov["computors"] == 1 and cov["unattributed_slots"] == 0   # no divide-by-zero
    c = ids(3)
    zero = cluster_epoch(c, {x: 0 for x in c}, None, None)            # revenue not yet paid
    s = summarize([cl.revenue for cl in zero])
    assert s.gini == 0.0 and s.nakamoto_one_third == 0


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} forward-compat tests passed")
