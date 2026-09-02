"""Build REAL report snapshots from the live Qubic RPC.

Unlike scripts/generate_sample.py (which fabricates plausible data for the UI), this pulls
live data via qdr and writes the same output files. Run it wherever rpc.qubic.org is
reachable (a normal machine / server; the Cowork sandbox blocks it).

Usage:
    python scripts/build_report.py --check                 # just test RPC connectivity
    python scripts/build_report.py                          # last 9 epochs -> api/sample + dashboard/data.js
    python scripts/build_report.py --epochs 12             # last 12 epochs
    python scripts/build_report.py --epoch 228             # a single epoch (report only)
    python scripts/build_report.py --base https://mirror   # use an RPC mirror

Outputs (same shapes the API and dashboard already consume):
    api/sample/report_latest.json, timeseries.json, epoch_clusters.json
    dashboard/data.js
Despite the api/sample path, these are REAL once built from the live RPC; the API serves
live first and only falls back to these snapshots when the RPC is unreachable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdr.client import CachedClient, QubicRPCError
from qdr.report import build_dashboard_bundle
from qdr.clustering import load_registry


def write_outputs(bundle: dict) -> None:
    out = ROOT / "api" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report_latest.json").write_text(json.dumps(bundle["report"], indent=2))
    (out / "timeseries.json").write_text(json.dumps(bundle["timeseries"], indent=2))
    (out / "epoch_clusters.json").write_text(json.dumps(bundle["epoch_clusters"], indent=2))
    dash = ROOT / "dashboard"
    dash.mkdir(parents=True, exist_ok=True)
    (dash / "data.js").write_text("window.QDR_DATA = " + json.dumps(bundle) + ";\n")
    print("wrote api/sample/*.json and dashboard/data.js")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build live Qubic decentralization report snapshots")
    ap.add_argument("--base", default=None, help="RPC base URL (default: env QUBIC_RPC_BASE or rpc.qubic.org)")
    ap.add_argument("--epochs", type=int, default=9, help="number of most-recent epochs (default 9)")
    ap.add_argument("--epoch", type=int, default=None, help="build a single epoch (report only)")
    ap.add_argument("--check", action="store_true", help="only test RPC connectivity and exit")
    args = ap.parse_args()

    client = CachedClient(base_url=args.base) if args.base else CachedClient()

    try:
        info = client.tick_info()
    except QubicRPCError as e:
        print(f"RPC unreachable: {e}", file=sys.stderr)
        print("Hint: run where rpc.qubic.org is reachable, or pass --base <mirror>.", file=sys.stderr)
        return 2
    cur = int(info["epoch"])
    print(f"connected · current epoch {cur} · tick {info.get('tick')}")
    if args.check:
        return 0

    if args.epoch is not None:
        epochs = [args.epoch]
    else:
        epochs = list(range(max(0, cur - args.epochs + 1), cur + 1))
    print(f"building epochs {epochs[0]}..{epochs[-1]} ({len(epochs)}) …")

    bundle = build_dashboard_bundle(client, epochs, registry=load_registry())
    write_outputs(bundle)
    tot = bundle["report"]["totals"]
    cbr = bundle["report"]["concentration_by_revenue"]
    print(f"epoch {bundle['report']['epoch']}: {tot['operators']} operators "
          f"({tot['declared_operators']} declared), Nakamoto⅓={cbr['nakamoto_one_third']}, "
          f"Gini={cbr['gini']}, top1={cbr['top1_share']*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
