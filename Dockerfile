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
COPY data/self_reporting/ ./data/self_reporting/

# The RPC cache lives in the volume, not in the image layer.
RUN mkdir -p /data/raw && useradd -r -u 10001 qdr && chown -R qdr:qdr /app /data
USER qdr
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
