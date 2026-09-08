# Qubic Decentralization Report — API + bundled dashboard.
# Multi-stage: wheels are built once, so the runtime image carries no compiler.
FROM python:3.12-slim AS build
WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    QUBIC_RPC_BASE=https://rpc.qubic.org \
    DATA_DIR=/data \
    PORT=8000

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Application code. api/sample ships with the image so the service can always
# render something, even before the first live RPC pull succeeds.
COPY qdr/ ./qdr/
COPY api/ ./api/
COPY dashboard/ ./dashboard/
COPY examples/ ./examples/
COPY scripts/ ./scripts/
# A Windows checkout can hand the build CRLF line endings despite .gitattributes
# (the rule only applies on a fresh checkout, not to files already on disk), and
# /bin/sh then fails on the first line with "Illegal option". Normalising here
# makes the image independent of how the build host stores its files.
RUN sed -i 's/\r$//' scripts/*.sh && chmod +x scripts/*.sh
COPY data/self_reporting/ ./data/self_reporting/
COPY docs/ ./docs/

# The /how-it-works page is generated from docs/CONCEPT*.md at build time, so the
# image can never ship a page that disagrees with the concept it was built from.
RUN python scripts/build_how_it_works.py

# The RPC cache and the persistent store both live in the volume (DATA_DIR=/data),
# not in the image layer: sealed epochs must survive image upgrades.
RUN mkdir -p /data/raw
VOLUME ["/data"]

# Runs as root, like the other services on the host this deploys to
# (qubic_spotlight, qubic_doge_stats). They bind-mount a root-owned host
# directory — /root/<service>/data:/data — and a non-root container cannot
# write there: the store failed to open, every store-backed endpoint answered
# 500, and the report stayed empty. Fixing that from the image side would mean
# asking the host's admin to chown one directory differently from every other
# service they run, so the image follows the established convention instead.
#
# The trade-off is deliberate and worth naming: this container writes as root,
# so a compromise of this process is a compromise of the mounted directory. The
# service is a read-only public report with no authentication, no user input
# that reaches the filesystem, and no secrets — it fetches public RPC data and
# serves derived numbers. USER qdr is the better default and is one line away
# if the host ever mounts a directory this image may own.

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

# Runs the API and the ingest worker together by default, so that pulling this
# image and starting one container produces a working report. Set QDR_ROLE=api
# or =ingest to run just one half (docker-compose.yaml does exactly that).
ENTRYPOINT ["sh", "scripts/entrypoint.sh"]
