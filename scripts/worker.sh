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
#   2. Watch. Refreshes the running epoch, takes the balance snapshots that
#      revenue derivation needs (an epoch boundary is not replayable, so a
#      missed one is missed for good), and seals each epoch as it closes.
#   3. Export after every pass, so dashboard/data.js and api/sample/*.json can
#      never drift behind the store the way they did before.
set -eu

BACKFILL="${QDR_BACKFILL_EPOCHS:-10}"
INTERVAL="${QDR_INGEST_INTERVAL:-300}"

echo "[worker] backfilling last ${BACKFILL} epochs …"
# A failed backfill must not stop the service: --watch would still recover the
# current epoch, and a transient RPC outage is not a reason to stay down.
python scripts/ingest.py --backfill "${BACKFILL}" || \
  echo "[worker] backfill incomplete; continuing into watch" >&2

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
