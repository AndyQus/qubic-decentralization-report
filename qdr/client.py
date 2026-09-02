"""Qubic RPC 2.0 ingestion client.

Talks to https://rpc.qubic.org and caches every raw pull under data/raw/ so that
analyses are reproducible and we stay polite to the public endpoint.

Endpoints used (verified 2026-09-02, see docs/DATA_SOURCES.md):
  GET /v1/tick-info
  GET /v1/latest-stats
  GET /v1/epochs/{epoch}/computors
  GET /v1/rich-list?page=&pageSize=
  GET /identities/{identity}/transfer-transactions   (for revenue derivation)

Per-computor revenue is NOT a single endpoint; it is derived from the arbitrator's
epoch-boundary payouts to each computor identity (see derive_computor_revenue).

Note: some hosted environments cannot reach rpc.qubic.org directly. The client is
network-agnostic — point BASE_URL at any mirror, or load cached fixtures via
CachedClient(offline=True).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

BASE_URL = os.environ.get("QUBIC_RPC_BASE", "https://rpc.qubic.org")
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "data" / "raw"


class QubicRPCError(RuntimeError):
    pass


class CachedClient:
    """Thin RPC client with a file cache keyed by request path.

    offline=True never hits the network; it only reads the cache and raises if a
    key is missing. Useful for reproducible analysis and for testing.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        cache_dir: Path | str = DEFAULT_CACHE,
        offline: bool = False,
        timeout: int = 20,
        min_interval_s: float = 0.2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.timeout = timeout
        self.min_interval_s = min_interval_s
        self._last_call = 0.0

    # -- low level ---------------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        safe = key.strip("/").replace("/", "__").replace("?", "__q__").replace("&", "_").replace("=", "-")
        return self.cache_dir / f"{safe}.json"

    def _get(self, path: str, use_cache: bool = True) -> Any:
        cache_file = self._cache_path(path)
        if use_cache and cache_file.exists():
            return json.loads(cache_file.read_text())
        if self.offline:
            raise QubicRPCError(f"offline and not cached: {path}")
        # be polite
        dt = time.time() - self._last_call
        if dt < self.min_interval_s:
            time.sleep(self.min_interval_s - dt)
        url = f"{self.base_url}{path}"
        resp = requests.get(url, timeout=self.timeout)
        self._last_call = time.time()
        if resp.status_code != 200:
            raise QubicRPCError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        cache_file.write_text(json.dumps(data, indent=2))
        return data

    # -- typed calls -------------------------------------------------------
    def tick_info(self) -> dict:
        return self._get("/v1/tick-info", use_cache=False)["tickInfo"]

    def latest_stats(self) -> dict:
        return self._get("/v1/latest-stats", use_cache=False)["data"]

    def current_epoch(self) -> int:
        return int(self.tick_info()["epoch"])

    def computors(self, epoch: int) -> list[str]:
        """Return the list of computor identities for an epoch."""
        data = self._get(f"/v1/epochs/{epoch}/computors")
        return list(data["computors"]["identities"])

    def rich_list(self, page: int = 1, page_size: int = 100) -> dict:
        return self._get(f"/v1/rich-list?page={page}&pageSize={page_size}")

    def identity_transfers(self, identity: str) -> list[dict]:
        data = self._get(f"/identities/{identity}/transfer-transactions")
        # shape may nest under "transferTransactionsPerTick" or similar; normalize best-effort
        if isinstance(data, dict):
            for k in ("transferTransactionsPerTick", "transactions", "data"):
                if k in data:
                    return data[k] if isinstance(data[k], list) else [data[k]]
        return data if isinstance(data, list) else [data]


# -- revenue derivation ----------------------------------------------------

# The Arbitrator identity distributes computor revenue at each epoch boundary.
# Confirm against a direct pull before trusting in production (see DATA_SOURCES §4).
ARBITRATOR_IDENTITY = os.environ.get(
    "QUBIC_ARBITRATOR",
    "AFZPUAIYVPNUYGJRQVLUKOPPVLHAZQTGLYAAUUNBXFTVTAMSBKQBLEIEPCVJF",
)


def derive_computor_revenue(
    client: CachedClient,
    epoch: int,
    arbitrator: str = ARBITRATOR_IDENTITY,
) -> dict[str, int]:
    """Derive per-computor revenue for an epoch from arbitrator payout transfers.

    Returns {computor_identity: amount}. Amounts come from transfers whose source
    is the arbitrator and whose destination is a computor identity of that epoch.

    This is intentionally source-agnostic about the exact transfer JSON shape: it
    looks for the common {sourceId,destId,amount} triples. Adapt field names once
    verified against a live pull.
    """
    computors = set(client.computors(epoch))
    revenue: dict[str, int] = {c: 0 for c in computors}
    transfers = client.identity_transfers(arbitrator)
    for entry in transfers:
        # entry may itself wrap a per-tick list of transactions
        txs = entry.get("transactions", [entry]) if isinstance(entry, dict) else []
        for tx in txs:
            src = tx.get("sourceId") or tx.get("source")
            dst = tx.get("destId") or tx.get("destination")
            amt = tx.get("amount", 0)
            if src == arbitrator and dst in computors:
                revenue[dst] = revenue.get(dst, 0) + int(amt)
    return revenue
