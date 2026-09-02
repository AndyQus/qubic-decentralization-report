"""Decentralization Report API.

Serves the computed report as JSON for explorers to embed. Read-only, CORS-open,
versioned under /v1. If the live pipeline can reach rpc.qubic.org it serves fresh
data; otherwise it falls back to the static snapshots in api/sample so the service
(and the dashboard) always have something to render.

Run:
    pip install -r requirements.txt
    uvicorn api.server:app --reload --port 8000

Then e.g. GET http://localhost:8000/v1/report/latest
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as e:  # pragma: no cover
    raise SystemExit("Install deps first: pip install -r requirements.txt") from e

from qdr.client import CachedClient, derive_computor_revenue, QubicRPCError
from qdr.report import build_epoch_report, build_timeseries, build_dashboard_bundle
from qdr.clustering import load_registry

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "api" / "sample"

app = FastAPI(title="Qubic Decentralization Report API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _sample(name: str) -> dict:
    p = SAMPLE / name
    if not p.exists():
        raise HTTPException(status_code=503, detail=f"no data available ({name})")
    return json.loads(p.read_text())


def _live_or_sample_latest() -> dict:
    """Try to build a live 'latest' report; fall back to the sample snapshot."""
    try:
        client = CachedClient()
        epoch = client.current_epoch()
        return build_epoch_report(client, epoch)
    except (QubicRPCError, Exception):
        return _sample("report_latest.json")


@app.get("/")
def root():
    # convenience: send humans to the bundled dashboard, which will fetch the API
    return RedirectResponse(url="/dashboard/")


@app.get("/api")
def api_index():
    return {
        "service": "Qubic Decentralization Report API",
        "version": app.version,
        "endpoints": [
            "/v1/report/latest",
            "/v1/report/{epoch}",
            "/v1/clusters/{epoch}",
            "/v1/metrics/timeseries",
            "/v1/report/{epoch}/snapshot.json",
            "/v1/dashboard-data",
        ],
        "dashboard": "/dashboard/",
        "docs": "/docs",
    }


@app.get("/v1/report/latest")
def report_latest():
    return _live_or_sample_latest()


@app.get("/v1/report/{epoch}")
def report_epoch(epoch: int):
    try:
        client = CachedClient()
        return build_epoch_report(client, epoch)
    except Exception:
        data = _sample("report_latest.json")
        if data.get("epoch") == epoch:
            return data
        raise HTTPException(status_code=404, detail=f"epoch {epoch} not available offline")


@app.get("/v1/clusters/{epoch}")
def clusters_epoch(epoch: int):
    rep = report_epoch(epoch)
    return {"epoch": rep["epoch"], "clusters": rep["clusters"]}


@app.get("/v1/metrics/timeseries")
def metrics_timeseries():
    try:
        client = CachedClient()
        epoch = client.current_epoch()
        epochs = list(range(max(0, epoch - 8), epoch + 1))
        return build_timeseries(client, epochs, registry=load_registry())
    except Exception:
        return _sample("timeseries.json")


@app.get("/v1/report/{epoch}/snapshot.json")
def snapshot(epoch: int):
    return JSONResponse(report_epoch(epoch))


DASHBOARD_DIR = ROOT / "dashboard"


@app.get("/v1/dashboard-data")
def dashboard_data():
    """One bundle for the SPA: latest report + timeseries + per-epoch bubbles."""
    try:
        client = CachedClient()
        epoch = client.current_epoch()
        epochs = list(range(max(0, epoch - 8), epoch + 1))
        return build_dashboard_bundle(client, epochs, registry=load_registry())
    except Exception:
        # assemble from the static samples
        rep = _sample("report_latest.json")
        ts = _sample("timeseries.json")
        try:
            ec = _sample("epoch_clusters.json")
        except HTTPException:
            ec = {"epochs": []}
        return {"report": rep, "timeseries": {"series": ts.get("series", [])},
                "epoch_clusters": {"epochs": ec.get("epochs", [])}, "sample": True}


# Serve the reference dashboard as static files at /dashboard (mounted last so the
# /v1 API routes above always take precedence). When opened via http the dashboard
# fetches this same origin's /v1/dashboard-data automatically.
if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")

_EXAMPLES = ROOT / "examples"
if _EXAMPLES.exists():
    app.mount("/examples", StaticFiles(directory=str(_EXAMPLES), html=True), name="examples")
