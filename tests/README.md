# Tests

Run all:

```
python3 tests/test_metrics.py
python3 tests/test_report.py
# or, with pytest installed:
python3 -m pytest -q
```

- `test_metrics.py` — Gini / HHI / top-N / Nakamoto against known inputs.
- `test_report.py` — clustering + report assembly on a synthetic epoch, incl. the
  key case: equal per-slot revenue looks decentralized until you cluster by operator.
