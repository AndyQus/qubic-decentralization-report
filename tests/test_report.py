"""End-to-end test of clustering + report assembly on a synthetic epoch.

Uses an offline client whose computors() is stubbed, with injected revenue and a
synthetic self-reporting registry — so it verifies the wiring without network.

The cases below encode the v0.2 rule (CONCEPT §4.2): on-chain linkage is the
default attribution layer, and "unattributed" means the ledger showed no link —
not that nobody filed a self-report.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr.clustering import (
    CONF_DECLARED,
    CONF_DECLARED_LINKED,
    CONF_LINKED,
    CONF_UNATTRIBUTED,
    cluster_epoch,
    declared_vs_detected,
    linkage_coverage,
)
from qdr.report import build_epoch_report


class StubClient:
    def __init__(self, computors):
        self._c = computors

    def computors(self, epoch):
        return list(self._c)


def make_case():
    # 8 slots. Pool "big" secretly holds 4, pool "mid" holds 2, 2 are independent.
    computors = [f"ID{i:02d}" + "A" * 57 for i in range(8)]
    big = computors[0:4]
    mid = computors[4:6]
    registry = {
        "pools": [
            {"id": "big", "label": "BigPool", "computors": big},
            {"id": "mid", "label": "MidPool", "computors": mid},
        ]
    }
    # revenue: every slot earns 100 (equal per-slot pay)
    revenue = {c: 100 for c in computors}
    return computors, registry, revenue


def test_cluster_counts():
    computors, registry, revenue = make_case()
    clusters = cluster_epoch(computors, revenue, registry)
    by_id = {c.cluster_id: c for c in clusters}
    assert by_id["big"].computor_count == 4
    assert by_id["mid"].computor_count == 2
    # 2 slots with no declaration and no on-chain link -> 2 unattributed singletons
    unattributed = [c for c in clusters if c.confidence == CONF_UNATTRIBUTED]
    assert len(unattributed) == 2
    # total operators = 2 pools + 2 singletons = 4
    assert len(clusters) == 4


def test_report_metrics_reflect_hidden_concentration():
    computors, registry, revenue = make_case()
    rep = build_epoch_report(StubClient(computors), 228, revenue=revenue, registry=registry)
    assert rep["totals"]["computors"] == 8
    assert rep["totals"]["operators"] == 4
    assert rep["totals"]["declared_operators"] == 2
    assert rep["totals"]["unattributed_slots"] == 2
    # 8 equal slots look decentralized; but by operator, top1 holds 4/8 = 0.5 of revenue
    assert abs(rep["concentration_by_revenue"]["top1_share"] - 0.5) < 1e-9
    # nakamoto(1/3) by revenue: BigPool alone = 0.5 > 1/3 -> 1
    assert rep["concentration_by_revenue"]["nakamoto_one_third"] == 1


def test_onchain_linkage_is_the_default_layer():
    """Linkage clusters with no registry at all — the core of the v0.2 change.

    In v0.1 an undeclared-but-linked pair stayed two 'unattributed' singletons
    unless an optional hook was called. It must now cluster by default.
    """
    computors, _, revenue = make_case()
    # the ledger shows slots 6 and 7 paying out to one wallet
    linkage = {computors[6]: "WALLETX", computors[7]: "WALLETX"}
    clusters = cluster_epoch(computors, revenue, registry=None, linkage=linkage)
    linked = [c for c in clusters if c.confidence == CONF_LINKED]
    assert len(linked) == 1
    assert linked[0].computor_count == 2  # the "smoke": 2 slots, 1 real owner
    # and with no registry the other 6 are genuinely unattributed, not mislabeled
    assert sum(c.computor_count for c in clusters if c.confidence == CONF_UNATTRIBUTED) == 6


def test_declaration_confirmed_by_chain_outranks_declaration_alone():
    computors, registry, revenue = make_case()
    # the chain confirms BigPool's declared slots share a payout wallet
    linkage = {c: "BIGWALLET" for c in computors[0:4]}
    clusters = cluster_epoch(computors, revenue, registry, linkage)
    by_id = {c.cluster_id: c for c in clusters}
    assert by_id["big"].confidence == CONF_DECLARED_LINKED
    assert by_id["mid"].confidence == CONF_DECLARED  # declared, unconfirmed
    # a declaration must never split what the chain linked
    assert by_id["big"].computor_count == 4


def test_declaration_does_not_split_onchain_cluster():
    """A self-report cannot un-prove a payout path."""
    computors, _, revenue = make_case()
    # chain: slots 0-3 are one owner. registry: claims only 0-1 as "big".
    linkage = {c: "WALLETY" for c in computors[0:4]}
    registry = {"pools": [{"id": "big", "label": "BigPool", "computors": computors[0:2]}]}
    clusters = cluster_epoch(computors, revenue, registry, linkage)
    # slots 2-3 stay linked (undeclared concentration), never dissolved to singletons
    linked = [c for c in clusters if c.confidence == CONF_LINKED]
    assert sum(c.computor_count for c in linked) == 2
    assert all(c.computor_count == 1 for c in clusters if c.confidence == CONF_UNATTRIBUTED)


def test_linkage_coverage_reports_how_much_is_resolved():
    computors, registry, revenue = make_case()
    linkage = {c: "BIGWALLET" for c in computors[0:4]}
    clusters = cluster_epoch(computors, revenue, registry, linkage)
    cov = linkage_coverage(computors, clusters)
    assert cov["computors"] == 8
    assert cov["onchain_linked_slots"] == 4
    assert cov["declared_slots"] == 6          # big(4) + mid(2)
    assert cov["unattributed_slots"] == 2
    assert abs(cov["attributed_share"] - 0.75) < 1e-9


def test_declared_vs_detected_quantifies_the_smoke():
    computors, _, revenue = make_case()
    # 4 slots provably one owner, none of them declared
    linkage = {c: "WALLETZ" for c in computors[0:4]}
    clusters = cluster_epoch(computors, revenue, registry=None, linkage=linkage)
    delta = declared_vs_detected(clusters)
    assert delta["undeclared_linked_operators"] == 1
    assert delta["undeclared_linked_slots"] == 4
    assert delta["largest_undeclared_cluster"] == 4
    assert delta["confirmed_operators"] == 0


def test_report_carries_provenance_and_coverage():
    computors, registry, revenue = make_case()
    rep = build_epoch_report(StubClient(computors), 228, revenue=revenue, registry=registry)
    # the blocks that let a third party locate a divergence
    assert "revenue_provenance" in rep
    assert "linkage_coverage" in rep
    assert "declared_vs_detected" in rep
    assert rep["revenue_provenance"]["total_revenue"] == 800
    assert rep["status"] == "live"


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} report tests passed")
