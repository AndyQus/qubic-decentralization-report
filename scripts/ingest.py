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
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdr import __version__, pipeline
from qdr.bob import BOB_URL, BobClient, BobError
from qdr.client import CachedClient, QubicRPCError
from qdr.clustering import load_registry
from qdr.revenue import ARBITRATOR_IDENTITY, verify_arbitrator
from qdr.store import Store


def start_mining_sampler(store: Store, interval: int) -> threading.Thread:
    """Sample live mining state on its own timer, in the background.

    It runs beside the main watch loop rather than inside it because the two
    have nothing in common but the store: the report only changes at an epoch
    boundary (~4.4 days, hence a 300s loop), while mining state is a live
    reading that is gone if not taken. Sharing one interval would force either
    a useless flood of report recomputes or a mining curve with 5-minute gaps.

    The store is WAL-mode and lock-guarded, so two writers are safe.
    Failures are swallowed on purpose: these are other people's nodes, and an
    unreachable one must never take the ingest worker down with it.
    """
    def loop() -> None:
        last_prune = 0.0
        while True:
            # Measured 2026-09-20: a collect() takes 10-17s, because it probes
            # several peers. Sleeping the full interval *after* that stretched a
            # 30s cadence to 47s, so the wait is what remains of the interval,
            # not the whole of it.
            started = time.time()
            try:
                pipeline.sample_mining(store)
            except Exception as e:      # never let a sampling error kill the thread
                print(f"[mining] sample failed: {e}", file=sys.stderr)
            try:
                # Hourly is far more often than an epoch boundary (~4.4 days);
                # the point is only that no single prune ever has much to do.
                if time.time() - last_prune > 3600:
                    dropped = store.prune_mining()
                    last_prune = time.time()
                    if dropped:
                        print(f"[mining] pruned {dropped} sample(s) "
                              f"outside the {store.MINING_KEEP_EPOCHS}-epoch window")
            except Exception as e:
                print(f"[mining] prune failed: {e}", file=sys.stderr)
            # A floor of a second keeps this a loop rather than a spin, for the
            # case where one pass somehow takes longer than the whole interval.
            time.sleep(max(1.0, interval - (time.time() - started)))

    t = threading.Thread(target=loop, name="mining-sampler", daemon=True)
    t.start()
    return t


def start_price_sampler(client, store: Store, interval: int) -> threading.Thread:
    """Sample the market on its own timer, in the background.

    Its own thread for the same reason mining has one: the cadences have nothing
    in common. The report changes at an epoch boundary (~4.4 days), the price
    moves in minutes. Sharing one interval would mean either recomputing the
    report 1440 times a day or drawing the price at 5-minute resolution.

    There is no backfill, and cannot be: the RPC serves only the price now. A
    missed pass is therefore a permanent gap, not something a later pass heals —
    which is exactly why `polls` is stored per interval and coverage is
    published, so the gap shows as one instead of being drawn through.

    Failures are swallowed on purpose: an unreachable RPC is a transient state
    upstream and must never take the worker down.
    """
    def loop() -> None:
        last_rollup = 0.0
        last_prune = 0.0
        while True:
            started = time.time()
            try:
                row = pipeline.sample_price(client, store)
                if row is None:
                    # Absence, not an exception: the RPC was unreachable or
                    # carried no usable price. Reported so a silent stall in the
                    # series has a matching line in the log.
                    print("[price] no reading stored — RPC unreachable or "
                          "no price in the response", file=sys.stderr)
            except Exception as e:
                print(f"[price] sample failed: {e}", file=sys.stderr)
            try:
                # Fold finished intervals into hours every 10 min. The rollup
                # only touches hours that are over, so running it often costs
                # little and keeps the permanent record close behind the live one.
                if time.time() - last_rollup > 600:
                    n = store.rollup_price_hours(since=int(time.time()) - 86400 * 2)
                    last_rollup = time.time()
                    if n:
                        print(f"[price] rolled up {n} hour(s) from measured readings")
            except Exception as e:
                print(f"[price] rollup failed: {e}", file=sys.stderr)
            try:
                if time.time() - last_prune > 3600:
                    dropped = store.prune_price()
                    last_prune = time.time()
                    if dropped:
                        print(f"[price] pruned {dropped} row(s) outside the "
                              f"{store.PRICE_KEEP_DAYS}-day window")
            except Exception as e:
                print(f"[price] prune failed: {e}", file=sys.stderr)
            # Sleep what REMAINS of the interval: a pass takes a second or two,
            # and sleeping the whole interval after it would drift the cadence
            # off the minute boundary the readings sit on.
            time.sleep(max(1.0, interval - (time.time() - started)))

    t = threading.Thread(target=loop, name="price-sampler", daemon=True)
    t.start()
    return t


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
    ap.add_argument("--bob", default=None,
                    help=f"Bob node RPC URL, the source of epoch payouts (default: {BOB_URL})")
    ap.add_argument("--no-bob", action="store_true",
                    help="skip Bob; fall back to balance-delta revenue only")
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
    ap.add_argument("--burn-scan", action="store_true",
                    help="advance the burn scan toward the current tick (one budgeted pass)")
    ap.add_argument("--burn-from", type=int, metavar="TICK",
                    help="with --burn-scan: start at this tick instead of resuming")
    ap.add_argument("--burn-calls", type=int, default=None,
                    help="with --burn-scan: chunk budget for this pass")
    ap.add_argument("--burn-backfill", action="store_true",
                    help="count burns in PAST ticks, dated by measured tick "
                         "timestamps (/v1/ticks/{t}/tick-data) rather than by "
                         "observation time — fills days before the worker ran")
    ap.add_argument("--burn-to", type=int, metavar="TICK",
                    help="with --burn-backfill: stop at this tick (default: "
                         "Bob's current indexing tick)")
    ap.add_argument("--burn-backfill-days", type=int, metavar="N", default=None,
                    help="with --burn-backfill: fill the last N whole UTC days, "
                         "resolving the tick range by measurement. Runs once per "
                         "store: a completed window is recorded and a later run "
                         "skips it, so this is safe on every container start")
    ap.add_argument("--burn-backfill-force", action="store_true",
                    help="with --burn-backfill-days: re-run a window already "
                         "recorded as done")
    ap.add_argument("--burn-status", action="store_true",
                    help="what the burn scan has measured, and its coverage")
    ap.add_argument("--refresh-stale", action="store_true",
                    help="re-derive every epoch computed by an older code version, "
                         "then exit (run on startup so a deploy never serves figures "
                         "its own code has since corrected)")
    ap.add_argument("--mining-sample", action="store_true",
                    help="take one live mining sample into the store and exit")
    ap.add_argument("--mining-status", action="store_true",
                    help="what the mining sampler has stored")
    ap.add_argument("--mining-interval", type=int,
                    default=pipeline.MINING_SAMPLE_INTERVAL_S,
                    help="with --watch: seconds between mining samples "
                         f"(default {pipeline.MINING_SAMPLE_INTERVAL_S})")
    ap.add_argument("--no-mining", action="store_true",
                    help="with --watch: do not sample mining state")
    ap.add_argument("--price-sample", action="store_true",
                    help="take one market sample into the store and exit")
    ap.add_argument("--price-status", action="store_true",
                    help="what the price sampler has stored")
    ap.add_argument("--price-interval", type=int,
                    default=pipeline.PRICE_SAMPLE_INTERVAL_S,
                    help="with --watch: seconds between market samples "
                         f"(default {pipeline.PRICE_SAMPLE_INTERVAL_S})")
    ap.add_argument("--no-price", action="store_true",
                    help="with --watch: do not sample market price")
    ap.add_argument("--export", action="store_true", help="write static snapshots from the store")
    ap.add_argument("--export-each", action="store_true",
                    help="with --watch: re-export the static snapshots after every pass, "
                         "so dashboard/data.js never lags the store")
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

    if args.mining_sample:
        row = pipeline.sample_mining(store)
        if row is None:
            print("no Qubic node answered — nothing sampled", file=sys.stderr)
            return 1
        print(f"epoch {row['epoch']} tick {row['tick']}: "
              f"{row['solution_count']:,} solutions, threshold {row['threshold']} "
              f"(via {row['node_ip']})")
        return 0

    if args.mining_status:
        latest = store.latest_mining_sample()
        if latest is None:
            print("mining sampler has not run yet")
            return 0
        age = int(time.time()) - latest["at"]
        print(f"latest sample: epoch {latest['epoch']}, "
              f"{latest['solution_count']:,} solutions, {age}s ago")
        rate = pipeline.mining_growth(store, latest["epoch"])
        if rate is not None:
            print(f"growth (15 min): {rate}/min")
        print(f"samples held: {store.stats()['mining_samples']:,} "
              f"(window: {store.MINING_KEEP_EPOCHS} epochs)")
        for row in store.mining_epochs(limit=10):
            print(f"  epoch {row['epoch']:>5}  final {row['final_count'] or 0:>12,}  "
                  f"peak {row['peak_count'] or 0:>12,}  {row['samples']:>6} samples")
        return 0

    if args.price_sample:
        # Its own client: the shared one is built further down, after the
        # store-only commands have had their turn, and this is the first of
        # those commands that needs the network.
        # Its own client: the shared one is built further down, after the
        # store-only commands have had their turn, and this is the first of
        # those commands that needs the network.
        row = pipeline.sample_price(
            CachedClient(base_url=args.base) if args.base else CachedClient(), store)
        if row is None:
            print("nothing sampled — RPC unreachable or no price in the response",
                  file=sys.stderr)
            return 1
        # "held" vs. "new" is the distinction the interval store exists to make,
        # so the CLI reports which one this reading was rather than implying
        # every pass moved the price.
        held = int(row["until"]) - int(row["at"])
        if row.get("changed"):
            print(f"new price {row['price']:.4e} USD/QU")
        else:
            print(f"held at {row['price']:.4e} USD/QU "
                  f"({held}s over {row['polls']} poll(s))")
        if row.get("market_cap"):
            print(f"market cap {row['market_cap']:,} USD")
        return 0

    if args.price_status:
        summary = pipeline.price_summary(store)
        if summary.get("status") == "no data":
            print("price sampler has not run yet")
            return 0
        stats = store.stats()
        print(f"{summary['price']:.4e} USD/QU  "
              f"({summary['age_s']}s ago, {summary['status']})")
        print(f"held {summary['held_for_s']}s over {summary['polls']} poll(s)")
        if summary.get("market_cap"):
            print(f"market cap {summary['market_cap']:,} USD")
        for label, ch in (summary.get("change") or {}).items():
            print(f"{label:>4}: {ch['pct']:+.2f}%  "
                  # ASCII on purpose: a Windows console defaults to cp1252 and
                  # dies on an arrow, which would take the whole command down
                  # over a decoration.
                  f"({ch['from']:.4e} -> {ch['to']:.4e}, "
                  f"{ch['moves']} move(s), coverage {ch['coverage_pct']}%)")
        cov = summary["coverage"]
        pt = cov["point"]
        print(f"held: {stats['price_points']:,} point(s) over {pt['polls']:,} poll(s) "
              f"(window: {store.PRICE_KEEP_DAYS} days), "
              f"{stats['price_hours']:,} hourly (permanent)")
        if pt.get("poll_ratio") is not None:
            print(f"poll ratio: {pt['poll_ratio']} "
                  f"(1.0 = a reading every minute of the observed span)")
        if cov.get("detail_from"):
            since = time.strftime("%Y-%m-%d %H:%M",
                                  time.gmtime(cov["detail_from"]))
            print(f"detail begins {since} UTC")
        return 0

    if args.burn_status:
        state = store.burn_scan_state()
        if not state:
            print("burn scan has not run yet")
        else:
            print(f"scanned ticks {state['first_tick']}..{state['last_tick']}")
        latest = store.latest_burn_total()
        if latest:
            print(f"official total: {latest['burned_total']:,} QU "
                  f"(epoch {latest['epoch']})")
        # Per day: what was counted, and how much of the day was actually
        # scanned. Coverage is the difference between "this day burned X" and
        # "the slice of it we looked at burned X" — the page distinguishes them,
        # so the CLI should too.
        series = pipeline.build_burn_series(store, by="day")["series"]
        for point in series[-14:]:
            cov = point.get("coverage")
            mark = ""
            if cov is not None:
                src = "" if point.get("period_measured") else "~"
                mark = f"  {src}{cov:>6.1%} of day"
                if point.get("partial"):
                    mark += " (partial)"
            print(f"  {point['key']}  {point['burned']:>18,} QU  "
                  f"{point['events']:>6} events  "
                  f"{point['contract_burned']:>12,} QU contract{mark}")

        # Which contract burned what — the only source that says what a burn was
        # FOR, resolved to names via Qubic's own registry.
        from qdr import contracts as _contracts
        by_contract = store.burn_by_contract()
        if by_contract:
            print("  by contract:")
            for idx, amount in sorted(by_contract.items(), key=lambda kv: -kv[1]):
                d = _contracts.describe(int(idx) if str(idx).lstrip("-").isdigit()
                                        else idx)
                name = d.get("label") or d.get("name") or f"index {idx}"
                if not d.get("known"):
                    name = f"index {idx} (not in registry)"
                print(f"    {name:<28} {amount:>14,} QU")
        cov = pipeline.burn_coverage(store)
        for c in cov["epochs"][-5:]:
            ratio = "n/a" if c["ratio"] is None else f"{c['ratio']:.3f}"
            print(f"  epoch {c['epoch']}: coverage {ratio} "
                  f"(measured {c['measured_delta']:,} / official {c['official_delta']:,})")
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

    # Bob carries the epoch-end payouts the public RPC does not expose, so it is
    # the default revenue source; --no-bob falls back to balance deltas.
    bob = None
    if not args.no_bob:
        bob = BobClient(url=args.bob) if args.bob else BobClient()
        try:
            be = bob.current_epoch()
            print(f"bob connected · processing epoch {be}")
        except BobError as e:
            print(f"bob unavailable ({e}); falling back to balance deltas", file=sys.stderr)
            bob = None

    registry = load_registry()

    if args.burn_scan:
        pipeline.sample_burn_total(client, store)
        if bob is None:
            print("burn scan needs a Bob node (the RPC total alone has no daily "
                  "resolution); pass --bob or drop --no-bob", file=sys.stderr)
            return 2
        out = pipeline.scan_burns(client, store, bob, from_tick=args.burn_from,
                                  max_calls=args.burn_calls or 20)
        if out.get("reason"):
            print(f"burn scan: {out['reason']}")
            return 0
        print(f"burn scan: ticks {out['from_tick']}..{out['to_tick']} "
              f"({out['scanned']} ticks, {out['calls']} calls), "
              f"{out['days_written']} day(s) written, {out['behind']} ticks behind")
        for g in out.get("gaps") or []:
            print(f"  ! gap, will retry from {g[0]}: {g[0]}..{g[1]}", file=sys.stderr)
        return 0

    if args.burn_backfill:
        if bob is None:
            print("burn backfill needs a Bob node; pass --bob or drop --no-bob",
                  file=sys.stderr)
            return 2

        def _tick(ev):
            done = "" if ev["complete"] else "  (stopped at a gap)"
            print(f"  {ev['day']}  ticks {ev['from_tick']}..{ev['to_tick']}"
                  f"  [{ev['calls']} calls]{done}", flush=True)

        if args.burn_backfill_days:
            # Scale the budget to the ask: a day is ~134,000 ticks at the
            # measured 1.55 ticks/s and a call covers 500, so a flat 400 would
            # cover one day and quietly leave the rest undone.
            budget = args.burn_calls or (args.burn_backfill_days * 300 + 100)
            out = pipeline.backfill_burns_days(
                client, store, bob, days=args.burn_backfill_days,
                max_calls=budget,
                force=args.burn_backfill_force, progress=_tick)
        else:
            out = pipeline.backfill_burns(
                client, store, bob,
                from_tick=args.burn_from, to_tick=args.burn_to,
                max_calls=args.burn_calls or 400, progress=_tick)
        if out.get("reason"):
            print(f"burn backfill: {out['reason']}")
            return 0
        print(f"burn backfill: ticks {out['from_tick']}..{out['to_tick']} "
              f"({out['scanned']:,} ticks, {out['calls']} calls), "
              f"{out['days']} day-bucket(s) written")
        for g in out.get("gaps") or []:
            print(f"  ! gap, not counted: {g[0]}..{g[1]}", file=sys.stderr)
        if out.get("still_missing"):
            # The question an operator actually asks, answered after the work:
            # is the last complete day in the report?
            print(f"  ! still not fully measured: {', '.join(out['still_missing'])}")
        if args.burn_backfill_days and not out.get("recorded"):
            # Say it out loud: an unrecorded run will be attempted again on the
            # next start, which is the intended behaviour but looks like a loop
            # to anyone reading the log without knowing why.
            print("  (not recorded as done — a gap remains; "
                  "the next start will resume it)")
        return 0

    if args.snapshot:
        out = pipeline.snapshot_epoch_balances(client, store)
        print(f"epoch {out['epoch']}: snapshotted {out['snapshots']}/{out['identities']} "
              f"computors at ticks {out['tick_range'][0]}..{out['tick_range'][1]}")
        return 0

    if args.epoch is not None:
        rep = pipeline.compute_epoch(client, store, args.epoch, registry, bob=bob)
        print(describe(rep))
        for w in rep.get("warnings", []):
            print(f"  ! {w}")
        return 0

    if args.refresh_stale:
        stale = store.stale_epochs()
        if not stale:
            print(f"nothing stale — every epoch is at code_version {__version__}")
            return 0
        print(f"re-deriving {len(stale)} epoch(s) computed by an older version: "
              f"{stale[0]}..{stale[-1]} -> {__version__}")
        out = pipeline.backfill(client, store, stale, registry, force=True, bob=bob)
        for d in out["computed"]:
            print(f"  epoch {d['epoch']}: {d['status']}, {d['operators']} operators")
        for e, err in out["failed"].items():
            print(f"  FAILED epoch {e}: {err}", file=sys.stderr)
        return 0

    if args.backfill:
        epochs = list(range(max(0, current - args.backfill + 1), current + 1))
        print(f"backfilling epochs {epochs[0]}..{epochs[-1]} (force={args.force}) …")
        out = pipeline.backfill(client, store, epochs, registry, force=args.force, bob=bob)
        for d in out["computed"]:
            print(f"  epoch {d['epoch']}: {d['status']}, {d['operators']} operators")
        if out["skipped_sealed"]:
            print(f"  skipped (already sealed): {out['skipped_sealed']}")
        for e, err in out["failed"].items():
            print(f"  FAILED epoch {e}: {err}", file=sys.stderr)
        return 0

    if args.watch:
        print(f"watching · refreshing the live epoch every {args.interval}s · Ctrl-C to stop")
        if not args.no_mining:
            start_mining_sampler(store, args.mining_interval)
            print(f"           mining state sampled every {args.mining_interval}s")
        if not args.no_price:
            start_price_sampler(client, store, args.price_interval)
            print(f"           market price polled every {args.price_interval}s "
                  f"(stored on change)")
        while True:
            try:
                # snapshot first: revenue for an epoch can only be derived if its
                # boundary was actually observed, and a boundary is not replayable.
                snap = pipeline.snapshot_epoch_balances(client, store)
                rep = pipeline.update_live(client, store, registry, bob=bob)
                print(f"[{time.strftime('%H:%M:%S')}] {describe(rep)} "
                      f"| snapshots {snap['snapshots']}")
                # Burns are only measurable near the chain head: tick logs carry
                # no timestamp, so a pass that falls far behind can read ticks
                # but not date them (pipeline.MAX_DATING_LAG_TICKS). Keeping the
                # scan in the watch loop is what keeps it close enough to count.
                pipeline.sample_burn_total(client, store)
                if bob is not None:
                    burn_out = pipeline.scan_burns(client, store, bob)
                    if not burn_out.get("reason"):
                        print(f"           burn: +{burn_out['scanned']} ticks, "
                              f"{burn_out['days_written']} day(s), "
                              f"{burn_out['behind']} behind")
                # seal the epoch that just closed, if it is complete
                prev = rep["epoch"] - 1
                if not store.is_sealed(prev):
                    done = pipeline.finalize(client, store, prev, registry, bob=bob)
                    if done:
                        print(f"           finalized epoch {prev}: {done['status']}")
                if args.export_each:
                    # the static snapshots are a projection of the store; refreshing
                    # them here is what keeps the two from drifting apart
                    export_snapshots(store)
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
    rep = pipeline.update_live(client, store, registry, bob=bob)
    print(describe(rep))
    for w in rep.get("warnings", []):
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
