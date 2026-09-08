#!/bin/sh
# Container entrypoint: serve the API, and — unless told otherwise — run the
# ingest worker alongside it.
#
# Why both in one container, when docker-compose.yaml runs them as two services:
# the image has to work when it is simply pulled and started, because that is how
# it actually gets deployed. A watcher that installs a new image and runs one
# container never sees a second service defined in a compose file, so the API came
# up alone, no ingest ever ran, and the report sat on "building" forever with a
# perfectly healthy API in front of an empty store.
#
# The compose file still splits them, and that split is still better where it is
# available: a crashing ingest cannot take the dashboard down with it, and the
# logs stay separable. So this is a default, not a policy — QDR_ROLE picks:
#
#   both   (default) API + ingest in one container; correct for a plain `docker run`
#   api               API only; what docker-compose.yaml's first service wants
#   ingest            worker only; the second service
#
set -eu

ROLE="${QDR_ROLE:-both}"
PORT="${PORT:-8000}"

start_api() {
  exec uvicorn api.server:app --host 0.0.0.0 --port "$PORT"
}

case "$ROLE" in
  api)
    echo "[entrypoint] role=api — ingest disabled; something else must fill the store"
    start_api
    ;;
  ingest)
    echo "[entrypoint] role=ingest — worker only"
    exec sh scripts/worker.sh
    ;;
  both)
    echo "[entrypoint] role=both — API and ingest in one container"
    # The worker supervises itself (it retries its own watch loop), so a crash
    # here must not kill the API: an unreachable RPC would otherwise take the
    # whole report offline instead of leaving it serving what it already has.
    sh scripts/worker.sh &
    start_api
    ;;
  *)
    echo "[entrypoint] unknown QDR_ROLE='$ROLE' (use: both | api | ingest)" >&2
    exit 64
    ;;
esac
