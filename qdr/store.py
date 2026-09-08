"""Persistent store for reports, revenue and linkage.

Follows the pattern used by the sibling Qubic projects (qubic_doge_stats,
qubic_spotlight): one embedded, file-backed database inside a mounted volume,
located via DATA_DIR — no server to operate, trivially copyable and archivable.
Those projects use LiteDB because they are .NET; the Python equivalent is SQLite
(stdlib, no new dependency), so we adopt the architecture, not the library.

The rule that shapes everything here (CONCEPT §5.1):

    the CURRENT epoch is live      -> recomputed on every poll, never frozen
    a CLOSED epoch is sealed       -> computed once, then served from the store

An epoch is only sealed when its successor has started AND its revenue derivation
was complete (full pagination + reconciliation). An epoch that closes with gaps
stays 'partial' and is retried, rather than being frozen wrong.

Writes are upserts with dedupe, and a sealed epoch is never silently overwritten:
recomputing under a new code_version goes through backfill, which keeps the prior
computation so a changed number always has a recorded reason.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import __version__

# Same DATA_DIR convention as qdr.client's RPC cache, so a container mounts one
# volume and gets both the raw cache and the store.
_DATA_DIR = os.environ.get("DATA_DIR")
DEFAULT_DB = Path(
    os.environ.get(
        "QDR_DB",
        (Path(_DATA_DIR) / "qdr.db") if _DATA_DIR
        else Path(__file__).resolve().parent.parent / "data" / "qdr.db",
    )
)

# Epoch lifecycle
STATUS_LIVE = "live"        # currently running; recomputed every poll
STATUS_PARTIAL = "partial"  # closed but derivation incomplete -> retry, keep out of headline
STATUS_SEALED = "sealed"    # closed and complete; immutable

SCHEMA = """
CREATE TABLE IF NOT EXISTS epochs (
    epoch           INTEGER PRIMARY KEY,
    status          TEXT NOT NULL,
    first_tick      INTEGER,
    last_tick       INTEGER,
    computor_count  INTEGER,
    first_computed  INTEGER,
    last_computed   INTEGER,
    code_version    TEXT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS computor_revenue (
    epoch       INTEGER NOT NULL,
    identity    TEXT NOT NULL,
    revenue     INTEGER NOT NULL DEFAULT 0,
    first_tick  INTEGER,
    last_tick   INTEGER,
    complete    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (epoch, identity)
);
CREATE INDEX IF NOT EXISTS ix_revenue_epoch ON computor_revenue(epoch);

-- the payout transfers backing that revenue: the audit trail that makes a
-- divergence between two implementations explainable rather than arguable.
CREATE TABLE IF NOT EXISTS transfers (
    tx_id       TEXT PRIMARY KEY,
    epoch       INTEGER NOT NULL,
    tick        INTEGER,
    source_id   TEXT,
    dest_id     TEXT,
    amount      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_transfers_epoch ON transfers(epoch);
CREATE INDEX IF NOT EXISTS ix_transfers_dest ON transfers(dest_id);

CREATE TABLE IF NOT EXISTS clusters (
    epoch       INTEGER NOT NULL,
    cluster_id  TEXT NOT NULL,
    label       TEXT,
    confidence  TEXT,
    computors   TEXT NOT NULL,   -- json array
    revenue     INTEGER NOT NULL DEFAULT 0,
    evidence    TEXT,            -- json array
    PRIMARY KEY (epoch, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_clusters_epoch ON clusters(epoch);

-- payout-graph edges, kept so linkage extends incrementally instead of being
-- recomputed from zero on every run.
CREATE TABLE IF NOT EXISTS linkage (
    identity    TEXT NOT NULL,
    owner_key   TEXT NOT NULL,
    epoch       INTEGER NOT NULL,
    evidence    TEXT,
    PRIMARY KEY (identity, owner_key, epoch)
);
CREATE INDEX IF NOT EXISTS ix_linkage_owner ON linkage(owner_key);
CREATE INDEX IF NOT EXISTS ix_linkage_epoch ON linkage(epoch);

-- Per-computor balance snapshots. Qubic credits computor revenue by protocol-level
-- emission, not by a transfer the RPC exposes (see DATA_SOURCES §6.2), so revenue
-- has to be measured as the delta of these cumulative counters across an epoch
-- boundary. /v1/balances is current-state only, which is exactly why we snapshot.
CREATE TABLE IF NOT EXISTS balance_snapshots (
    identity        TEXT NOT NULL,
    tick            INTEGER NOT NULL,
    epoch           INTEGER,
    balance         INTEGER NOT NULL DEFAULT 0,
    incoming_amount INTEGER NOT NULL DEFAULT 0,
    outgoing_amount INTEGER NOT NULL DEFAULT 0,
    incoming_count  INTEGER NOT NULL DEFAULT 0,
    taken_at        INTEGER NOT NULL,
    PRIMARY KEY (identity, tick)
);
CREATE INDEX IF NOT EXISTS ix_balsnap_epoch ON balance_snapshots(epoch);
CREATE INDEX IF NOT EXISTS ix_balsnap_ident ON balance_snapshots(identity, tick);

CREATE TABLE IF NOT EXISTS reports (
    epoch        INTEGER NOT NULL,
    code_version TEXT NOT NULL,
    payload      TEXT NOT NULL,   -- the finished report json
    status       TEXT NOT NULL,
    computed_at  INTEGER NOT NULL,
    PRIMARY KEY (epoch, code_version)
);
CREATE INDEX IF NOT EXISTS ix_reports_epoch ON reports(epoch);
"""


class Store:
    """SQLite-backed store. Safe for concurrent use from the API and a worker."""

    def __init__(self, path: Path | str = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL lets the API read while a worker writes.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- epochs ------------------------------------------------------------
    def upsert_epoch(
        self,
        epoch: int,
        status: str,
        first_tick: Optional[int] = None,
        last_tick: Optional[int] = None,
        computor_count: Optional[int] = None,
        note: Optional[str] = None,
    ) -> None:
        """Record/refresh an epoch's lifecycle row.

        A sealed epoch stays sealed: once an epoch is complete its status is not
        walked back by a later poll (the same "once correctly set, immutable"
        rule qubic_doge_stats applies to finalized epochs). Use the backfill path
        to redo one deliberately.
        """
        now = int(time.time())
        with self._tx() as c:
            row = c.execute("SELECT status FROM epochs WHERE epoch=?", (epoch,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO epochs (epoch,status,first_tick,last_tick,computor_count,"
                    "first_computed,last_computed,code_version,note) VALUES (?,?,?,?,?,?,?,?,?)",
                    (epoch, status, first_tick, last_tick, computor_count, now, now,
                     __version__, note),
                )
                return
            if row["status"] == STATUS_SEALED and status != STATUS_SEALED:
                return  # never downgrade a sealed epoch
            c.execute(
                "UPDATE epochs SET status=?, first_tick=COALESCE(?,first_tick), "
                "last_tick=COALESCE(?,last_tick), computor_count=COALESCE(?,computor_count), "
                "last_computed=?, code_version=?, note=COALESCE(?,note) WHERE epoch=?",
                (status, first_tick, last_tick, computor_count, now, __version__, note, epoch),
            )

    def get_epoch(self, epoch: int) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM epochs WHERE epoch=?", (epoch,)).fetchone()
        return dict(r) if r else None

    def epoch_status(self, epoch: int) -> Optional[str]:
        e = self.get_epoch(epoch)
        return e["status"] if e else None

    def is_sealed(self, epoch: int) -> bool:
        return self.epoch_status(epoch) == STATUS_SEALED

    def list_epochs(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM epochs ORDER BY epoch DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def seal_epoch(self, epoch: int, note: Optional[str] = None) -> None:
        """Mark a closed, completely derived epoch immutable."""
        now = int(time.time())
        with self._tx() as c:
            c.execute(
                "UPDATE epochs SET status=?, last_computed=?, note=COALESCE(?,note) WHERE epoch=?",
                (STATUS_SEALED, now, note, epoch),
            )

    # -- revenue -----------------------------------------------------------
    def put_revenue(
        self,
        epoch: int,
        revenue: dict[str, int],
        first_tick: Optional[int] = None,
        last_tick: Optional[int] = None,
        complete: bool = False,
    ) -> None:
        """Upsert per-computor revenue for an epoch, with the tick window it came
        from — that window is what lets a third party recompute and locate a
        divergence (CONCEPT §4.3)."""
        if not revenue:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO computor_revenue (epoch,identity,revenue,first_tick,last_tick,complete) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(epoch,identity) DO UPDATE SET "
                "revenue=excluded.revenue, first_tick=excluded.first_tick, "
                "last_tick=excluded.last_tick, complete=excluded.complete",
                [(epoch, i, int(v), first_tick, last_tick, 1 if complete else 0)
                 for i, v in revenue.items()],
            )

    def get_revenue(self, epoch: int) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT identity, revenue FROM computor_revenue WHERE epoch=?", (epoch,)
            ).fetchall()
        return {r["identity"]: r["revenue"] for r in rows}

    def revenue_is_complete(self, epoch: int) -> bool:
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) n, SUM(complete) c FROM computor_revenue WHERE epoch=?", (epoch,)
            ).fetchone()
        return bool(r and r["n"] and r["n"] == (r["c"] or 0))

    # -- transfers (audit trail) -------------------------------------------
    def put_transfers(self, epoch: int, transfers: Iterable[dict]) -> int:
        """Store the payout transfers backing an epoch's revenue. Deduped by tx id."""
        rows = []
        for t in transfers:
            tx_id = t.get("txId") or t.get("id") or t.get("transactionId")
            if not tx_id:
                continue
            rows.append((str(tx_id), epoch, t.get("tick"), t.get("sourceId"),
                         t.get("destId"), int(t.get("amount") or 0)))
        if not rows:
            return 0
        with self._tx() as c:
            c.executemany(
                "INSERT INTO transfers (tx_id,epoch,tick,source_id,dest_id,amount) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(tx_id) DO NOTHING", rows,
            )
        return len(rows)

    def get_transfers(self, epoch: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transfers WHERE epoch=? ORDER BY tick", (epoch,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- clusters ----------------------------------------------------------
    def put_clusters(self, epoch: int, clusters: list) -> None:
        """Replace an epoch's clustering. Clusters are derived, so a rewrite is the
        correct semantic — the epoch row is what guards whether it may happen."""
        with self._tx() as c:
            c.execute("DELETE FROM clusters WHERE epoch=?", (epoch,))
            c.executemany(
                "INSERT INTO clusters (epoch,cluster_id,label,confidence,computors,revenue,evidence) "
                "VALUES (?,?,?,?,?,?,?)",
                [(epoch, cl.cluster_id, cl.label, cl.confidence,
                  json.dumps(cl.computors), int(cl.revenue), json.dumps(cl.evidence))
                 for cl in clusters],
            )

    def get_clusters(self, epoch: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM clusters WHERE epoch=? ORDER BY revenue DESC", (epoch,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["computors"] = json.loads(d["computors"])
            d["evidence"] = json.loads(d["evidence"] or "[]")
            out.append(d)
        return out

    # -- linkage -----------------------------------------------------------
    def put_linkage(self, epoch: int, linkage: dict[str, str], evidence: str = "") -> None:
        """Persist payout-graph edges so linkage accumulates across epochs."""
        if not linkage:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO linkage (identity,owner_key,epoch,evidence) VALUES (?,?,?,?) "
                "ON CONFLICT(identity,owner_key,epoch) DO NOTHING",
                [(i, o, epoch, evidence) for i, o in linkage.items()],
            )

    def get_linkage(self, epoch: Optional[int] = None) -> dict[str, str]:
        """Linkage for one epoch, or the accumulated graph up to it.

        Accumulated is the useful default for clustering: an ownership link proven
        in an earlier epoch does not stop being true later.
        """
        with self._lock:
            if epoch is None:
                rows = self._conn.execute(
                    "SELECT identity, owner_key FROM linkage ORDER BY epoch"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT identity, owner_key FROM linkage WHERE epoch<=? ORDER BY epoch",
                    (epoch,),
                ).fetchall()
        return {r["identity"]: r["owner_key"] for r in rows}

    # -- balance snapshots -------------------------------------------------
    def put_balance_snapshots(self, epoch: int, snapshots: Iterable[dict]) -> int:
        """Record per-identity balance counters at a tick.

        Snapshots are immutable observations, so a repeat at the same tick is a
        no-op rather than an update.
        """
        now = int(time.time())
        rows = [
            (s["identity"], int(s["tick"]), epoch, int(s.get("balance", 0)),
             int(s.get("incoming_amount", 0)), int(s.get("outgoing_amount", 0)),
             int(s.get("incoming_count", 0)), now)
            for s in snapshots if s.get("identity") and s.get("tick") is not None
        ]
        if not rows:
            return 0
        with self._tx() as c:
            c.executemany(
                "INSERT INTO balance_snapshots (identity,tick,epoch,balance,incoming_amount,"
                "outgoing_amount,incoming_count,taken_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(identity,tick) DO NOTHING", rows,
            )
        return len(rows)

    def latest_snapshot_before(self, identity: str, tick: int) -> Optional[dict]:
        """The newest snapshot for an identity at or before `tick`."""
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM balance_snapshots WHERE identity=? AND tick<=? "
                "ORDER BY tick DESC LIMIT 1", (identity, tick),
            ).fetchone()
        return dict(r) if r else None

    def first_snapshot_after(self, identity: str, tick: int) -> Optional[dict]:
        """The oldest snapshot for an identity at or after `tick`."""
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM balance_snapshots WHERE identity=? AND tick>=? "
                "ORDER BY tick ASC LIMIT 1", (identity, tick),
            ).fetchone()
        return dict(r) if r else None

    def snapshot_ticks(self, epoch: Optional[int] = None) -> list[int]:
        with self._lock:
            if epoch is None:
                rows = self._conn.execute(
                    "SELECT DISTINCT tick FROM balance_snapshots ORDER BY tick").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT DISTINCT tick FROM balance_snapshots WHERE epoch=? ORDER BY tick",
                    (epoch,)).fetchall()
        return [r["tick"] for r in rows]

    # -- reports -----------------------------------------------------------
    def put_report(self, epoch: int, payload: dict, status: str) -> None:
        """Store a finished report, keyed by (epoch, code_version).

        Keying on the code version is what makes a changed number explainable: a
        recompute under new logic lands beside the old one instead of erasing it.
        """
        with self._tx() as c:
            c.execute(
                "INSERT INTO reports (epoch,code_version,payload,status,computed_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(epoch,code_version) DO UPDATE SET "
                "payload=excluded.payload, status=excluded.status, computed_at=excluded.computed_at",
                (epoch, __version__, json.dumps(payload), status, int(time.time())),
            )

    def get_report(self, epoch: int, code_version: Optional[str] = None) -> Optional[dict]:
        """Newest report for an epoch (or a specific code version)."""
        with self._lock:
            if code_version:
                r = self._conn.execute(
                    "SELECT * FROM reports WHERE epoch=? AND code_version=?",
                    (epoch, code_version),
                ).fetchone()
            else:
                r = self._conn.execute(
                    "SELECT * FROM reports WHERE epoch=? "
                    "ORDER BY (code_version = ?) DESC, computed_at DESC LIMIT 1",
                    (epoch, __version__),
                ).fetchone()
        if not r:
            return None
        rep = json.loads(r["payload"])
        rep["status"] = r["status"]
        rep["computed_at"] = r["computed_at"]
        rep["code_version"] = r["code_version"]
        return rep

    def latest_report(self, settled_only: bool = True) -> Optional[dict]:
        """The newest report worth showing as *the* report.

        Revenue is only credited when an epoch closes, so the running epoch has a
        real computor list but zero revenue — presenting it as the headline would
        show an empty report while a complete one sits right behind it. By default
        this returns the newest SETTLED epoch; the running epoch is surfaced
        separately (the live pulse), which is what it is good for.
        """
        with self._lock:
            if settled_only:
                r = self._conn.execute(
                    "SELECT epoch FROM reports WHERE status IN (?,?) "
                    "ORDER BY epoch DESC, computed_at DESC LIMIT 1",
                    (STATUS_SEALED, STATUS_PARTIAL),
                ).fetchone()
                if r:
                    return self.get_report(r["epoch"])
                # nothing settled yet (fresh install): fall through to whatever exists
            r = self._conn.execute(
                "SELECT epoch FROM reports ORDER BY epoch DESC, computed_at DESC LIMIT 1"
            ).fetchone()
        return self.get_report(r["epoch"]) if r else None

    def stale_epochs(self, code_version: Optional[str] = None) -> list[int]:
        """Epochs whose newest report was computed by an older code version.

        A correctness fix changes what the right answer is, so an epoch sealed by
        the previous version is not "already done" — it is wrong and still on the
        page. The ingest worker re-derives these on startup; the prior computation
        is kept beside the new one (reports are keyed by (epoch, code_version)),
        so a changed number always has a recorded before and after.
        """
        want = code_version or __version__
        with self._lock:
            rows = self._conn.execute(
                "SELECT epoch, MAX(computed_at) AS t, code_version FROM reports "
                "GROUP BY epoch ORDER BY epoch"
            ).fetchall()
        stale = []
        for r in rows:
            # the row MAX() picked may not be the newest version's, so ask directly
            match = self._conn.execute(
                "SELECT 1 FROM reports WHERE epoch=? AND code_version=? LIMIT 1",
                (r["epoch"], want)).fetchone()
            if not match:
                stale.append(r["epoch"])
        return stale

    def report_epochs(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT epoch FROM reports ORDER BY epoch"
            ).fetchall()
        return [r["epoch"] for r in rows]

    def report_versions(self, epoch: int) -> list[dict]:
        """Every computation we hold for an epoch — the "why did this number change" view."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT code_version, status, computed_at FROM reports WHERE epoch=? "
                "ORDER BY computed_at DESC", (epoch,)
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            def one(q: str, *a) -> int:
                return int(self._conn.execute(q, a).fetchone()[0] or 0)
            return {
                "db": str(self.path),
                "epochs": one("SELECT COUNT(*) FROM epochs"),
                "sealed": one("SELECT COUNT(*) FROM epochs WHERE status=?", STATUS_SEALED),
                "partial": one("SELECT COUNT(*) FROM epochs WHERE status=?", STATUS_PARTIAL),
                "reports": one("SELECT COUNT(*) FROM reports"),
                "transfers": one("SELECT COUNT(*) FROM transfers"),
                "linkage_edges": one("SELECT COUNT(*) FROM linkage"),
                "balance_snapshots": one("SELECT COUNT(*) FROM balance_snapshots"),
            }
