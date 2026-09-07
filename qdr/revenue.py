"""Per-computor revenue derivation — full transaction tracking.

Concentration metrics are only as good as the revenue underneath them, and this
is exactly where independent reimplementations diverge (community feedback:
"mine doesn't converge with the numbers you have ... full transaction tracking").
The v0.1 derivation made three mistakes that all bias concentration *downward*:

  1. no epoch/tick scoping   -> Qubic pays with a one-epoch lag, so an unscoped
                                scan mixes two epochs into one figure
  2. no pagination           -> a truncated first page silently under-counts the
                                *largest* operators (they have the most transfers)
  3. unverified arbitrator   -> an env constant nobody checked against the chain

This module fixes all three and, crucially, makes the result *auditable*: every
epoch reports the tick window, the transfer count and the totals it was derived
from, so a divergence between two implementations can be located (which epoch,
which tick range, which transfers) instead of argued about.

An epoch that cannot be derived completely is returned as `complete=False` and
the pipeline marks it `partial` — kept out of the headline metrics rather than
published as if it were solid.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .bob import BobClient, BobError, computor_revenue, payout_sources
from .client import CachedClient, QubicRPCError

# The arbitrator distributes computor revenue at each epoch boundary. This is
# still a candidate value until confirmed against boundary-tick transfers — see
# verify_arbitrator() and DATA_SOURCES §4. Nothing here trusts it blindly: a
# derivation that finds no payouts from it says so instead of returning zeros.
ARBITRATOR_IDENTITY = os.environ.get(
    "QUBIC_ARBITRATOR",
    "AFZPUAIYVPNUYGJRQVLUKOPPVLHAZQTGLYAAUUNBXFTVTAMSBKQBLEIEPCVJF",
)

# Qubic pays an epoch's revenue during the *following* epoch. Payout transfers for
# epoch N are therefore searched in N's tick range plus the boundary of N+1.
PAYOUT_LAG_EPOCHS = 1

# Hard limit of /v2/identities/{id}/transfers, verified live 2026-09-07: a larger
# pageSize is rejected with HTTP 400, which would silently turn every epoch into
# an incomplete pull. Do not raise this without re-checking the endpoint.
MAX_PAGE_SIZE = 250

# A full sweep of 676 balance reads spans a few hundred ticks (one read per
# identity), so boundary lookups need a little slack rather than an exact tick
# match. Keep this in sweep-width territory: too wide and the start lookup starts
# picking up observations from the wrong side of the boundary.
SNAPSHOT_TOLERANCE_TICKS = int(os.environ.get("QDR_SNAPSHOT_TOLERANCE", 1000))

# Observed epoch lengths are ~1.0-1.1M ticks (epochs 228/229, verified 2026-09-07).
# Below this a window is a sample, not an epoch, so epoch-level sanity checks
# (like "most computors should have been paid") do not apply to it.
FULL_EPOCH_MIN_TICKS = int(os.environ.get("QDR_FULL_EPOCH_MIN_TICKS", 500_000))

# Documented distribution model, used for reconciliation (DATA_SOURCES §2):
# max ~1.48B QU per computor per epoch after the 10% operator fee.
MAX_REVENUE_PER_COMPUTOR = int(os.environ.get("QDR_MAX_COMPUTOR_REVENUE", 1_480_000_000))


@dataclass
class RevenueResult:
    """Derived revenue plus everything needed to re-derive and audit it."""

    epoch: int
    revenue: dict[str, int]
    first_tick: Optional[int] = None
    last_tick: Optional[int] = None
    complete: bool = False
    transfers: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    arbitrator: str = ARBITRATOR_IDENTITY
    method: str = "arbitrator-payouts"   # or "balance-delta"

    @property
    def total(self) -> int:
        return sum(self.revenue.values())

    @property
    def paid_computors(self) -> int:
        return sum(1 for v in self.revenue.values() if v > 0)

    def provenance(self) -> dict:
        """The block every report carries so a third party can recompute it."""
        return {
            "method": self.method,
            "arbitrator": self.arbitrator if self.method == "arbitrator-payouts" else None,
            "tick_range": [self.first_tick, self.last_tick],
            "payout_lag_epochs": PAYOUT_LAG_EPOCHS,
            "transfers_matched": len(self.transfers),
            "computors_paid": self.paid_computors,
            "total_revenue": self.total,
            "complete": self.complete,
            "warnings": self.warnings,
        }


# -- tick windows ----------------------------------------------------------

def epoch_tick_range(client: CachedClient, epoch: int) -> tuple[Optional[int], Optional[int]]:
    """First and last processed tick of an epoch.

    Verified against the live RPC (2026-09-07): `/v1/epochs/{e}/computors` does NOT
    carry a tick pointer, so the window comes from two other endpoints —

        first tick  <- /v2/epochs/{e}/ticks   (first row of the tick listing)
        last tick   <- /v1/status             (lastProcessedTicksPerEpoch[e])

    Returns (None, None) when the boundary cannot be resolved; the caller then
    treats the epoch as incomplete rather than guessing a window.
    """
    return _first_tick(client, epoch), _last_tick(client, epoch)


def _first_tick(client: CachedClient, epoch: int) -> Optional[int]:
    """First tick of an epoch, from the paginated tick listing."""
    try:
        data = client._get(f"/v2/epochs/{epoch}/ticks?page=0&pageSize=1")
        ticks = data.get("ticks") or []
        if ticks:
            return int(ticks[0]["tickNumber"])
    except (QubicRPCError, AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    # current epoch: tick-info carries initialTick directly
    try:
        info = client.tick_info()
        if int(info.get("epoch", -1)) == epoch and info.get("initialTick") is not None:
            return int(info["initialTick"])
    except (QubicRPCError, AttributeError, KeyError, TypeError, ValueError):
        pass
    return None


def _last_tick(client: CachedClient, epoch: int) -> Optional[int]:
    """Last processed tick of an epoch, from /v1/status."""
    try:
        status = client._get("/v1/status", use_cache=False)
        per_epoch = status.get("lastProcessedTicksPerEpoch") or {}
        val = per_epoch.get(str(epoch), per_epoch.get(epoch))
        if val is not None:
            return int(val)
    except (QubicRPCError, AttributeError, KeyError, TypeError, ValueError):
        pass
    return None


# -- transfer extraction ---------------------------------------------------

def _iter_transactions(payload) -> Iterable[dict]:
    """Yield individual transactions from the RPC's several nesting shapes.

    Transfers come back as flat lists, as {transactions:[...]} wrappers, or as
    per-tick buckets. Normalizing here keeps the derivation logic readable.
    """
    if payload is None:
        return
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_transactions(item)
        return
    if not isinstance(payload, dict):
        return
    for key in ("transactions", "transferTransactionsPerTick", "data", "transfers"):
        if key in payload:
            yield from _iter_transactions(payload[key])
            return
    if "transaction" in payload and isinstance(payload["transaction"], dict):
        merged = dict(payload["transaction"])
        if "tickNumber" in payload and "tickNumber" not in merged:
            merged["tickNumber"] = payload["tickNumber"]
        yield merged
        return
    yield payload


def _normalize(tx: dict) -> dict:
    """Flatten one transaction to {txId,tick,sourceId,destId,amount}."""
    inner = tx.get("transaction") if isinstance(tx.get("transaction"), dict) else tx
    tick = tx.get("tickNumber", inner.get("tickNumber", inner.get("tick")))
    try:
        tick = int(tick) if tick is not None else None
    except (TypeError, ValueError):
        tick = None
    try:
        amount = int(inner.get("amount", 0) or 0)
    except (TypeError, ValueError):
        amount = 0
    return {
        "txId": inner.get("txId") or inner.get("id") or inner.get("transactionId"),
        "tick": tick,
        "sourceId": inner.get("sourceId") or inner.get("source") or inner.get("sourceIdentity"),
        "destId": inner.get("destId") or inner.get("destination") or inner.get("destId"),
        "amount": amount,
    }


def fetch_identity_transfers(
    client: CachedClient,
    identity: str,
    first_tick: Optional[int] = None,
    last_tick: Optional[int] = None,
    page_size: int = MAX_PAGE_SIZE,
    max_pages: int = 2000,
) -> tuple[list[dict], bool]:
    """Fetch an identity's transfers, paginating to exhaustion.

    Returns (transfers, complete). `complete` is False when pagination hit
    max_pages or an RPC error — the caller must not treat a truncated pull as a
    full one, because truncation under-counts the busiest (largest) operators.

    Tries the tick-scoped endpoint form first and falls back to the plain one,
    since RPC deployments differ in which query parameters they honour.
    """
    out: list[dict] = []
    seen: set[str] = set()

    for page in range(max_pages):
        path = f"/v2/identities/{identity}/transfers?page={page}&pageSize={page_size}"
        if first_tick is not None and last_tick is not None:
            path += f"&startTick={first_tick}&endTick={last_tick}"
        try:
            payload = client._get(path)
        except AttributeError:
            return out, False  # client cannot do raw GETs (stub/offline)
        except QubicRPCError:
            if page == 0:
                # v2 unavailable: fall back to the v1 shape (single, unpaged pull)
                try:
                    payload = client._get(f"/identities/{identity}/transfer-transactions")
                except QubicRPCError:
                    return out, False
                batch = [_normalize(t) for t in _iter_transactions(payload)]
                for t in batch:
                    if t["txId"] and t["txId"] not in seen:
                        seen.add(t["txId"])
                        out.append(t)
                # a single unpaged pull cannot be proven exhaustive
                return out, False
            return out, False

        batch = [_normalize(t) for t in _iter_transactions(payload)]
        for t in batch:
            key = t["txId"] or f"{t['tick']}:{t['sourceId']}:{t['destId']}:{t['amount']}"
            if key not in seen:
                seen.add(key)
                out.append(t)

        # v2 returns an explicit pagination block; trust it over heuristics.
        pg = payload.get("pagination") if isinstance(payload, dict) else None
        if isinstance(pg, dict) and pg.get("totalPages") is not None:
            try:
                if int(pg.get("currentPage", 1)) >= int(pg["totalPages"]):
                    return out, True
            except (TypeError, ValueError):
                pass
        elif not batch:
            return out, True  # ran off the end

    return out, False  # hit the page ceiling: not provably complete


# -- derivation ------------------------------------------------------------

def derive_epoch_revenue(
    client: CachedClient,
    epoch: int,
    arbitrator: str = ARBITRATOR_IDENTITY,
    computors: Optional[list[str]] = None,
) -> RevenueResult:
    """Derive per-computor revenue for one epoch, epoch-scoped and complete.

    Payouts for epoch N settle during epoch N+1, so the search window spans N's
    ticks through the end of N+1's payout boundary. Only transfers *from the
    arbitrator to an identity that held a slot in epoch N* count.
    """
    if computors is None:
        try:
            computors = client.computors(epoch)
        except (QubicRPCError, AttributeError) as e:
            return RevenueResult(epoch=epoch, revenue={}, complete=False,
                                 warnings=[f"computor list unavailable: {e}"],
                                 arbitrator=arbitrator)

    slots = set(computors)
    revenue: dict[str, int] = {c: 0 for c in slots}
    warnings: list[str] = []

    first_tick = _first_tick(client, epoch)
    # payouts land in the following epoch, so extend the window through its end
    last_tick = _last_tick(client, epoch + PAYOUT_LAG_EPOCHS) or _last_tick(client, epoch)
    if first_tick is None or last_tick is None:
        warnings.append(
            "tick window unresolved (epoch boundary not available); "
            "revenue cannot be proven epoch-scoped"
        )

    transfers, complete = fetch_identity_transfers(
        client, arbitrator, first_tick, last_tick
    )
    if not complete:
        warnings.append("transfer pagination incomplete — totals may under-count")

    matched: list[dict] = []
    for t in transfers:
        if t["sourceId"] != arbitrator:
            continue
        if t["destId"] not in slots:
            continue
        # enforce the tick window ourselves; we cannot assume the RPC honoured it
        if first_tick is not None and last_tick is not None and t["tick"] is not None:
            if not (first_tick <= t["tick"] <= last_tick):
                continue
        revenue[t["destId"]] += t["amount"]
        matched.append(t)

    if not matched:
        warnings.append(
            f"no payouts matched arbitrator {arbitrator[:12]}… — "
            "identity unverified or window wrong (see DATA_SOURCES §4)"
        )

    result = RevenueResult(
        epoch=epoch,
        revenue=revenue,
        first_tick=first_tick,
        last_tick=last_tick,
        complete=bool(complete and matched and first_tick is not None and last_tick is not None),
        transfers=matched,
        warnings=warnings,
        arbitrator=arbitrator,
    )
    result.warnings.extend(reconcile(result))
    return result


def reconcile(result: RevenueResult) -> list[str]:
    """Check derived revenue against the documented distribution model.

    A mismatch is reported as a warning in the payload rather than averaged away —
    the point is that a reader (or a second implementation) can see it.
    """
    problems: list[str] = []
    if not result.revenue:
        return problems

    over = [i for i, v in result.revenue.items() if v > MAX_REVENUE_PER_COMPUTOR]
    if over:
        problems.append(
            f"{len(over)} computor(s) exceed the documented per-epoch cap "
            f"({MAX_REVENUE_PER_COMPUTOR:,} QU) — check the tick window for epoch bleed"
        )
    if any(v < 0 for v in result.revenue.values()):
        problems.append("negative revenue derived — transfer parsing is wrong")

    unpaid = len(result.revenue) - result.paid_computors
    if unpaid and result.paid_computors:
        # Some slots legitimately earn nothing (slashing), but a large share is a
        # smell — over a FULL epoch. Over a short observation window most balances
        # simply have not moved yet, which is normal, so only flag it when the
        # window is long enough for the payout to have happened.
        span = (result.last_tick or 0) - (result.first_tick or 0)
        share = unpaid / len(result.revenue)
        if share > 0.5 and span >= FULL_EPOCH_MIN_TICKS:
            problems.append(
                f"{unpaid}/{len(result.revenue)} computors have zero revenue "
                "over a full-epoch window — likely an incomplete pull rather than real"
            )
    return problems


def verify_arbitrator(
    client: CachedClient,
    epoch: int,
    candidates: Optional[list[str]] = None,
) -> dict:
    """Identify which source identity actually pays the epoch's computors.

    Answers the open question in DATA_SOURCES §4 empirically instead of trusting a
    constant: for each candidate, count how many of the epoch's computors it paid.
    The real arbitrator pays a large share of the 676.
    """
    candidates = candidates or [ARBITRATOR_IDENTITY]
    try:
        slots = set(client.computors(epoch))
    except (QubicRPCError, AttributeError) as e:
        return {"epoch": epoch, "error": str(e), "candidates": []}

    first_tick = _first_tick(client, epoch)
    last_tick = _last_tick(client, epoch + PAYOUT_LAG_EPOCHS) or _last_tick(client, epoch)

    findings = []
    for cand in candidates:
        transfers, complete = fetch_identity_transfers(client, cand, first_tick, last_tick)
        paid = {t["destId"] for t in transfers
                if t["sourceId"] == cand and t["destId"] in slots}
        findings.append({
            "identity": cand,
            "computors_paid": len(paid),
            "coverage": round(len(paid) / len(slots), 4) if slots else 0.0,
            "transfers_seen": len(transfers),
            "pagination_complete": complete,
        })
    findings.sort(key=lambda f: f["computors_paid"], reverse=True)
    return {
        "epoch": epoch,
        "computors": len(slots),
        "tick_range": [first_tick, last_tick],
        "candidates": findings,
        "verdict": findings[0]["identity"] if findings and findings[0]["computors_paid"] else None,
    }


# Identities that say nothing about ownership when funds move through them: the
# protocol's own emission source, and burn/null sinks. Grouping computors by these
# would "link" the entire network into one cluster (or each slot to itself), which
# is worse than no linkage at all because it looks like proof.
_NULL_PREFIX = "A" * 20
BURN_PREFIXES = ("AAAAAAAAAAAAAAAAAAAA", "BAAAAAAAAAAAAAAAAAAA", "DAAAAAAAAAAAAAAAAAAA")


def is_uninformative_identity(identity: Optional[str]) -> bool:
    """True for null/burn/system addresses that cannot indicate a shared owner."""
    if not identity:
        return True
    return any(identity.startswith(p) for p in BURN_PREFIXES)


def payout_linkage(result: RevenueResult) -> dict[str, str]:
    """Linkage candidates from an epoch's payout transfers.

    Deliberately returns nothing for the epoch-payout leg itself. Measured on
    epoch 228: all 676 computors are credited by the SAME null address, because
    the credit is protocol emission — so "shares a payout source" is true of the
    whole network and proves nothing. Mapping a computor to its own identity
    (what this did before) is worse still: it claims 100% on-chain coverage while
    proving no link at all.

    Real linkage is where a computor forwards its revenue ON to — see
    `forward_linkage()`, which needs the tick log rather than the end-epoch log.
    """
    return {}


def forward_linkage(
    transfers: list[dict],
    computors: list[str],
    min_amount: int = 0,
) -> dict[str, str]:
    """Group computors by where they forward their funds.

    `transfers` are ordinary tick-log transfers (from Bob's `qubic_getLogs`). A
    computor that sends to destination D is grouped under D; two computors paying
    out to the same wallet are the same economic owner, which is the actual
    anti-Sybil signal (CONCEPT §4.2 layer 1).

    Only outgoing transfers count, null/burn destinations are ignored, and a
    destination that every computor sends to (an exchange, a fee sink) is dropped
    — a "cluster" of the whole network is noise, not a finding.
    """
    slots = set(computors)
    dest_counts: dict[str, set[str]] = {}
    for t in transfers:
        src, dst = t.get("sourceId"), t.get("destId")
        amt = int(t.get("amount") or 0)
        if src not in slots or amt <= min_amount:
            continue
        if dst in slots or is_uninformative_identity(dst):
            continue
        dest_counts.setdefault(dst, set()).add(src)

    if not dest_counts:
        return {}

    # A destination shared by almost everyone is infrastructure, not an owner.
    ceiling = max(2, int(len(slots) * 0.5))
    linkage: dict[str, str] = {}
    for dst, senders in dest_counts.items():
        if len(senders) < 2 or len(senders) > ceiling:
            continue
        for src in senders:
            linkage.setdefault(src, dst)
    return linkage


# -- balance-delta derivation ----------------------------------------------
#
# Verified on 2026-09-07 (DATA_SOURCES §6.2): computor revenue is credited by
# protocol-level emission at the epoch boundary, NOT by a transfer that the public
# transaction endpoints expose. Scanning ~4,500 transactions per computor across a
# full epoch window finds zero inbound payments, while /v1/balances reports large
# inbound totals for the same identities. So the payout-tracking method above
# cannot work against the RPC as it stands, no matter how completely it paginates.
#
# What DOES work: /v1/balances exposes cumulative `incomingAmount` per identity.
# The difference between two observations of that counter is the value credited in
# between. It is current-state only, so the history has to be OUR snapshots — which
# is precisely what the store is for. This produces correct data from the next
# epoch boundary forward without depending on anyone else.


def snapshot_balances(
    client: CachedClient,
    identities: list[str],
    max_identities: Optional[int] = None,
) -> list[dict]:
    """Read the balance counters for a set of identities, once.

    Returns rows shaped for Store.put_balance_snapshots(). Identities that fail to
    read are skipped rather than recorded as zero — a missing row is honest, a
    zero row would corrupt the next delta.
    """
    out: list[dict] = []
    for ident in identities[:max_identities] if max_identities else identities:
        try:
            b = client.balance(ident)
        except (QubicRPCError, AttributeError, KeyError, TypeError, ValueError):
            continue
        try:
            out.append({
                "identity": ident,
                "tick": int(b["validForTick"]),
                "balance": int(b.get("balance", 0) or 0),
                "incoming_amount": int(b.get("incomingAmount", 0) or 0),
                "outgoing_amount": int(b.get("outgoingAmount", 0) or 0),
                "incoming_count": int(b.get("numberOfIncomingTransfers", 0) or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def revenue_from_balance_deltas(
    store,
    epoch: int,
    computors: list[str],
    first_tick: Optional[int],
    last_tick: Optional[int],
) -> RevenueResult:
    """Derive an epoch's revenue from stored balance snapshots.

    Revenue per computor = incomingAmount(at/after the epoch end)
                         - incomingAmount(at/before the epoch start)

    Requires snapshots on both sides of the boundary. Without them the epoch is
    returned incomplete (and the pipeline marks it `partial`) rather than
    reporting zeros — the honest answer to "we did not observe this epoch".
    """
    revenue: dict[str, int] = {c: 0 for c in computors}
    warnings: list[str] = []
    if first_tick is None or last_tick is None:
        return RevenueResult(epoch=epoch, revenue=revenue, complete=False,
                             method="balance-delta",
                             warnings=["tick window unresolved; cannot bound a balance delta"])

    # Snapshots are taken per identity, so each lands on its own tick and a full
    # sweep of 676 spans a few hundred ticks — no observation sits exactly on the
    # boundary. Take the widest pair actually observed inside the window: the
    # FIRST observation at/after the start, and the LAST at/before the end. The
    # tolerance only absorbs a sweep that straddles the boundary, so it must stay
    # far smaller than an epoch, never wide enough to swallow the whole window.
    tol = max(SNAPSHOT_TOLERANCE_TICKS, 0)
    measured = 0
    missing_start = missing_end = single_obs = 0
    for ident in computors:
        start = store.first_snapshot_after(ident, max(first_tick - tol, 0))
        end = store.latest_snapshot_before(ident, last_tick + tol)
        if start is None:
            missing_start += 1
            continue
        if end is None:
            missing_end += 1
            continue
        if int(end["tick"]) <= int(start["tick"]):
            # only one observation for this identity: no window to measure over
            single_obs += 1
            continue
        delta = int(end["incoming_amount"]) - int(start["incoming_amount"])
        if delta < 0:
            # counters are cumulative; a decrease means the identity was reset or
            # the snapshots are misordered. Report it, do not silently clamp.
            warnings.append(f"negative balance delta for {ident[:12]}...; snapshot suspect")
            continue
        revenue[ident] = delta
        measured += 1

    if missing_start or missing_end:
        warnings.append(
            f"no balance snapshot at all for {missing_start} start / {missing_end} end "
            "identities - run ingest.py --snapshot regularly to close the gap"
        )
    if single_obs:
        warnings.append(
            f"{single_obs} computor(s) have only one observation in this window - "
            "a delta needs two, so keep the snapshot worker running across the boundary"
        )

    complete = measured == len(computors) and not warnings
    if measured and not complete:
        warnings.append(f"measured {measured}/{len(computors)} computors")
    result = RevenueResult(
        epoch=epoch,
        revenue=revenue,
        first_tick=first_tick,
        last_tick=last_tick,
        complete=complete,
        warnings=warnings,
        method="balance-delta",
    )
    result.warnings.extend(reconcile(result))
    return result


# -- Bob-node derivation (primary) -----------------------------------------
#
# A Bob node keeps the full event log, including the virtual end-epoch tick where
# the protocol credits every computor. That is the only public source carrying
# these payouts (DATA_SOURCES §6.2/§6.4). Verified against bob.qubic.li on
# 2026-09-07: epoch 228 -> 676/676 computors paid, 178,477,462,349 QU, all from
# the null address, with history available back to at least epoch 220.
#
# This supersedes both the arbitrator-payout method (impossible: the records are
# not in the public RPC) and balance deltas (works, but only forward from the
# first observed boundary). Balance deltas stay as the fallback for when no Bob
# node is reachable.


def revenue_from_bob(
    epoch: int,
    computors: list[str],
    bob: Optional[BobClient] = None,
    first_tick: Optional[int] = None,
    last_tick: Optional[int] = None,
) -> RevenueResult:
    """Derive an epoch's revenue from a Bob node's end-epoch log.

    A complete result means every computor of the epoch was found in the log —
    that is the strong completeness signal the other methods cannot give us.
    """
    bob = bob or BobClient()
    warnings: list[str] = []
    try:
        logs = bob.end_epoch_logs(epoch)
    except BobError as e:
        return RevenueResult(
            epoch=epoch, revenue={c: 0 for c in computors}, complete=False,
            method="bob-end-epoch", first_tick=first_tick, last_tick=last_tick,
            warnings=[f"bob unavailable for epoch {epoch}: {e}"],
        )

    revenue, matched = computor_revenue(logs, computors)
    paid = sum(1 for v in revenue.values() if v > 0)

    if not matched:
        warnings.append(
            f"bob returned {len(logs)} entries for epoch {epoch} but no computor "
            "payouts — the epoch is probably still running"
        )
    elif paid < len(computors):
        warnings.append(f"bob paid {paid}/{len(computors)} computors")

    sources = payout_sources(matched)
    if len(sources) > 1:
        # one source (the null address) means protocol emission; more than one
        # would change what this number represents, so say so rather than sum it
        warnings.append(f"payouts came from {len(sources)} distinct sources, not one")

    # the end-epoch tick is the settlement point, and it is in the log itself
    ticks = [t["tick"] for t in matched if t.get("tick")]
    settle_tick = max(ticks) if ticks else None

    result = RevenueResult(
        epoch=epoch,
        revenue=revenue,
        first_tick=first_tick,
        last_tick=last_tick if last_tick is not None else settle_tick,
        complete=bool(matched) and paid == len(computors),
        transfers=matched,
        warnings=warnings,
        method="bob-end-epoch",
    )
    result.warnings.extend(reconcile(result))
    return result
