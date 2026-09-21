#!/bin/sh
# Ingest worker entrypoint.
#
# The report is only as current as the store behind it, and nothing else fills
# that store: the API serves what it finds and never backfills history. So this
# runs as its own service and owns three jobs.
#
#   1. Backfill on start. A fresh deployment has an empty store, and an epoch
#      lasts ~4.4 days — without this, a new instance would sit on "building"
#      until the next epoch boundary. Bob's end-epoch logs reach back far
#      enough to seal real history within minutes of first start.
#   1b. Re-derive stale epochs on start. A sealed epoch is normally immutable,
#      but a correctness fix changes what the right answer is — an epoch computed
#      by an older code version is not "already done", it is wrong and still on
#      the page. Every start compares each epoch's code_version against the
#      running code and recomputes whatever disagrees, so deploying new code is
#      all it takes to retire the numbers it corrects.
#   2. Watch. Refreshes the running epoch, takes the balance snapshots that
#      revenue derivation needs (an epoch boundary is not replayable, so a
#      missed one is missed for good), and seals each epoch as it closes.
#   2b. Measure burns. The RPC's burnedQus is an epoch aggregate and does not
#      move between boundaries, so a per-day burn figure has to be counted from
#      a Bob node's tick logs. Those logs carry no timestamp, so the count is
#      dated by when it was observed — which is only valid near the chain head.
#      That makes this a watch-loop job by nature: it is the continuous scanning
#      that keeps it close enough to the head to date anything at all.
#   2c. Sample mining state. The colony's live figures are not on the RPC and are
#      not replayable: a node answers what it looks like right now, so a reading
#      not taken is gone. The watch loop starts a sampler thread that stores one
#      reading every QDR_MINING_SAMPLE_INTERVAL seconds and keeps a rolling
#      two-epoch window of them (~2 MB), which is what gives the mining page a
#      curve instead of a single number.
#   3. Export after every pass, so dashboard/data.js and api/sample/*.json can
#      never drift behind the store the way they did before.
set -eu

BACKFILL="${QDR_BACKFILL_EPOCHS:-10}"
INTERVAL="${QDR_INGEST_INTERVAL:-300}"
# Mining state moves far faster than the report does, so it has its own cadence:
# the watch loop above runs every 5 minutes, this samples every 30 seconds.
MINING_INTERVAL="${QDR_MINING_SAMPLE_INTERVAL:-30}"

# Everything this worker prints also goes to a file in the shared volume, so the
# API can serve it at /v1/log and /log.html can answer "is it doing anything?"
# without shell access to the host. `docker compose logs` stays the full record;
# this is the tail an operator can reach from a browser. Kept bounded (the last
# LOG_MAX lines are retained on start) so a long-running deployment cannot fill
# the volume with its own log.
LOG_FILE="${QDR_LOG_FILE:-${DATA_DIR:-/data}/worker.log}"
LOG_MAX="${QDR_LOG_MAX_LINES:-2000}"

# The log follows the store: when the mounted volume cannot be written, both fall
# back to a path inside the container. A deployment we cannot administer must
# still be able to show its log — that page is the only channel there is.
if ! mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || ! touch "$LOG_FILE" 2>/dev/null; then
  ALT="${TMPDIR:-/tmp}/qdr-fallback"
  echo "[worker] WARNING: cannot write $LOG_FILE (uid $(id -u)); using $ALT/worker.log" >&2
  echo "[worker] The data volume is not writable — history will not survive a restart." >&2
  mkdir -p "$ALT" 2>/dev/null || true
  LOG_FILE="$ALT/worker.log"
  touch "$LOG_FILE" 2>/dev/null || LOG_FILE=""
fi

if [ -n "$LOG_FILE" ]; then
  if [ -s "$LOG_FILE" ]; then
    tail -n "$LOG_MAX" "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
  fi
  # Send stdout/stderr to both the container log and the file. Process
  # substitution (`> >(tee …)`) is a bashism and this runs under dash, so a
  # named pipe does the same job portably.
  FIFO="$(dirname "$LOG_FILE")/.worker.fifo"
  rm -f "$FIFO" 2>/dev/null || true
  if mkfifo "$FIFO" 2>/dev/null; then
    tee -a "$LOG_FILE" < "$FIFO" &
    exec > "$FIFO" 2>&1
  else
    exec >> "$LOG_FILE" 2>&1     # no fifo: the file still gets everything
  fi
fi
echo "[worker] --- start $(date -u +%Y-%m-%dT%H:%M:%SZ) ---"

echo "[worker] backfilling last ${BACKFILL} epochs …"
# A failed backfill must not stop the service: --watch would still recover the
# current epoch, and a transient RPC outage is not a reason to stay down.
python scripts/ingest.py --backfill "${BACKFILL}" || \
  echo "[worker] backfill incomplete; continuing into watch" >&2

# Anything the previous version computed differently is now wrong on the page.
# This is what makes a deploy self-correcting: no operator has to know that a
# fix changed a figure, or remember to pass --force by hand.
echo "[worker] checking for epochs computed by an older code version …"
python scripts/ingest.py --refresh-stale ||   echo "[worker] stale refresh incomplete; watch will retry the live epoch" >&2

# Seed the burn scan before the watch loop takes it over, so the burn page has
# a first measured day within a minute of start rather than after one interval.
echo "[worker] seeding the burn scan ..."
python scripts/ingest.py --burn-scan ||   echo "[worker] burn scan incomplete; the watch loop will continue it" >&2

# Fill the days BEFORE this worker existed, if asked to.
#
# The live scan can only date what it watches happen (tick logs carry no
# timestamp), so a fresh deployment's burn series starts at its own cold start
# and the page shows one partial day. QDR_BURN_BACKFILL_DAYS counts the days
# before that, dating each tick by measurement instead of by observation.
#
# DEFAULTS ON, at 4 days, which is a deliberate reversal worth explaining.
#
# It costs hundreds of heavy calls against a Bob node that is usually someone
# else's, so the polite default is off, and that is how this shipped first. But
# the deployment this image actually serves is installed by a watcher that pulls
# the image and runs it — it sets no environment, so an opt-in flag there can
# never be switched on, and the burn page would stay at one partial day forever.
#
# A default nobody can reach is not a choice, it is a permanent off. So the cost
# is paid once per deployment instead: the run is recorded in the store, and
# every restart after it skips the window rather than re-scanning it. Operators
# who do control their environment can still set QDR_BURN_BACKFILL_DAYS=0.
#
# Four days is one Qubic epoch (~4.4 days), which is the span the page's own
# reconciliation is measured over — less would leave the epoch view short.
BURN_BACKFILL_DAYS="${QDR_BURN_BACKFILL_DAYS:-4}"
if [ "$BURN_BACKFILL_DAYS" -gt 0 ] 2>/dev/null; then
  # Budget scales with the ask. A day of chain is ~134,000 ticks at the measured
  # 1.55 ticks/s, and a call covers 500 — so ~270 calls per day, plus headroom.
  # A fixed default would silently cover only the first day and leave the rest
  # for the next restart, which reads as a backfill that never finishes.
  BURN_BACKFILL_CALLS="${QDR_BURN_BACKFILL_CALLS:-$(( BURN_BACKFILL_DAYS * 300 + 100 ))}"

  # IN THE BACKGROUND, because it is slow and nothing waits on it.
  #
  # Measured against the public node: one 500-tick chunk takes ~2.1 s, so a day
  # is ~9 minutes and the 4-day default is ~38. Run in the foreground that is 38
  # minutes in which the watch loop has not started — no live burn scan, and no
  # balance snapshots. The snapshots are the part that cannot wait: an epoch
  # boundary is not replayable, so one missed while backfilling is missed for
  # good, and the backfill would have cost exactly the data it cannot recover.
  #
  # The two do not collide. The backfill writes whole measured days and the live
  # scan writes the running one; `put_burn_bucket` supersedes by containment, and
  # both are idempotent per day. The store is WAL-mode SQLite, which is built for
  # one writer plus readers — and these two writers touch different days.
  # It repeats, because a day does not stay filled. Midnight turns the running
  # day into a finished one that only the live scan's slice covers, and the
  # tick-window marker cannot notice: a new day lies outside every window it
  # recorded. So this asks the day-level question instead — "is the last
  # complete day in the report?" — and the answer changes once per day.
  #
  # Cheap when there is nothing to do: the check reads the store and returns
  # without touching a node, so the idle cost of the hourly pass is one local
  # query. Work only happens on the day a real gap appears.
  echo "[worker] backfilling the last ${BURN_BACKFILL_DAYS} day(s) of burns in the background"
  echo "[worker]   (~9 min per day against the public node; the watch loop starts now)"
  (
    while true; do
      python scripts/ingest.py --burn-backfill --burn-backfill-days "${BURN_BACKFILL_DAYS}" --burn-calls "${BURN_BACKFILL_CALLS}" ||         echo "[worker] burn backfill pass incomplete; retrying later" >&2
      sleep "${QDR_BURN_BACKFILL_INTERVAL:-3600}"
    done
  ) &
fi

# One mining reading before the watch loop, for the same reason the burn scan is
# seeded: the page should have a measurement in hand at start, not after the
# first interval. Two points make a curve, so the loop fills it out from here.
echo "[worker] taking a first mining sample ..."
python scripts/ingest.py --mining-sample ||   echo "[worker] no node answered; the watch loop will keep trying" >&2

python scripts/ingest.py --export || true

# The watch loop survives RPC errors on its own, but it cannot start at all if
# the RPC is down at that moment (it needs one tick-info call to find the current
# epoch). A supervisor loop makes that a delay instead of an outage: the service
# comes up by itself when the network does, without anyone restarting a container.
echo "[worker] watching · interval ${INTERVAL}s · mining sampled every ${MINING_INTERVAL}s"
while true; do
  python scripts/ingest.py --watch --interval "${INTERVAL}"     --mining-interval "${MINING_INTERVAL}" --export-each ||     echo "[worker] watch exited ($?); retrying in 60s" >&2
  sleep 60
done
