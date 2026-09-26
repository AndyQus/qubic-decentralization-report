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

import dataclasses
import os
import time
from typing import Optional

from . import __version__, burn, dating, trendlines
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


# -- market price ----------------------------------------------------------
# Read from Qubic's own RPC, not from an exchange.
#
# Exchange APIs were built against first and then removed on purpose. MEXC's
# terms (clause 17d) prohibit "data feeding or streaming services that make use
# of any market data of MEXC" without written consent, and storing their bars to
# republish them on a public page is precisely that. Gate.io publishes no clear
# permission either. `/v1/latest-stats` is an endpoint this project already
# reads, served by Qubic itself, and its stats service holds the licence for the
# price it publishes (it is configured with a CoinGecko token). One source, one
# set of terms, and those the ones we already operate under.
#
# The cost is stated rather than worked around: the RPC carries NO trading
# volume, so neither does this. A volume figure sourced elsewhere would be the
# same licensing problem under another name.
#
# Cadence: qubic-stats-service scrapes every 60s (SERVICE_DATA_SCRAPE_INTERVAL
# defaults to "1m") and its API caches 10s. Measured 2026-09-21 the published
# figure held still for 2.5 minutes while the response `timestamp` advanced on
# every request -- so one read per minute is the right rate, and whether a
# reading is NEW has to be decided by comparison, not by trusting that field.
PRICE_SAMPLE_INTERVAL_S = int(os.environ.get("QDR_PRICE_SAMPLE_INTERVAL", "60"))


def sample_price(client: CachedClient, store: Store) -> Optional[dict]:
    """Take one reading of the market and store it.

    Returns the stored row (carrying `changed`), or None when the RPC could not
    be read -- a transient upstream failure is reported by absence rather than
    raised, so the worker loop survives it.

    The reading is minute-aligned so the series lands on a regular grid: two
    passes inside one minute are the same minute observed twice, and the later
    one wins rather than producing a second point a chart would draw as motion.
    """
    # Narrow on purpose: CachedClient already wraps every network, status and
    # JSON failure in QubicRPCError, so that one class covers "the RPC was
    # unreachable" completely. Catching more would absorb bugs in here -- during
    # development a blanket `except Exception` hid a renamed method behind a
    # store that merely looked empty.
    try:
        stats = client.network_pulse()
    except QubicRPCError:
        return None

    price = float(stats.get("price") or 0.0)
    if price <= 0:
        # A zero price is not a market reading. Storing it would put a crash to
        # the floor of the chart, which is the most alarming thing this page
        # could invent.
        return None

    at = (int(time.time()) // 60) * 60
    return store.put_price_reading(
        at=at,
        price=price,
        market_cap=stats.get("market_cap"),
        circulating=stats.get("circulating_supply"),
        epoch=stats.get("epoch"),
        tick=stats.get("tick"),
        rpc_timestamp=stats.get("timestamp"),
    )


def price_change(store: Store, window_s: int = 86400) -> Optional[dict]:
    """How far the price moved over a window, from stored intervals only.

    Returns None when the window holds fewer than two prices: a single standing
    price cannot show a change, and reporting 0% there would claim a stability
    nobody measured.

    `coverage_pct` is polls against elapsed minutes -- how continuously the
    window was actually watched. It is what separates "the price was steady" from
    "nobody was looking", which a flat line alone cannot say.
    """
    now = int(time.time())
    rows = store.price_series(since=now - window_s)
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    if not first["price"]:
        return None
    polls = sum(int(r["polls"] or 0) for r in rows)
    expected = max(1, window_s // 60)
    return {
        "from": first["price"], "to": last["price"],
        "abs": last["price"] - first["price"],
        "pct": round((last["price"] - first["price"]) / first["price"] * 100, 2),
        "high": max(r["price"] for r in rows),
        "low": min(r["price"] for r in rows),
        "first_at": int(first["at"]), "last_at": int(last["until"]),
        # Distinct prices in the window, i.e. how often it actually moved.
        "moves": len(rows),
        "polls": polls,
        "window_s": window_s,
        "coverage_pct": round(min(polls / expected, 1.0) * 100, 1),
    }


def price_source() -> dict:
    """Where the price comes from, in one shape every price endpoint returns.

    Travels with the data rather than living only in the docs: a consumer that
    republishes these figures needs the provenance attached to them, and the
    missing volume is a property of the source that a caller should not have to
    discover by finding the field absent.
    """
    return {
        "endpoint": "/v1/latest-stats",
        "provider": "Qubic RPC",
        # Named here too: the figure originates with CoinGecko and reaches us
        # through Qubic's licensed stats service. Saying so is both honest about
        # provenance and the reason this is allowed.
        "upstream": "CoinGecko, via Qubic's stats service",
        "interval_s": PRICE_SAMPLE_INTERVAL_S,
        "quote": "USD per QU",
        "note": "Price and market cap only -- the RPC publishes no trading volume.",
    }


def price_summary(store: Store) -> dict:
    """Headline market figures, assembled from stored intervals.

    Everything here is measured. Nothing is carried forward: a sampler that has
    not run leaves `status: "no data"` rather than the last price it ever saw.
    """
    now = int(time.time())
    latest = store.latest_price()
    out: dict = {
        "at": now,
        "coverage": store.price_coverage(),
        "source": price_source(),
    }
    if not latest:
        out["status"] = "no data"
        return out

    # Age is measured from the last CONFIRMATION, not from when the price was
    # first seen: a price standing for an hour is current, not an hour stale.
    age = now - int(latest["until"])
    out.update({
        "status": "live" if age <= 300 else "stale",
        "price": latest["price"],
        "market_cap": latest["market_cap"],
        "circulating": latest["circulating"],
        "epoch": latest["epoch"],
        "at": int(latest["at"]),
        "confirmed_at": int(latest["until"]),
        "age_s": age,
        # How long this figure has stood, and how many readings confirm it.
        # "unchanged for 12 minutes over 12 polls" is a statement the page can
        # make; "unchanged" alone would not distinguish a quiet market from a
        # stalled worker.
        "held_for_s": int(latest["until"]) - int(latest["at"]),
        "polls": int(latest["polls"] or 1),
    })
    for label, w in (("24h", 86400), ("7d", 604800), ("30d", 2592000)):
        ch = price_change(store, window_s=w)
        if ch:
            out.setdefault("change", {})[label] = ch
    return out


# -- trend lines -----------------------------------------------------------
#
# docs/CONCEPT_TRENDLINES.de.md §5. The rule lives in qdr/trendlines.py and is
# pure; this is the part with a memory. The worker runs `update_trendlines`
# after each hourly rollup, and every line it sets keeps its anchors for good:
# it can only end, as broken or replaced. That is what lets the page show an
# archive of which lines held, and what keeps a line from jumping on reload.


def trend_bars(store: Store, scale: "trendlines.Scale",
               now: Optional[int] = None) -> list[dict]:
    """The bars a scale reads, from the permanent hourly summary.

    `price_hours` holds finished hours only, so the hourly scales never see an
    hour still being written. The day scale drops the running UTC day for the
    same reason: a half day's high and low are not the day's.
    """
    rows = store.price_series(resolution="hour")
    hours = [{"at": int(r["at"]), "high": float(r["high"]),
              "low": float(r["low"]), "close": float(r["close"])} for r in rows]
    if scale.period_s == trendlines.DAY:
        now = int(time.time()) if now is None else int(now)
        today = now // trendlines.DAY * trendlines.DAY
        return [d for d in trendlines.daily_bars(hours) if d["at"] < today]
    return hours


def update_trendlines(store: Store, now: Optional[int] = None) -> dict:
    """Bring the stored lines of every scale in line with the bars on record.

    Per scale, in this order:

      1. Every active line is checked for a break since its second anchor.
         A broken line ends at the first bar of the break.
      2. The rule runs on the bars. For resistance and support separately:
         the same anchors as the active line -> only its touches are updated;
         different anchors -> the active line is replaced; no line found -> the
         active one stays (it has not broken, so it still stands).
      3. A line found again after it was replaced comes back to life; one
         found again after it broke is left broken. The price went through it.
         A line the price is already through -- or one close into -- when the
         rule first finds it is not stored at all.

    Idempotent: running it twice on the same bars changes nothing, so the
    worker may call it as often as it rolls up. Returns counts per scale.
    """
    now = int(time.time()) if now is None else int(now)
    summary: dict = {}
    for scale in trendlines.SCALES.values():
        bars = trend_bars(store, scale, now)
        counts = {"found": 0, "broken": 0, "replaced": 0, "revived": 0}
        summary[scale.name] = counts

        active = {}
        for row in store.trendlines(scale=scale.name, status="active"):
            ln = _as_line(row)
            at = trendlines.broken_at(ln, bars, scale)
            if at is not None:
                store.end_trendline(row["id"], "broken", at)
                counts["broken"] += 1
            else:
                active[row["kind"]] = row

        found = trendlines.detect(bars, scale)
        if not found["ready"]:
            continue
        for d in found["lines"]:
            if d["kind"] not in ("resistance", "support"):
                continue   # channel lines are derived when served, never stored
            cur = active.get(d["kind"])
            if cur and (cur["t1"], cur["t2"]) == (d["t1"], d["t2"]):
                if cur["touches"] != d["touches"]:
                    store.touch_trendline(cur["id"], d["touches"], d["touch_at"])
                continue
            known = store.trendline_by_anchors(scale.name, d["kind"], d["t1"], d["t2"])
            if known and known["status"] == "broken":
                continue
            if known:   # replaced earlier, found again
                store.revive_trendline(known["id"])
                store.touch_trendline(known["id"], d["touches"], d["touch_at"])
                new_id = known["id"]
                counts["revived"] += 1
            elif d["broken_at"] is not None or _half_broken(d, bars, scale):
                # Already through the line, or one close into a break, in the
                # bars too young to anchor it. Such a line never held while
                # anyone could see it; stored, it ended an hour after it was
                # found -- three of 19 rows on the first replay's short scale.
                continue
            else:
                new_id = store.put_trendline(scale.name, d, found_at=now)
                counts["found"] += 1
            if cur:
                store.end_trendline(cur["id"], "replaced", now, replaced_by=new_id)
                counts["replaced"] += 1
    return summary


def _half_broken(d: dict, bars: list[dict], scale: "trendlines.Scale") -> bool:
    """True when the newest close is already beyond the line: half a break."""
    one = dataclasses.replace(scale, break_bars=1)
    return bool(bars) and trendlines.broken_at(_as_line(d), bars[-1:], one) is not None


def _as_line(row: dict) -> "trendlines.Line":
    return trendlines.Line(row["kind"], int(row["t1"]), float(row["p1"]),
                           int(row["t2"]), float(row["p2"]), int(row["touches"]),
                           tuple(row.get("touch_at") or ()))


def _trend_view(row: dict) -> dict:
    ln = _as_line(row)
    out = dict(row)
    out["slope_pct_per_day"] = round(ln.slope_pct_per_day(), 4)
    if row["status"] == "broken":
        # Which way it broke follows from the kind; said outright so a reader
        # of the archive does not have to know that.
        out["broke"] = "up" if row["kind"] == "resistance" else "down"
    return out


def build_trendlines(store: Store, scale_name: str, status: str = "active",
                     since: Optional[int] = None, limit: int = 50,
                     now: Optional[int] = None) -> dict:
    """What `/v1/price/trendlines` serves: stored lines plus the derived channel.

    The channel (parallel and mid line) is computed here from the ACTIVE pair
    and the bars, never stored, so it always belongs to the lines on screen.
    `shape` names what the active pair forms, for the tooltip: a channel, a
    converging wedge or triangle, or diverging lines.
    """
    scale = trendlines.SCALES[scale_name]
    bars = trend_bars(store, scale, now)
    rows = store.trendlines(scale=scale_name,
                            status=None if status == "all" else status,
                            since=since, limit=limit)
    act = {r["kind"]: r for r in store.trendlines(scale=scale_name, status="active")}
    res = _as_line(act["resistance"]) if "resistance" in act else None
    sup = _as_line(act["support"]) if "support" in act else None
    channel = [dict(ln.as_dict(), broken_at=None)
               for ln in trendlines.channel(bars, res, sup, scale)] if bars else []
    shape = None
    if res and sup:
        if channel:
            shape = "channel"
        else:
            # Gap between the lines now vs at the older first anchor.
            t_old = min(res.t1, sup.t1)
            t_new = bars[-1]["at"] if bars else max(res.t2, sup.t2)
            shape = "converging" if (res.at(t_new) - sup.at(t_new)) < \
                (res.at(t_old) - sup.at(t_old)) else "diverging"
    return {
        "scale": scale.name,
        "ready": len(bars) >= scale.ready_bars,
        "bars_on_record": len(bars),
        "bars_needed": scale.ready_bars,
        "period_s": scale.period_s,
        "params": {"k": scale.k, "min_sep": scale.min_sep, "tol": scale.tol,
                   "lookback": scale.lookback, "break_bars": scale.break_bars},
        "recording_since": store.trendlines_first_found(),
        "lines": [_trend_view(r) for r in rows],
        "channel": channel,
        "shape": shape,
        "measured": True,
        "note": "Geometry from past highs and lows, set by a fixed rule. Not a forecast.",
    }


# -- the epoch payout study ------------------------------------------------
# The one question this page can ask that no market site can.
#
# Once a week (Wednesday 12:00 UTC) the protocol pays 676 computors. Measured from our own sealed
# reports, epochs 227-228 each emitted ~180 billion QU (the epoch-227 halving is
# visible in that series: 226 emitted ~360 billion). At 4.07e-7 USD that is
# roughly 73,000 USD per epoch arriving in 676 wallets at once -- against a
# reported daily volume near 1.28 million USD, so on the order of 6% of a day's
# turnover, delivered in a moment.
#
# Whether any of it is sold, and whether the price moves when it lands, is not
# something anyone can currently answer: the payouts are protocol emission and
# do not appear on the public RPC as transfers at all. This project derives them
# from a Bob node's end-epoch log, which is why it alone can line the boundary
# up against a measured price series.
#
# This function does NOT claim causation. It reports what the price did around
# each boundary and how well that window was observed, and leaves the reading to
# whoever looks. A single epoch proves nothing; the value is in the column of
# them accumulating, which is why `epochs_needed` is published too.
PAYOUT_STUDY_WINDOW_S = int(os.environ.get("QDR_PAYOUT_WINDOW", str(12 * 3600)))

# Below this many observed boundaries the study says so rather than inviting a
# reading. Three is not a statistical threshold -- it is the point at which a
# column of numbers stops looking like a single anecdote.
PAYOUT_STUDY_MIN_EPOCHS = 3


def epoch_payout_study(store: Store, window_s: int = PAYOUT_STUDY_WINDOW_S,
                       limit: int = 12) -> dict:
    """How the price behaved around each observed epoch boundary.

    For every boundary our own readings witnessed, this compares the price one
    window before against the price one window after, and reports how much of
    each window was actually sampled. A boundary whose surroundings were barely
    observed is reported WITH that fact rather than dropped -- and never with a
    percentage that pretends to more than it measured.
    """
    boundaries = store.price_epoch_boundaries()
    if limit and len(boundaries) > limit:
        boundaries = boundaries[-limit:]

    cases: list[dict] = []
    for b in boundaries:
        at = int(b["at"])
        before = store.price_at(at - window_s)
        after = store.price_at(at + window_s)
        rows = store.price_around(at, window_s, window_s)
        # Coverage is polls against the minutes the window contains: a window
        # watched throughout scores 1.0, one sampled twice scores near zero.
        polls = sum(int(r["polls"] or 0) for r in rows)
        expected = max(1, (2 * window_s) // 60)

        case = {
            "epoch": b["epoch"],
            "at": at,
            "boundary_within_s": b["within_s"],
            "window_s": window_s,
            "polls": polls,
            "coverage_pct": round(min(polls / expected, 1.0) * 100, 1),
            "prices": len(rows),
        }
        if rows:
            case["high"] = max(r["price"] for r in rows)
            case["low"] = min(r["price"] for r in rows)
        # Before/after only when BOTH edges were actually observed. One edge
        # alone cannot make a change, and substituting the nearest reading would
        # dress a guess as a measurement.
        if before and after and before["price"]:
            case["before"] = before["price"]
            case["after"] = after["price"]
            case["change_pct"] = round(
                (after["price"] - before["price"]) / before["price"] * 100, 2)
        else:
            case["change_pct"] = None
            case["note"] = "not observed on both sides of the window"
        cases.append(case)

    measured = [c for c in cases if c["change_pct"] is not None]
    out: dict = {
        "window_s": window_s,
        "window_label": f"{window_s // 3600}h either side",
        "cases": cases,
        "observed": len(measured),
        "boundaries_seen": len(boundaries),
        "epochs_needed": max(0, PAYOUT_STUDY_MIN_EPOCHS - len(measured)),
        "conclusive": len(measured) >= PAYOUT_STUDY_MIN_EPOCHS,
        # Context for the reader, from our own sealed reports rather than a
        # claim: what actually gets paid out at one of these boundaries.
        "payout": _last_epoch_payout(store),
        "measures": (
            "Price before vs. after each epoch boundary, from readings taken "
            "either side. It reports what happened; it does not claim the "
            "payout caused it."
        ),
    }
    if measured:
        changes = [c["change_pct"] for c in measured]
        ups = len([c for c in changes if c > 0])
        out["summary"] = {
            "median_change_pct": round(sorted(changes)[len(changes) // 2], 2),
            "mean_change_pct": round(sum(changes) / len(changes), 2),
            "up": ups, "down": len(changes) - ups,
            # The spread matters more than the average here: a mean of +0.1%
            # across wildly scattered cases says nothing, and the page should be
            # able to show that rather than print one reassuring number.
            "min_change_pct": min(changes), "max_change_pct": max(changes),
        }
    return out


def _last_epoch_payout(store: Store) -> Optional[dict]:
    """What the most recent sealed epoch actually paid, from our own report.

    Returned beside the study so the reader can size the event themselves:
    this many QU, to this many computors, at the price standing then.
    """
    for epoch in sorted(store.report_epochs(), reverse=True):
        rep = store.get_report(epoch)
        if not rep or rep.get("status") != STATUS_SEALED:
            continue
        totals = rep.get("totals") or {}
        total = totals.get("total_revenue")
        if not total:
            continue
        out = {"epoch": epoch, "total_qu": int(total),
               "computors": totals.get("computors")}
        latest = store.latest_price()
        if latest and latest.get("price"):
            out["usd_at_current_price"] = round(int(total) * latest["price"], 2)
            out["priced_at"] = latest["price"]
        return out
    return None


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
    # ~1.6 ticks/s that is ~1,900 ticks of drift per pass, against a fixed budget
    # covering 10,000 — it keeps up, and still does if the RPC has a slow day and
    # the gap stretches to an hour (~5,800 ticks).
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
                      limit: Optional[int] = DEFAULT_BURN_DAYS,
                      now: Optional[float] = None) -> dict:
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
    now = time.time() if now is None else now
    today = dating.day_of(int(now))
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
            # The running UTC day is never whole, whatever its tick count says.
            # Tick numbers jump at an epoch change (epoch 231 ended at 81,259,537,
            # 232 began at 81,400,000), so on 2026-09-23 a half-day span held
            # 199,285 tick NUMBERS, beat TICKS_PER_DAY and read "100%, complete".
            # For today the clock is the measure: the scan runs live, so the
            # share of the day that has elapsed is the share it can have covered.
            if not period and r["key"] >= today:
                elapsed = (now - dating.day_start(today)) / dating.SECONDS_PER_DAY
                point["coverage"] = round(min(1.0, max(0.0, elapsed)), 4)
                point["partial"] = True
                point["in_progress"] = True
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


# How many recent whole days the per-day rate averages over. A week, so the rate
# spans every weekday once and an epoch change (Wednesday 12:00 UTC) at most once.
RATE_DAYS = 7


def _measured_rate(store: Store, now: Optional[float] = None) -> dict:
    """Burn rate per day, averaged over whole UTC days that were fully scanned.

    This used to project a per-tick rate through an assumed 2.7 ticks/s, over
    MIN..MAX of the scanned ticks. Both were wrong: the real rate is ~1.4-1.8
    ticks/s and varies by day, the scanned range can have gaps, and tick numbers
    jump at every epoch change, so a tick is not a unit of time. A whole scanned
    day is: its burns and events divided by one day, measured over measured.

    Empty when no whole day has been scanned yet — an invented rate would be
    exactly the thing this whole feature exists to avoid.
    """
    series = build_burn_series(store, by="day", limit=None, now=now)["series"]
    whole = [p for p in series
             if p.get("period_measured") and not p.get("partial")][-RATE_DAYS:]
    if not whole:
        return {}
    n = len(whole)
    events = sum(p["events"] for p in whole)
    burned = sum(p["burned"] for p in whole)
    return {
        "rate_days": n,
        "rate_from_day": whole[0]["key"],
        "rate_to_day": whole[-1]["key"],
        "events_per_second": round(events / (n * 86_400), 5),
        "events_per_day": int(round(events / n)),
        "burned_per_day": int(round(burned / n)),
        "rate_basis": f"{events} events, {burned} QU over {n} whole UTC "
                      f"day{'s' if n != 1 else ''} "
                      f"({whole[0]['key']} to {whole[-1]['key']})",
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
            # Rate over whole UTC days only, never the running one: a day still
            # being measured has only partial hours in it, so dividing its total
            # by 24 would understate the rate all day. See _measured_rate.
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
