"""Revenue / power concentration metrics.

All functions take a list of non-negative weights (revenue per operator, or slot
counts per operator) and return standard, defensible concentration indices. Pure
Python, no heavy deps, so results are trivially reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


def _clean(weights: Iterable[float]) -> list[float]:
    xs = [float(w) for w in weights if w is not None]
    if any(x < 0 for x in xs):
        raise ValueError("weights must be non-negative")
    return xs


def gini(weights: Iterable[float]) -> float:
    """Gini coefficient in [0,1]. 0 = perfectly equal, ->1 = one holder takes all.

    Uses the mean-absolute-difference definition, robust for small samples.
    """
    xs = sorted(_clean(weights))
    n = len(xs)
    s = sum(xs)
    if n == 0 or s == 0:
        return 0.0
    # G = (sum_i (2i - n - 1) x_i) / (n * sum x)   with i = 1..n
    cum = sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs))
    return cum / (n * s)


def hhi(weights: Iterable[float], normalized: bool = True) -> float:
    """Herfindahl-Hirschman Index of shares.

    Raw HHI = sum of squared shares in [1/n, 1]. If normalized, rescale to [0,1]
    so it is comparable across different n: (HHI - 1/n) / (1 - 1/n).
    """
    xs = _clean(weights)
    n = len(xs)
    s = sum(xs)
    if n == 0 or s == 0:
        return 0.0
    shares = [x / s for x in xs]
    raw = sum(p * p for p in shares)
    if not normalized or n == 1:
        return raw
    return (raw - 1.0 / n) / (1.0 - 1.0 / n)


def top_n_share(weights: Iterable[float], n: int) -> float:
    """Share of the total held by the largest n entries, in [0,1]."""
    xs = sorted(_clean(weights), reverse=True)
    s = sum(xs)
    if s == 0:
        return 0.0
    return sum(xs[:n]) / s


def nakamoto_coefficient(weights: Iterable[float], threshold: float = 0.5) -> int:
    """Minimum number of largest entries whose combined share exceeds `threshold`.

    The classic decentralization headline: how many operators must collude to pass
    a control threshold (⅓ for a halting/liveness attack, ½ for majority control).
    """
    if not 0 < threshold < 1:
        raise ValueError("threshold must be in (0,1)")
    xs = sorted(_clean(weights), reverse=True)
    s = sum(xs)
    if s == 0:
        return 0
    acc = 0.0
    for i, x in enumerate(xs, start=1):
        acc += x / s
        if acc > threshold:
            return i
    return len(xs)


@dataclass
class ConcentrationSummary:
    n_entries: int
    total: float
    gini: float
    hhi_normalized: float
    top1_share: float
    top3_share: float
    top5_share: float
    nakamoto_one_third: int
    nakamoto_half: int

    def to_dict(self) -> dict:
        return asdict(self)


def summarize(weights: Iterable[float]) -> ConcentrationSummary:
    """Compute the full concentration summary for a set of operator weights."""
    xs = _clean(weights)
    return ConcentrationSummary(
        n_entries=len(xs),
        total=sum(xs),
        gini=round(gini(xs), 6),
        hhi_normalized=round(hhi(xs, normalized=True), 6),
        top1_share=round(top_n_share(xs, 1), 6),
        top3_share=round(top_n_share(xs, 3), 6),
        top5_share=round(top_n_share(xs, 5), 6),
        nakamoto_one_third=nakamoto_coefficient(xs, 1 / 3),
        nakamoto_half=nakamoto_coefficient(xs, 1 / 2),
    )
