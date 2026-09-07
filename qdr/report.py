"""Assemble the decentralization report for a single epoch.

The persistent pipeline (qdr/pipeline.py) is what production uses — it derives,
clusters, persists and applies the sealed-vs-live rule. This module keeps the
stateless, single-epoch assembly used by tests and by anyone who wants to
recompute a report from injected inputs without a store.

Every report names its inputs and its code version, and carries the provenance of
the revenue it was built on, so a third party can re-derive it and locate any
divergence rather than argue about the totals.
"""
from __future__ import annotations

from typing import Optional

from .client import CachedClient
from .clustering import cluster_epoch, load_registry
from .pipeline import _assemble
from .revenue import RevenueResult, derive_epoch_revenue
from .store import STATUS_LIVE


def build_epoch_report(
    client: CachedClient,
    epoch: int,
    revenue: Optional[dict[str, int]] = None,
    registry: Optional[dict] = None,
    linkage: Optional[dict[str, str]] = None,
    status: str = STATUS_LIVE,
) -> dict:
    """Build the report for one epoch without touching a store.

    revenue/registry/linkage can be injected (tests, alternative sources);
    otherwise revenue is derived from the chain and the registry is loaded from
    the repo. On-chain linkage is a first-class input here too — passing none
    means "the ledger showed no links", not "linkage is optional".
    """
    computors = client.computors(epoch)

    if revenue is None:
        rev = derive_epoch_revenue(client, epoch, computors=computors)
    else:
        # injected revenue is taken as given, but still travels with provenance
        rev = RevenueResult(
            epoch=epoch,
            revenue=revenue,
            complete=True,
            warnings=["revenue injected by caller; not derived from chain"],
        )

    if registry is None:
        registry = load_registry()

    clusters = cluster_epoch(computors, rev.revenue, registry, linkage or {})
    return _assemble(epoch, computors, clusters, rev, status)
