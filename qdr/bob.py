"""Bob node client — the source that actually carries computor revenue.

Why this exists: the public RPC does not expose the epoch-end payouts at all
(DATA_SOURCES §6.2 — computor identities only emit `amount = 0` solution
submissions, and no inbound transfer is returned for them). A Bob node keeps the
full event log, including the **virtual end-epoch tick** where the protocol
credits every computor. Verified against `bob.qubic.li` on 2026-09-07:

    qubic_getEndEpochLogs [228]  ->  676 of 676 computors paid, 178,477,462,349 QU
    payout source: AAAA...FXIB (the null address = protocol emission)

That is the whole revenue problem solved, and with history: epochs back to at
least 220 are served, so the report does not have to start from zero.

Protocol: JSON-RPC 2.0 over POST, positional params. Same endpoint the
qubic_dividend_of_shares project uses.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

# Public Bob node. Point QDR_BOB_URL at your own node (default RPC port 40420)
# to avoid depending on someone else's, e.g. http://10.0.0.5:40420
BOB_URL = os.environ.get("QDR_BOB_URL", "https://bob.qubic.li/qubic")

# The end-epoch log is one big response (~6k entries), so it is cached on disk
# like the RPC pulls: an epoch's payouts never change once written.
_DATA_DIR = os.environ.get("DATA_DIR")
DEFAULT_CACHE = (
    Path(_DATA_DIR) / "bob" if _DATA_DIR
    else Path(__file__).resolve().parent.parent / "data" / "bob"
)

QU_TRANSFER = "QU_TRANSFER"


class BobError(RuntimeError):
    """Bob could not answer. Distinct from 'Bob answered: no data for that epoch'."""


class BobUnavailable(BobError):
    """Bob has no data for this epoch and never will (pruned / before its history)."""


class BobClient:
    """Minimal JSON-RPC client for a Bob node, with an on-disk cache."""

    def __init__(
        self,
        url: str = BOB_URL,
        cache_dir: Path | str = DEFAULT_CACHE,
        timeout: int = 180,
        max_attempts: int = 3,
    ) -> None:
        self.url = url
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_attempts = max_attempts

    # -- transport ---------------------------------------------------------
    def _call(self, method: str, params: list) -> Any:
        """One JSON-RPC call, retried on transport errors.

        Note the params are positional: {"epoch": N} is rejected by the node with
        "Missing epoch parameter".
        """
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last: Optional[Exception] = None
        for attempt in range(self.max_attempts):
            try:
                resp = requests.post(self.url, json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                last = BobError(f"{method} -> HTTP {resp.status_code}: {resp.text[:160]}")
                time.sleep(1.5 * (attempt + 1))
                continue
            try:
                data = resp.json()
            except ValueError as e:
                last = BobError(f"{method}: invalid JSON ({e})")
                time.sleep(1.5 * (attempt + 1))
                continue
            if isinstance(data, dict) and data.get("error"):
                # a protocol-level error is an answer, not a transport failure
                raise BobUnavailable(f"{method}: {data['error']}")
            return data.get("result") if isinstance(data, dict) else data
        raise BobError(f"{method} failed after {self.max_attempts} attempts: {last}")

    # -- typed calls -------------------------------------------------------
    def status(self) -> dict:
        """Node status: version, epoch being processed, indexing/fetching ticks."""
        res = self._call("qubic_status", [])
        return res if isinstance(res, dict) else {}

    def current_epoch(self) -> Optional[int]:
        try:
            return int(self.status().get("currentProcessingEpoch"))
        except (BobError, TypeError, ValueError):
            return None

    def end_epoch_logs(self, epoch: int, use_cache: bool = True) -> list[dict]:
        """All log events of an epoch's virtual end-epoch tick.

        This single call contains every rollover distribution of that epoch. A
        sealed epoch's logs never change, so they are cached permanently.
        """
        cache_file = self.cache_dir / f"end_epoch_{epoch}.json"
        if use_cache and cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass  # corrupt cache: fall through and refetch

        res = self._call("qubic_getEndEpochLogs", [epoch])
        logs = res if isinstance(res, list) else []
        # Only cache a response that actually carries the epoch's payouts. A
        # running epoch returns a near-empty list that must not be frozen.
        if logs and any(e.get("logTypename") == QU_TRANSFER
                        for e in logs if isinstance(e, dict)):
            try:
                cache_file.write_text(json.dumps(logs), encoding="utf-8")
            except OSError:
                pass
        return logs

    def tick_logs(self, from_tick: int, to_tick: int) -> list[dict]:
        """Log events for a tick range.

        The node caps a request at ~1000 ticks regardless of the range asked for,
        so callers must page. Kept for the linkage layer (following payouts on to
        where they end up); revenue only needs end_epoch_logs.
        """
        res = self._call("qubic_getLogs",
                         [{"fromTick": int(from_tick), "toTick": int(to_tick)}])
        return res if isinstance(res, list) else []


# -- parsing ---------------------------------------------------------------

def _amount(body: dict) -> int:
    for key in ("amount", "value", "qu"):
        if key in body:
            try:
                return int(body[key])
            except (TypeError, ValueError):
                return 0
    return 0


def iter_qu_transfers(logs: list[dict]) -> list[dict]:
    """Flatten end-epoch log entries to {from,to,amount,tick}.

    End-epoch entries nest the payload under `body` and name the type
    `logTypename` (the per-tick log shape differs: flat, `source`/`destination`,
    `logTypeName`). Both spellings are handled so one parser covers both calls.
    """
    out: list[dict] = []
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        type_name = entry.get("logTypename") or entry.get("logTypeName")
        if type_name != QU_TRANSFER:
            continue
        body = entry.get("body") if isinstance(entry.get("body"), dict) else entry
        src = body.get("from") or body.get("source")
        dst = body.get("to") or body.get("destination")
        amt = _amount(body)
        if not src or not dst or amt <= 0:
            continue
        try:
            tick = int(entry.get("tick") or 0)
        except (TypeError, ValueError):
            tick = 0
        out.append({"sourceId": src, "destId": dst, "amount": amt, "tick": tick,
                    "txId": entry.get("txHash") or entry.get("logId")})
    return out


def computor_revenue(logs: list[dict], computors: list[str]) -> tuple[dict[str, int], list[dict]]:
    """Per-computor revenue for an epoch, plus the transfers backing it.

    Returns ({identity: amount}, matched_transfers). Every identity of the epoch
    is present, including those credited nothing — a missing key would be
    indistinguishable from a zero payout.
    """
    slots = set(computors)
    revenue: dict[str, int] = {c: 0 for c in slots}
    matched: list[dict] = []
    for t in iter_qu_transfers(logs):
        if t["destId"] in slots:
            revenue[t["destId"]] += t["amount"]
            matched.append(t)
    return revenue, matched


def scan_tick_transfers(
    bob: "BobClient",
    from_tick: int,
    to_tick: int,
    span: int = 1000,
    max_calls: int = 40,
) -> list[dict]:
    """Collect QU transfers across a tick range, paging around the node's cap.

    The node caps a request at ~1000 ticks whatever range is asked for, so this
    walks the range in chunks. `max_calls` bounds the work: tracing every tick of
    a 1.4M-tick epoch is not the point — sampling the ticks right after the epoch
    payout is where operators move their revenue on.
    """
    out: list[dict] = []
    tick = int(from_tick)
    end = int(to_tick)
    calls = 0
    while tick <= end and calls < max_calls:
        chunk_end = min(tick + span - 1, end)
        try:
            out.extend(iter_qu_transfers(bob.tick_logs(tick, chunk_end)))
        except BobError:
            pass  # a gap in the log must not abort the whole scan
        tick = chunk_end + 1
        calls += 1
    return out


def payout_sources(matched: list[dict]) -> dict[str, int]:
    """Which identities paid the computors, and how many transfers each sent.

    Measured on epoch 228: a single source, the null address — i.e. the credit is
    protocol emission rather than a wallet paying out. Worth reporting, because a
    second source appearing would change what the revenue figure means.
    """
    counts: dict[str, int] = {}
    for t in matched:
        counts[t["sourceId"]] = counts.get(t["sourceId"], 0) + 1
    return counts
