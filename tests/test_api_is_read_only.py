"""The API must never write to the store.

This is what kept putting an empty distribution panel on the page: get_report()
recomputed the running epoch on request and persisted the result, stamped with
the serving process's own code_version. An API process running an older build —
one started before a version bump, or a container not yet redeployed — therefore
overwrote epochs behind the ingest worker's back and undid its corrections. One
writer (the worker), many readers.
"""
import time

import pytest

from qdr import pipeline
from qdr.store import Store


class _FakeClient:
    """Stands in for the RPC. Any call here means a recompute was attempted."""

    def __init__(self):
        self.calls = 0

    def current_epoch(self):
        self.calls += 1
        return 10

    def computors(self, epoch):
        self.calls += 1
        return [f"ID{i:03d}" for i in range(8)]


def _live_report(epoch=10):
    return {"epoch": epoch, "status": "live",
            "totals": {"computors": 8, "operators": 8, "declared_operators": 0,
                       "unattributed_slots": 8, "total_revenue": 0},
            "concentration_by_revenue": {"gini": 0.0, "hhi_normalized": 0.0,
                                         "top1_share": 0.0, "nakamoto_one_third": 0,
                                         "nakamoto_half": 0},
            "concentration_by_slots": {"nakamoto_one_third": 3},
            "clusters": [], "linkage_coverage": {}}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_epoch(10, "live", 1, 2, 8)
    s.put_report(10, _live_report(), "live")
    # make the stored copy look stale so a writable call would recompute
    with s._lock:
        s._conn.execute("UPDATE reports SET computed_at=? WHERE epoch=10",
                        (int(time.time()) - 9999,))
        s._conn.commit()
    return s


def test_read_only_get_report_does_not_recompute(store):
    client = _FakeClient()
    rep = pipeline.get_report(store, 10, client=client, max_live_age_s=1,
                              allow_write=False)
    assert rep is not None and rep["epoch"] == 10
    # current_epoch is allowed (epoch=10 was given, so not even that); a
    # computors() call would mean it went off to rebuild the epoch
    assert client.calls == 0


def test_the_stored_report_is_returned_unchanged(store):
    before = store.get_report(10)["computed_at"]
    pipeline.get_report(store, 10, client=_FakeClient(), max_live_age_s=1,
                        allow_write=False)
    assert store.get_report(10)["computed_at"] == before
