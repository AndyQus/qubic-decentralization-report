"""Assemble the decentralization report.

Combines ingestion (client), clustering, and metrics into the JSON shape the API
serves and the dashboard renders. Every report is reproducible: it names its
inputs and the code version.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Optional

from . import __version__
from .client import CachedClient, derive_computor_revenue
from .clustering import cluster_epoch, load_registry, Cluster
from .metrics import summarize


def build_epoch_report(
    client: CachedClient,
    epoch: int,
    revenue: Optional[dict[str, int]] = None,
    registry: Optional[dict] = None,
) -> dict:
    """Build the full report for a single epoch.

    revenue/registry can be injected (tests, alternative sources); otherwise they
    are pulled/derived and loaded from the repo registry.
    """
    computors = client.computors(epoch)
    if revenue is None:
        revenue = derive_computor_revenue(client, epoch)
    if registry is None:
        registry = load_registry()

    clusters: list[Cluster] = cluster_epoch(computors, revenue, registry)
    total_rev = sum(revenue.values())

    # concentration is measured across *operators* (clusters), not raw slots —
    # that is the whole point: 676 slots can be far fewer operators.
    cluster_weights_rev = [c.revenue for c in clusters]
    cluster_weights_slots = [c.computor_count for c in clusters]

    declared = [c for c in clusters if c.confidence == "declared"]
    unattributed = [c for c in clusters if c.confidence == "unattributed"]

    return {
        "epoch": epoch,
        "generated_at": int(time.time()),
        "code_version": __version__,
        "reproducible": True,
        "data_sources": {
            "computors": f"/v1/epochs/{epoch}/computors",
            "revenue": "derived from arbitrator epoch-boundary transfers",
            "self_reporting": "data/self_reporting/pools.json",
        },
        "totals": {
            "computors": len(computors),
            "operators": len(clusters),
            "declared_operators": len(declared),
            "unattributed_slots": sum(c.computor_count for c in unattributed),
            "total_revenue": total_rev,
        },
        "concentration_by_revenue": summarize(cluster_weights_rev).to_dict(),
        "concentration_by_slots": summarize(cluster_weights_slots).to_dict(),
        "clusters": [c.to_dict(total_rev) for c in clusters],
    }


def _bubbles_from_report(rep: dict) -> list[dict]:
    """Compact per-operator bubbles for the dashboard: named clusters in full, the
    unattributed tail folded into a single summary bubble."""
    named = [c for c in rep["clusters"] if c["confidence"] != "unattributed"]
    tail = [c for c in rep["clusters"] if c["confidence"] == "unattributed"]
    total_rev = rep["totals"]["total_revenue"] or 1
    bubbles = [
        {"id": c["cluster_id"], "label": c["label"], "confidence": c["confidence"],
         "slots": c["computor_count"], "revenue_share": c["revenue_share"]}
        for c in named
    ]
    tail_slots = sum(c["computor_count"] for c in tail)
    if tail_slots:
        tail_rev = sum(c["revenue"] for c in tail)
        bubbles.append({"id": "unattributed", "label": "Unattributed",
                        "confidence": "unattributed", "slots": tail_slots,
                        "revenue_share": round(tail_rev / total_rev, 6)})
    return bubbles


def build_dashboard_bundle(
    client: CachedClient,
    epochs: list[int],
    registry: Optional[dict] = None,
    revenue_by_epoch: Optional[dict[int, dict[str, int]]] = None,
) -> dict:
    """Everything the dashboard needs in one payload: latest report, the
    concentration timeseries, and per-epoch operator bubbles for the animated map.
    The API serves this and the static SPA can fetch it directly.
    """
    if registry is None:
        registry = load_registry()
    reports = {}
    for e in epochs:
        rev = (revenue_by_epoch or {}).get(e)
        reports[e] = build_epoch_report(client, e, revenue=rev, registry=registry)

    latest = max(epochs)
    series, epoch_clusters = [], []
    for e in epochs:
        rep = reports[e]
        cbr = rep["concentration_by_revenue"]
        series.append({
            "epoch": e,
            "operators": rep["totals"]["operators"],
            "declared_operators": rep["totals"]["declared_operators"],
            "unattributed_slots": rep["totals"]["unattributed_slots"],
            "gini_revenue": cbr["gini"], "hhi_revenue": cbr["hhi_normalized"],
            "nakamoto_one_third_revenue": cbr["nakamoto_one_third"],
            "nakamoto_half_revenue": cbr["nakamoto_half"], "top1_share": cbr["top1_share"],
        })
        epoch_clusters.append({
            "epoch": e, "nakamoto_one_third": cbr["nakamoto_one_third"],
            "gini": cbr["gini"], "bubbles": _bubbles_from_report(rep),
        })
    return {
        "report": reports[latest],
        "timeseries": {"series": series},
        "epoch_clusters": {"epochs": epoch_clusters},
    }


def build_timeseries(
    client: CachedClient,
    epochs: list[int],
    registry: Optional[dict] = None,
    revenue_by_epoch: Optional[dict[int, dict[str, int]]] = None,
) -> dict:
    """Build the concentration time series over a list of epochs (feeds charts)."""
    points = []
    for e in epochs:
        rev = (revenue_by_epoch or {}).get(e)
        rep = build_epoch_report(client, e, revenue=rev, registry=registry)
        points.append({
            "epoch": e,
            "operators": rep["totals"]["operators"],
            "declared_operators": rep["totals"]["declared_operators"],
            "gini_revenue": rep["concentration_by_revenue"]["gini"],
            "hhi_revenue": rep["concentration_by_revenue"]["hhi_normalized"],
            "nakamoto_one_third_revenue": rep["concentration_by_revenue"]["nakamoto_one_third"],
            "nakamoto_half_revenue": rep["concentration_by_revenue"]["nakamoto_half"],
            "nakamoto_one_third_slots": rep["concentration_by_slots"]["nakamoto_one_third"],
        })
    return {"generated_at": int(time.time()), "code_version": __version__, "series": points}
