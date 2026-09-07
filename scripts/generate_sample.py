"""Generate a synthetic-but-realistic sample report + timeseries for the dashboard.

Clearly labeled as SAMPLE data (no live RPC access in this environment). Once the
ingestion client can reach rpc.qubic.org, the same report.py functions produce the
real thing; this only fabricates plausible inputs so the front-end has data.

Writes:
  api/sample/report_latest.json
  api/sample/timeseries.json
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdr.report import build_epoch_report

random.seed(42)
N_COMPUTORS = 676


def fake_id(n: int) -> str:
    return f"C{n:04d}" + "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(55))


class SampleClient:
    """Stub client: returns the same 676 identities each epoch."""
    def __init__(self, computors):
        self._c = computors
    def computors(self, epoch):
        return list(self._c)


def build_registry(computors, epoch_shift=0):
    """A few pools declare growing shares of the 676 slots across epochs."""
    # pool sizes drift over epochs to make the animation meaningful
    pools_spec = [
        ("jetski", "JetSki Pool", 150 + epoch_shift * 6),
        ("qubic.li", "Qubic.li Pool", 120 - epoch_shift * 2),
        ("apool", "Apool", 90 + epoch_shift * 3),
        ("minerlab", "MinerLab", 60),
        ("solutions", "Solutions", 45),
    ]
    idx = 0
    pools = []
    for pid, label, size in pools_spec:
        size = max(0, size)
        pools.append({"id": pid, "label": label, "computors": computors[idx:idx + size]})
        idx += size
    # the rest stay undeclared. Some of them are NOT independent: see hidden_linkage().
    return {"pools": pools}


def hidden_linkage(computors, registry, size=70):
    """Simulate what the payout graph reveals: a large undeclared operator.

    This is the case the report exists for — slots that file no self-report but
    provably share a payout destination. In v0.1 these looked like `size`
    independent operators; now they cluster as one.
    """
    declared = {c for p in registry["pools"] for c in p["computors"]}
    undeclared = [c for c in computors if c not in declared]
    hidden = undeclared[:size]
    linkage = {c: "HIDDENPOOLWALLET" for c in hidden}
    # plus a couple of small linked pairs further down the tail
    for i in range(size, min(size + 12, len(undeclared)), 2):
        pair = undeclared[i:i + 2]
        if len(pair) == 2:
            linkage[pair[0]] = linkage[pair[1]] = f"SMALLWALLET{i}"
    return linkage


def revenue_for(computors):
    # per-slot revenue roughly equal with mild noise (Qubic pays per solution/rank)
    return {c: int(1_400_000_000 * random.uniform(0.85, 1.0)) for c in computors}


def main():
    computors = [fake_id(i) for i in range(N_COMPUTORS)]
    client = SampleClient(computors)

    epochs = list(range(220, 229))  # 9 epochs
    revenue_by_epoch = {e: revenue_for(computors) for e in epochs}
    registries = {e: build_registry(computors, epoch_shift=e - 220) for e in epochs}
    # the payout graph is the primary attribution layer, so the sample has one too
    # the undeclared cluster grows over the epochs, so the timeline shows a real
    # trend rather than a flat line: concentration creeping up is the story.
    linkages = {e: hidden_linkage(computors, registries[e], size=25 + (e - 220) * 7)
                for e in epochs}

    def report_for(e, status="sealed"):
        return build_epoch_report(client, e, revenue=revenue_by_epoch[e],
                                  registry=registries[e], linkage=linkages[e], status=status)

    # latest full report — the running epoch, so it is live
    latest = 228
    latest_report = report_for(latest, status="live")
    latest_report["_sample"] = True

    # timeseries (per-epoch registry differs, so build manually)
    points = []
    reports = {e: report_for(e, status="live" if e == latest else "sealed") for e in epochs}
    for e in epochs:
        rep = reports[e]
        cov = rep["linkage_coverage"]
        points.append({
            "epoch": e,
            "status": rep["status"],
            "operators": rep["totals"]["operators"],
            "declared_operators": rep["totals"]["declared_operators"],
            "unattributed_slots": rep["totals"]["unattributed_slots"],
            "onchain_linked_share": cov["onchain_linked_share"],
            "attributed_share": cov["attributed_share"],
            "gini_revenue": rep["concentration_by_revenue"]["gini"],
            "hhi_revenue": rep["concentration_by_revenue"]["hhi_normalized"],
            "nakamoto_one_third_revenue": rep["concentration_by_revenue"]["nakamoto_one_third"],
            "nakamoto_half_revenue": rep["concentration_by_revenue"]["nakamoto_half"],
            "nakamoto_one_third_slots": rep["concentration_by_slots"]["nakamoto_one_third"],
            "top1_share": rep["concentration_by_revenue"]["top1_share"],
        })
    timeseries = {"_sample": True, "series": points}

    # per-epoch cluster snapshots (for the animated bubble view). Keep it compact:
    # named clusters in full, the unattributed tail folded into one summary bubble.
    epoch_clusters = []
    for e in epochs:
        rep = reports[e]
        named = [c for c in rep["clusters"] if c["confidence"] != "unattributed"]
        tail = [c for c in rep["clusters"] if c["confidence"] == "unattributed"]
        tail_slots = sum(c["computor_count"] for c in tail)
        tail_rev = sum(c["revenue"] for c in tail)
        total_rev = rep["totals"]["total_revenue"]
        bubbles = [
            {
                "id": c["cluster_id"], "label": c["label"], "confidence": c["confidence"],
                "slots": c["computor_count"], "revenue_share": c["revenue_share"],
            }
            for c in named
        ]
        if tail_slots:
            bubbles.append({
                "id": "unattributed", "label": "Unattributed", "confidence": "unattributed",
                "slots": tail_slots,
                "revenue_share": round(tail_rev / total_rev, 6) if total_rev else 0.0,
            })
        epoch_clusters.append({
            "epoch": e,
            "status": rep["status"],
            "nakamoto_one_third": rep["concentration_by_revenue"]["nakamoto_one_third"],
            "gini": rep["concentration_by_revenue"]["gini"],
            "bubbles": bubbles,
        })

    out = ROOT / "api" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report_latest.json").write_text(json.dumps(latest_report, indent=2), encoding="utf-8")
    (out / "timeseries.json").write_text(json.dumps(timeseries, indent=2), encoding="utf-8")
    (out / "epoch_clusters.json").write_text(json.dumps({"_sample": True, "epochs": epoch_clusters}, indent=2), encoding="utf-8")

    # dashboard bootstrap: a JS file the SPA can load over file:// (fetch is blocked there)
    dash = ROOT / "dashboard"
    dash.mkdir(parents=True, exist_ok=True)
    bootstrap = "window.QDR_DATA = " + json.dumps({
        "report": latest_report,
        "timeseries": timeseries,
        "epoch_clusters": {"epochs": epoch_clusters},
        "sample": True,
    }) + ";\n"
    (dash / "data.js").write_text(bootstrap, encoding="utf-8")

    print("epoch", latest, "operators:", latest_report["totals"]["operators"],
          "declared:", latest_report["totals"]["declared_operators"],
          "unattributed slots:", latest_report["totals"]["unattributed_slots"])
    print("nakamoto(1/3) by revenue:", latest_report["concentration_by_revenue"]["nakamoto_one_third"])
    print("top1 share:", latest_report["concentration_by_revenue"]["top1_share"])
    print("wrote sample JSON + dashboard/data.js")


if __name__ == "__main__":
    main()
