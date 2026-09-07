"""Build report snapshots from the live Qubic RPC.

Since v0.2 the pipeline is persistent: data goes into the store first
(scripts/ingest.py), and the static snapshots are an *export* of it rather than
the source of truth. This script is the one-shot convenience wrapper — it
backfills the last N epochs into the store, then exports the snapshot files.

Usage:
    python scripts/build_report.py --check              # just test RPC connectivity
    python scripts/build_report.py                       # last 9 epochs -> store + snapshots
    python scripts/build_report.py --epochs 12          # last 12 epochs
    python scripts/build_report.py --epoch 228          # a single epoch
    python scripts/build_report.py --base https://mirror # use an RPC mirror

For continuous operation use scripts/ingest.py --watch instead, which keeps the
running epoch fresh and seals epochs as they close.

Outputs (the shapes the dashboard consumes for cold start):
    api/sample/report_latest.json, timeseries.json, epoch_clusters.json
    dashboard/data.js
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdr import pipeline
from qdr.client import CachedClient, QubicRPCError
from qdr.clustering import load_registry
from qdr.store import Store

from ingest import describe, export_snapshots  # same directory


def main() -> int:
    ap = argparse.ArgumentParser(description="Build live Qubic decentralization report snapshots")
    ap.add_argument("--base", default=None,
                    help="RPC base URL (default: env QUBIC_RPC_BASE or rpc.qubic.org)")
    ap.add_argument("--epochs", type=int, default=9,
                    help="number of most-recent epochs (default 9)")
    ap.add_argument("--epoch", type=int, default=None, help="build a single epoch")
    ap.add_argument("--db", default=None, help="store path (default: env QDR_DB / data/qdr.db)")
    ap.add_argument("--force", action="store_true", help="re-derive already-sealed epochs")
    ap.add_argument("--check", action="store_true", help="only test RPC connectivity and exit")
    args = ap.parse_args()

    client = CachedClient(base_url=args.base) if args.base else CachedClient()
    store = Store(args.db) if args.db else Store()

    try:
        info = client.tick_info()
    except QubicRPCError as e:
        print(f"RPC unreachable: {e}", file=sys.stderr)
        print("Hint: run where rpc.qubic.org is reachable, or pass --base <mirror>.",
              file=sys.stderr)
        return 2
    cur = int(info["epoch"])
    print(f"connected · current epoch {cur} · tick {info.get('tick')}")
    if args.check:
        return 0

    registry = load_registry()
    if args.epoch is not None:
        epochs = [args.epoch]
    else:
        epochs = list(range(max(0, cur - args.epochs + 1), cur + 1))
    print(f"building epochs {epochs[0]}..{epochs[-1]} ({len(epochs)}) …")

    out = pipeline.backfill(client, store, epochs, registry, force=args.force)
    for d in out["computed"]:
        print(f"  epoch {d['epoch']}: {d['status']}, {d['operators']} operators")
    if out["skipped_sealed"]:
        print(f"  skipped (already sealed, use --force to redo): {out['skipped_sealed']}")
    for e, err in out["failed"].items():
        print(f"  FAILED epoch {e}: {err}", file=sys.stderr)

    export_snapshots(store)
    latest = store.latest_report()
    if latest:
        print(describe(latest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
