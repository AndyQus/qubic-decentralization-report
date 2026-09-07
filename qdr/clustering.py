"""Slot-to-operator clustering.

**On-chain linkage is the default attribution layer, not a fallback bucket.**
(Community feedback: "on chain linkage should be the default not the fallback
bucket labeled unattributed" — accepted, see CONCEPT §4.2.)

Self-reporting is what operators *claim*; the payout graph is what the ledger
*shows*. Both run on every epoch and the report publishes both plus their delta:

  1. on-chain linkage   the payout graph merges identities that share an economic
                        owner. Runs with no registry at all and produces the
                        baseline clustering.
  2. self-reported      the registry LABELS linkage clusters and may merge slots
                        the chain has not linked yet. It never SPLITS what the
                        chain has linked — a declaration cannot un-prove a payout.
  3. behavioral         timing/source fingerprints -> flags only, never accusations.

Why the ordering matters: treating every undeclared slot as its own operator is
not neutral, it is systematically optimistic — singletons inflate the operator
count and push Nakamoto/Gini/HHI toward "more decentralized than reality". So
`unattributed` here means *the ledger shows no link*, not *nobody filed a form*,
and every report states its linkage coverage.
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

# Confidence levels, strongest first. Ordering is meaningful: a merge keeps the
# strongest confidence of its parts.
CONF_DECLARED_LINKED = "declared+linked"  # claimed AND proven on chain
CONF_LINKED = "linked"                    # proven on chain, not declared
CONF_DECLARED = "declared"                # declared, no on-chain confirmation yet
CONF_FLAGGED = "flagged"                  # behavioral correlation only
CONF_UNATTRIBUTED = "unattributed"        # ledger shows no link, nobody declared

_CONF_RANK = {
    CONF_DECLARED_LINKED: 0,
    CONF_LINKED: 1,
    CONF_DECLARED: 2,
    CONF_FLAGGED: 3,
    CONF_UNATTRIBUTED: 4,
}


@dataclass
class Cluster:
    cluster_id: str
    label: str
    confidence: str
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
    return json.loads(p.read_text(encoding="utf-8"))


def _identity_to_operator(registry: dict) -> dict[str, dict]:
    """Build {computor_identity -> {operator_id,label}} from the registry."""
    idx: dict[str, dict] = {}
    for pool in registry.get("pools", []):
        op_id = pool["id"]
        label = pool.get("label", op_id)
        for ident in pool.get("computors", []):
            idx[ident] = {"operator_id": op_id, "label": label}
    return idx


def _union_find(identities: list[str], linkage: dict[str, str]) -> dict[str, str]:
    """Group identities by shared owner key into {identity -> group_key}.

    Uses the owner key itself as the group root, so two identities paying out to
    the same destination land in one group regardless of iteration order.
    """
    groups: dict[str, str] = {}
    for ident in identities:
        owner = linkage.get(ident)
        if owner:
            groups[ident] = owner
    return groups


def cluster_epoch(
    computors: list[str],
    revenue: dict[str, int],
    registry: Optional[dict] = None,
    linkage: Optional[dict[str, str]] = None,
) -> list[Cluster]:
    """Assign each computor of an epoch to a cluster.

    On-chain linkage runs first and forms the baseline clusters; the registry then
    labels and supplements them. A slot ends up `unattributed` only when neither
    layer has anything on it.
    """
    registry = registry or {"pools": []}
    linkage = linkage or {}
    declared_idx = _identity_to_operator(registry)
    link_groups = _union_find(computors, linkage)

    # An operator is the natural merge key: a declared pool wins over a raw owner
    # key, so declared+linked slots collapse into one named cluster.
    def merge_key(ident: str) -> tuple[str, str, str]:
        """-> (key, label, confidence)"""
        decl = declared_idx.get(ident)
        link = link_groups.get(ident)
        if decl and link:
            return decl["operator_id"], decl["label"], CONF_DECLARED_LINKED
        if decl:
            return decl["operator_id"], decl["label"], CONF_DECLARED
        if link:
            return f"linked:{link}", f"Linked ({link[:8]})", CONF_LINKED
        return f"unattributed:{ident[:8]}", "Unattributed", CONF_UNATTRIBUTED

    evidence_for = {
        CONF_DECLARED_LINKED: ["self-reported registry", "shared on-chain payout destination"],
        CONF_DECLARED: ["self-reported registry"],
        CONF_LINKED: ["shared on-chain payout destination"],
        CONF_UNATTRIBUTED: ["no on-chain link found; no self-report"],
    }

    clusters: dict[str, Cluster] = {}
    for ident in computors:
        key, label, conf = merge_key(ident)
        c = clusters.get(key)
        if c is None:
            c = Cluster(cluster_id=key, label=label, confidence=conf,
                        evidence=list(evidence_for[conf]))
            clusters[key] = c
        else:
            # a group can contain both declared and merely-linked members;
            # keep the strongest confidence and union the evidence
            if _CONF_RANK[conf] < _CONF_RANK[c.confidence]:
                c.confidence = conf
                for e in evidence_for[conf]:
                    if e not in c.evidence:
                        c.evidence.append(e)
        c.computors.append(ident)
        c.revenue += int(revenue.get(ident, 0))

    return sorted(clusters.values(), key=lambda c: (c.revenue, c.computor_count), reverse=True)


def linkage_coverage(computors: list[str], clusters: list[Cluster]) -> dict:
    """How much of the network is actually resolved, versus assumed independent.

    This is the honesty figure that keeps the headline metrics readable: with low
    coverage, a high Nakamoto coefficient means "we could not link them", not
    "they are independent".
    """
    total = len(computors) or 1
    by_conf: dict[str, int] = {}
    for c in clusters:
        by_conf[c.confidence] = by_conf.get(c.confidence, 0) + c.computor_count
    attributed = total - by_conf.get(CONF_UNATTRIBUTED, 0)
    linked = by_conf.get(CONF_LINKED, 0) + by_conf.get(CONF_DECLARED_LINKED, 0)
    declared = by_conf.get(CONF_DECLARED, 0) + by_conf.get(CONF_DECLARED_LINKED, 0)
    return {
        "computors": total,
        "attributed_slots": attributed,
        "attributed_share": round(attributed / total, 6),
        "onchain_linked_slots": linked,
        "onchain_linked_share": round(linked / total, 6),
        "declared_slots": declared,
        "declared_share": round(declared / total, 6),
        "unattributed_slots": by_conf.get(CONF_UNATTRIBUTED, 0),
        "slots_by_confidence": by_conf,
    }


def declared_vs_detected(clusters: list[Cluster]) -> dict:
    """The 'smoke' figure: concentration the chain shows but nobody declared.

    `undeclared_linked_slots` is the headline — slots provably run by one owner
    that no self-report accounts for.
    """
    linked_only = [c for c in clusters if c.confidence == CONF_LINKED]
    confirmed = [c for c in clusters if c.confidence == CONF_DECLARED_LINKED]
    declared_only = [c for c in clusters if c.confidence == CONF_DECLARED]
    return {
        "confirmed_operators": len(confirmed),
        "declared_unconfirmed_operators": len(declared_only),
        "undeclared_linked_operators": len(linked_only),
        "undeclared_linked_slots": sum(c.computor_count for c in linked_only),
        "undeclared_linked_revenue": sum(c.revenue for c in linked_only),
        "largest_undeclared_cluster": max(
            (c.computor_count for c in linked_only), default=0
        ),
    }


def apply_flags(clusters: list[Cluster], flags: dict[str, str]) -> list[Cluster]:
    """Annotate clusters with behavioral correlation hints.

    Flags never merge or re-attribute anything — they are surfaced as "possible
    undeclared cluster" evidence so a reader can judge, per CONCEPT §4.2 layer 3.
    """
    if not flags:
        return clusters
    for c in clusters:
        hits = {flags[i] for i in c.computors if i in flags}
        if hits and c.confidence == CONF_UNATTRIBUTED:
            c.confidence = CONF_FLAGGED
            c.evidence.append(f"behavioral correlation: {', '.join(sorted(hits))}")
    return clusters
