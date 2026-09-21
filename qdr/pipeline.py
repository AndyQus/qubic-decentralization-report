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

import os
import time
from typing import Optional

from . import __version__, burn, dating
from .client import CachedClient, QubicRPCError
from .clustering import (
    Cluster,
    cluster_epoch,
    declared_vs_detected,
    linkage_coverage,
    load_registry,
)
from .metrics import summarize
from .bob import BobClient, BobError, scan_tick_transfers
from .revenue import (
    RevenueResult,
    forward_linkage,
    payout_linkage,
    revenue_from_balance_deltas,
    revenue_from_bob,
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
    bob: Optional[BobClient] = None,
) -> dict:
    """Derive, cluster and persist one epoch. Returns the report payload.

    Revenue completeness decides the status: an epoch whose payout pull could not
    be proven exhaustive stays `partial` and is kept out of the headline metrics
    rather than published as though it were solid.
    """
    registry = registry if registry is not None else load_registry()
    computors = client.computors(epoch)

    # Revenue, in order of preference (DATA_SOURCES §6):
    #   1. a Bob node's end-epoch log — the only public source that carries the
    #      protocol's computor payouts, and it has history;
    #   2. balance deltas from our own snapshots — works without Bob, but only
    #      forward from the first boundary we observed ourselves.
    first_tick = _first_tick(client, epoch)
    last_tick = _last_tick(client, epoch + 1) or _last_tick(client, epoch)

    try:
        current = client.current_epoch()
    except QubicRPCError:
        current = epoch
    is_running = epoch >= current

    rev = None
    if bob is not None:
        rev = revenue_from_bob(epoch, computors, bob, first_tick, last_tick)
    if rev is None or not rev.complete:
        fallback = revenue_from_balance_deltas(store, epoch, computors, first_tick, last_tick)
        # keep whichever actually resolved the epoch; prefer Bob on a tie since
        # it identifies the settlement transfers, not just the net change
        if rev is None or (fallback.complete and not rev.complete):
            rev = fallback
    if is_running and rev.complete:
        # A running epoch's payouts are still accruing. "Every computor appears in
        # the log" becomes true long before the epoch settles, so the epoch itself
        # — not the log — has the final say on completeness.
        rev.complete = False
        rev.warnings.append(
            f"epoch {epoch} is still running; revenue is partial until it closes")
    store.put_revenue(epoch, rev.revenue, rev.first_tick, rev.last_tick, rev.complete)
    if rev.transfers:
        store.put_transfers(epoch, rev.transfers)

    # On-chain linkage is the default attribution layer. The epoch-payout leg
    # itself proves nothing (every computor is credited by the same null address —
    # it is protocol emission), so the signal is where a computor forwards its
    # revenue ON to. That needs the tick log right after the payout settles.
    linkage_found: dict[str, str] = {}
    if bob is not None and rev.last_tick:
        try:
            moves = scan_tick_transfers(bob, int(rev.last_tick), int(rev.last_tick) + 20_000)
            linkage_found = forward_linkage(moves, computors)
        except (BobError, ValueError, TypeError):
            linkage_found = {}
    if linkage_found:
        store.put_linkage(epoch, linkage_found,
                          evidence="shared forward-payout destination")
    linkage = store.get_linkage(epoch)

    clusters = cluster_epoch(computors, rev.revenue, registry, linkage)
    store.put_clusters(epoch, clusters)

    if is_running:
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


def update_live(client: CachedClient, store: Store, registry: Optional[dict] = None,
                bob: Optional[BobClient] = None) -> dict:
    """Recompute the currently running epoch. Safe to call on every poll."""
    return compute_epoch(client, store, client.current_epoch(), registry, bob)


def finalize(
    client: CachedClient,
    store: Store,
    epoch: int,
    registry: Optional[dict] = None,
    force: bool = False,
    bob: Optional[BobClient] = None,
) -> Optional[dict]:
    """Compute a closed epoch once and seal it.

    Returns None when the epoch is already sealed — recomputing settled history
    only risks drift. `force=True` is the deliberate backfill path (new code
    version); the prior computation is kept beside the new one either way.
    """
    if store.is_sealed(epoch) and not force:
        return None
    return compute_epoch(client, store, epoch, registry, bob)


def get_report(
    store: Store,
    epoch: Optional[int] = None,
    client: Optional[CachedClient] = None,
    registry: Optional[dict] = None,
    max_live_age_s: int = 300,
    bob: Optional[BobClient] = None,
    allow_write: bool = True,
) -> Optional[dict]:
    """Serve an epoch's report: from the store when sealed, recomputed when live.

    A sealed epoch is immutable, so it never touches the network. The live epoch
    is refreshed when the stored copy is older than `max_live_age_s`, so the
    running epoch is always current without hammering the RPC per request.

    `allow_write=False` makes this a pure read. The API passes it: one writer
    (the ingest worker) keeps the store, everything else reads. Otherwise any
    API process — including one still running an older build, which is exactly
    what happened — recomputes and overwrites epochs behind the worker's back,
    stamping them with its own code_version and undoing corrections.
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

    if client is not None and allow_write:
        stale = (not stored) or (int(time.time()) - int(stored.get("computed_at", 0)) > max_live_age_s)
        if stale:
            try:
                return compute_epoch(client, store, epoch, registry, bob)
            except (QubicRPCError, KeyError, ValueError):
                pass  # fall through to whatever we have stored
    return stored


def backfill(
    client: CachedClient,
    store: Store,
    epochs: list[int],
    registry: Optional[dict] = None,
    force: bool = False,
    bob: Optional[BobClient] = None,
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
            rep = compute_epoch(client, store, e, registry, bob)
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
        # A running epoch is excluded outright, not just when its revenue is still
        # zero. Payouts land progressively, so a partially-paid epoch yields real
        # but meaningless figures — epoch 229 mid-flight showed 859M QU against the
        # ~178,000M it settles at, which reads as extreme concentration purely
        # because most slots have not been paid yet.
        if rep.get("status") == STATUS_LIVE:
            continue   # revenue is only final once the epoch closes
        cbr = rep["concentration_by_revenue"]
        cov = rep.get("linkage_coverage", {})
        series.append({
            "epoch": e,
            "status": rep.get("status"),
            "computors": rep["totals"].get("computors", rep["totals"]["operators"]),
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


# With an empty registry and no provable on-chain linkage, every slot is its own
# unattributed cluster — 676 of them. Listing those individually is noise: they
# are indistinguishable from each other and say nothing. Only clusters that
# actually represent an operator are listed; the rest is one honest summary row.
MAX_LISTED_CLUSTERS = 60


def _bubbles(rep: dict) -> list[dict]:
    """Per-operator bubbles for the animated map: real clusters in full, the
    unattributed tail folded into one summary bubble.

    A singleton unattributed slot is not an operator we identified — it is a slot
    we could not attribute, so it belongs in the tail with the others rather than
    as its own row.
    """
    def is_tail(c):
        return c["confidence"] == "unattributed" and c["computor_count"] <= 1
    named = [c for c in rep["clusters"] if not is_tail(c)][:MAX_LISTED_CLUSTERS]
    tail = [c for c in rep["clusters"] if is_tail(c)]
    # anything beyond the listing cap joins the tail rather than vanishing
    listed_ids = {c["cluster_id"] for c in named}
    tail = tail + [c for c in rep["clusters"]
                   if not is_tail(c) and c["cluster_id"] not in listed_ids]
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
                        "revenue_share": round(tail_rev / total_rev, 6),
                        "operators": len(tail)})
    return bubbles


# Slot-level revenue distribution. While operator attribution is empty the operator
# map has exactly one tile ("unattributed, 676 slots, 100%"), which renders as a
# blank rectangle and tells the reader nothing. The per-computor revenue behind it
# is real and varies by a factor of three, so this is what the page can honestly
# show instead: how revenue is spread across the slots themselves.
SLOT_BANDS = (
    ("top1", 0.00, 0.01), ("top10", 0.01, 0.10), ("upper", 0.10, 0.25),
    ("mid", 0.25, 0.50), ("lower", 0.50, 0.75), ("bottom", 0.75, 1.00),
)


def slot_distribution(store: Store, epoch: int) -> Optional[dict]:
    """Revenue per computor slot, as ranked bands plus a decile curve.

    Returns None when the epoch has no revenue yet (a running epoch): an empty
    distribution is not something to draw.
    """
    # Two independent guards, because they fail in different ways: the epoch
    # status catches a running epoch even if its revenue rows were written as
    # complete by an older code version, and revenue completeness catches a
    # closed epoch whose derivation did not finish.
    row = store.get_epoch(epoch)
    if row and row.get("status") == STATUS_LIVE:
        return None
    if not store.revenue_is_complete(epoch):
        return None
    rev = sorted(store.get_revenue(epoch).values(), reverse=True)
    total = sum(rev)
    if not rev or total <= 0:
        return None
    n = len(rev)
    bands = []
    cursor = 0          # bands must tile the ranking, never overlap it: an
                        # overlapping band counts a slot's revenue twice and the
                        # shares then sum past 100%
    for name, lo, hi in SLOT_BANDS:
        a = max(int(n * lo), cursor)
        b = max(int(n * hi), a + 1)
        chunk = rev[a:b]
        if not chunk:
            continue
        cursor = a + len(chunk)
        bands.append({
            "id": name, "slots": len(chunk),
            "revenue_share": round(sum(chunk) / total, 6),
            "min": chunk[-1], "max": chunk[0],
        })
    deciles = [round(sum(rev[int(n * i / 10):int(n * (i + 1) / 10)]) / total, 6)
               for i in range(10)]
    # The most striking measured fact about Qubic's payouts is how many slots are
    # paid to the qu: a flat share is worth stating outright rather than leaving
    # the reader to infer it from a nearly-uniform bar chart.
    counts: dict[int, int] = {}
    for v in rev:
        counts[v] = counts.get(v, 0) + 1
    mode_value, mode_count = max(counts.items(), key=lambda kv: kv[1])
    return {
        "epoch": epoch, "slots": n, "total_revenue": total,
        "min": rev[-1], "max": rev[0], "median": rev[n // 2],
        "spread": round(rev[0] / rev[-1], 2) if rev[-1] else None,
        "flat_value": mode_value, "flat_slots": mode_count,
        "flat_share": round(mode_count / n, 6),
        "distinct_values": len(counts),
        "bands": bands, "deciles": deciles,
    }


# -- mining ----------------------------------------------------------------
# Live mining state is not on the RPC and is not replayable: a node answers what
# the colony looks like right now. A sample not taken is gone, which is why this
# is collected on a timer rather than derived on demand.
#
# Sampled at the cadence the API already queries a node (QDR_MINING_TTL, 30s):
# a coarser interval would throw away readings the service had in hand. At that
# rate one epoch of samples is ~1 MB, and only MINING_KEEP_EPOCHS of them are
# retained, so the raw table stays around 2 MB no matter how long this runs.
MINING_SAMPLE_INTERVAL_S = int(os.environ.get("QDR_MINING_SAMPLE_INTERVAL", "30"))


def sample_mining(store: Store, peers: Optional[list[str]] = None) -> Optional[dict]:
    """Take one reading of live mining state and store it.

    Returns the stored row, or None when no node answered — an unreachable node
    is a normal, transient state here (these are other people's machines), so it
    is reported by absence rather than raised.
    """
    from qdr import antnode

    try:
        data = antnode.collect(peers=peers)
    except Exception:
        return None
    if data.get("status") != "live":
        return None

    ep = data.get("epoch") or {}
    epoch = ep.get("epoch")
    if not epoch:
        # Without an epoch the sample cannot be placed in the window that gets
        # pruned, and a mis-filed row would be deleted on the wrong boundary.
        return None

    colony = data.get("colony") or {}
    node = data.get("node") or {}
    row = {
        "at": int(data.get("fetched_at") or time.time()),
        "epoch": int(epoch),
        "tick": ep.get("tick"),
        "solution_count": colony.get("solution_count"),
        "threshold": colony.get("threshold"),
        "free_ann_slots": colony.get("free_ann_slots"),
        "peers_responding": node.get("peers_responding"),
        "node_ip": node.get("ip"),
        "latency_ms": node.get("latency_ms"),
    }
    store.put_mining_sample(**row)
    return row


def mining_growth(store: Store, epoch: int, window_s: int = 900) -> Optional[float]:
    """Solutions per minute over the last `window_s`, measured from stored samples.

    Read from the table rather than from two consecutive fetches, so the figure
    is a trend over minutes and is correct immediately after a restart instead
    of only once the process has polled twice.
    """
    rows = [r for r in store.mining_series(epoch=epoch, since=int(time.time()) - window_s)
            if r.get("solution_count") is not None]
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    elapsed_min = (last["at"] - first["at"]) / 60.0
    # The counter only ever rises within an epoch; a fall means the window spans
    # a reset, and no rate can be read across that boundary.
    if elapsed_min <= 0 or last["solution_count"] < first["solution_count"]:
        return None
    return round((last["solution_count"] - first["solution_count"]) / elapsed_min, 1)


# -- burn ------------------------------------------------------------------
# History only grows; the burn series is windowed like the timeseries is.
DEFAULT_BURN_DAYS = 90

# How far behind the chain head a live scan may be and still date its events by
# the observation time. Re-measured 2026-09-20 over three days of real ticks:
# 1.55 ticks/s, not the ~2.7 this comment used to assume — so 100,000 ticks is
# ~18 hours, not ~10. That is still inside a day, which is what the rule needs,
# but the margin is much thinner than it looked.
#
# It matters far less than it used to: a pass that falls further behind no
# longer has to discard those ticks, because `backfill_burns` can date them by
# measurement (see qdr/dating.py). The live scan still prefers observation time
# — it is free and exact near the head — and the backfill picks up the rest.
MAX_DATING_LAG_TICKS = 100_000

# Ceiling on a catch-up pass. 120 calls x 500 ticks = 60,000 ticks, about 6 hours
# of chain at the measured rate — enough to absorb a long RPC outage in one pass,
# and still bounded so the scan cannot run away with the worker or hammer a
# public node. Beyond this the dating rule takes over anyway (a pass further
# behind than MAX_DATING_LAG_TICKS skips to the head).
CATCHUP_MAX_CALLS = 120


def sample_burn_total(client: CachedClient, store: Store) -> Optional[dict]:
    """Record the official cumulative burn counter for the running epoch.

    This is the anchor, not the measurement (CONCEPT_BURN §3.2): the counter is
    an epoch aggregate and does not move between boundaries, so sampling it more
    often buys nothing but keeps the anchor current when an epoch does close.
    """
    try:
        stats = client.latest_stats()
    except QubicRPCError:
        return None
    epoch = int(stats.get("epoch") or 0)
    burned = int(stats.get("burnedQus") or 0)
    if not epoch or not burned:
        return None
    store.put_burn_total(epoch, burned,
                         circulating=int(stats.get("circulatingSupply") or 0) or None,
                         tick=int(stats.get("currentTick") or 0) or None)
    return {"epoch": epoch, "burned_total": burned}


def scan_burns(
    client: CachedClient,
    store: Store,
    bob: BobClient,
    from_tick: Optional[int] = None,
    max_calls: int = burn.MAX_CALLS,
) -> dict:
    """Advance the burn scan toward the current tick, one budgeted pass.

    Resumes from the stored pointer, so a restart continues rather than
    rescanning. A pass that hits a range Bob cannot serve stops at the gap
    instead of advancing past it: skipping unread ticks would lose those burns
    permanently, and they are not replayable.
    """
    state = store.burn_scan_state()
    pulse_epoch, tick = None, None
    try:
        info = client.tick_info()
        pulse_epoch, tick = int(info.get("epoch") or 0), int(info.get("tick") or 0)
    except QubicRPCError:
        return {"scanned": 0, "reason": "rpc unreachable"}
    if not tick:
        return {"scanned": 0, "reason": "no current tick"}

    # Scan only as far as Bob has actually INDEXED, not as far as the chain has
    # advanced. Measured 2026-09-19: Bob's currentIndexingTick trails the RPC's
    # tick by ~36. Asking for the ticks in between returns an empty log — which
    # is indistinguishable from "no burns happened" — and the pointer would then
    # advance past them, losing those burns permanently. A tick that has not been
    # indexed yet is not empty, it is not ready.
    try:
        status = bob.status()
        indexed = int(status.get("currentIndexingTick") or 0)
    except (BobError, TypeError, ValueError):
        indexed = 0
    if indexed:
        tick = min(tick, indexed)

    if from_tick is not None:
        start = int(from_tick)
    elif state:
        start = int(state["last_tick"]) + 1
    else:
        # First ever pass: start close to the head rather than at the epoch's
        # first tick. A cold start that tried to scan a whole epoch would issue
        # ~1400 heavy calls against a public node before the page showed
        # anything; the series simply begins where we began measuring, and says so.
        start = max(0, tick - burn.CHUNK_TICKS * max_calls)

    if start > tick:
        return {"scanned": 0, "reason": "up to date", "last_tick": start - 1}

    # Tick logs carry no timestamp (measured 2026-09-19), and no RPC endpoint
    # exposes a tick's wall time, so a scan dates its events by when it observed
    # them. That only holds near the chain head — which is where the worker runs,
    # counting burns minutes after they happen.
    #
    # When the pointer is further behind than a day's worth of ticks (an outage,
    # a long restart), this pass cannot date those ticks by observation. Grinding
    # through them chunk by chunk would spend every pass on ticks it must discard
    # while TODAY's burns scroll past uncounted — the scan would never catch up,
    # and the chart would stay empty. So the pointer skips to the head.
    #
    # That skipped stretch is no longer lost, only deferred: `backfill_burns`
    # dates those ticks by measurement (`/v1/ticks/{t}/tick-data`) and counts
    # them properly. The live scan's job is to keep up with the present; filling
    # the past is a separate pass with a source of truth this one does not need.
    skipped_from = None
    if max(0, tick - start) > MAX_DATING_LAG_TICKS:
        skipped_from = start
        start = max(0, tick - burn.CHUNK_TICKS * max_calls)

    # Size the budget to the actual backlog rather than a fixed number of calls.
    #
    # The watch loop does not run at its nominal interval: the balance snapshot
    # ahead of it makes 676 sequential RPC calls at ~1.75 s each (measured), so a
    # pass comes round roughly every 20 minutes whatever --interval says. At
    # ~2.7 ticks/s that is ~3,250 ticks of drift per pass, against a fixed budget
    # covering 10,000 — it keeps up, but the margin shrinks to almost nothing if
    # the RPC has a slow day and the gap stretches to an hour.
    #
    # Rather than depend on that arithmetic staying true, let a pass that is
    # behind buy the calls it needs to catch up, with a ceiling so it still
    # cannot monopolise the worker or the public node.
    behind = max(0, tick - start + 1)
    needed = -(-behind // burn.CHUNK_TICKS)           # ceil
    budget = max(max_calls, min(CATCHUP_MAX_CALLS, needed))
    out = burn.scan_range(bob, start, tick, epoch=pulse_epoch or 0,
                          max_calls=budget,
                          default_day=burn.observed_day())
    written = 0
    for day, agg in out["days"].items():
        store.put_burn_bucket(
            from_tick=agg["from_tick"] or start,
            to_tick=agg["to_tick"] or out["scanned_to"],
            epoch=pulse_epoch or 0, day=day,
            burned=agg["burned"], burn_events=agg["burn_events"],
            contract_burned=agg["contract_burned"],
            contract_events=agg["contract_events"],
            by_contract=agg["by_contract"],
        )
        written += 1
    if out["scanned_to"] >= start:
        store.set_burn_scan_state(last_tick=out["scanned_to"], first_tick=start)
    result = {"scanned": max(0, out["scanned_to"] - start + 1),
              "from_tick": start, "to_tick": out["scanned_to"],
              "days_written": written, "calls": out["calls"],
              "gaps": out["gaps"], "complete": out["complete"],
              "behind": max(0, tick - out["scanned_to"])}
    if skipped_from is not None:
        # Surfaced, not swallowed: this is a hole in the measured series. It is
        # now a fillable one — `--burn-backfill --burn-from <lo> --burn-to <hi>`
        # counts exactly this range with measured dates — but until that runs,
        # the page says the days are incomplete rather than implying continuity.
        result["skipped_undatable"] = [skipped_from, start - 1]
    return result


def backfill_burns(
    client: CachedClient,
    store: Store,
    bob: BobClient,
    from_tick: Optional[int] = None,
    to_tick: Optional[int] = None,
    max_calls: int = 400,
    progress=None,
) -> dict:
    """Count burns in PAST ticks, dating them by measurement rather than by now.

    `scan_burns` can only date what it watches happen: tick logs carry no
    timestamp, so it stamps events with the day it observed them, and that is
    only true near the chain head. Everything older it skipped — which is why
    the burn series started the day the worker did.

    This closes that hole. `/v1/ticks/{t}/tick-data` gives a tick's real wall
    time, so `dating.day_boundaries` can resolve the exact tick range of each
    UTC day (~20 lookups per boundary by bisection, not one per tick), and a
    scan of those ranges files every burn under the day it actually happened.
    No tick-rate assumption is involved anywhere — which matters, because the
    rate the old code reasoned from (~2.7/s) is nearly double the measured one.

    Each day is scanned and stored separately, so an interrupted backfill leaves
    correct days behind rather than a half-counted smear across the boundary. A
    range Bob cannot serve ends that day's pass and is reported in `gaps`
    instead of being silently treated as zero burns.

    Returns {days, scanned, calls, gaps, boundaries}. Bounded by `max_calls`
    across the whole run so it cannot monopolise a public node; call it again to
    continue.
    """
    head = None
    try:
        info = client.tick_info()
        head = int(info.get("tick") or 0)
        epoch = int(info.get("epoch") or 0)
    except QubicRPCError:
        return {"scanned": 0, "reason": "rpc unreachable"}

    # Never scan past what Bob has INDEXED — an unindexed tick answers empty,
    # which is indistinguishable from "no burns" (the same trap scan_burns
    # documents).
    try:
        status = bob.status()
        indexed = int(status.get("currentIndexingTick") or 0)
        initial = int(status.get("initialTick") or 0)
    except (BobError, TypeError, ValueError):
        indexed, initial = 0, 0

    hi = int(to_tick) if to_tick is not None else (indexed or head)
    if indexed:
        hi = min(hi, indexed)
    # Bob's own history floor: asking below it is answered with an error, not data.
    lo = int(from_tick) if from_tick is not None else max(initial, hi - 500_000)
    if initial:
        lo = max(lo, initial)
    if lo > hi:
        return {"scanned": 0, "reason": "nothing to backfill", "from_tick": lo}

    ranges = dating.day_boundaries(client.tick_timestamp, lo, hi)
    if not ranges:
        # No tick in the range carries a timestamp. Saying so beats inventing a
        # date for burns we cannot place.
        return {"scanned": 0, "reason": "range carries no dated tick",
                "from_tick": lo, "to_tick": hi}

    # Record each day's measured span before scanning. This is what lets
    # coverage be a measurement over a measurement instead of a division by an
    # assumed tick rate. A day is `complete` when a later range exists, i.e. its
    # end was found by measurement rather than by running out of chain.
    today = burn.observed_day()
    for i, r in enumerate(ranges):
        bounded = (i < len(ranges) - 1) and r["day"] != today
        store.put_day_ticks(r["day"], r["from_tick"], r["to_tick"],
                            complete=bounded)

    days_written, calls, gaps, scanned = 0, 0, [], 0
    # How far the count actually reached. `gaps` alone cannot answer that: a run
    # can stop with none of them, by exhausting its call budget or by breaking
    # out of a day whose scan was incomplete. A caller deciding whether the
    # window is finished needs the tick, not the absence of an error.
    reached = lo - 1
    for r in ranges:
        if calls >= max_calls:
            break
        budget = max_calls - calls
        out = burn.scan_range(
            bob, r["from_tick"], r["to_tick"], epoch=epoch,
            max_calls=budget,
            # The day is MEASURED for this whole tick range, so it is the right
            # default for the undated events inside it — unlike the live scan,
            # which can only use "today".
            default_day=r["day"],
        )
        calls += out["calls"]
        gaps.extend(out["gaps"])
        scanned += max(0, out["scanned_to"] - r["from_tick"] + 1)
        # Store the window that was SCANNED, not the span the events happened to
        # fall in. Two reasons, both load-bearing:
        #
        #  * `put_burn_bucket` supersedes stored buckets *contained* in the new
        #    window. An event-span window (say the one tick that held a burn)
        #    contains nothing, so the live scan's narrow buckets would survive
        #    beside the backfill's and the day would be counted twice.
        #  * the tick window is what makes a figure recomputable by a third
        #    party (store.py's own rule). "We scanned this range and found this"
        #    is checkable; "we found this somewhere" is not.
        #
        # A day whose scan found NO burns still gets a bucket, for the same
        # reason: a zero we measured and a day we never scanned are different
        # claims, and only the bucket's tick window tells them apart.
        covered_to = min(out["scanned_to"], r["to_tick"])
        reached = max(reached, covered_to)
        found = out["days"].get(r["day"])
        agg = found or {"burned": 0, "burn_events": 0, "contract_burned": 0,
                        "contract_events": 0, "by_contract": {}}
        if covered_to >= r["from_tick"]:
            store.put_burn_bucket(
                from_tick=r["from_tick"], to_tick=covered_to,
                epoch=epoch, day=r["day"],
                burned=agg["burned"], burn_events=agg["burn_events"],
                contract_burned=agg["contract_burned"],
                contract_events=agg["contract_events"],
                by_contract=agg["by_contract"],
            )
            days_written += 1
        # Events dated to a DIFFERENT day than the range they were scanned in
        # can only come from an entry carrying its own timestamp (end-epoch
        # logs do). Those are stored on their own event span: the scanned window
        # belongs to the range's day, not to theirs.
        for day, other in out["days"].items():
            if day == r["day"]:
                continue
            store.put_burn_bucket(
                from_tick=other["from_tick"] or r["from_tick"],
                to_tick=other["to_tick"] or covered_to,
                epoch=epoch, day=day,
                burned=other["burned"], burn_events=other["burn_events"],
                contract_burned=other["contract_burned"],
                contract_events=other["contract_events"],
                by_contract=other["by_contract"],
            )
            days_written += 1
        if progress:
            progress({"day": r["day"], "from_tick": r["from_tick"],
                      "to_tick": out["scanned_to"], "calls": calls,
                      "complete": out["complete"]})
        if not out["complete"]:
            break            # stopped at a gap; resume here next run

    return {"days": days_written, "scanned": scanned, "calls": calls,
            "gaps": gaps, "from_tick": lo, "to_tick": hi,
            "reached_tick": reached, "complete": reached >= hi,
            "boundaries": ranges}


def missing_burn_days(store: Store, days: int, today: Optional[str] = None) -> list[str]:
    """Which of the last `days` FINISHED UTC days are not fully measured yet.

    The tick-window marker (`burn_backfill_runs`) answers "did we already scan
    this stretch of chain?", which is the right question for skipping repeated
    work but the wrong one for staying current: tomorrow is a new day outside
    every recorded window, so the marker says nothing about it, while the live
    scan only ever covers a slice of it.

    This asks the question an operator actually has — "is the last complete day
    in the report?" — against the measurement itself. A day counts as done when
    its stored buckets cover at least 95% of the day's own measured tick span,
    the same threshold `build_burn_series` uses for the `partial` flag, so the
    chart and this check can never disagree about what "complete" means.

    Today is excluded: it is still running, the live scan owns it, and it cannot
    be complete by definition. A day with no measured span at all is reported as
    missing, because "we never looked" is not "we found nothing".
    """
    if days <= 0:
        return []
    today = today or burn.observed_day()
    spans = store.day_ticks()
    scanned: dict[str, int] = {}
    for row in store.burn_series(by="day"):
        span_ticks = int(row["to_tick"]) - int(row["from_tick"]) + 1
        scanned[row["key"]] = max(scanned.get(row["key"], 0), span_ticks)

    out = []
    start = dating.day_start(today)
    # `days` counts back INCLUSIVE of today, matching the tick window
    # `backfill_burns_days` resolves (days=4 -> today and the three days before
    # it). Today is then dropped, because it is still running. The two must
    # agree: a check looking one day further back than the window can fill would
    # report a day as missing forever and the backfill would never settle.
    for back in range(1, int(days)):            # 1 = yesterday; today is excluded
        day = dating.day_of(start - back * dating.SECONDS_PER_DAY)
        span = spans.get(day)
        if not span or not span.get("complete"):
            out.append(day)                      # never dated, or still open
            continue
        period = int(span["to_tick"]) - int(span["from_tick"]) + 1
        if scanned.get(day, 0) < period * 0.95:
            out.append(day)
    return sorted(out)


def backfill_burns_days(
    client: CachedClient,
    store: Store,
    bob: BobClient,
    days: int,
    max_calls: int = 400,
    force: bool = False,
    progress=None,
    today: Optional[str] = None,
) -> dict:
    """Backfill the last `days` whole UTC days, once per deployment.

    `backfill_burns` takes ticks, but an operator thinks in days and a tick
    number is not something they can know in advance — especially on a host
    where the only control surface is an environment variable. This resolves
    "the last N days" to a tick range by measurement (`dating`), then hands off.

    Two guards make it safe to run unconditionally on every container start:

      * a completed run is recorded (`store.mark_burn_backfill`) and a later
        start whose window is already covered returns `reason="already done"`
        without touching the node. Without this, a host whose watcher restarts
        the image on every push would re-scan the same hundreds of thousands of
        ticks against a public Bob node forever.
      * a run that did not reach the end of the window is NOT recorded, so the
        next start resumes it rather than declaring the hole filled. That covers
        both ways of stopping short: a range Bob could not serve, and a run that
        simply ran out of call budget (measured: ~270 calls buy one day).

    `force=True` skips the first guard, for an operator who knows the stored run
    was wrong (a corrected scanner, say) and wants the days recounted.
    """
    if days <= 0:
        return {"scanned": 0, "reason": "disabled"}

    # The cheap question first, before any network call.
    #
    # This runs on a timer (the worker re-checks hourly, because midnight turns
    # the running day into a finished one). Resolving the tick window costs ~20
    # RPC lookups by bisection, and paying that every hour to learn there is
    # nothing to do would be 20 pointless requests an hour against a public node
    # for the lifetime of the deployment. `missing_burn_days` reads the local
    # store only, so an idle pass costs one SQLite query and no network at all.
    #
    # `force` still has to reach the work, so it skips this shortcut.
    # Which UTC day it is decides both the window and what counts as missing, so
    # it is resolved once here and threaded through. A caller may pin it — tests
    # do, because a test whose result depends on the wall clock passes today and
    # fails tomorrow, which is how this parameter came to exist.
    today = today or burn.observed_day()
    missing = missing_burn_days(store, days, today=today)
    if not force and not missing and store.burn_backfill_runs():
        return {"scanned": 0, "reason": "already done", "missing_days": [],
                "still_missing": []}

    try:
        info = client.tick_info()
        head = int(info.get("tick") or 0)
    except QubicRPCError:
        return {"scanned": 0, "reason": "rpc unreachable"}
    if not head:
        return {"scanned": 0, "reason": "no current tick"}

    # Never ask beyond what Bob has indexed or below what it still retains:
    # outside that window the node answers empty, which the scanner cannot
    # distinguish from "no burns happened".
    try:
        status = bob.status()
        indexed = int(status.get("currentIndexingTick") or 0)
        initial = int(status.get("initialTick") or 0)
    except (BobError, TypeError, ValueError):
        indexed, initial = 0, 0
    hi = min(head, indexed) if indexed else head

    # Where the window starts is a measurement, not an estimate. Deriving it
    # from a tick rate is exactly the mistake this module's dating exists to
    # retire: the rate the old code assumed (~2.7/s) is nearly double the
    # measured one, so "N days back" by arithmetic would land on the wrong day.
    target = dating.day_start(today) - (int(days) - 1) * dating.SECONDS_PER_DAY
    floor = max(initial, 0) if initial else max(0, hi - 2_000_000)
    lo = dating.find_first_tick_at_or_after(client.tick_timestamp, target, floor, hi)
    if lo is None:
        # Bob's retained history does not reach back that far. Start at its
        # floor and say so, rather than silently backfilling a shorter window
        # as though it were the one that was asked for.
        lo = floor
    if lo >= hi:
        return {"scanned": 0, "reason": "nothing to backfill", "from_tick": lo}

    # Skip only when there is nothing left to fill. Two questions, and the day
    # one has to come first:
    #
    #   * `missing_burn_days` asks whether the last N finished days are actually
    #     in the report. This is what keeps a long-running deployment current:
    #     tomorrow is a new day outside every recorded tick window, so the window
    #     marker alone would never notice it was missing.
    #   * the window marker then prevents re-scanning a stretch of chain that a
    #     previous run already counted, which is what makes this safe to call on
    #     every container start.
    if not force and not missing and store.burn_backfill_done(lo, hi):
        return {"scanned": 0, "reason": "already done",
                "from_tick": lo, "to_tick": hi, "missing_days": []}

    out = backfill_burns(client, store, bob, from_tick=lo, to_tick=hi,
                         max_calls=max_calls, progress=progress)
    # Only a run that reached the end of the window earns the marker.
    #
    # Testing `gaps` alone was wrong, and a live run against the real nodes is
    # what showed it: a pass that exhausts its call budget stops partway with no
    # gap recorded at all. It reported 20,000 of 200,000 ticks and still marked
    # the whole window done, which would have made the next start skip the 90%
    # it never counted — the exact hole the marker exists to prevent.
    #
    # `complete` answers the question directly: did the count reach `hi`?
    reached_end = bool(out.get("complete"))
    if not out.get("reason") and reached_end and not out.get("gaps"):
        store.mark_burn_backfill(lo, hi, days=int(out.get("days") or 0))
        out["recorded"] = True
    else:
        out["recorded"] = False
    out["requested_days"] = int(days)
    out["missing_days"] = missing
    # What is STILL missing after this pass — the honest answer to "is the last
    # complete day in the report?", asked again now that the work is done.
    out["still_missing"] = missing_burn_days(store, days, today=today)
    return out


def burn_coverage(store: Store) -> dict:
    """Measured burns against the official counter's step, per epoch.

    A ratio below 1.0 means a burn category the scan does not recognise, and is
    published rather than hidden — the same discipline `linkage_coverage: 0`
    applies to operator attribution. Only epochs we scanned end to end can be
    compared at all; a partially scanned epoch would understate by construction.
    """
    totals = store.burn_totals()
    by_epoch = {row["epoch"]: row for row in totals}
    measured = {int(r["key"]): r for r in store.burn_series(by="epoch")}
    out = []
    for prev, cur in zip(totals, totals[1:]):
        epoch = cur["epoch"]
        if epoch not in measured:
            continue
        official = int(cur["burned_total"]) - int(prev["burned_total"])
        row = burn.reconcile(int(measured[epoch]["burned"] or 0)
                             + int(measured[epoch]["contract_burned"] or 0),
                             official)
        row["epoch"] = epoch
        out.append(row)
    return {"epochs": out, "anchored_epochs": len(by_epoch)}


def build_burn_series(store: Store, by: str = "day",
                      limit: Optional[int] = DEFAULT_BURN_DAYS) -> dict:
    """The burn series, with every point labelled measured or derived.

    Two regimes share one chart (CONCEPT_BURN §4.1): what we counted ourselves,
    and — before that — what can only be read off the epoch counter's steps. They
    are not smoothed together. A reader has to be able to see where real
    measurement begins, so `first_measured_day` marks the boundary and each point
    carries `measured`.
    """
    rows = store.burn_series(by=by, limit=limit)
    state = store.burn_scan_state()
    spans = store.day_ticks()
    series = []
    cumulative = 0
    for r in rows:
        burned = int(r["burned"] or 0)
        cumulative += burned
        scanned = (int(r["to_tick"]) - int(r["from_tick"]) + 1) if (
            r["to_tick"] is not None and r["from_tick"] is not None) else 0
        point = {
            "key": r["key"],
            "burned": burned,
            "events": int(r["events"] or 0),
            "contract_burned": int(r["contract_burned"] or 0),
            "contract_events": int(r["contract_events"] or 0),
            "from_tick": r["from_tick"], "to_tick": r["to_tick"],
            "cumulative": cumulative,
            "measured": True,
        }
        # How much of the period the scan actually covered. A bucket holding
        # 10,000 ticks is about an hour, and labelling that "19.09." invites the
        # reader to take an hour for a day. Measured 2026-09-19: exactly that
        # happened, and the bar read 17.6 Mrd/day.
        #
        # The denominator is the day's OWN measured tick span where we have it
        # (see store.day_ticks). Dividing by a constant instead assumed a fixed
        # tick rate; the real rate averages 1.58/s and varies 29% between days,
        # which made complete days report 50-65% coverage and carry a "partial"
        # warning. TICKS_PER_DAY remains only for days never dated.
        if by == "day" and scanned:
            span = spans.get(r["key"])
            period = None
            if span and span.get("complete"):
                period = int(span["to_tick"]) - int(span["from_tick"]) + 1
            point["scanned_ticks"] = scanned
            point["period_ticks"] = period or TICKS_PER_DAY
            point["period_measured"] = bool(period)
            point["coverage"] = round(
                min(1.0, scanned / (period or TICKS_PER_DAY)), 4)
            point["partial"] = scanned < (period or TICKS_PER_DAY) * 0.95
        series.append(point)
    out = {
        "by": by,
        "series": series,
        "first_measured_tick": state["first_tick"] if state else None,
        "first_measured_day": series[0]["key"] if series else None,
        "last_scanned_tick": state["last_tick"] if state else None,
        "generated_at": int(time.time()),
        "code_version": __version__,
    }
    out["reconciliation"] = burn_reconciliation(store)
    return out


# A day's worth of ticks, used only to say how much of a day a bucket covers —
# never to project a partial bucket up to a full one.
#
# This was 86400 * 2.7, and that was wrong enough to matter. Measured
# 2026-09-20 from real day boundaries (`/v1/ticks/{t}/tick-data`, see
# qdr/dating.py), three complete UTC days ran 151,983 / 138,564 / 117,846 ticks
# — an average of 1.58 ticks/s, not 2.7. Against the old constant a FULLY
# scanned day scored 50-65% coverage and was flagged `partial`, so the page
# warned that complete days were incomplete.
#
# The rate is not constant either — those three days vary by 29% between
# themselves — so any single number here is an approximation. It stays as a
# fallback for buckets whose day boundaries are not known, and `coverage` is
# capped at 1.0 so an above-average day cannot report more than a whole day.
TICKS_PER_DAY = int(86400 * 1.58)


def burn_reconciliation(store: Store) -> dict:
    """How the counted burns compare to the protocol's own burned counter.

    This is the check that stops the series from publishing a figure nobody can
    corroborate. It earned its place: on 2026-09-20 the scan counted 17.6 Mrd QU
    over ~10,000 ticks while `burnedQus` did not move at all over the following
    66,666 ticks. The counter was right — the transfers were being refunded
    inside their own transaction and nothing was burned (see burn.burn_events).

    The lesson that outlives that particular bug: where our count and the
    protocol's counter disagree, the counter is the authority. So a measurement
    is reported with its disagreement attached rather than as a bare number:
    `status` is `unreconciled` until an epoch boundary has been observed on both
    sides, and `consistent` / `diverging` once it has.
    """
    totals = store.burn_totals()
    measured = {int(r["key"]): r for r in store.burn_series(by="epoch")}

    steps = []
    for prev, cur in zip(totals, totals[1:]):
        epoch = cur["epoch"]
        if epoch not in measured:
            continue
        official = int(cur["burned_total"]) - int(prev["burned_total"])
        counted = (int(measured[epoch]["burned"] or 0)
                   + int(measured[epoch]["contract_burned"] or 0))
        steps.append({"epoch": epoch, "official_step": official,
                      "counted": counted,
                      "ratio": round(counted / official, 4) if official else None})

    latest = store.latest_burn_total()
    if not steps:
        return {
            "status": "unreconciled",
            "reason": ("no epoch boundary observed on both sides yet, so the counted "
                       "figure has not been checked against the protocol's own "
                       "burned counter"),
            "official_total": int(latest["burned_total"]) if latest else None,
            "official_epoch": int(latest["epoch"]) if latest else None,
            "steps": [],
        }

    worst = max(steps, key=lambda s: abs((s["ratio"] or 1) - 1))
    diverging = worst["ratio"] is None or not (0.5 <= worst["ratio"] <= 2.0)
    return {
        "status": "diverging" if diverging else "consistent",
        "reason": ("the counted burns disagree with the official counter's step; "
                   "the counted event stream includes flows the protocol does not "
                   "treat as burned supply") if diverging else None,
        "official_total": int(latest["burned_total"]) if latest else None,
        "official_epoch": int(latest["epoch"]) if latest else None,
        "steps": steps,
    }


# Measured 2026-09-07 and unchanged since: the chain advances ~2.7 ticks/s. Used
# only to project a per-tick observation into a per-day figure a reader can hold
# onto; the underlying measurement is per tick and is reported as such.
TICKS_PER_SECOND = 2.7


def _measured_rate(store: Store) -> dict:
    """Burn rate over the ticks actually scanned.

    Returns per-tick, per-second and per-day figures, plus the tick span they
    came from, so a consumer can show the projection and still see its basis.
    Empty when nothing has been scanned — an invented rate would be exactly the
    thing this whole feature exists to avoid.
    """
    with store._lock:                      # noqa: SLF001 - same module family
        row = store._conn.execute(
            "SELECT SUM(burned) b, SUM(burn_events) e, "
            "MIN(from_tick) lo, MAX(to_tick) hi FROM burn_buckets"
        ).fetchone()
    if not row or not row["e"] or row["lo"] is None:
        return {}
    ticks = max(1, int(row["hi"]) - int(row["lo"]) + 1)
    events_per_tick = int(row["e"]) / ticks
    burned_per_tick = int(row["b"] or 0) / ticks
    per_day = 86_400 * TICKS_PER_SECOND
    return {
        "scanned_ticks": ticks,
        "events_per_tick": round(events_per_tick, 4),
        "events_per_second": round(events_per_tick * TICKS_PER_SECOND, 3),
        "events_per_day": int(round(events_per_tick * per_day)),
        "burned_per_day": int(round(burned_per_tick * per_day)),
        "rate_basis": f"{int(row['e'])} events over {ticks} scanned ticks "
                      f"@ {TICKS_PER_SECOND} ticks/s",
    }


def build_burn_latest(store: Store) -> Optional[dict]:
    """Headline burn figures: the official total, plus what we measured.

    The official total is what explorer.qubic.org shows and the only figure
    directly comparable to it. Everything with finer resolution than an epoch is
    ours, and is labelled as such.
    """
    total = store.latest_burn_total()
    if not total:
        return None
    days = store.burn_series(by="day")
    recent = days[-1] if days else None
    state = store.burn_scan_state()
    burned_total = int(total["burned_total"])
    circulating = int(total["circulating"] or 0)
    cov = burn_coverage(store)
    return {
        "burned_total": burned_total,
        "circulating": circulating or None,
        # Share of the supply that WOULD exist had nothing burned. Dividing by
        # circulating alone would understate it — burned QU are not in that
        # figure any more.
        "burned_share": (round(burned_total / (burned_total + circulating), 6)
                         if circulating else None),
        "epoch": total["epoch"],
        "tick": total["tick"],
        "observed_at": total["observed_at"],
        "measured": {
            "last_day": recent["key"] if recent else None,
            "burned": int(recent["burned"]) if recent else 0,
            "events": int(recent["events"]) if recent else 0,
            "first_measured_tick": state["first_tick"] if state else None,
            "last_scanned_tick": state["last_tick"] if state else None,
            # Rate over the ticks actually scanned, not over a wall-clock day: a
            # day still being measured has only partial hours in it, so dividing
            # its total by 24 would understate the rate all day and only become
            # right at midnight. Per-tick is what was observed; the ~2.7 ticks/s
            # measured tick rate turns it into a per-day figure for the reader,
            # and it is labelled as the projection it is.
            **_measured_rate(store),
        },
        "coverage": cov["epochs"][-1] if cov["epochs"] else None,
        # Carried here too, not only on the series: these are the figures the
        # page shows LARGEST, and a headline number that disagrees with the
        # protocol's own counter must say so where it is read.
        "reconciliation": burn_reconciliation(store),
        "source": "rpc:latest-stats (total) + bob:getLogs (per-day measurement)",
        "code_version": __version__,
    }


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
    # The epoch slider is driven by the timeseries, so the cluster list has to
    # cover exactly the same epochs. build_timeseries drops the running epoch
    # (no revenue until it closes); leaving it in here made the dashboard open on
    # an epoch the slider had no entry for, showing a half-paid epoch's figures.
    charted = {p["epoch"] for p in ts["series"]}
    epoch_clusters = []
    for e in sorted(epochs):
        if e not in charted:
            continue
        rep = store.get_report(e)
        if not rep:
            continue
        cbr = rep["concentration_by_revenue"]
        epoch_clusters.append({
            "epoch": e, "status": rep.get("status"),
            "nakamoto_one_third": cbr["nakamoto_one_third"],
            "gini": cbr["gini"], "bubbles": _bubbles(rep),
            "slot_distribution": slot_distribution(store, e),
        })
    # The headline report has to be an epoch the panels can actually show. The
    # slider is driven by the timeseries, which excludes running and partial
    # epochs, so taking "the newest settled report" independently could label the
    # page with one epoch while every chart below it describes another.
    # No settled epoch means there is nothing to report yet — a fresh deployment
    # whose worker has not finished its first backfill. Returning the running
    # epoch here would publish a report of 676 slots at zero revenue, with a
    # Nakamoto of 0, as though that described the network. The API turns an empty
    # bundle into a 503 and the page says it is still building.
    latest = store.get_report(max(charted)) if charted else None
    return {
        "report": latest,
        "timeseries": {"series": ts["series"]},
        "epoch_clusters": {"epochs": epoch_clusters},
    }
