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

from qdr.report import build_epoch_report, build_timeseries

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
    # the rest stay unattributed (independent operators)
    return {"pools": pools}


def revenue_for(computors):
    # per-slot revenue roughly equal with mild noise (Qubic pays per solution/rank)
    return {c: int(1_400_000_000 * random.uniform(0.85, 1.0)) for c in computors}


def main():
    computors = [fake_id(i) for i in range(N_COMPUTORS)]
    client = SampleClient(computors)

    epochs = list(range(220, 229))  # 9 epochs
    revenue_by_epoch = {e: revenue_for(computors) for e in epochs}
    registries = {e: build_registry(computors, epoch_shift=e - 220) for e in epochs}

    # latest full report
    latest = 228
    latest_report = build_epoch_report(
        client, latest, revenue=revenue_by_epoch[latest], registry=registries[latest]
    )
    latest_report["_sample"] = True

    # timeseries (per-epoch registry differs, so build manually)
    points = []
    for e in epochs:
        rep = build_epoch_report(client, e, revenue=revenue_by_epoch[e], registry=registries[e])
        points.append({
            "epoch": e,
            "operators": rep["totals"]["operators"],
            "declared_operators": rep["totals"]["declared_operators"],
            "unattributed_slots": rep["totals"]["unattributed_slots"],
            "gini_revenue": rep["concentration_by_revenue"]["gini"],
            "hhi_revenue": rep["concentration_by_revenue"]["hhi_normalized"],
            "nakamoto_one_third_revenue": rep["concentration_by_revenue"]["nakamoto_one_third"],
            "nakamoto_half_revenue": rep["concentration_by_revenue"]["nakamoto_half"],
            "top1_share": rep["concentration_by_revenue"]["top1_share"],
        })
    timeseries = {"_sample": True, "series": points}

    # per-epoch cluster snapshots (for the animated bubble view). Keep it compact:
    # named clusters in full, the unattributed tail folded into one summary bubble.
    epoch_clusters = []
    for e in epochs:
        rep = build_epoch_report(client, e, revenue=revenue_by_epoch[e], registry=registries[e])
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
            "nakamoto_one_third": rep["concentration_by_revenue"]["nakamoto_one_third"],
            "gini": rep["concentration_by_revenue"]["gini"],
            "bubbles": bubbles,
        })

    out = ROOT / "api" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report_latest.json").write_text(json.dumps(latest_report, indent=2))
    (out / "timeseries.json").write_text(json.dumps(timeseries, indent=2))
    (out / "epoch_clusters.json").write_text(json.dumps({"_sample": True, "epochs": epoch_clusters}, indent=2))

    # dashboard bootstrap: a JS file the SPA can load over file:// (fetch is blocked there)
    dash = ROOT / "dashboard"
    dash.mkdir(parents=True, exist_ok=True)
    bootstrap = "window.QDR_DATA = " + json.dumps({
        "report": latest_report,
        "timeseries": timeseries,
        "epoch_clusters": {"epochs": epoch_clusters},
        "sample": True,
    }) + ";\n"
    (dash / "data.js").write_text(bootstrap)

    print("epoch", latest, "operators:", latest_report["totals"]["operators"],
          "declared:", latest_report["totals"]["declared_operators"],
          "unattributed slots:", latest_report["totals"]["unattributed_slots"])
    print("nakamoto(1/3) by revenue:", latest_report["concentration_by_revenue"]["nakamoto_one_third"])
    print("top1 share:", latest_report["concentration_by_revenue"]["top1_share"])
    print("wrote sample JSON + dashboard/data.js")


if __name__ == "__main__":
    main()
