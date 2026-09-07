"""Ingest worker: keep the store current, and fill history.

Mirrors the polling-worker pattern of the sibling projects (qubic_doge_stats):
a cheap loop that refreshes the running epoch, plus a backfill path for history.

    python scripts/ingest.py --once                  # refresh the live epoch once
    python scripts/ingest.py --watch                 # keep it fresh (default 5 min)
    python scripts/ingest.py --backfill 20           # last 20 epochs into the store
    python scripts/ingest.py --backfill 20 --force   # re-derive, new code_version
    python scripts/ingest.py --epoch 228             # one specific epoch
    python scripts/ingest.py --snapshot              # snapshot balances (revenue input)
    python scripts/ingest.py --verify-arbitrator     # confirm the payout source
    python scripts/ingest.py --status                # what the store holds
    python scripts/ingest.py --export                # write dashboard/api snapshots

Closed epochs are computed once and sealed; the current epoch is always recomputed.
A sealed epoch is only re-derived with --force, and the previous computation is
kept beside the new one so a changed number has a recorded reason.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdr import pipeline
from qdr.client import CachedClient, QubicRPCError
from qdr.clustering import load_registry
from qdr.revenue import ARBITRATOR_IDENTITY, verify_arbitrator
from qdr.store import Store


def export_snapshots(store: Store) -> None:
    """Write the static snapshots the dashboard/API use for cold start.

    These are a convenience export of the store, not the source of truth any more.
    """
    bundle = pipeline.build_dashboard_bundle(store)
    if not bundle.get("report"):
        print("nothing to export — store is empty")
        return
    out = ROOT / "api" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report_latest.json").write_text(json.dumps(bundle["report"], indent=2), encoding="utf-8")
    (out / "timeseries.json").write_text(json.dumps(bundle["timeseries"], indent=2), encoding="utf-8")
    (out / "epoch_clusters.json").write_text(
        json.dumps(bundle["epoch_clusters"], indent=2), encoding="utf-8")
    dash = ROOT / "dashboard"
    dash.mkdir(parents=True, exist_ok=True)
    (dash / "data.js").write_text(
        "window.QDR_DATA = " + json.dumps(bundle) + ";\n", encoding="utf-8")
    print("wrote api/sample/*.json and dashboard/data.js")


def describe(rep: dict) -> str:
    t, c = rep["totals"], rep["concentration_by_revenue"]
    cov = rep.get("linkage_coverage", {})
    return (f"epoch {rep['epoch']} [{rep.get('status')}]: {t['operators']} operators "
            f"({t['declared_operators']} declared), "
            f"linked {cov.get('onchain_linked_share', 0):.0%}, "
            f"Nakamoto1/3={c['nakamoto_one_third']}, Gini={c['gini']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Qubic decentralization report ingest")
    ap.add_argument("--base", default=None, help="RPC base URL (default: env QUBIC_RPC_BASE)")
    ap.add_argument("--db", default=None, help="store path (default: env QDR_DB / data/qdr.db)")
    ap.add_argument("--once", action="store_true", help="refresh the live epoch once")
    ap.add_argument("--watch", action="store_true", help="keep the live epoch fresh in a loop")
    ap.add_argument("--interval", type=int, default=300, help="watch interval seconds (default 300)")
    ap.add_argument("--backfill", type=int, metavar="N", help="compute the last N epochs")
    ap.add_argument("--epoch", type=int, help="compute one specific epoch")
    ap.add_argument("--force", action="store_true", help="re-derive sealed epochs (backfill)")
    ap.add_argument("--verify-arbitrator", action="store_true",
                    help="check which identity actually pays the computors")
    ap.add_argument("--snapshot", action="store_true",
                    help="snapshot computor balances (input for balance-delta revenue)")
    ap.add_argument("--status", action="store_true", help="show what the store holds")
    ap.add_argument("--export", action="store_true", help="write static snapshots from the store")
    args = ap.parse_args()

    store = Store(args.db) if args.db else Store()

    if args.status:
        print(json.dumps(store.stats(), indent=2))
        for e in store.list_epochs(limit=20):
            print(f"  epoch {e['epoch']:>5}  {e['status']:<8} "
                  f"ticks {e['first_tick']}..{e['last_tick']}  v{e['code_version']}")
        return 0

    if args.export:
        export_snapshots(store)
        return 0

    client = CachedClient(base_url=args.base) if args.base else CachedClient()
    try:
        info = client.tick_info()
    except QubicRPCError as e:
        print(f"RPC unreachable: {e}", file=sys.stderr)
        print("Hint: run where rpc.qubic.org is reachable, or pass --base <mirror>.",
              file=sys.stderr)
        return 2
    current = int(info["epoch"])
    print(f"connected · current epoch {current} · tick {info.get('tick')}")

    if args.verify_arbitrator:
        # answers DATA_SOURCES §4 empirically instead of trusting the constant
        result = verify_arbitrator(client, current - 1, [ARBITRATOR_IDENTITY])
        print(json.dumps(result, indent=2))
        if not result.get("verdict"):
            print("\nNo candidate paid the computors — the arbitrator identity is wrong.",
                  file=sys.stderr)
            print("Set QUBIC_ARBITRATOR once the real payout source is identified.",
                  file=sys.stderr)
            return 1
        return 0

    registry = load_registry()

    if args.snapshot:
        out = pipeline.snapshot_epoch_balances(client, store)
        print(f"epoch {out['epoch']}: snapshotted {out['snapshots']}/{out['identities']} "
              f"computors at ticks {out['tick_range'][0]}..{out['tick_range'][1]}")
        return 0

    if args.epoch is not None:
        rep = pipeline.compute_epoch(client, store, args.epoch, registry)
        print(describe(rep))
        for w in rep.get("warnings", []):
            print(f"  ! {w}")
        return 0

    if args.backfill:
        epochs = list(range(max(0, current - args.backfill + 1), current + 1))
        print(f"backfilling epochs {epochs[0]}..{epochs[-1]} (force={args.force}) …")
        out = pipeline.backfill(client, store, epochs, registry, force=args.force)
        for d in out["computed"]:
            print(f"  epoch {d['epoch']}: {d['status']}, {d['operators']} operators")
        if out["skipped_sealed"]:
            print(f"  skipped (already sealed): {out['skipped_sealed']}")
        for e, err in out["failed"].items():
            print(f"  FAILED epoch {e}: {err}", file=sys.stderr)
        return 0

    if args.watch:
        print(f"watching · refreshing the live epoch every {args.interval}s · Ctrl-C to stop")
        while True:
            try:
                # snapshot first: revenue for an epoch can only be derived if its
                # boundary was actually observed, and a boundary is not replayable.
                snap = pipeline.snapshot_epoch_balances(client, store)
                rep = pipeline.update_live(client, store, registry)
                print(f"[{time.strftime('%H:%M:%S')}] {describe(rep)} "
                      f"| snapshots {snap['snapshots']}")
                # seal the epoch that just closed, if it is complete
                prev = rep["epoch"] - 1
                if not store.is_sealed(prev):
                    done = pipeline.finalize(client, store, prev, registry)
                    if done:
                        print(f"           finalized epoch {prev}: {done['status']}")
            except QubicRPCError as e:
                print(f"[{time.strftime('%H:%M:%S')}] RPC error: {e}", file=sys.stderr)
            except KeyboardInterrupt:
                print("\nstopped")
                return 0
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nstopped")
                return 0

    # default: --once
    rep = pipeline.update_live(client, store, registry)
    print(describe(rep))
    for w in rep.get("warnings", []):
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
