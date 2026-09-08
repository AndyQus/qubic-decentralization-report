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
#   3. Export after every pass, so dashboard/data.js and api/sample/*.json can
#      never drift behind the store the way they did before.
set -eu

BACKFILL="${QDR_BACKFILL_EPOCHS:-10}"
INTERVAL="${QDR_INGEST_INTERVAL:-300}"

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

python scripts/ingest.py --export || true

# The watch loop survives RPC errors on its own, but it cannot start at all if
# the RPC is down at that moment (it needs one tick-info call to find the current
# epoch). A supervisor loop makes that a delay instead of an outage: the service
# comes up by itself when the network does, without anyone restarting a container.
echo "[worker] watching · interval ${INTERVAL}s"
while true; do
  python scripts/ingest.py --watch --interval "${INTERVAL}" --export-each ||     echo "[worker] watch exited ($?); retrying in 60s" >&2
  sleep 60
done
