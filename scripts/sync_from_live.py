"""Copy the last days of non-replayable data from the live API into the local store.

For F5 only. start_worker.ps1 runs this before the local worker starts, so the
dashboard on localhost shows what the live instance measured while the laptop
was off. It is never part of the image: the deployment IS the source.

Only what a local worker cannot fetch after the fact is copied:

  * price points  -- a reading describes a moment; the RPC serves only "now"
  * price hours   -- the permanent summary of those points
  * mining samples and per-epoch summaries -- a node answers only "now"

Everything else is left to the local worker, which rebuilds it from sources that
DO replay: epochs and reports from the RPC, burns from a Bob node's tick logs,
trend lines from the hours copied here.

It reads the public /v1 endpoints the dashboard already uses -- no export
endpoint, nothing to deploy first. Those endpoints thin long series to a point
ceiling, and a thinned price series would be wrong rather than coarse (each row
is an interval; dropping one makes its neighbour look like a gap nobody
watched). So price points are taken from the largest window the API returns
UNthinned, and the hours carry the rest of the 7 days. Mining samples are
single readings, so thinned ones stay true; several windows are layered to get
dense recent data and sparser older data.

Live wins: within the span live covers, local price points are replaced, not
merged -- the same price seen by two pollers a few seconds apart would otherwise
count as two moves.

Never fails the launch: offline, live down, or an unexpected shape all end in a
warning and exit 0, and the local worker starts as before.

    python scripts/sync_from_live.py [--days 7] [--upstream https://report.qubic.tools]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr.store import Store  # noqa: E402

DAY = 86400
PRICE_MAX_POINTS = 1500      # api/server.py PRICE_MAX_POINTS
MINING_MAX_POINTS = 500      # api/server.py MINING_MAX_POINTS
# Layered mining windows: the last ~4 h arrive dense (500 x 30 s), each wider
# window adds sparser readings further back.
MINING_WINDOWS = (4 * 3600, DAY, 3 * DAY)


def log(msg: str) -> None:
    print(f"[sync] {msg}", flush=True)


def get(upstream: str, path: str) -> dict | None:
    """GET a live endpoint. None for a 503 ("no data yet") -- that is an answer."""
    req = urllib.request.Request(upstream + path, headers={
        "accept": "application/json", "user-agent": "qdr-dev-sync"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 503:
            return None
        raise


def sync_price(store: Store, upstream: str, days: int) -> str:
    # Hours: 24 a day, far below the ceiling, so one request is complete.
    hours = get(upstream, f"/v1/price/series?resolution=hour&window={days * DAY}"
                          f"&points={PRICE_MAX_POINTS}")
    # Points: widest window that comes back unthinned.
    points = None
    for d in range(days, 0, -1):
        got = get(upstream, f"/v1/price/series?window={d * DAY}&points={PRICE_MAX_POINTS}")
        if got is None:
            break
        if got["points"] == got["stored_total"]:
            points = got
            break

    now = int(time.time())
    with store._tx() as c:
        if hours:
            c.executemany(
                "INSERT INTO price_hours (at,open,high,low,close,avg_price,"
                "market_cap,moves,polls,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(at) DO UPDATE SET open=excluded.open, high=excluded.high, "
                "low=excluded.low, close=excluded.close, avg_price=excluded.avg_price, "
                "market_cap=excluded.market_cap, moves=excluded.moves, "
                "polls=excluded.polls, updated_at=excluded.updated_at",
                [(h["at"], h["o"], h["h"], h["l"], h["c"], h["avg"], h["cap"],
                  h["moves"], h["polls"], now) for h in hours["series"]])
        if points and points["series"]:
            rows = points["series"]
            # Live covers everything from its first row on; local rows in that
            # span are the same prices seen by a second poller.
            c.execute("DELETE FROM price_points WHERE at >= ?", (rows[0]["at"],))
            c.executemany(
                "INSERT INTO price_points (at,until,polls,price,market_cap,fetched_at) "
                "VALUES (?,?,?,?,?,?)",
                [(p["at"], p["until"], p["polls"], p["price"], p["cap"], now)
                 for p in rows])

    n_h = len(hours["series"]) if hours else 0
    if points:
        span = (points["series"][-1]["until"] - points["series"][0]["at"]) / DAY
        return f"price: {n_h} hours, {len(points['series'])} points over {span:.1f} days"
    return f"price: {n_h} hours, no unthinned points"


def sync_mining(store: Store, upstream: str, days: int) -> str:
    latest = get(upstream, f"/v1/mining/series?points={MINING_MAX_POINTS}"
                           f"&window={MINING_WINDOWS[0]}")
    if latest is None:
        return "mining: nothing on live yet"
    epoch = latest["epoch"]
    cutoff = int(time.time()) - days * DAY

    samples: dict[int, tuple[int, dict]] = {}
    for ep in (epoch, epoch - 1):
        for w in MINING_WINDOWS:
            got = get(upstream, f"/v1/mining/series?epoch={ep}"
                                f"&points={MINING_MAX_POINTS}&window={w}")
            if got is None:
                continue
            for s in got["series"]:
                if s["at"] >= cutoff:
                    samples[s["at"]] = (ep, s)

    with store._tx() as c:
        c.executemany(
            "INSERT INTO mining_samples (at,epoch,tick,solution_count,threshold,"
            "free_ann_slots,peers_responding) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(at) DO NOTHING",
            [(at, ep, s["tick"], s["solution_count"], s["threshold"],
              s["free_ann_slots"], s["peers_responding"])
             for at, (ep, s) in sorted(samples.items())])
        # The summaries are live's, computed from every sample it took rather
        # than the subset copied here.
        c.executemany(
            "INSERT INTO mining_epochs (epoch,final_count,peak_count,avg_threshold,"
            "samples,first_at,last_at,updated_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(epoch) DO UPDATE SET final_count=excluded.final_count, "
            "peak_count=MAX(COALESCE(mining_epochs.peak_count,0), excluded.peak_count), "
            "avg_threshold=excluded.avg_threshold, samples=excluded.samples, "
            "first_at=MIN(COALESCE(mining_epochs.first_at, excluded.first_at), excluded.first_at), "
            "last_at=MAX(COALESCE(mining_epochs.last_at, 0), excluded.last_at), "
            "updated_at=excluded.updated_at",
            [(e["epoch"], e["final_count"], e["peak_count"], e["avg_threshold"],
              e["samples"], e["first_at"], e["last_at"], e["updated_at"])
             for e in latest["epochs"]])
    return f"mining: {len(samples)} samples (epochs {epoch - 1}-{epoch})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--upstream", default="https://report.qubic.tools")
    args = ap.parse_args()
    upstream = args.upstream.rstrip("/")

    log(f"copying the last {args.days} days from {upstream}")
    store = Store()
    for job in (sync_price, sync_mining):
        try:
            log(job(store, upstream, args.days))
        except Exception as e:  # noqa: BLE001 -- never block the launch
            log(f"{job.__name__} skipped: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
