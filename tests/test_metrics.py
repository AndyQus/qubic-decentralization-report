"""Unit tests for the concentration metrics. Run: python -m pytest -q  (or python tests/test_metrics.py)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr.metrics import gini, hhi, top_n_share, nakamoto_coefficient, summarize


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_gini_equal_is_zero():
    assert approx(gini([5, 5, 5, 5]), 0.0)


def test_gini_all_but_one_zero_is_high():
    # one holder among n -> G = 1 - 1/n
    g = gini([0, 0, 0, 100])
    assert approx(g, 1 - 1 / 4)


def test_gini_empty_and_zero():
    assert gini([]) == 0.0
    assert gini([0, 0]) == 0.0


def test_hhi_normalized_bounds():
    # equal shares -> normalized HHI 0
    assert approx(hhi([1, 1, 1, 1], normalized=True), 0.0)
    # single holder -> normalized HHI 1
    assert approx(hhi([0, 0, 10], normalized=True), 1.0)


def test_top_n_share():
    assert approx(top_n_share([10, 30, 60], 1), 0.6)
    assert approx(top_n_share([10, 30, 60], 2), 0.9)


def test_nakamoto_coefficient():
    # shares .6/.3/.1 : half exceeded by 1 (0.6>0.5); one-third exceeded by 1 too
    assert nakamoto_coefficient([60, 30, 10], 0.5) == 1
    assert nakamoto_coefficient([60, 30, 10], 1 / 3) == 1
    # even split of 5 -> need 3 to pass 0.5
    assert nakamoto_coefficient([1, 1, 1, 1, 1], 0.5) == 3
    # to pass one-third of an even 5-split -> 2 (0.4>0.333)
    assert nakamoto_coefficient([1, 1, 1, 1, 1], 1 / 3) == 2


def test_summary_shape():
    s = summarize([1, 1, 1, 1, 1]).to_dict()
    assert s["n_entries"] == 5
    assert s["nakamoto_half"] == 3
    assert 0.0 <= s["gini"] <= 1.0
    assert 0.0 <= s["hhi_normalized"] <= 1.0


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} metric tests passed")
