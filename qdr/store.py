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
import tempfile
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

-- Burn totals per scanned tick window (CONCEPT_BURN §4).
--
-- The raw events are deliberately NOT kept: measured at ~900 burns per 500 ticks,
-- an epoch's event log is gigabytes and says nothing the aggregate does not. The
-- tick window is what makes a figure recomputable by a third party — the same
-- role first_tick/last_tick play for revenue.
CREATE TABLE IF NOT EXISTS burn_buckets (
    from_tick       INTEGER NOT NULL,   -- inclusive
    to_tick         INTEGER NOT NULL,   -- inclusive
    epoch           INTEGER NOT NULL,
    day             TEXT NOT NULL,      -- UTC 'YYYY-MM-DD', read off event timestamps
    burned          INTEGER NOT NULL DEFAULT 0,   -- sum of QU_TRANSFER to a burn sink
    burn_events     INTEGER NOT NULL DEFAULT 0,
    contract_burned INTEGER NOT NULL DEFAULT 0,   -- sum of BURNING events
    contract_events INTEGER NOT NULL DEFAULT 0,
    by_contract     TEXT,               -- json {contractIndex: amount}
    scanned_at      INTEGER NOT NULL,
    PRIMARY KEY (from_tick, to_tick, day)
);
CREATE INDEX IF NOT EXISTS ix_burn_epoch ON burn_buckets(epoch);
CREATE INDEX IF NOT EXISTS ix_burn_day   ON burn_buckets(day);

-- A UTC day's real tick span, measured from /v1/ticks/{t}/tick-data rather than
-- assumed from a tick rate. Coverage ("did we scan all of this day?") used to be
-- computed against a constant 86400 * 2.7 ticks; measured over three real days
-- the rate is 1.58 and varies by 29% between days, so that constant flagged
-- fully-scanned days as partial. With the day's own boundaries stored, coverage
-- is a measurement divided by a measurement.
CREATE TABLE IF NOT EXISTS day_ticks (
    day        TEXT PRIMARY KEY,   -- UTC 'YYYY-MM-DD'
    from_tick  INTEGER NOT NULL,   -- first tick whose timestamp falls in the day
    to_tick    INTEGER NOT NULL,   -- last such tick
    complete   INTEGER NOT NULL DEFAULT 0,  -- 1 once the day is over and bounded
    measured_at INTEGER NOT NULL
);

-- The official cumulative counter, sampled at epoch boundaries. This is the
-- anchor the measured buckets reconcile against, and the only figure directly
-- comparable to what explorer.qubic.org publishes. Measured 2026-09-19: it does
-- not move between boundaries, which is precisely why it cannot supply the
-- daily resolution on its own.
CREATE TABLE IF NOT EXISTS burn_totals (
    epoch         INTEGER PRIMARY KEY,
    burned_total  INTEGER NOT NULL,
    circulating   INTEGER,
    tick          INTEGER,
    observed_at   INTEGER NOT NULL
);

-- How far the burn scan has got. Kept as a row rather than derived from
-- MAX(to_tick) so a resume point survives a gap: a tick range Bob could not
-- serve must not look like one we already counted.
CREATE TABLE IF NOT EXISTS burn_scan_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    last_tick       INTEGER NOT NULL,   -- last tick counted (inclusive)
    first_tick      INTEGER NOT NULL,   -- first tick ever counted: where measurement begins
    updated_at      INTEGER NOT NULL
);

-- Burn backfills that have already run to completion.
--
-- A backfill is expensive (hundreds of heavy calls against a public Bob node)
-- and idempotent in its RESULT but not in its COST: `put_burn_bucket` would
-- happily supersede the same buckets with the same numbers on every container
-- restart, re-scanning ticks already counted. On a host whose watcher restarts
-- the image on every push, that turns a one-off catch-up into a recurring load
-- on someone else's node.
--
-- So a completed run is recorded by the window it covered. The worker asks
-- `burn_backfill_done()` before starting and skips a range already filled. A run
-- that stopped at a gap is NOT recorded, because it has more to do.
CREATE TABLE IF NOT EXISTS burn_backfill_runs (
    from_tick   INTEGER NOT NULL,
    to_tick     INTEGER NOT NULL,
    days        INTEGER NOT NULL DEFAULT 0,
    finished_at INTEGER NOT NULL,
    PRIMARY KEY (from_tick, to_tick)
);

-- Live mining state, sampled from a node's peer port (DATA_SOURCES.md §8).
--
-- None of this is on the RPC and none of it is replayable: a node answers what
-- the colony looks like NOW, so a sample not taken is a sample lost forever.
-- That is the whole reason this table exists -- the API's in-process cache holds
-- exactly one reading and forgets it on restart.
--
-- Sampled at the same cadence the API already queries a node (QDR_MINING_TTL,
-- 30s), because a coarser sample would discard readings we had in hand.
CREATE TABLE IF NOT EXISTS mining_samples (
    at               INTEGER PRIMARY KEY,  -- unix ts; one sample per second at most
    epoch            INTEGER NOT NULL,
    tick             INTEGER,
    solution_count   INTEGER,
    threshold        INTEGER,
    free_ann_slots   INTEGER,
    peers_responding INTEGER,
    node_ip          TEXT,
    latency_ms       INTEGER
);
CREATE INDEX IF NOT EXISTS ix_mining_epoch ON mining_samples(epoch, at);

-- One row per epoch, summarising what the samples showed. Samples are pruned to
-- a rolling window; this is not, and it is tiny (~100 bytes per week), so
-- long-run history survives without keeping the detail that produced it. Same
-- split as burn_buckets (detail, windowed) vs burn_totals (anchor, permanent).
CREATE TABLE IF NOT EXISTS mining_epochs (
    epoch           INTEGER PRIMARY KEY,
    final_count     INTEGER,   -- last solution_count seen in the epoch
    peak_count      INTEGER,
    avg_threshold   REAL,
    samples         INTEGER NOT NULL DEFAULT 0,
    first_at        INTEGER,
    last_at         INTEGER,
    updated_at      INTEGER NOT NULL
);

-- Market price, polled every minute from Qubic's own RPC -- one row per PRICE,
-- not one row per poll.
--
-- Source and licensing: `/v1/latest-stats`, the same endpoint this project
-- already reads for supply and tick quality. Exchange APIs were built against
-- first and then removed: MEXC's terms (clause 17d) prohibit "data feeding or
-- streaming services that make use of any market data of MEXC" without written
-- consent, and storing their bars to republish them here is exactly that.
-- Qubic's RPC carries a price it is licensed to publish (its stats service is
-- configured with a CoinGecko token), so reading Qubic's own endpoint keeps
-- this project inside one set of terms -- the ones it already operates under.
--
-- The cost, stated rather than worked around: the RPC publishes NO trading
-- volume, so neither does this table. A volume column filled from elsewhere
-- would be the same licensing problem under another name.
--
-- Why a row per price instead of a row per minute. The upstream scrapes every
-- 60s (qubic-stats-service, SERVICE_DATA_SCRAPE_INTERVAL=1m) but the published
-- figure often holds still across several of those: measured 2026-09-21 it sat
-- unchanged for 2.5 minutes while the endpoint's own `timestamp` advanced on
-- every request. Storing each poll would put ~1440 rows a day on disk, most of
-- them repetitions that a chart would have to collapse again before drawing.
-- So a row is opened when the price MOVES, and the poll that finds it unchanged
-- extends the open row instead.
--
-- That makes every row an interval: `at` is when this price was first seen,
-- `until` when it was last confirmed, and `polls` how many readings stand
-- behind it. `until - at` is how long the price held.
--
-- `polls` is what keeps a quiet market distinguishable from a stopped worker.
-- Without it a flat stretch would be ambiguous -- price steady, or nobody
-- looking? With it, "held 40 minutes over 40 polls" and "a 40-minute gap with
-- no polls at all" are different facts, and the page can draw them differently.
CREATE TABLE IF NOT EXISTS price_points (
    at            INTEGER PRIMARY KEY,  -- unix second the price was FIRST seen
    until         INTEGER NOT NULL,     -- unix second it was last confirmed
    polls         INTEGER NOT NULL DEFAULT 1,  -- readings behind this interval
    price         REAL NOT NULL,        -- USD per QU
    market_cap    INTEGER,
    circulating   INTEGER,              -- supply behind the market cap
    epoch         INTEGER,
    tick          INTEGER,
    rpc_timestamp INTEGER,              -- endpoint's own field: response time, not price time
    fetched_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_price_until ON price_points(until);

-- Hourly summary: the permanent record. Price points are pruned to a rolling
-- window (PRICE_KEEP_DAYS); these are not, so a chart zoomed out to a year keeps
-- drawing at the resolution that survives. Same detail/summary split as
-- burn_buckets vs burn_totals and mining_samples vs mining_epochs.
--
-- open/high/low/close summarise the price points falling in the hour. They are
-- a summary of OUR observations -- not an exchange candle, and the page must not
-- dress them as one. `moves` counts how often the price actually changed in the
-- hour and `polls` how many readings stand behind it, so a quiet hour and a
-- busy one are told apart rather than both drawn as implied activity.
--
-- `avg_price` is time-weighted, not a mean of the points: a price held for 50
-- minutes and one held for 2 are not equal contributions to "the hour's price".
CREATE TABLE IF NOT EXISTS price_hours (
    at          INTEGER PRIMARY KEY,  -- unix second, hour-aligned
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    avg_price   REAL,                 -- time-weighted over the hour
    market_cap  INTEGER,              -- last reading in the hour
    moves       INTEGER NOT NULL DEFAULT 0,  -- price changes within the hour
    polls       INTEGER NOT NULL DEFAULT 0,  -- readings behind those changes
    updated_at  INTEGER NOT NULL
);
"""


class Store:
    """SQLite-backed store. Safe for concurrent use from the API and a worker."""

    def __init__(self, path: Path | str = DEFAULT_DB,
                 allow_fallback: bool = True) -> None:
        self.path = Path(path)
        # Where the store WANTED to live, kept even when it could not: the
        # difference between the two is the whole diagnosis.
        self.configured_path = Path(path)
        self.degraded_reason: Optional[str] = None
        self._lock = threading.RLock()
        try:
            self._conn = self._open(self.path)
        except (sqlite3.Error, OSError) as first:
            # A mount the container cannot write is not repairable from inside
            # the image, and on a host we do not administer nobody may be able
            # to fix it either. Dying takes the whole report down and reports
            # nothing; falling back to a writable path keeps the service up and
            # honest — it serves, it says it is degraded, and /v1/diagnostics
            # names the real path so the condition is visible rather than
            # silently tolerated. History does not survive a restart this way,
            # which is why this is a fallback and never the normal path.
            if not allow_fallback:
                raise
            self.degraded_reason = f"{type(first).__name__}: {first}"
            fallback = self._fallback_path()
            try:
                self._conn = self._open(fallback)
            except (sqlite3.Error, OSError):
                raise first
            self.path = fallback

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL lets the API read while a worker writes.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
        return conn

    @staticmethod
    def _fallback_path() -> Path:
        """A path inside the container, writable regardless of how /data is mounted."""
        return Path(tempfile.gettempdir()) / "qdr-fallback" / "qdr.db"

    @property
    def is_degraded(self) -> bool:
        """True when the store is not where it was configured to be."""
        return self.degraded_reason is not None

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
        if self.epoch_status(epoch) == STATUS_LIVE:
            return False   # still accruing; no stored flag can override that
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
            row = self._conn.execute(
                "SELECT code_version FROM epochs WHERE epoch=?", (r["epoch"],)).fetchone()
            # An older build that touched this epoch after us leaves its version
            # on the epoch row, and it rewrote the unversioned tables too.
            if not match or (row and row["code_version"] != want):
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

    # -- burn --------------------------------------------------------------
    def put_burn_bucket(
        self,
        from_tick: int,
        to_tick: int,
        epoch: int,
        day: str,
        burned: int = 0,
        burn_events: int = 0,
        contract_burned: int = 0,
        contract_events: int = 0,
        by_contract: Optional[dict] = None,
    ) -> None:
        """Record one scanned tick window's burn totals.

        A rescan overwrites rather than accumulating: scanning ticks 100-199
        twice must not double the day's burn. That makes a rescan safe, which is
        what lets the worker retry a window it could not finish.

        Overlapping windows are the harder case, and the one that actually bit:
        the primary key only dedupes an IDENTICAL window, so a catch-up pass
        re-reading 80,812,215-80,829,173 landed beside two earlier buckets inside
        that range and the day's total came out 13.1% high. Any stored bucket the
        new window covers is therefore deleted first — the new, wider measurement
        supersedes it, having counted exactly the same events.

        Partial overlap is deliberately NOT merged: it would mean either dropping
        events or keeping a bucket whose tick window no longer describes what it
        counted, and the window is what makes a figure recomputable by a third
        party. The scan advances a single pointer, so partial overlap does not
        arise in practice; if it ever does, the two buckets stay side by side and
        the coverage figure is what surfaces it.
        """
        with self._tx() as c:
            # drop buckets fully contained in the window being written
            c.execute(
                "DELETE FROM burn_buckets WHERE day=? AND from_tick>=? AND to_tick<=? "
                "AND NOT (from_tick=? AND to_tick=?)",
                (day, int(from_tick), int(to_tick), int(from_tick), int(to_tick)),
            )
            c.execute(
                "INSERT INTO burn_buckets (from_tick,to_tick,epoch,day,burned,burn_events,"
                "contract_burned,contract_events,by_contract,scanned_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(from_tick,to_tick,day) DO UPDATE SET "
                "epoch=excluded.epoch, burned=excluded.burned, "
                "burn_events=excluded.burn_events, "
                "contract_burned=excluded.contract_burned, "
                "contract_events=excluded.contract_events, "
                "by_contract=excluded.by_contract, scanned_at=excluded.scanned_at",
                (int(from_tick), int(to_tick), int(epoch), day, int(burned),
                 int(burn_events), int(contract_burned), int(contract_events),
                 json.dumps(by_contract or {}), int(time.time())),
            )

    def burn_series(self, by: str = "day", limit: Optional[int] = None) -> list[dict]:
        """Measured burn totals grouped by day, epoch or year.

        Day/epoch/year are all a GROUP BY over the one bucket table — nothing is
        stored three times. Newest rows win when a limit applies, but the result
        is returned oldest-first so a chart can draw it directly.
        """
        key = {"day": "day", "epoch": "epoch",
               "year": "substr(day,1,4)"}.get(by)
        if key is None:
            raise ValueError(f"unsupported grouping: {by!r}")
        sql = (f"SELECT {key} AS key, SUM(burned) AS burned, "
               "SUM(burn_events) AS events, SUM(contract_burned) AS contract_burned, "
               "SUM(contract_events) AS contract_events, "
               "MIN(from_tick) AS from_tick, MAX(to_tick) AS to_tick, "
               "MIN(epoch) AS first_epoch, MAX(epoch) AS last_epoch "
               f"FROM burn_buckets GROUP BY {key} ORDER BY key DESC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["key"] = str(r["key"])
        return list(reversed(out))

    def burn_contract_window(self, epoch: Optional[int] = None) -> dict:
        """The span the contract-attributed sum actually covers.

        Without this the panel's total invites the one misreading it must not
        invite: that it is a lifetime figure comparable to the header's
        cumulative counter. It is not — it covers the days this project has been
        scanning, which is a handful against the chain's whole history. Reported
        from the buckets themselves rather than a constant, so it stays true as
        the scan grows.

        Only buckets that actually carry a contract burn count: a day scanned
        with no BURNING event in it says nothing about contract burns, and
        stretching the window over it would overstate the coverage.
        """
        sql = ("SELECT MIN(day) AS first_day, MAX(day) AS last_day, "
               "MIN(epoch) AS first_epoch, MAX(epoch) AS last_epoch, "
               "MIN(from_tick) AS first_tick, MAX(to_tick) AS last_tick, "
               "COUNT(*) AS days "
               "FROM burn_buckets WHERE contract_burned > 0")
        params: tuple = ()
        if epoch is not None:
            sql += " AND epoch = ?"
            params = (int(epoch),)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        if not row or row["first_day"] is None:
            return {"first_day": None, "last_day": None, "first_epoch": None,
                    "last_epoch": None, "first_tick": None, "last_tick": None,
                    "days": 0}
        return {k: row[k] for k in ("first_day", "last_day", "first_epoch",
                                    "last_epoch", "first_tick", "last_tick",
                                    "days")}

    def burn_by_contract(self, epoch: Optional[int] = None) -> dict[str, int]:
        """Contract-attributed burns, summed per contract index.

        Stored as a json blob per bucket (the set of indices is small and sparse),
        so this folds them in Python rather than with a json1 query that would tie
        the store to an optional SQLite extension.
        """
        with self._lock:
            if epoch is None:
                rows = self._conn.execute(
                    "SELECT by_contract FROM burn_buckets").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT by_contract FROM burn_buckets WHERE epoch=?", (epoch,)).fetchall()
        totals: dict[str, int] = {}
        for r in rows:
            try:
                blob = json.loads(r["by_contract"] or "{}")
            except ValueError:
                continue
            for idx, amount in blob.items():
                totals[str(idx)] = totals.get(str(idx), 0) + int(amount or 0)
        return totals

    def put_burn_total(self, epoch: int, burned_total: int,
                       circulating: Optional[int] = None,
                       tick: Optional[int] = None) -> None:
        """Sample the official cumulative counter for an epoch.

        An epoch's closing total does not change once observed, so a repeat
        observation of an epoch already on record is a no-op — re-reading a stale
        RPC value must not walk a recorded total backwards.
        """
        with self._tx() as c:
            c.execute(
                "INSERT INTO burn_totals (epoch,burned_total,circulating,tick,observed_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(epoch) DO NOTHING",
                (int(epoch), int(burned_total),
                 int(circulating) if circulating is not None else None,
                 int(tick) if tick is not None else None, int(time.time())),
            )

    def burn_totals(self, limit: Optional[int] = None) -> list[dict]:
        """Official cumulative totals per epoch, oldest first."""
        sql = "SELECT * FROM burn_totals ORDER BY epoch DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [dict(r) for r in reversed(rows)]

    def latest_burn_total(self) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM burn_totals ORDER BY epoch DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def set_burn_scan_state(self, last_tick: int, first_tick: Optional[int] = None) -> None:
        """Advance the scan pointer. `first_tick` is only ever set once — it marks
        where our own measurement begins, and the series is labelled against it."""
        now = int(time.time())
        with self._tx() as c:
            row = c.execute("SELECT first_tick FROM burn_scan_state WHERE id=1").fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO burn_scan_state (id,last_tick,first_tick,updated_at) "
                    "VALUES (1,?,?,?)",
                    (int(last_tick),
                     int(first_tick if first_tick is not None else last_tick), now),
                )
            else:
                c.execute(
                    "UPDATE burn_scan_state SET last_tick=?, updated_at=? WHERE id=1",
                    (int(last_tick), now),
                )

    def mark_burn_backfill(self, from_tick: int, to_tick: int, days: int = 0) -> None:
        """Record that a backfill covered this tick window to completion.

        Only call this for a run that finished without stopping at a gap: the
        point of the record is to let the next start skip work already done, and
        a partial run has work left.
        """
        with self._tx() as c:
            c.execute(
                "INSERT INTO burn_backfill_runs (from_tick,to_tick,days,finished_at) "
                "VALUES (?,?,?,?) ON CONFLICT(from_tick,to_tick) DO UPDATE SET "
                "days=excluded.days, finished_at=excluded.finished_at",
                (int(from_tick), int(to_tick), int(days), int(time.time())),
            )

    def burn_backfill_done(self, from_tick: int, to_tick: int) -> bool:
        """True when a completed run already covers this whole window.

        Containment, not equality: a later start computes a slightly different
        `from_tick` every time (it is derived from the current head), so an exact
        match would never hit and the backfill would re-run on every restart.
        """
        with self._lock:
            r = self._conn.execute(
                "SELECT 1 FROM burn_backfill_runs "
                "WHERE from_tick <= ? AND to_tick >= ? LIMIT 1",
                (int(from_tick), int(to_tick)),
            ).fetchone()
        return r is not None

    def burn_backfill_runs(self) -> list[dict]:
        """Completed backfill windows, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM burn_backfill_runs ORDER BY finished_at DESC").fetchall()
        return [dict(r) for r in rows]

    def put_day_ticks(self, day: str, from_tick: int, to_tick: int,
                      complete: bool = False) -> None:
        """Record a UTC day's measured tick span.

        Widening only: a later pass that sees more of the day extends the span,
        it never narrows it. A day is `complete` once its end was found by
        measurement (the first tick of the next day exists), which is what lets
        coverage distinguish "we scanned the whole day" from "the day is still
        running".
        """
        with self._tx() as c:
            c.execute(
                "INSERT INTO day_ticks (day,from_tick,to_tick,complete,measured_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET "
                "from_tick=MIN(from_tick,excluded.from_tick), "
                "to_tick=MAX(to_tick,excluded.to_tick), "
                "complete=MAX(complete,excluded.complete), "
                "measured_at=excluded.measured_at",
                (day, int(from_tick), int(to_tick), 1 if complete else 0,
                 int(time.time())),
            )

    def day_ticks(self) -> dict[str, dict]:
        """Measured tick spans per day, keyed by 'YYYY-MM-DD'."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT day,from_tick,to_tick,complete FROM day_ticks").fetchall()
        return {r["day"]: dict(r) for r in rows}

    def burn_scan_state(self) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM burn_scan_state WHERE id=1").fetchone()
        return dict(r) if r else None

    # ---- mining samples ----------------------------------------------------
    # How many epochs of raw samples to keep. Two, not one, because
    # solution_count resets to zero at an epoch boundary: a one-epoch window
    # would delete the previous peak at exactly the moment the drop appears,
    # leaving a curve that starts near zero with nothing explaining why.
    MINING_KEEP_EPOCHS = 2

    def put_mining_sample(
        self,
        at: int,
        epoch: int,
        tick: Optional[int] = None,
        solution_count: Optional[int] = None,
        threshold: Optional[int] = None,
        free_ann_slots: Optional[int] = None,
        peers_responding: Optional[int] = None,
        node_ip: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> None:
        """Record one reading of live mining state, and fold it into the epoch row.

        Two samples landing in the same second are the same reading twice (the
        API caches for 30s), so the later one simply wins rather than doubling
        the epoch's sample count.
        """
        def _i(v):
            return int(v) if v is not None else None

        with self._tx() as c:
            c.execute(
                "INSERT INTO mining_samples (at,epoch,tick,solution_count,threshold,"
                "free_ann_slots,peers_responding,node_ip,latency_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(at) DO UPDATE SET "
                "epoch=excluded.epoch, tick=excluded.tick, "
                "solution_count=excluded.solution_count, threshold=excluded.threshold, "
                "free_ann_slots=excluded.free_ann_slots, "
                "peers_responding=excluded.peers_responding, "
                "node_ip=excluded.node_ip, latency_ms=excluded.latency_ms",
                (int(at), int(epoch), _i(tick), _i(solution_count), _i(threshold),
                 _i(free_ann_slots), _i(peers_responding), node_ip, _i(latency_ms)),
            )
            # The summary is recomputed from the samples still present rather
            # than incremented, so a re-written second cannot drift it. It is
            # one indexed scan over a single epoch's rows.
            c.execute(
                "INSERT INTO mining_epochs "
                "(epoch,final_count,peak_count,avg_threshold,samples,"
                " first_at,last_at,updated_at) "
                "SELECT ?, "
                "  (SELECT solution_count FROM mining_samples "
                "    WHERE epoch=? AND solution_count IS NOT NULL "
                "    ORDER BY at DESC LIMIT 1), "
                "  MAX(solution_count), AVG(threshold), COUNT(*), "
                "  MIN(at), MAX(at), ? "
                "FROM mining_samples WHERE epoch=? "
                "ON CONFLICT(epoch) DO UPDATE SET "
                "  final_count=excluded.final_count, "
                # A pruned window can only ever lower MAX(solution_count), and the
                # epoch's real peak is a fact we already observed -- so the stored
                # peak is kept whenever it is the higher of the two.
                "  peak_count=MAX(COALESCE(mining_epochs.peak_count,0), "
                "                 COALESCE(excluded.peak_count,0)), "
                "  avg_threshold=excluded.avg_threshold, "
                "  samples=excluded.samples, "
                "  first_at=MIN(COALESCE(mining_epochs.first_at, excluded.first_at), "
                "               excluded.first_at), "
                "  last_at=excluded.last_at, updated_at=excluded.updated_at",
                (int(epoch), int(epoch), int(time.time()), int(epoch)),
            )

    def mining_series(
        self,
        epoch: Optional[int] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Stored mining samples, oldest first so a chart can draw them directly.

        `limit` keeps the most RECENT samples (then reverses), matching
        burn_series: a windowed read should show now, not the start of history.
        """
        where, args = [], []
        if epoch is not None:
            where.append("epoch=?")
            args.append(int(epoch))
        if since is not None:
            where.append("at>=?")
            args.append(int(since))
        sql = "SELECT * FROM mining_samples"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in reversed(rows)]

    def mining_epochs(self, limit: Optional[int] = None) -> list[dict]:
        """Per-epoch mining summaries, oldest first. Never pruned."""
        sql = "SELECT * FROM mining_epochs ORDER BY epoch DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [dict(r) for r in reversed(rows)]

    def latest_mining_sample(self) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM mining_samples ORDER BY at DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def prune_mining(self, keep_epochs: Optional[int] = None) -> int:
        """Drop samples older than the newest `keep_epochs` epochs. Returns rows deleted.

        Only the raw samples go; mining_epochs keeps the summary of every epoch
        ever sampled, so pruning costs resolution, never history.
        """
        keep = int(keep_epochs if keep_epochs is not None else self.MINING_KEEP_EPOCHS)
        if keep < 1:
            raise ValueError("keep_epochs must be at least 1")
        with self._tx() as c:
            row = c.execute(
                "SELECT epoch FROM mining_samples GROUP BY epoch "
                "ORDER BY epoch DESC LIMIT 1 OFFSET ?", (keep - 1,)).fetchone()
            if row is None:
                return 0    # fewer epochs on record than we keep: nothing to do
            cur = c.execute("DELETE FROM mining_samples WHERE epoch < ?", (int(row[0]),))
            return cur.rowcount or 0

    # -- market price ------------------------------------------------------
    # How long price points are kept. Hourly rows are permanent, so this costs
    # resolution and never history. A row is written per price CHANGE, not per
    # poll, so 90 days is a few thousand rows rather than 130k.
    PRICE_KEEP_DAYS = 90

    # Prices are compared as exact floats. Both sides come from the same JSON
    # field through the same parser, so an unchanged figure round-trips bit for
    # bit; an epsilon here would silently swallow a real move of the last digit
    # (the price is ~4e-7, and its smallest published step is 1e-10).
    def put_price_reading(self, at: int, price: float,
                          market_cap: Optional[int] = None,
                          circulating: Optional[int] = None,
                          epoch: Optional[int] = None,
                          tick: Optional[int] = None,
                          rpc_timestamp: Optional[int] = None) -> dict:
        """Record one reading. Opens a new interval, or extends the open one.

        This is the shape the price data actually has: the upstream refreshes
        every 60s but the published figure often holds across several of those,
        so a row per poll would be mostly repetition. A row is opened when the
        price moves and extended while it does not.

        Returns the stored row plus `changed`: True when this reading opened a
        new interval, False when it extended the current one. Callers use that
        to log "held at X" rather than claiming a fresh measurement.

        A reading older than the open interval's start is ignored -- clocks and
        retries can deliver one out of order, and rewriting history backwards
        would corrupt an interval that was correct when it was written.
        """
        at = int(at)
        price = float(price)
        cap = int(market_cap) if market_cap is not None else None
        now = int(time.time())

        def _i(v):
            return int(v) if v is not None else None

        with self._tx() as c:
            cur = c.execute(
                "SELECT * FROM price_points ORDER BY at DESC LIMIT 1").fetchone()

            if cur is not None and at < int(cur["at"]):
                return {**dict(cur), "changed": False, "ignored": "out of order"}

            same = (cur is not None
                    and float(cur["price"]) == price
                    and (cur["market_cap"] or 0) == (cap or 0))
            if same:
                # Extend: the price is still this one, and one more reading
                # stands behind it. `until` never moves backwards.
                c.execute(
                    "UPDATE price_points SET until=?, polls=polls+1, "
                    "epoch=COALESCE(?,epoch), tick=COALESCE(?,tick), "
                    "rpc_timestamp=COALESCE(?,rpc_timestamp), fetched_at=? "
                    "WHERE at=?",
                    (max(at, int(cur["until"])), _i(epoch), _i(tick),
                     _i(rpc_timestamp), now, int(cur["at"])),
                )
                row = dict(c.execute("SELECT * FROM price_points WHERE at=?",
                                     (int(cur["at"]),)).fetchone())
                return {**row, "changed": False}

            # A new price: close nothing (the previous row's `until` already
            # says when it was last confirmed) and open an interval here.
            c.execute(
                "INSERT INTO price_points (at,until,polls,price,market_cap,"
                "circulating,epoch,tick,rpc_timestamp,fetched_at) "
                "VALUES (?,?,1,?,?,?,?,?,?,?) "
                "ON CONFLICT(at) DO UPDATE SET "
                "until=excluded.until, price=excluded.price, "
                "market_cap=excluded.market_cap, circulating=excluded.circulating, "
                "epoch=excluded.epoch, tick=excluded.tick, "
                "rpc_timestamp=excluded.rpc_timestamp, fetched_at=excluded.fetched_at",
                (at, at, price, cap, _i(circulating), _i(epoch), _i(tick),
                 _i(rpc_timestamp), now),
            )
            row = dict(c.execute("SELECT * FROM price_points WHERE at=?",
                                 (at,)).fetchone())
            return {**row, "changed": True}

    def price_series(self, resolution: str = "point",
                     since: Optional[int] = None, until: Optional[int] = None,
                     limit: Optional[int] = None) -> list[dict]:
        """Stored price history, oldest first so a chart can draw it directly.

        Each point row is an interval: `at` when the price was first seen,
        `until` when it was last confirmed, `polls` how many readings back it.
        A chart draws these as steps -- the price held that value throughout, so
        interpolating between two points would invent motion that did not occur.

        `since` selects intervals that OVERLAP the window, not only those
        starting in it: a price set an hour before the window and still standing
        is part of what the window shows, and dropping it would leave the chart
        with no value at its left edge.

        `limit` keeps the most RECENT rows (then reverses), matching burn_series
        and mining_series: a windowed read shows now, not the start of history.
        """
        table = "price_hours" if resolution in ("hour", "hourly") else "price_points"
        where, args = [], []
        if since is not None:
            if table == "price_points":
                where.append("until>=?")
            else:
                where.append("at>=?")
            args.append(int(since))
        if until is not None:
            where.append("at<=?")
            args.append(int(until))
        sql = f"SELECT * FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in reversed(rows)]

    def latest_price(self) -> Optional[dict]:
        """The open interval: the price standing right now."""
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM price_points ORDER BY at DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    # How far past an interval's `until` a reading is still taken to describe a
    # moment. One sampling interval: the price held at `until`, and the next
    # poll a minute later is what would have revealed a change. Beyond that we
    # genuinely were not looking, and saying so is the point.
    PRICE_AT_SLACK_S = 90

    def price_at(self, t: int) -> Optional[dict]:
        """The price standing at moment `t`, or None if we were not watching.

        The interval shape makes this a lookup rather than a nearest-neighbour
        search: exactly one row can satisfy `at <= t <= until`.

        None is returned for an unobserved moment rather than the closest
        reading. Answering "what was the price then?" with a figure measured an
        hour either side would dress a guess as a measurement, which is the one
        thing this store exists not to do.
        """
        t = int(t)
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM price_points WHERE at<=? AND until>=? "
                "ORDER BY at DESC LIMIT 1", (t, t)).fetchone()
            if r is None:
                # Between two intervals, or just past the newest one: accept the
                # preceding interval if `t` falls inside one sampling slack of
                # where it was last confirmed.
                r = self._conn.execute(
                    "SELECT * FROM price_points WHERE until<=? AND until>=? "
                    "ORDER BY until DESC LIMIT 1",
                    (t, t - self.PRICE_AT_SLACK_S)).fetchone()
        return dict(r) if r else None

    def price_epoch_boundaries(self, since: Optional[int] = None) -> list[dict]:
        """When the epoch number changed, as OUR readings observed it.

        Derived from the price samples themselves rather than from a tick
        timestamp, for two reasons. It is a measurement we took -- the minute we
        first saw the new epoch -- rather than a figure fetched from elsewhere.
        And it still works for the running epoch, whose start tick a node may no
        longer be able to date (Bob's log retention is finite; measured
        2026-09-21, the current epoch's first tick already answered "unknown").

        The boundary carries a `within_s`: we know it fell between the last
        reading of the old epoch and the first of the new one, and that window
        is as precise as our sampling was. A gap in sampling widens it, and the
        page shows the window rather than a false exact minute.
        """
        where, args = [], []
        if since is not None:
            where.append("until>=?")
            args.append(int(since))
        sql = "SELECT at, until, epoch FROM price_points"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY at ASC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()

        out: list[dict] = []
        prev = None
        for r in rows:
            e = r["epoch"]
            if e is None:
                continue
            if prev is not None and e != prev["epoch"]:
                out.append({
                    "epoch": int(e),
                    "from_epoch": int(prev["epoch"]),
                    # First moment the new epoch was seen, and the last the old
                    # one was: the change happened somewhere in between.
                    "at": int(r["at"]),
                    "after": int(prev["until"]),
                    "within_s": int(r["at"]) - int(prev["until"]),
                })
            prev = {"epoch": e, "until": r["until"]}
        return out

    def price_around(self, t: int, before_s: int = 43200,
                     after_s: int = 43200) -> list[dict]:
        """Price intervals overlapping a window centred on `t`.

        Used for the epoch-payout study: what the price did either side of a
        boundary. Returns the raw intervals, so the caller can see the gaps
        rather than receiving a smoothed series that hides them.
        """
        t = int(t)
        return self.price_series(since=t - int(before_s), until=t + int(after_s))

    def price_days(self, days: Optional[int] = None,
                   until: Optional[int] = None) -> list[dict]:
        """One row per UTC day, folded from the permanent hourly summary.

        Built from `price_hours` rather than `price_points` on purpose: points
        are pruned to a rolling window, hours are not. A moving average over
        tens of days has to survive that pruning, or it would quietly shorten
        its own reach as history ages.

        Each day's price is the mean of its hourly time-weighted averages,
        weighted by nothing further: an hour is an hour. `hours` says how many
        of the day's 24 are actually on record, which is what separates a day
        we watched from one we only caught the end of. The caller decides what
        to do with a thin day -- this method does not silently drop it, because
        a gap in the record is information.

        The newest day is normally still running and therefore incomplete;
        `complete` marks it, the same distinction burn_days draws.
        """
        where, args = [], []
        if until is not None:
            where.append("at<=?")
            args.append(int(until))
        if days is not None:
            # Reach back a little further than asked: the oldest day in range
            # would otherwise be cut mid-day by the cutoff and report a price
            # built from a handful of hours.
            cutoff = int(until if until is not None else time.time()) - (int(days) + 1) * 86400
            where.append("at>=?")
            args.append(cutoff)
        sql = "SELECT at, avg_price, close, high, low FROM price_hours"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY at ASC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()

        buckets: dict[int, dict] = {}
        for r in rows:
            price = r["avg_price"] if r["avg_price"] is not None else r["close"]
            if price is None or price <= 0:
                continue
            day = (int(r["at"]) // 86400) * 86400
            b = buckets.setdefault(day, {"at": day, "sum": 0.0, "hours": 0,
                                         "high": price, "low": price,
                                         "close": price, "close_at": int(r["at"])})
            b["sum"] += float(price)
            b["hours"] += 1
            if r["high"] is not None and r["high"] > b["high"]:
                b["high"] = float(r["high"])
            if r["low"] is not None and 0 < r["low"] < b["low"]:
                b["low"] = float(r["low"])
            if int(r["at"]) >= b["close_at"]:
                b["close"] = float(price)
                b["close_at"] = int(r["at"])

        today = (int(time.time()) // 86400) * 86400
        out = []
        for day in sorted(buckets):
            b = buckets[day]
            out.append({
                "at": day,
                "avg_price": b["sum"] / b["hours"],
                "close": b["close"],
                "high": b["high"],
                "low": b["low"],
                "hours": b["hours"],
                "complete": 1 if (day < today and b["hours"] >= 24) else 0,
            })
        if days is not None:
            out = out[-int(days):]
        return out

    def price_coverage(self) -> dict:
        """What the price store holds, and how continuously it was observed.

        `polls` vs. elapsed time is what separates a quiet market from a stopped
        worker: intervals covering 6 hours with 360 polls were watched the whole
        time, the same span with 12 polls was not. The page needs that to draw a
        flat stretch as "steady" rather than "unknown".

        `detail_from` is where point resolution begins. It is not a constant:
        this data has no backfill (the RPC serves only "now"), so detail starts
        when the worker first ran and the boundary moves as history accumulates.
        """
        with self._lock:
            pt = self._conn.execute(
                "SELECT COUNT(*) n, MIN(at) lo, MAX(until) hi, SUM(polls) p "
                "FROM price_points").fetchone()
            hr = self._conn.execute(
                "SELECT COUNT(*) n, MIN(at) lo, MAX(at) hi FROM price_hours").fetchone()
        points = int(pt["n"] or 0)
        polls = int(pt["p"] or 0)
        lo = int(pt["lo"]) if pt["lo"] is not None else None
        hi = int(pt["hi"]) if pt["hi"] is not None else None
        span = (hi - lo) if (lo is not None and hi is not None) else 0
        return {
            "keep_days": self.PRICE_KEEP_DAYS,
            "point": {
                "points": points, "polls": polls,
                "first_at": lo, "last_at": hi, "span_s": span,
                # Share of the observed span actually covered by a poll, at the
                # nominal one-per-minute rate. Below 1.0 means gaps.
                #
                # The expected count is span/60 + 1, not span/60: n readings a
                # minute apart span (n-1) minutes, because the span measures the
                # gaps between them and the count measures their endpoints.
                # Without the +1 a perfectly continuous watch reports 1.5 at
                # three readings and only approaches 1.0 after hours -- so a
                # fresh, healthy sampler would look over-polled and the page
                # could not use this to tell steady from unobserved.
                "poll_ratio": round(polls / (span / 60 + 1), 3) if span >= 60 else None,
            },
            "hour": {"hours": int(hr["n"] or 0),
                     "first_at": int(hr["lo"]) if hr["lo"] is not None else None,
                     "last_at": int(hr["hi"]) if hr["hi"] is not None else None},
            "detail_from": lo,
        }

    def rollup_price_hours(self, since: Optional[int] = None) -> int:
        """Fold price intervals into hourly rows. Returns hours written.

        Only hours that are OVER are folded: an hour still running would be
        stored as if it were whole, the same error the burn page refuses for
        partial days.

        An interval spanning an hour boundary contributes to BOTH hours -- the
        price really did stand in each of them. That is why this is computed in
        Python rather than one GROUP BY: a row does not belong to a single hour.
        """
        hour_now = (int(time.time()) // 3600) * 3600
        where = ["at < ?"]
        args: list = [hour_now]
        if since is not None:
            where.append("until >= ?")
            args.append(int(since))
        sql = ("SELECT * FROM price_points WHERE " + " AND ".join(where)
               + " ORDER BY at ASC")
        with self._lock:
            rows = [dict(r) for r in self._conn.execute(sql, args).fetchall()]
        if not rows:
            return 0

        # Each hour collects the intervals overlapping it, clipped to the hour.
        #
        # An interval's true end is where the NEXT one begins, not its own last
        # confirmation: a price confirmed once at 14:50 and replaced at 15:00
        # stood for those ten minutes, and weighting it by `until - at` (zero
        # seconds) would let a long-held price be outweighed by a briefly-held
        # one. The final interval has no successor, so it ends at its own
        # `until` -- we cannot claim it stood past the last time we looked.
        hours: dict[int, dict] = {}
        for i, r in enumerate(rows):
            start = int(r["at"])
            nxt = int(rows[i + 1]["at"]) if i + 1 < len(rows) else None
            if nxt is not None:
                # Superseded: it demonstrably stood until the next price began.
                effective_end = max(int(r["until"]), nxt - 1)
            else:
                # The last interval in the batch has no successor. It stood at
                # least until its final confirmation; beyond that nobody looked,
                # so it is credited one poll interval past it and no further.
                # Claiming it held to the end of the hour would assert a stretch
                # we never observed -- the same error as drawing a partial day
                # as a whole one.
                effective_end = int(r["until"]) + 60
            stop = min(effective_end, hour_now - 1)
            if stop < start:
                continue
            h = (start // 3600) * 3600
            while h <= (stop // 3600) * 3600 and h < hour_now:
                lo = max(start, h)
                hi = min(stop, h + 3599)
                held = max(1, hi - lo)      # a point seen once still occupies a moment
                e = hours.setdefault(h, {
                    "open": r["price"], "close": r["price"],
                    "high": r["price"], "low": r["price"],
                    "weighted": 0.0, "seconds": 0, "moves": 0, "polls": 0,
                    "market_cap": r["market_cap"], "first": start, "last": start,
                })
                if start <= e["first"]:
                    e["first"], e["open"] = start, r["price"]
                if start >= e["last"]:
                    e["last"], e["close"] = start, r["price"]
                    e["market_cap"] = r["market_cap"]
                e["high"] = max(e["high"], r["price"])
                e["low"] = min(e["low"], r["price"])
                e["weighted"] += r["price"] * held
                e["seconds"] += held
                e["moves"] += 1
                # Polls are attributed to the hour that holds most of the
                # interval rather than split: they are a count of readings, and
                # half a reading is not a thing.
                if lo == start:
                    e["polls"] += int(r["polls"] or 1)
                h += 3600

        now = int(time.time())
        with self._tx() as c:
            for h, e in hours.items():
                avg = (e["weighted"] / e["seconds"]) if e["seconds"] else e["close"]
                c.execute(
                    "INSERT INTO price_hours (at,open,high,low,close,avg_price,"
                    "market_cap,moves,polls,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(at) DO UPDATE SET "
                    "open=excluded.open, high=excluded.high, low=excluded.low, "
                    "close=excluded.close, avg_price=excluded.avg_price, "
                    "market_cap=excluded.market_cap, moves=excluded.moves, "
                    "polls=excluded.polls, updated_at=excluded.updated_at",
                    (h, e["open"], e["high"], e["low"], e["close"], avg,
                     e["market_cap"], e["moves"], e["polls"], now),
                )
        return len(hours)

    def prune_price(self, keep_days: Optional[int] = None) -> int:
        """Drop price points older than the window. Returns rows deleted.

        An interval is dropped only once it ENDED before the cutoff: one that
        began earlier but still stands is the current price, and deleting it
        would leave the chart with no value at all.

        Hourly rows are never pruned, so this costs resolution and never reach.
        """
        keep = int(keep_days if keep_days is not None else self.PRICE_KEEP_DAYS)
        if keep < 1:
            raise ValueError("keep_days must be at least 1")
        cutoff = int(time.time()) - keep * 86400
        with self._tx() as c:
            return c.execute("DELETE FROM price_points WHERE until < ?",
                             (cutoff,)).rowcount or 0

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
                "burn_buckets": one("SELECT COUNT(*) FROM burn_buckets"),
                "burn_totals": one("SELECT COUNT(*) FROM burn_totals"),
                "mining_samples": one("SELECT COUNT(*) FROM mining_samples"),
                "mining_epochs": one("SELECT COUNT(*) FROM mining_epochs"),
                "price_points": one("SELECT COUNT(*) FROM price_points"),
                "price_hours": one("SELECT COUNT(*) FROM price_hours"),
            }
