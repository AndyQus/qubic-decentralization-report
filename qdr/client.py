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
epoch-boundary payouts to each computor identity — see qdr/revenue.py, which does
the epoch-scoping, full pagination and reconciliation that derivation requires.

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
# DATA_DIR lets a container point the RPC cache at a writable volume; without it
# the cache stays inside the repo checkout (data/raw), which is what local runs want.
_DATA_DIR = os.environ.get("DATA_DIR")
DEFAULT_CACHE = (
    Path(_DATA_DIR) / "raw" if _DATA_DIR
    else Path(__file__).resolve().parent.parent / "data" / "raw"
)


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
        try:
            resp = requests.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise QubicRPCError(f"GET {path} failed: {e}") from e
        self._last_call = time.time()
        if resp.status_code != 200:
            raise QubicRPCError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise QubicRPCError(f"GET {path}: invalid JSON ({e})") from e
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

    def epoch_lengths(self, count: int = 6) -> list[int]:
        """Recent epoch lengths in ticks, newest last.

        Measured lengths vary a lot (1.08M-2.29M ticks over epochs 223-229), so
        anything showing "progress through the epoch" has to derive the expected
        length from recent history rather than assume a constant.
        """
        try:
            per_epoch = self._get("/v1/status", use_cache=False).get(
                "lastProcessedTicksPerEpoch") or {}
        except (QubicRPCError, AttributeError, TypeError):
            return []
        pairs = []
        for k, v in per_epoch.items():
            try:
                pairs.append((int(k), int(v)))
            except (TypeError, ValueError):
                continue
        pairs.sort()
        lengths = [b[1] - a[1] for a, b in zip(pairs, pairs[1:]) if b[1] > a[1]]
        return lengths[-count:]

    def network_pulse(self) -> dict:
        """Cheap, always-fresh view of the running epoch.

        Two things move on very different timescales (measured 2026-09-07):
        the tick advances ~2.7/s and epoch quality drifts continuously, while the
        computor list, revenue and clustering only change at an epoch boundary —
        roughly every 4.4 days. So the live view polls this, and the analysis
        pipeline stays on its slow cadence instead of recomputing 676 operators
        every minute for data that has not moved.
        """
        # latest-stats alone answers almost everything; tick-info only adds
        # initialTick. A public endpoint hiccups now and then, so a pulse must
        # survive one of the two failing rather than dropping the whole update.
        stats = self.latest_stats()
        try:
            info = self.tick_info()
        except QubicRPCError:
            info = {}
        first = int(info.get("initialTick") or 0)
        tick = int(stats.get("currentTick") or info.get("tick") or 0)
        return {
            "epoch": int(stats.get("epoch") or info.get("epoch") or 0),
            "tick": tick,
            "initial_tick": first,
            "ticks_in_epoch": int(stats.get("ticksInCurrentEpoch") or 0),
            "empty_ticks": int(stats.get("emptyTicksInCurrentEpoch") or 0),
            "tick_quality": float(stats.get("epochTickQuality") or 0.0),
            "recent_tick_quality": float(stats.get("last10000TickQuality") or 0.0),
            "active_addresses": int(stats.get("activeAddresses") or 0),
            "circulating_supply": int(stats.get("circulatingSupply") or 0),
            "burned_qus": int(stats.get("burnedQus") or 0),
            "price": float(stats.get("price") or 0.0),
            "market_cap": int(stats.get("marketCap") or 0),
            "timestamp": int(stats.get("timestamp") or 0),
            "expected_epoch_ticks": self._expected_epoch_ticks(),
        }

    def _expected_epoch_ticks(self, fallback: int = 1_400_000) -> int:
        """Average of recent epoch lengths, for the progress indicator."""
        lengths = self.epoch_lengths()
        return int(sum(lengths) / len(lengths)) if lengths else fallback

    def balance(self, identity: str) -> dict:
        """Current balance record for an identity.

        Returns the `balance` object, which carries cumulative `incomingAmount` /
        `outgoingAmount` counters alongside the spendable balance. Those counters
        are what the balance-delta revenue method samples (see qdr/revenue.py) —
        they are current-state only, so history has to be snapshotted by us.
        """
        data = self._get(f"/v1/balances/{identity}", use_cache=False)
        return data["balance"] if isinstance(data, dict) and "balance" in data else data

    def identity_transfers(self, identity: str) -> list[dict]:
        data = self._get(f"/identities/{identity}/transfer-transactions")
        # shape may nest under "transferTransactionsPerTick" or similar; normalize best-effort
        if isinstance(data, dict):
            for k in ("transferTransactionsPerTick", "transactions", "data"):
                if k in data:
                    return data[k] if isinstance(data[k], list) else [data[k]]
        return data if isinstance(data, list) else [data]


# Revenue derivation lives in qdr/revenue.py — it needs epoch tick windows,
# exhaustive pagination and reconciliation, which is more than a client concern.
# The arbitrator constant moved there too (revenue.ARBITRATOR_IDENTITY).
