"""Decentralization Report API.

Serves the computed report as JSON for explorers to embed. Read-only, CORS-open,
versioned under /v1.

Reads go through the persistent store (CONCEPT §5.1), not a rebuild-per-request:

  * a SEALED epoch is served straight from disk — immutable, no RPC call, and
    available for *any* epoch we ever computed, not just the last snapshot;
  * the LIVE (current) epoch is refreshed from the RPC when the stored copy is
    older than QDR_LIVE_MAX_AGE seconds, so the running epoch is always current
    without hammering the RPC once per request.

If the store is empty (fresh install) and the RPC is unreachable, the bundled
an empty store answers 503 rather than serving example figures, so nothing this
API returns is ever anything but measured.

Run:
    pip install -r requirements.txt
    uvicorn api.server:app --reload --port 8000

Then e.g. GET http://localhost:8000/v1/report/latest
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as e:  # pragma: no cover
    raise SystemExit("Install deps first: pip install -r requirements.txt") from e

from qdr import __version__ as QDR_VERSION
from qdr import pipeline
from qdr.client import CachedClient, QubicRPCError
from qdr.clustering import load_registry
from qdr.store import STATUS_SEALED, Store

ROOT = Path(__file__).resolve().parent.parent

# How stale the live epoch may get before a request triggers a recompute.
LIVE_MAX_AGE_S = int(os.environ.get("QDR_LIVE_MAX_AGE", "300"))

# Measured 2026-09-07: ~2.7 ticks/s. Epoch LENGTH varies a lot though (1.08M-2.29M
# ticks over epochs 223-229), so the pulse derives the expected length from recent
# history; this constant is only the fallback when that lookup fails.
TYPICAL_EPOCH_TICKS = int(os.environ.get("QDR_EPOCH_TICKS", "1400000"))
TICKS_PER_SECOND = 2.7

app = FastAPI(
    title="Qubic Decentralization Report API",
    version=QDR_VERSION,
    description=(
        "Read-only JSON API measuring how decentralized Qubic's 676 Computors are.\n\n"
        "CORS is open, everything is versioned under `/v1`, and every response names its "
        "inputs so consumers can show provenance.\n\n"
        "**History is persistent.** Closed epochs are sealed and served from the store; "
        "the current epoch is recomputed live. Each response carries `status` "
        "(`sealed` / `partial` / `live`), `computed_at` and `code_version`.\n\n"
        "**On-chain linkage is the primary attribution layer** — `unattributed` means the "
        "ledger showed no link, not that no self-report exists. See `linkage_coverage` on "
        "every report for how much of the network is actually resolved.\n\n"
        "The reference dashboard is served at [`/dashboard/`](/dashboard/)."
    ),
    openapi_tags=[
        {"name": "report", "description": "Per-epoch decentralization reports and snapshots."},
        {"name": "metrics", "description": "Concentration indices over time, for charts."},
        {"name": "burn", "description": "Burned supply: the official total, and a per-day "
                                        "measurement counted from a Bob node's logs."},
        {"name": "service", "description": "Index, health and store status."},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_store: Store | None = None


def get_store() -> Store:
    """Lazily open the store; one connection for the process (WAL, thread-safe)."""
    global _store
    if _store is None:
        _store = Store()
    return _store


def get_client() -> CachedClient | None:
    """An RPC client, or None when the network is unavailable.

    Returning None (instead of raising) is what lets every endpoint degrade to
    store-only mode cleanly.
    """
    try:
        return CachedClient()
    except Exception:
        return None


# There is deliberately no sample fallback. Example figures that are shaped like
# real ones are indistinguishable from measurements once rendered, and this report
# is read as a statement about the network. An empty store is a normal state (a
# fresh deployment, before the ingest worker's first pass) and is reported as such:
# 503 with a reason, so the dashboard can say "building" instead of showing numbers
# nobody measured.
def _no_data(what: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(f"no {what} yet — the store is still being filled. "
                "Run scripts/ingest.py (the ingest service does this automatically)."),
    )


@app.on_event("shutdown")
def _close_store() -> None:
    global _store
    if _store is not None:
        _store.close()
        _store = None


@app.get("/", include_in_schema=False)
def root():
    # convenience: send humans to the bundled dashboard, which will fetch the API
    return RedirectResponse(url="/dashboard/")


@app.get("/how-it-works", include_in_schema=False)
def how_it_works():
    """Clean URL for the generated concept page (built by scripts/build_how_it_works.py)."""
    page = DASHBOARD_DIR / "how-it-works.html"
    if not page.exists():
        raise HTTPException(
            status_code=404,
            detail="how-it-works.html has not been generated; "
                   "run: python scripts/build_how_it_works.py",
        )
    return FileResponse(page, media_type="text/html")


@app.get("/api", tags=["service"], summary="Endpoint index")
def api_index():
    return {
        "service": "Qubic Decentralization Report API",
        "version": app.version,
        "endpoints": [
            "/v1/report/latest",
            "/v1/report/{epoch}",
            "/v1/report/{epoch}/versions",
            "/v1/clusters/{epoch}",
            "/v1/epochs",
            "/v1/metrics/timeseries",
            "/v1/burn/latest",
            "/v1/burn/series",
            "/v1/burn/contracts",
            "/v1/burn/coverage",
            "/v1/report/{epoch}/snapshot.json",
            "/v1/dashboard-data",
            "/v1/store",
            "/v1/pulse",
            # The page that consumes this is deliberately unlisted; the data behind
            # it is not secret, so the endpoint is indexed like any other.
            "/v1/mining",
            "/v1/mining/series",
            "/health",
        ],
        "dashboard": "/dashboard/",
        "how_it_works": "/how-it-works",
        "docs": "/docs",
    }


@app.get("/health", tags=["service"], summary="Liveness probe")
def health():
    """Liveness probe for Docker/orchestrators. Deliberately does no RPC call —
    it answers whether the process serves, not whether upstream data is fresh.

    A store that cannot be read is reported as such. This used to swallow the
    exception and answer `stored_reports: 0`, which is what an empty store on a
    fresh deployment also looks like — so a read-only volume was indistinguishable
    from "the backfill has not finished yet", and the dashboard showed its
    "building" notice for a deployment that was never going to fill.
    """
    store_error = None
    stored = 0
    degraded_store = False
    try:
        store = get_store()
        stored = store.stats()["reports"]
        degraded_store = getattr(store, "is_degraded", False)
    except Exception as e:                      # noqa: BLE001 - reported, not raised
        store_error = f"{type(e).__name__}: {e}"
    ok = store_error is None and not degraded_store
    body = {"status": "ok" if ok else "degraded",
            "version": app.version,
            "stored_reports": stored}
    if store_error:
        body["store_error"] = store_error
    if degraded_store:
        # Serving from a throwaway path is not a healthy state, even though every
        # endpoint answers: history dies on restart. Say so where orchestrators look.
        body["store_error"] = ("data volume unusable; running on a temporary "
                               "in-container store — see /v1/diagnostics")
    return body


def _probe_store() -> dict:
    """What the store can tell us, or why it cannot."""
    try:
        store = get_store()
    except Exception as e:                      # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out: dict = {"ok": True}
    if getattr(store, "is_degraded", False):
        out["degraded"] = True
        out["configured_path"] = str(store.configured_path)
        out["actual_path"] = str(store.path)
        out["degraded_reason"] = store.degraded_reason
    try:
        out["stats"] = store.stats()
    except Exception as e:                      # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "db": str(getattr(store, "path", "?"))}
    try:
        out["epochs"] = store.list_epochs(limit=30)
    except Exception as e:                      # noqa: BLE001
        out["epochs_error"] = f"{type(e).__name__}: {e}"
    return out


def _probe_writable() -> dict:
    """Can the ingest actually write? A read-only volume is the failure that
    looks exactly like an empty one from the outside."""
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    probe = data_dir / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"ok": True, "path": str(data_dir)}
    except Exception as e:                      # noqa: BLE001
        return {"ok": False, "path": str(data_dir),
                "error": f"{type(e).__name__}: {e}"}


def _probe_rpc() -> dict:
    """Can this host reach the RPC the report is built from?

    Deliberately a plain one-shot request rather than the ingest's client: that
    one retries five times with a backoff, which is right for an ingest riding
    out a blip and wrong here — a diagnostics page must answer fastest exactly
    when the network is the thing that is broken.
    """
    base = os.environ.get("QUBIC_RPC_BASE", "https://rpc.qubic.org").rstrip("/")
    try:
        import requests
        r = requests.get(f"{base}/v1/tick-info", timeout=5)
        r.raise_for_status()
        tick = (r.json() or {}).get("tickInfo", {})
        return {"ok": True, "base": base,
                "epoch": tick.get("epoch"), "tick": tick.get("tick")}
    except Exception as e:                      # noqa: BLE001
        return {"ok": False, "base": base, "error": f"{type(e).__name__}: {e}"}


@app.get("/v1/diagnostics", tags=["service"], summary="Why is the report empty?")
def diagnostics():
    """One call that answers "is it building, or is it broken?".

    The dashboard shows the same "still building" notice whether the ingest is
    mid-backfill or cannot write at all, and from a browser there is no way to
    tell those apart. This checks the three things that actually differ: can the
    store be read, can the volume be written, and is the RPC reachable.
    """
    store = _probe_store()
    writable = _probe_writable()
    rpc = _probe_rpc()

    stats = store.get("stats") or {}
    sealed = stats.get("sealed", 0)
    problems: list[str] = []
    if store.get("degraded"):
        problems.append(
            "The configured data directory could not be used, so the store fell "
            f"back to {store['actual_path']} inside the container "
            f"({store['degraded_reason']}). The report works, but history is lost "
            "on every restart. The mounted volume needs to be writable by uid "
            "10001 — on the host: chown -R 10001:10001 the mounted directory."
        )
    if not store["ok"]:
        problems.append(
            "The store cannot be read. If this says 'readonly database' or "
            "'unable to open', the /data volume is not writable by the container "
            "user (uid 10001). A host bind mount needs 'chown -R 10001:10001'; "
            "a named volume gets this right by itself."
        )
    if not writable["ok"]:
        problems.append(
            "The data volume is not writable, so the ingest cannot store anything "
            "it fetches. Same fix as above: chown the mounted directory to uid 10001."
        )
    if not rpc["ok"]:
        problems.append(
            "The RPC is unreachable from this host, so there is nothing to ingest. "
            "Check outbound HTTPS and QUBIC_RPC_BASE."
        )
    if sealed == 0 and not _log_file().exists():
        # No sealed epoch AND no worker log: nothing is filling the store. On a
        # deployment that runs the image directly this used to be invisible —
        # the API is its own healthy service, and the ingest simply was not
        # there. QDR_ROLE=both (the default) runs it in this container.
        problems.append(
            "The ingest worker has not run: there is no worker log. Nothing is "
            "filling the store, so the report will stay empty indefinitely. This "
            "container runs the worker unless QDR_ROLE is set to 'api' — check "
            f"that variable (currently {os.environ.get('QDR_ROLE', 'both')!r})."
        )
    elif not problems and sealed == 0:
        problems.append(
            "No sealed epoch yet. The first backfill takes a few minutes; the "
            "report appears once at least one epoch is sealed."
        )

    return {
        "version": app.version,
        "healthy": not problems,
        "store": store,
        "writable": writable,
        "rpc": rpc,
        "problems": problems,
        "log": f"/v1/log  ({_LOG_FILE})",
    }


_LOG_FILE = Path(os.environ.get(
    "QDR_LOG_FILE", str(Path(os.environ.get("DATA_DIR", "/data")) / "worker.log")))
# The worker moves its log to the same fallback the store uses when DATA_DIR is
# unwritable. Reading only the configured path would hide the log in exactly the
# situation it is needed, so fall back the same way the writer did.
_LOG_FALLBACK = Path(tempfile.gettempdir()) / "qdr-fallback" / "worker.log"


def _log_file() -> Path:
    if _LOG_FILE.exists():
        return _LOG_FILE
    return _LOG_FALLBACK if _LOG_FALLBACK.exists() else _LOG_FILE


@app.get("/v1/log", tags=["service"], summary="Tail of the ingest worker log")
def worker_log(lines: int = Query(200, ge=1, le=2000)):
    """The ingest worker's own output, so an operator can see what it is doing
    without shell access to the host. The worker tees stdout here; `docker
    compose logs` remains the complete record."""
    log_file = _log_file()
    if not log_file.exists():
        return {"path": str(log_file), "exists": False, "lines": [],
                "note": ("No log yet. The ingest service writes this on start — "
                         "if it stays missing, that service is not running.")}
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:                      # noqa: BLE001
        return {"path": str(log_file), "exists": True, "lines": [],
                "error": f"{type(e).__name__}: {e}"}
    tail = text.splitlines()[-lines:]
    return {"path": str(log_file), "exists": True,
            "mtime": int(log_file.stat().st_mtime), "lines": tail}


# The live pulse is cached for a few seconds: many dashboards may poll it, but
# the upstream only moves ~2.7 ticks/s, so re-fetching per request is pointless.
_pulse_cache: dict = {"at": 0.0, "data": None}
PULSE_TTL_S = float(os.environ.get("QDR_PULSE_TTL", "10"))


@app.get("/v1/pulse", tags=["service"], summary="Live state of the running epoch")
def pulse():
    """Fast-moving network state, for a live view that updates every few seconds.

    Deliberately separate from the report: ticks advance continuously, but the
    computor list, revenue and clustering only change at an epoch boundary
    (~4.4 days). Poll this often; poll `/v1/report/latest` slowly.

    `epoch_progress` is 0..1 through the current epoch, derived from the observed
    epoch length, so a UI can show the epoch filling up.
    """
    import time as _t
    now = _t.time()
    if _pulse_cache["data"] and now - _pulse_cache["at"] < PULSE_TTL_S:
        return _pulse_cache["data"]

    client = get_client()
    if client is None:
        raise HTTPException(status_code=503, detail="RPC unreachable")
    try:
        data = client.network_pulse()
    except Exception as e:
        # serve the last good pulse rather than failing a polling dashboard
        if _pulse_cache["data"]:
            stale = dict(_pulse_cache["data"])
            stale["stale"] = True
            return stale
        raise HTTPException(status_code=503, detail=f"pulse unavailable: {e}")

    span = data["tick"] - data["initial_tick"]
    expected = data.get("expected_epoch_ticks") or TYPICAL_EPOCH_TICKS
    data["epoch_progress"] = round(min(max(span / expected, 0.0), 1.0), 4)
    data["ticks_per_second"] = TICKS_PER_SECOND
    data["served_at"] = int(now)
    _pulse_cache.update(at=now, data=data)
    return data


# Mining state is read from a Qubic node's peer port, not the RPC, and those are
# other people's nodes. A TTL well above the page's poll interval keeps many viewers
# collapsed into one query every MINING_TTL_S, however many browsers are watching.
_mining_cache: dict = {"at": 0.0, "data": None, "prev_count": None, "prev_at": 0.0}
MINING_TTL_S = float(os.environ.get("QDR_MINING_TTL", "30"))


@app.get("/v1/mining", tags=["service"], summary="Live ant-colony mining state from a node")
def mining():
    """What the mining algorithm is doing right now, straight off a Qubic node.

    None of this exists on the public RPC: the ant-colony queries live on the
    peer-to-peer port (21841). All three reads used here are unsigned and public.

    `next_refresh_in` tells the page when this server will actually go back to a
    node, so a live view can count down to real new data instead of polling blind.
    """
    import time as _t
    now = _t.time()
    cached = _mining_cache["data"]
    if cached and now - _mining_cache["at"] < MINING_TTL_S:
        out = dict(cached)
        out["next_refresh_in"] = max(0, round(MINING_TTL_S - (now - _mining_cache["at"])))
        return out

    try:
        from qdr import antnode
        data = antnode.collect()
    except Exception as e:
        if cached:
            stale = dict(cached)
            stale["stale"] = True
            stale["next_refresh_in"] = 0
            return stale
        raise HTTPException(status_code=503, detail=f"mining state unavailable: {e}")

    # Epoch length varies a lot between epochs, so the progress bar needs the observed
    # expectation rather than a constant. This comes from the RPC, not the node, and a
    # missing value only costs the bar its denominator.
    if data.get("status") == "live":
        expected = TYPICAL_EPOCH_TICKS
        client = get_client()
        if client is not None:
            try:
                expected = client._expected_epoch_ticks(TYPICAL_EPOCH_TICKS)
            except Exception:
                pass
        data.setdefault("epoch", {})["expected_ticks"] = expected

    # Growth rate, measured rather than assumed. Preferably from the stored
    # samples: that is a trend over ~15 minutes and it is right immediately after
    # a restart, whereas the in-process pair below is a single 30s interval that
    # starts empty on every deploy. The pair is kept as the fallback for when the
    # ingest worker is not running (a plain `docker run` of the API alone).
    count = (data.get("colony") or {}).get("solution_count")
    epoch_no = (data.get("epoch") or {}).get("epoch")
    if data.get("status") == "live" and count is not None:
        rate = None
        if epoch_no:
            try:
                rate = pipeline.mining_growth(get_store(), int(epoch_no))
            except Exception:
                rate = None
        if rate is not None:
            data["colony"]["delta_per_min"] = rate
            data["colony"]["delta_source"] = "stored"
        else:
            prev, prev_at = _mining_cache["prev_count"], _mining_cache["prev_at"]
            if prev is not None and count >= prev and now > prev_at:
                elapsed_min = (now - prev_at) / 60.0
                if elapsed_min > 0:
                    data["colony"]["delta_per_min"] = round((count - prev) / elapsed_min, 1)
                    data["colony"]["delta_source"] = "live"
        _mining_cache.update(prev_count=count, prev_at=now)

    _mining_cache.update(at=now, data=data)
    out = dict(data)
    out["next_refresh_in"] = round(MINING_TTL_S)
    return out


# The stored mining history. Sampling is the ingest worker's job; this only
# reads what it wrote, so the endpoint costs one indexed query and never a node
# call. Cached briefly because a new sample can only appear every 30s anyway.
_mining_series_cache: dict = {}
MINING_SERIES_TTL_S = float(os.environ.get("QDR_MINING_SERIES_TTL", "20"))

# Ceiling on points returned. An epoch at a 30s cadence holds ~12,700 samples;
# sending them all would cost a megabyte to draw a line a few hundred pixels
# wide, so the series is thinned server-side to this many evenly spaced points.
MINING_MAX_POINTS = 500


@app.get("/v1/mining/series", tags=["service"],
         summary="Stored history of live mining state")
def mining_series(
    epoch: int | None = Query(None, description="Which epoch. Omitted: the most "
                                                "recent one sampled."),
    points: int = Query(MINING_MAX_POINTS, ge=2, le=MINING_MAX_POINTS,
                        description="Maximum points to return; the series is "
                                    "thinned evenly to fit."),
    window: int | None = Query(None, ge=60,
                               description="Only the last N seconds, instead of "
                                           "the whole epoch."),
):
    """What the colony has been doing over the running epoch, as measured.

    Every point was sampled from a node — there is no interpolation and no
    back-fill before the first sample, because mining state is not replayable:
    a reading not taken cannot be reconstructed afterwards.

    Raw samples are kept for the last two epochs only. `epochs` carries the
    permanent one-row-per-epoch summary, which outlives that window.
    """
    import time as _t

    store = get_store()
    key = (epoch, points, window)
    hit = _mining_series_cache.get(key)
    now = _t.time()
    if hit and now - hit["at"] < MINING_SERIES_TTL_S:
        return hit["data"]

    target = epoch
    if target is None:
        latest = store.latest_mining_sample()
        if latest is None:
            raise _no_data("mining history")
        target = latest["epoch"]

    since = int(now) - window if window else None
    rows = store.mining_series(epoch=target, since=since)
    if not rows:
        raise _no_data("mining history")

    # Even thinning, with the last row always kept: the newest point is the one
    # the live poll will append to, and a gap there would show as a jump.
    total = len(rows)
    if total > points:
        step = total / float(points)
        picked = [rows[int(i * step)] for i in range(points)]
        if picked[-1]["at"] != rows[-1]["at"]:
            picked[-1] = rows[-1]
        rows = picked

    data = {
        "epoch": target,
        "series": [
            {"at": r["at"], "tick": r["tick"],
             "solution_count": r["solution_count"],
             "threshold": r["threshold"],
             "free_ann_slots": r["free_ann_slots"],
             "peers_responding": r["peers_responding"]}
            for r in rows
        ],
        "points": len(rows),
        "sampled_total": total,
        "first_at": rows[0]["at"],
        "last_at": rows[-1]["at"],
        "interval_s": pipeline.MINING_SAMPLE_INTERVAL_S,
        "growth_per_min": pipeline.mining_growth(store, target),
        "epochs": store.mining_epochs(limit=50),
        "measured": True,
    }
    _mining_series_cache.clear()      # one shape of this query is enough to hold
    _mining_series_cache[key] = {"at": now, "data": data}
    return data


# The burn figures move on the epoch clock (the official counter) and on the
# worker's scan cadence (our measurement). Neither changes per request, so the
# headline is cached like the pulse is.
_burn_cache: dict = {"at": 0.0, "data": None}
BURN_TTL_S = float(os.environ.get("QDR_BURN_TTL", "15"))


@app.get("/v1/burn/latest", tags=["burn"], summary="Burned supply: official total and measured rate")
def burn_latest():
    """Headline burn figures.

    `burned_total` is the official cumulative counter — the same figure
    explorer.qubic.org publishes, and the only one directly comparable to it.
    Everything at finer resolution than an epoch is measured by this project from
    a Bob node's logs, and is reported under `measured` rather than mixed in:
    the official counter is an epoch aggregate and does not move between epoch
    boundaries (measured 2026-09-19 over 375 ticks), so a per-day figure cannot
    come from it.

    `coverage` compares what we counted against the counter's own step. A ratio
    below 1.0 means a burn category the scan does not recognise yet, and is
    published rather than hidden.
    """
    import time as _t
    now = _t.time()
    if _burn_cache["data"] and now - _burn_cache["at"] < BURN_TTL_S:
        return _burn_cache["data"]
    data = pipeline.build_burn_latest(get_store())
    if data is None:
        raise _no_data("burn data")
    _burn_cache.update(at=now, data=data)
    return data


@app.get("/v1/burn/series", tags=["burn"], summary="Burned supply over time")
def burn_series(
    by: str = Query("day", pattern="^(day|epoch|year)$",
                    description="Grouping: day, epoch or year."),
    limit: int = Query(pipeline.DEFAULT_BURN_DAYS, ge=1, le=2000,
                       description="How many of the most recent periods to return. "
                                   "History only grows, so this is windowed by default."),
):
    """The stored burn history, grouped by day, epoch or year.

    Every point carries `measured: true` — this series contains only what was
    counted from events. `first_measured_day` marks where our measurement begins;
    there is deliberately no interpolated history before it, because the official
    counter only steps once per epoch and smoothing that into daily values would
    present an invented curve as a measurement.
    """
    ts = pipeline.build_burn_series(get_store(), by=by, limit=limit)
    if not ts["series"]:
        raise _no_data("burn series")
    return ts


@app.get("/v1/burn/contracts", tags=["burn"], summary="Burns attributed to a contract")
def burn_contracts(epoch: int | None = Query(None, description="Restrict to one epoch.")):
    """Burns carrying a `contractIndexBurnedFor`, summed per contract index.

    These are reported separately from the transfer burns, never summed with
    them: they are a different kind of event (BURNING, not a QU_TRANSFER to a
    burn sink), and summing two different things into one figure would be wrong
    regardless of their relative size. That size is not fixed — measured over
    2026-09-16..20 the contract burns ran ~60x ABOVE the transfer burns in the
    same window, the reverse of what an earlier note here assumed — which is
    another reason the page states its measured window rather than implying the
    total covers the chain's history.

    Each row carries the contract's NAME, resolved against the registry Qubic
    publishes and the official explorer itself renders
    (`static.qubic.org/v1/general/data/smart_contracts.json`). New contracts
    therefore appear by name without a change here. An index the registry does
    not list is returned with `known: false` and no name, so the page shows the
    bare index rather than a guess.
    """
    # An empty result is an ANSWER here, not a missing service: measured across
    # epochs 225-228, contract burns appear only in the end-epoch logs, so a
    # store that has been scanning mid-epoch ticks for days legitimately holds
    # none. 503 would be wrong twice over — it claims the data is not ready when
    # it is, and it puts a red error in the browser console for a normal state.
    # (Contrast /v1/burn/latest, where an empty store genuinely has nothing to
    # say and 503 is the honest answer.)
    from qdr import contracts

    totals = get_store().burn_by_contract(epoch)
    grand = sum(totals.values()) or 1
    rows = []
    for k, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        idx = int(k) if k.lstrip("-").isdigit() else k
        row = {"index": idx, "burned": v, "share": round(v / grand, 6)}
        # describe() never raises and never invents: an unlisted index comes back
        # known=false with no name, and the page falls back to the index.
        row.update({key: val for key, val in contracts.describe(idx).items()
                    if key in ("name", "label", "address", "known")})
        rows.append(row)
    # The window is not decoration: without it this total reads as a lifetime
    # figure and gets compared against the header's cumulative counter, which
    # covers the chain since genesis. It covers the days we have scanned.
    window = get_store().burn_contract_window(epoch)
    _official_row = get_store().latest_burn_total()
    official = (_official_row or {}).get("burned_total")
    return {"epoch": epoch, "total": sum(totals.values()), "by_contract": rows,
            "window": window,
            # Published so the page can state the ratio rather than let a reader
            # assume one. None when the official counter has not been sampled.
            "official_total": official,
            "registry": {
                "source": contracts.REGISTRY_URL,
                "contracts_known": len(contracts.registry()),
            }}


@app.get("/v1/burn/coverage", tags=["burn"], summary="Measured burns vs. the official counter")
def burn_coverage():
    """Per-epoch reconciliation of our event count against `burnedQus` steps.

    This is the honest answer to "is this measurement complete?". Published for
    the same reason `linkage_coverage: 0` is: a silent undercount is the failure
    mode worth engineering against.
    """
    return pipeline.burn_coverage(get_store())


@app.get("/v1/store", tags=["service"], summary="What the store holds")
def store_status():
    """Store contents: how many epochs are sealed, partial, and on record.

    This is the honest answer to "is this history complete?" — a partial count
    above zero means some epochs could not be derived exhaustively yet.
    """
    return get_store().stats()


@app.get("/v1/epochs", tags=["report"], summary="Epochs on record and their status")
def epochs():
    return {"epochs": get_store().list_epochs()}


@app.get("/v1/report/latest", tags=["report"], summary="Report for the current epoch")
def report_latest():
    """The running epoch, refreshed from the chain when the stored copy is stale."""
    store = get_store()
    rep = pipeline.get_report(store, None, client=get_client(),
                              registry=load_registry(), max_live_age_s=LIVE_MAX_AGE_S,
                              allow_write=False)
    if rep is None:
        raise _no_data("report")
    return rep


@app.get("/v1/report/{epoch}", tags=["report"], summary="Report for a specific epoch")
def report_epoch(epoch: int):
    """A sealed epoch comes from the store untouched; a live/missing one is built."""
    store = get_store()
    rep = pipeline.get_report(store, epoch, client=get_client(),
                              registry=load_registry(), max_live_age_s=LIVE_MAX_AGE_S,
                              allow_write=False)
    if rep is None:
        raise HTTPException(status_code=404, detail=f"epoch {epoch} not available")
    return rep


@app.get("/v1/report/{epoch}/versions", tags=["report"],
         summary="Every computation on record for an epoch")
def report_versions(epoch: int):
    """Why did this number change? Each recompute under a new code version is kept
    beside the one it replaced, rather than overwriting it."""
    versions = get_store().report_versions(epoch)
    if not versions:
        raise HTTPException(status_code=404, detail=f"no computations stored for epoch {epoch}")
    return {"epoch": epoch, "versions": versions}


@app.get("/v1/clusters/{epoch}", tags=["report"], summary="Operator clusters for an epoch")
def clusters_epoch(epoch: int):
    rep = report_epoch(epoch)
    return {
        "epoch": rep["epoch"],
        "status": rep.get("status"),
        "linkage_coverage": rep.get("linkage_coverage"),
        "declared_vs_detected": rep.get("declared_vs_detected"),
        "clusters": rep["clusters"],
    }


@app.get("/v1/metrics/timeseries", tags=["metrics"], summary="Concentration indices across epochs")
def metrics_timeseries(
    include_partial: bool = Query(
        False, description="Include epochs whose revenue derivation was incomplete. "
                           "Off by default: an under-counted epoch shows as a bogus "
                           "decentralization spike."
    ),
    epochs: int = Query(
        pipeline.DEFAULT_TIMESERIES_EPOCHS, ge=1, le=1000,
        description="How many of the most recent epochs to return. History grows "
                    "without bound, so the response is windowed by default."
    ),
):
    store = get_store()
    ts = pipeline.build_timeseries(store, include_partial=include_partial, limit=epochs)
    if not ts["series"]:
        raise _no_data("timeseries")
    return ts


@app.get("/v1/report/{epoch}/snapshot.json", tags=["report"], summary="Frozen archivable snapshot")
def snapshot(epoch: int):
    return JSONResponse(report_epoch(epoch))


DASHBOARD_DIR = ROOT / "dashboard"


@app.get("/v1/dashboard-data", tags=["metrics"], summary="One bundle for the dashboard SPA")
def dashboard_data():
    """One bundle for the SPA: latest report + timeseries + per-epoch bubbles."""
    store = get_store()
    # No refresh here. The bundle is assembled from settled epochs, so recomputing
    # the running one changed nothing it uses — while writing to a store the
    # ingest worker owns. One writer, many readers.
    bundle = pipeline.build_dashboard_bundle(store)
    if bundle.get("report"):
        return bundle
    raise _no_data("report")


class _RevalidatingStatics(StaticFiles):
    """StaticFiles that tells the browser to check back before reusing a file.

    Starlette sends only ETag and Last-Modified, and no Cache-Control. A browser
    that gets no instruction is free to invent its own freshness window, and it
    does: the file is then reused for hours without a request reaching us. That
    was survivable while every page carried its own CSS inline, because the HTML
    came fresh each time. Now that the design lives in dashboard/theme.css, a
    stale copy of that one file strips the styling off pages whose markup already
    expects it — the tiles lose their card, their rail and their type scale, and
    the page looks broken rather than merely old.

    `no-cache` is not "do not cache": it stores the file and revalidates it, so a
    304 still costs no bytes. It only removes the browser's licence to skip
    asking. The pages link theme.css with a ?v=<hash> that changes whenever the
    file does, which handles proxies in between that ignore this header.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers.setdefault("Cache-Control", "no-cache")
        return resp


# Serve the reference dashboard as static files at /dashboard (mounted last so the
# /v1 API routes above always take precedence). When opened via http the dashboard
# fetches this same origin's /v1/dashboard-data automatically.
if DASHBOARD_DIR.exists():
    app.mount("/dashboard", _RevalidatingStatics(directory=str(DASHBOARD_DIR), html=True), name="dashboard")

_EXAMPLES = ROOT / "examples"
if _EXAMPLES.exists():
    app.mount("/examples", _RevalidatingStatics(directory=str(_EXAMPLES), html=True), name="examples")
