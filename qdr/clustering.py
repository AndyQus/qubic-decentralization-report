"""Slot-to-operator clustering.

Layered from most trustworthy to most inferential (see CONCEPT §4.2):
  1. self-reported   pools declare the computor identities they control (registry)
  2. on-chain        shared payout linkage merges identities (hook, extensible)
  3. behavioral      timing/source fingerprints -> flags only (not implemented v1)

A cluster = one operator (or a set of provably linked identities). Each cluster
carries a confidence level and the evidence used, so the report is transparent
about what is declared vs. inferred.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# QDR_REGISTRY lets an operator mount their own pools.json over the bundled one
# (e.g. a Docker volume) without rebuilding the image.
DEFAULT_REGISTRY = Path(
    os.environ.get(
        "QDR_REGISTRY",
        Path(__file__).resolve().parent.parent / "data" / "self_reporting" / "pools.json",
    )
)


@dataclass
class Cluster:
    cluster_id: str
    label: str
    confidence: str  # "declared" | "linked" | "flagged" | "unattributed"
    computors: list[str] = field(default_factory=list)
    revenue: int = 0
    evidence: list[str] = field(default_factory=list)

    @property
    def computor_count(self) -> int:
        return len(self.computors)

    def to_dict(self, total_revenue: int = 0) -> dict:
        d = asdict(self)
        d["computor_count"] = self.computor_count
        d["revenue_share"] = round(self.revenue / total_revenue, 6) if total_revenue else 0.0
        return d


def load_registry(path: Path | str = DEFAULT_REGISTRY) -> dict:
    p = Path(path)
    if not p.exists():
        return {"pools": []}
    return json.loads(p.read_text())


def _identity_to_operator(registry: dict) -> dict[str, dict]:
    """Build {computor_identity -> {operator_id,label}} from the registry."""
    idx: dict[str, dict] = {}
    for pool in registry.get("pools", []):
        op_id = pool["id"]
        label = pool.get("label", op_id)
        for ident in pool.get("computors", []):
            idx[ident] = {"operator_id": op_id, "label": label}
    return idx


def cluster_epoch(
    computors: list[str],
    revenue: dict[str, int],
    registry: dict,
) -> list[Cluster]:
    """Assign each computor of an epoch to a cluster.

    Declared identities group under their operator. Undeclared identities each
    form their own singleton "unattributed" cluster — we never invent linkage we
    cannot prove, so unattributed slots inflate apparent decentralization on
    purpose (the on-chain/behavioral layers are what erode that later).
    """
    idx = _identity_to_operator(registry)
    clusters: dict[str, Cluster] = {}

    for ident in computors:
        rev = int(revenue.get(ident, 0))
        if ident in idx:
            op = idx[ident]
            cid = op["operator_id"]
            c = clusters.get(cid)
            if c is None:
                c = Cluster(
                    cluster_id=cid,
                    label=op["label"],
                    confidence="declared",
                    evidence=["self-reported registry"],
                )
                clusters[cid] = c
            c.computors.append(ident)
            c.revenue += rev
        else:
            cid = f"unattributed:{ident[:8]}"
            clusters[cid] = Cluster(
                cluster_id=cid,
                label="Unattributed",
                confidence="unattributed",
                computors=[ident],
                revenue=rev,
                evidence=["no self-report; no proven on-chain link"],
            )

    return sorted(clusters.values(), key=lambda c: (c.revenue, c.computor_count), reverse=True)


def apply_onchain_linkage(
    clusters: list[Cluster],
    linkage: dict[str, str],
) -> list[Cluster]:
    """Hook: merge unattributed clusters that share a proven on-chain owner.

    `linkage` maps computor_identity -> shared_owner_key (e.g. common payout
    destination). Only merges among unattributed/flagged clusters; declared
    clusters are left intact but a delta can be reported separately.
    """
    if not linkage:
        return clusters
    merged: dict[str, Cluster] = {}
    passthrough: list[Cluster] = []
    for c in clusters:
        if c.confidence != "unattributed":
            passthrough.append(c)
            continue
        owners = {linkage.get(i) for i in c.computors if linkage.get(i)}
        if not owners:
            passthrough.append(c)
            continue
        owner = sorted(owners)[0]
        key = f"linked:{owner}"
        m = merged.get(key)
        if m is None:
            m = Cluster(
                cluster_id=key,
                label=f"Linked ({owner[:8]})",
                confidence="linked",
                evidence=["shared on-chain payout destination"],
            )
            merged[key] = m
        m.computors.extend(c.computors)
        m.revenue += c.revenue
    out = passthrough + list(merged.values())
    return sorted(out, key=lambda c: (c.revenue, c.computor_count), reverse=True)
