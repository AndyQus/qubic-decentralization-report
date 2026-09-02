"""End-to-end test of clustering + report assembly on a synthetic epoch.

Uses an offline client whose computors() is stubbed, with injected revenue and a
synthetic self-reporting registry — so it verifies the wiring without network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr.clustering import cluster_epoch, apply_onchain_linkage
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
    # 2 undeclared -> 2 singleton unattributed clusters
    unattributed = [c for c in clusters if c.confidence == "unattributed"]
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


def test_onchain_linkage_merges_unattributed():
    computors, registry, revenue = make_case()
    clusters = cluster_epoch(computors, revenue, registry)
    # say the 2 "independent" slots actually share one payout wallet -> detected link
    indep = computors[6:8]
    linkage = {indep[0]: "WALLETX", indep[1]: "WALLETX"}
    merged = apply_onchain_linkage(clusters, linkage)
    linked = [c for c in merged if c.confidence == "linked"]
    assert len(linked) == 1
    assert linked[0].computor_count == 2  # the "smoke": 2 slots were really 1 owner


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} report tests passed")
