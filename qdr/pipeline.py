"""The ingest pipeline: derive -> cluster -> persist.

This is where the sealed-vs-live rule from CONCEPT §5.1 actually lives, mirroring
the UpdateLive()/FinalizeEpoch() split that qubic_doge_stats uses:

    update_live(epoch)    the current epoch, recomputed on every poll
    finalize(epoch)       a closed epoch, computed once and then sealed

Reads go through `get_report()`, which serves a sealed epoch straight from the
store and only recomputes what is genuinely live. That is what makes the API fast,
independent of RPC uptime, and able to serve *any* historical epoch rather than
whichever snapshot was built last.
"""
from __future__ import annotations

import time
from typing import Optional

from . import __version__
from .client import CachedClient, QubicRPCError
from .clustering import (
    Cluster,
    cluster_epoch,
    declared_vs_detected,
    linkage_coverage,
    load_registry,
)
from .metrics import summarize
from .revenue import (
    RevenueResult,
    derive_epoch_revenue,
    payout_linkage,
    revenue_from_balance_deltas,
    snapshot_balances,
    _first_tick,
    _last_tick,
)
from .store import STATUS_LIVE, STATUS_PARTIAL, STATUS_SEALED, Store


def _assemble(
    epoch: int,
    computors: list[str],
    clusters: list[Cluster],
    rev: RevenueResult,
    status: str,
) -> dict:
    """Build the report payload. Shape stays compatible with the v0.1 API, plus
    the new provenance/coverage/delta blocks the feedback asked for."""
    total_rev = rev.total
    declared_like = [c for c in clusters if c.confidence.startswith("declared")]
    coverage = linkage_coverage(computors, clusters)

    return {
        "epoch": epoch,
        "status": status,
        "generated_at": int(time.time()),
        "code_version": __version__,
        "reproducible": True,
        "data_sources": {
            "computors": f"/v1/epochs/{epoch}/computors",
            "revenue": rev.provenance()["method"],
            "self_reporting": "data/self_reporting/pools.json",
            "onchain_linkage": "payout graph (primary attribution layer)",
        },
        "revenue_provenance": rev.provenance(),
        "totals": {
            "computors": len(computors),
            "operators": len(clusters),
            "declared_operators": len(declared_like),
            "unattributed_slots": coverage["unattributed_slots"],
            "total_revenue": total_rev,
        },
        "linkage_coverage": coverage,
        "declared_vs_detected": declared_vs_detected(clusters),
        "concentration_by_revenue": summarize([c.revenue for c in clusters]).to_dict(),
        "concentration_by_slots": summarize([c.computor_count for c in clusters]).to_dict(),
        "clusters": [c.to_dict(total_rev) for c in clusters],
        "warnings": rev.warnings,
    }


def compute_epoch(
    client: CachedClient,
    store: Store,
    epoch: int,
    registry: Optional[dict] = None,
) -> dict:
    """Derive, cluster and persist one epoch. Returns the report payload.

    Revenue completeness decides the status: an epoch whose payout pull could not
    be proven exhaustive stays `partial` and is kept out of the headline metrics
    rather than published as though it were solid.
    """
    registry = registry if registry is not None else load_registry()
    computors = client.computors(epoch)

    # Revenue: balance deltas are the method that actually works against the public
    # RPC (DATA_SOURCES §6.2 — payouts are protocol emission, not exposed transfers).
    # Payout tracking stays as a fallback so the code path is ready if a feed appears.
    first_tick = _first_tick(client, epoch)
    last_tick = _last_tick(client, epoch + 1) or _last_tick(client, epoch)
    rev = revenue_from_balance_deltas(store, epoch, computors, first_tick, last_tick)
    if not rev.complete and not store.snapshot_ticks(epoch):
        # no snapshots at all for this epoch: try the payout route before giving up
        alt = derive_epoch_revenue(client, epoch, computors=computors)
        if alt.total > 0:
            rev = alt
    store.put_revenue(epoch, rev.revenue, rev.first_tick, rev.last_tick, rev.complete)
    if rev.transfers:
        store.put_transfers(epoch, rev.transfers)

    # on-chain linkage is the default layer: accumulate the payout graph, then
    # cluster against everything proven up to and including this epoch.
    store.put_linkage(epoch, payout_linkage(rev), evidence="arbitrator payout destination")
    linkage = store.get_linkage(epoch)

    clusters = cluster_epoch(computors, rev.revenue, registry, linkage)
    store.put_clusters(epoch, clusters)

    try:
        current = client.current_epoch()
    except QubicRPCError:
        current = epoch
    if epoch >= current:
        status = STATUS_LIVE
    else:
        status = STATUS_SEALED if rev.complete else STATUS_PARTIAL

    report = _assemble(epoch, computors, clusters, rev, status)
    store.upsert_epoch(epoch, status, rev.first_tick, rev.last_tick, len(computors))
    store.put_report(epoch, report, status)
    if status == STATUS_SEALED:
        store.seal_epoch(epoch, note="revenue derivation complete")
    return report


def snapshot_epoch_balances(client: CachedClient, store: Store,
                            epoch: Optional[int] = None) -> dict:
    """Snapshot every computor's balance counters into the store.

    This is the input the balance-delta revenue method needs. Run it regularly
    (and especially close to an epoch boundary): revenue for epoch N is the
    difference between a snapshot before N starts and one after N ends, so a
    boundary that was never observed can never be derived later.
    """
    epoch = epoch if epoch is not None else client.current_epoch()
    computors = client.computors(epoch)
    rows = snapshot_balances(client, computors)
    written = store.put_balance_snapshots(epoch, rows)
    ticks = sorted({r["tick"] for r in rows})
    return {"epoch": epoch, "identities": len(computors), "snapshots": written,
            "tick_range": [ticks[0], ticks[-1]] if ticks else [None, None]}


def update_live(client: CachedClient, store: Store, registry: Optional[dict] = None) -> dict:
    """Recompute the currently running epoch. Safe to call on every poll."""
    return compute_epoch(client, store, client.current_epoch(), registry)


def finalize(
    client: CachedClient,
    store: Store,
    epoch: int,
    registry: Optional[dict] = None,
    force: bool = False,
) -> Optional[dict]:
    """Compute a closed epoch once and seal it.

    Returns None when the epoch is already sealed — recomputing settled history
    only risks drift. `force=True` is the deliberate backfill path (new code
    version); the prior computation is kept beside the new one either way.
    """
    if store.is_sealed(epoch) and not force:
        return None
    return compute_epoch(client, store, epoch, registry)


def get_report(
    store: Store,
    epoch: Optional[int] = None,
    client: Optional[CachedClient] = None,
    registry: Optional[dict] = None,
    max_live_age_s: int = 300,
) -> Optional[dict]:
    """Serve an epoch's report: from the store when sealed, recomputed when live.

    A sealed epoch is immutable, so it never touches the network. The live epoch
    is refreshed when the stored copy is older than `max_live_age_s`, so the
    running epoch is always current without hammering the RPC per request.
    """
    if epoch is None:
        if client is not None:
            try:
                epoch = client.current_epoch()
            except QubicRPCError:
                epoch = None
        if epoch is None:
            rep = store.latest_report()
            return rep

    stored = store.get_report(epoch)
    if stored and stored.get("status") == STATUS_SEALED:
        return stored

    if client is not None:
        stale = (not stored) or (int(time.time()) - int(stored.get("computed_at", 0)) > max_live_age_s)
        if stale:
            try:
                return compute_epoch(client, store, epoch, registry)
            except (QubicRPCError, KeyError, ValueError):
                pass  # fall through to whatever we have stored
    return stored


def backfill(
    client: CachedClient,
    store: Store,
    epochs: list[int],
    registry: Optional[dict] = None,
    force: bool = False,
) -> dict:
    """Fill history, or re-derive it under a new code version.

    Sealed epochs are skipped unless `force`, and a forced recompute lands beside
    the prior computation (keyed by code_version) rather than erasing it — so a
    number that changes always has a recorded reason.
    """
    registry = registry if registry is not None else load_registry()
    done, skipped, failed = [], [], {}
    for e in epochs:
        if store.is_sealed(e) and not force:
            skipped.append(e)
            continue
        try:
            rep = compute_epoch(client, store, e, registry)
            done.append({"epoch": e, "status": rep["status"],
                         "operators": rep["totals"]["operators"]})
        except (QubicRPCError, KeyError, ValueError) as err:
            failed[e] = str(err)
    return {"computed": done, "skipped_sealed": skipped, "failed": failed,
            "code_version": __version__}


# History only grows, so the default views take a bounded window rather than the
# whole store. Callers that genuinely want everything pass limit=None.
DEFAULT_TIMESERIES_EPOCHS = 52   # ~a year of weekly epochs
DEFAULT_BUNDLE_EPOCHS = 26


def build_timeseries(store: Store, epochs: Optional[list[int]] = None,
                     include_partial: bool = False,
                     limit: Optional[int] = DEFAULT_TIMESERIES_EPOCHS) -> dict:
    """Concentration indices across epochs, read from the store.

    Partial epochs are excluded by default — an under-counted epoch would show as
    a bogus decentralization spike in the chart. `limit` keeps the response bounded
    as history accumulates (most recent epochs win); pass None for the full range.
    """
    epochs = epochs if epochs is not None else store.report_epochs()
    if limit is not None and len(epochs) > limit:
        epochs = sorted(epochs)[-limit:]
    series = []
    for e in sorted(epochs):
        rep = store.get_report(e)
        if not rep:
            continue
        if rep.get("status") == STATUS_PARTIAL and not include_partial:
            continue
        cbr = rep["concentration_by_revenue"]
        cov = rep.get("linkage_coverage", {})
        series.append({
            "epoch": e,
            "status": rep.get("status"),
            "operators": rep["totals"]["operators"],
            "declared_operators": rep["totals"]["declared_operators"],
            "unattributed_slots": rep["totals"]["unattributed_slots"],
            "onchain_linked_share": cov.get("onchain_linked_share", 0.0),
            "attributed_share": cov.get("attributed_share", 0.0),
            "gini_revenue": cbr["gini"],
            "hhi_revenue": cbr["hhi_normalized"],
            "nakamoto_one_third_revenue": cbr["nakamoto_one_third"],
            "nakamoto_half_revenue": cbr["nakamoto_half"],
            "nakamoto_one_third_slots": rep["concentration_by_slots"]["nakamoto_one_third"],
            "top1_share": cbr["top1_share"],
        })
    return {"generated_at": int(time.time()), "code_version": __version__, "series": series}


def _bubbles(rep: dict) -> list[dict]:
    """Per-operator bubbles for the animated map: named clusters in full, the
    unattributed tail folded into one summary bubble."""
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


def build_dashboard_bundle(store: Store, epochs: Optional[list[int]] = None,
                           limit: Optional[int] = DEFAULT_BUNDLE_EPOCHS) -> dict:
    """Everything the dashboard needs, assembled from the store.

    Bounded by default: the animated map carries per-operator bubbles per epoch,
    so an unbounded bundle would grow without limit as epochs accumulate.
    """
    epochs = epochs if epochs is not None else store.report_epochs()
    if limit is not None and len(epochs) > limit:
        epochs = sorted(epochs)[-limit:]
    ts = build_timeseries(store, epochs, limit=None)
    epoch_clusters = []
    for e in sorted(epochs):
        rep = store.get_report(e)
        if not rep:
            continue
        cbr = rep["concentration_by_revenue"]
        epoch_clusters.append({
            "epoch": e, "status": rep.get("status"),
            "nakamoto_one_third": cbr["nakamoto_one_third"],
            "gini": cbr["gini"], "bubbles": _bubbles(rep),
        })
    latest = store.latest_report()
    return {
        "report": latest,
        "timeseries": {"series": ts["series"]},
        "epoch_clusters": {"epochs": epoch_clusters},
    }
