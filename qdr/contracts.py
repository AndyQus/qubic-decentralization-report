"""Smart-contract registry — turning a contract index into a contract name.

The burn panel used to print "Contract 13", because this repo had no index→name
source and inventing one was not an option. There is one: Qubic publishes the
list the official explorer itself renders at

    https://static.qubic.org/v1/general/data/smart_contracts.json

confirmed 2026-09-20 by watching explorer.qubic.org/network/assets/smart-contracts
load exactly that file. It carries `contractIndex`, `name` (QSWAP) and `label`
(QSwap) for all 28 contracts deployed so far, so index 13 — the one in our own
burn data — resolves to QSwap.

Two properties make it the right source rather than a hardcoded table:

  * it is maintained by Qubic, not by us, so a contract deployed next month
    appears without anyone editing this repo;
  * it is the same file the official explorer reads, so a name here and a name
    there cannot drift apart.

What this module will NOT do is guess. An index missing from the registry is
reported as missing and the page keeps showing the bare index, because a plausible
invented name is worse than an honest number — the same rule the rest of this
repo follows for unattributed operators.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import requests

REGISTRY_URL = os.environ.get(
    "QDR_CONTRACTS_URL",
    "https://static.qubic.org/v1/general/data/smart_contracts.json",
)

# Cached on disk beside the other pulls, so a restart does not refetch and an
# outage at static.qubic.org costs nothing: contract names change about as often
# as contracts are deployed.
_DATA_DIR = os.environ.get("DATA_DIR")
DEFAULT_CACHE = (
    Path(_DATA_DIR) / "raw" if _DATA_DIR
    else Path(__file__).resolve().parent.parent / "data" / "raw"
)
CACHE_FILE = "smart_contracts.json"

# A day. New contracts are rare (28 in the chain's lifetime), and the page must
# not depend on a third-party fetch per request.
TTL_S = int(os.environ.get("QDR_CONTRACTS_TTL", str(24 * 3600)))

_lock = threading.RLock()
_memo: dict = {"at": 0.0, "data": None}


def _cache_path() -> Path:
    try:
        DEFAULT_CACHE.mkdir(parents=True, exist_ok=True)
        return DEFAULT_CACHE / CACHE_FILE
    except OSError:
        # Same fallback the RPC cache uses: an unwritable volume must not stop
        # the lookup, it only costs the on-disk copy.
        alt = Path(tempfile.gettempdir()) / "qdr-fallback" / "raw"
        alt.mkdir(parents=True, exist_ok=True)
        return alt / CACHE_FILE


def _parse(payload: dict) -> dict[int, dict]:
    """Index → {name, label, address} from the published registry shape."""
    out: dict[int, dict] = {}
    for entry in (payload or {}).get("smart_contracts") or []:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("contractIndex")
        if idx is None:
            continue
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        name = entry.get("name") or entry.get("label")
        if not name:
            continue
        out[idx] = {
            "index": idx,
            "name": str(name),
            # `label` is the human spelling ("QSwap"); `name` the ticker
            # ("QSWAP"). The page wants the former and falls back to the latter.
            "label": str(entry.get("label") or name),
            "address": entry.get("address"),
        }
    return out


def _read_cache() -> Optional[tuple[float, dict[int, dict]]]:
    path = _cache_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return path.stat().st_mtime, _parse(raw)
    except (OSError, ValueError):
        return None


def registry(force: bool = False) -> dict[int, dict]:
    """The contract index → name map, cached in memory and on disk.

    Never raises: a failed fetch falls back to the disk copy, and a missing disk
    copy yields an empty map. An empty map means every index renders as an index,
    which is exactly what the page did before this module existed — a degradation,
    not a breakage.
    """
    now = time.time()
    with _lock:
        if not force and _memo["data"] is not None and now - _memo["at"] < TTL_S:
            return _memo["data"]

        cached = _read_cache()
        if not force and cached and now - cached[0] < TTL_S:
            _memo.update(at=now, data=cached[1])
            return cached[1]

        try:
            resp = requests.get(REGISTRY_URL, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            parsed = _parse(payload)
            if parsed:
                try:
                    _cache_path().write_text(json.dumps(payload), encoding="utf-8")
                except OSError:
                    pass        # the memo still holds it for this process
                _memo.update(at=now, data=parsed)
                return parsed
        except (requests.RequestException, ValueError):
            pass

        # Upstream unreachable: a stale copy is far better than no names at all.
        stale = cached[1] if cached else {}
        _memo.update(at=now, data=stale)
        return stale


def name_for(index: Optional[int]) -> Optional[str]:
    """Display name for a contract index, or None when it is not in the registry.

    None rather than a placeholder on purpose: the caller shows the bare index,
    which is true, instead of a name nobody published.
    """
    if index is None:
        return None
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    entry = registry().get(idx)
    return entry["label"] if entry else None


def describe(index: Optional[int]) -> dict:
    """Everything known about one contract index, for an API response."""
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return {"index": index, "name": None, "label": None, "address": None,
                "known": False}
    entry = registry().get(idx)
    if not entry:
        return {"index": idx, "name": None, "label": None, "address": None,
                "known": False}
    return {**entry, "known": True}
