"""The task the ant colony is mining, read from the network itself.

Until epoch 239 the task was a file in qubic/core (`data/bpp9000.task`) and this
module found it by the hash a node reported. Since core v1.309 -- live from epoch
240 -- there is no file. The targets are built from hourly BTC/USDT prices that the
nodes fetch on-chain through the Price oracle, and they grow by one hour at a time
during the epoch (qubic/core doc/ant_colony_mining.md section 2.3a):

    usdt(H)   = (priceE8(H) + 50000000) / 100000000          whole USDT, half up
    target[t] = 1 if usdt(h) >= usdt(h - 1) else 0            h = baseHour - W + 1 + t

`baseHour` is the last hour the series held when the epoch began. Targets
0 .. W-1 (W = 8760, one year) are the first frame; every hour stored during the
epoch adds one target, up to SHIFT_CAP (168) more. How many have been added by a
given tick is that tick's `shiftLimit`.

Two public peer messages carry all of it, no signature needed:

  * REQUEST_BPP9000_TASK_DATA (80) -- the targets and the shift limit an anchor
    tick is graded on, ready for the scorer;
  * REQUEST_PRICE_SERIES (78) -- the state of the feed: which hour comes next,
    failed attempts, and per hour the tick it was stored in.

Verification is the same idea as before, against a different object: the epoch
context's `dataHash` is K12 of the epoch's first W target bytes, so recomputing it
from message 80 proves the targets shown are the ones the network grades.

Prices: until 2026-11-18 they were dropped here and only directions shown.
Since then -- the user's decision -- the hourly BTC/USDT price the oracle stored
on-chain is kept and shown on the BTC page, always attributed (PRICE_SOURCE).
They are Binance prices relayed by Qubic's consensus; this project still fetches
no exchange API itself and shows no volume.

What a node does not report -- SHIFT_CAP, ADVANCE_THRESHOLD, the default
threshold -- is still read from qubic/core's `src/public_settings.h`, at the
release tag matching the node's version. Cached per version, resolved in a
background thread: the caller gets None until it is done and never waits on
GitHub.
"""
from __future__ import annotations

import ast
import json
import os
import re
import struct
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

RAW_URL = "https://raw.githubusercontent.com/qubic/core/{ref}/{path}"
TAGS_URL = "https://api.github.com/repos/qubic/core/tags?per_page={n}"
SETTINGS_PATH = "src/public_settings.h"

MAX_TAGS = 30
RETRY_AFTER_S = 600
FETCH_TIMEOUT_S = 15.0

# Hour H is H hours after 2022-04-13 12:00:00 UTC (doc section 2.3a).
HOUR_ZERO = 1649851200
HOUR_S = 3600
# The leader may ask the oracle for hour H no earlier than this after H
# (BTC_FEED_QUERY_DELAY_SEC); before that the hour cannot appear.
FEED_QUERY_DELAY_S = 300
# Point flag bit 0: the oracle failed for 24 h and the previous price was reused;
# the protocol counts such an hour as "up".
FLAG_FILLED = 1

TASK_DATA_HEADER = struct.Struct("<IIHBBII")             # 20 bytes
PRICE_SERIES_HEADER = struct.Struct("<IHBBIIIqq32sQQQQQQII")  # 124 bytes
PRICE_POINT = struct.Struct("<IIIq")                      # 20 bytes, packed

# How much of the year-long first frame the live views carry before the epoch's
# own hours: two days is enough to see the pattern the live hours continue.
RECENT_FRAME_HOURS = 48

PRICE_SOURCE = "Binance BTC/USDT via Qubic's Price oracle (stored on-chain, read from a node)"

_DATA_DIR = os.environ.get("DATA_DIR")
DEFAULT_CACHE_FILE = (
    Path(_DATA_DIR) / "raw" if _DATA_DIR
    else Path(__file__).resolve().parent.parent / "data" / "raw"
) / "ant_settings.json"

Fetch = Callable[[str], bytes]


def hour_at(hour: int) -> int:
    """Unix seconds of protocol hour `hour`."""
    return HOUR_ZERO + int(hour) * HOUR_S


def k12_hex(data: bytes) -> str:
    """KangarooTwelve, 32 bytes, empty customisation -- the core's K12()."""
    from Crypto.Hash import KangarooTwelve
    return KangarooTwelve.new(data=data).read(32).hex()


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "qubic-report"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return resp.read()


# -- wire formats ------------------------------------------------------------

def parse_task_data(payload: bytes) -> Optional[dict]:
    """RESPOND_BPP9000_TASK_DATA (81). None when the node said `valid = 0`."""
    if len(payload) < TASK_DATA_HEADER.size:
        raise ValueError("task data shorter than its header")
    tick, anchor, epoch, valid, _reserved, base_hour, shift_limit = TASK_DATA_HEADER.unpack_from(payload)
    if not valid:
        return None
    targets = bytes(payload[TASK_DATA_HEADER.size:])
    if any(b > 1 for b in targets):
        raise ValueError("task targets must be 0 or 1")
    return {"tick": tick, "anchor_tick": anchor, "epoch": epoch,
            "base_hour": base_hour, "shift_limit": shift_limit, "targets": targets}


def parse_price_series(payload: bytes) -> dict:
    """RESPOND_PRICE_SERIES (79): the feed's state and, per hour, when it was
    stored, whether it was filled in, and the price in USDT (priceE8 / 1e8)."""
    if len(payload) < PRICE_SERIES_HEADER.size:
        raise ValueError("price series shorter than its header")
    (tick, epoch, synced, bound, source, next_hour, failed, _first_fail, _last_fail,
     digest, n_ok, n_timeout, n_unres, n_invalid, n_start_failed, n_filled,
     base_hour, count) = PRICE_SERIES_HEADER.unpack_from(payload)
    points = []
    off = PRICE_SERIES_HEADER.size
    for _ in range(count):
        if off + PRICE_POINT.size > len(payload):
            break
        hour, append_tick, flags, price_e8 = PRICE_POINT.unpack_from(payload, off)
        points.append({"hour": hour, "append_tick": append_tick, "flags": flags,
                       "price": round(price_e8 / 1e8, 2)})
        off += PRICE_POINT.size
    return {
        "tick": tick, "epoch": epoch, "synced": bool(synced), "bound": bool(bound),
        "source": source, "next_hour": next_hour, "failed_attempts": failed,
        "series_digest": digest.hex(), "success": n_ok, "timeout": n_timeout,
        "unresolvable": n_unres, "invalid_reply": n_invalid, "start_failed": n_start_failed,
        "filled": n_filled, "base_hour": base_hour, "points": points,
    }


# -- settings (what no node reports) -----------------------------------------

_CONST_RE = re.compile(
    r"static\s+constexpr\s+unsigned\s+(?:long\s+long|int)\s+(BPP9000_\w+)\s*=\s*([0-9\s*+\-()]+);")


def _eval_int(expr: str) -> int:
    """Evaluate `24 * 365 + 24 * 7` and the like -- integers and + - * only."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            a, b = ev(node.left), ev(node.right)
            return a + b if isinstance(node.op, ast.Add) else a - b if isinstance(node.op, ast.Sub) else a * b
        raise ValueError("unsupported expression: %r" % expr)
    return ev(ast.parse(expr.strip(), mode="eval"))


def parse_settings(text: str) -> dict:
    """Every integer BPP9000_* constant in public_settings.h, by name."""
    return {name: _eval_int(expr) for name, expr in _CONST_RE.findall(text)}


def candidate_refs(version: Optional[int], fetch: Fetch = _http_get) -> list[str]:
    """The release tags of this node version (newest first), then main.

    A node reports its version as the middle number of the release (v1.313.0 ->
    313). Its own tag is what it runs; main is the fallback for a version that has
    no tag yet.
    """
    refs: list[str] = []
    try:
        tags = [t["name"] for t in json.loads(fetch(TAGS_URL.format(n=MAX_TAGS))) if t.get("name")]
    except Exception:
        tags = []
    if version is not None:
        refs += [t for t in tags if re.fullmatch(r"v1\.%d\.\d+" % int(version), t)]
    refs.append("main")
    return refs


def find_settings(version: Optional[int], fetch: Fetch = _http_get) -> Optional[dict]:
    for ref in candidate_refs(version, fetch):
        try:
            settings = parse_settings(fetch(RAW_URL.format(ref=ref, path=SETTINGS_PATH))
                                      .decode("utf-8", "replace"))
        except Exception:
            continue
        if "BPP9000_WINDOW_WIDTH" in settings:
            return {"ref": ref, "constants": settings, "resolved_at": int(time.time())}
    return None


class SettingsResolver:
    """Node version -> core constants, cached, resolved off the caller's thread."""

    def __init__(self, cache_file: Optional[Path] = DEFAULT_CACHE_FILE,
                 fetch: Fetch = _http_get, background: bool = True):
        self.cache_file = cache_file
        self.fetch = fetch
        self.background = background
        self._lock = threading.Lock()
        self._known: dict[str, dict] = {}
        self._failed_at: dict[str, float] = {}
        self._running: set[str] = set()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.cache_file:
            return
        try:
            self._known.update(json.loads(self.cache_file.read_text("utf-8")))
        except Exception:
            pass

    def _save(self) -> None:
        if not self.cache_file:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self._known, indent=1), "utf-8")
        except Exception:
            pass

    def _resolve(self, key: str, version: Optional[int]) -> None:
        try:
            found = find_settings(version, self.fetch)
        except Exception:
            found = None
        with self._lock:
            self._running.discard(key)
            if found is None:
                self._failed_at[key] = time.time()
            else:
                self._known[key] = found
                self._failed_at.pop(key, None)
                self._save()

    def get(self, version: Optional[int]) -> Optional[dict]:
        key = str(version) if version is not None else "main"
        with self._lock:
            self._load()
            if key in self._known:
                return self._known[key]
            if key in self._running:
                return None
            if time.time() - self._failed_at.get(key, 0.0) < RETRY_AFTER_S:
                return None
            self._running.add(key)
        if self.background:
            threading.Thread(target=self._resolve, args=(key, version), daemon=True).start()
            return None
        self._resolve(key, version)
        with self._lock:
            return self._known.get(key)


_resolver = SettingsResolver()


# -- the blocks the pages read -------------------------------------------------

def task_block(data_hash: str, task: Optional[dict], series: Optional[dict] = None,
               version: Optional[int] = None,
               resolver: Optional[SettingsResolver] = None) -> dict:
    """What collect() puts under "task".

    `hash_verified` is True only when K12 of the first frame recomputed here
    equals the node's `dataHash`: every number below is then the one the network
    grades. False means the node answered with targets that do not hash to its
    own context -- shown as a failure, never papered over. None means no task
    data was read (yet); the page says so and shows no numbers.
    """
    out: dict = {"source": "live-feed", "scoring": "rolling-frame",
                 "data_hash": data_hash, "hash_verified": None}
    settings = (resolver or _resolver).get(version)
    consts = (settings or {}).get("constants") or {}
    if settings:
        out["source_ref"] = settings["ref"]
    shift_cap = consts.get("BPP9000_SHIFT_CAP")
    out["shift_cap"] = shift_cap
    out["advance_threshold"] = consts.get("BPP9000_ADVANCE_THRESHOLD")
    out["threshold_default"] = consts.get("BPP9000_SOLUTION_THRESHOLD_DEFAULT")
    if task is None:
        return out

    targets = task["targets"]
    shift_limit = int(task["shift_limit"])
    window = len(targets) - shift_limit
    expected = consts.get("BPP9000_WINDOW_WIDTH")
    if window <= 0 or (expected is not None and window != expected):
        # A node whose answer does not have the shape the core defines is not a
        # task to describe; reported as unverified rather than trusted.
        out["hash_verified"] = False
        out["problem"] = "task data does not match the core's frame width"
        return out
    out["hash_verified"] = bool(data_hash) and k12_hex(targets[:window]) == data_hash.lower()
    base = int(task["base_hour"])
    ones = sum(targets)
    out.update({
        "anchor_tick": task["anchor_tick"],
        "window_width": window,
        "score_max": window,
        "sequence_length": window + shift_cap if shift_cap is not None else None,
        "base_hour": base,
        "base_at": hour_at(base),
        "shift_limit": shift_limit,
        "target_zeros": len(targets) - ones,
        "target_ones": ones,
        "target_unknown": 0,
    })
    if series is not None:
        nxt = series.get("next_hour")
        out["oracle"] = {
            "synced": series.get("synced"),
            "querying": series.get("bound"),
            "next_hour": nxt,
            "next_at": hour_at(nxt) if nxt else None,
            # The earliest the next hour can land: the leader may only ask the
            # oracle FEED_QUERY_DELAY_S after it.
            "next_due_at": hour_at(nxt) + FEED_QUERY_DELAY_S if nxt else None,
            "failed_attempts": series.get("failed_attempts"),
            "success": series.get("success"),
            "timeout": series.get("timeout"),
            "unresolvable": series.get("unresolvable"),
            "invalid_reply": series.get("invalid_reply"),
            "filled": series.get("filled"),
            "series_digest": series.get("series_digest"),
        }
    return out


def hours_block(task: Optional[dict], series: Optional[dict],
                recent: int = RECENT_FRAME_HOURS) -> list[dict]:
    """The hours a live view draws: the end of the first frame, then the epoch's own.

    `up` is the target bit itself (1: the hour closed at or above the previous
    one, in whole USDT). `live` marks hours stored during this epoch -- the ones
    the colony could not have seen when the epoch began. `append_tick` and
    `filled` come from the price series and are absent for frame hours.
    """
    if task is None:
        return []
    targets = task["targets"]
    shift_limit = int(task["shift_limit"])
    window = len(targets) - shift_limit
    base = int(task["base_hour"])
    first_hour = base - window + 1
    meta = {p["hour"]: p for p in (series or {}).get("points", [])}
    out = []
    for t in range(max(0, window - recent), len(targets)):
        hour = first_hour + t
        row = {"hour": hour, "at": hour_at(hour), "up": bool(targets[t]), "live": t >= window}
        p = meta.get(hour)
        if row["live"] and p is not None:
            row["append_tick"] = p["append_tick"]
            row["filled"] = bool(p["flags"] & FLAG_FILLED)
            if p.get("price"):
                row["price"] = p["price"]
                prev = meta.get(hour - 1)
                if prev and prev.get("price"):
                    row["change_pct"] = round((p["price"] / prev["price"] - 1) * 100, 3)
        out.append(row)
    return out


if __name__ == "__main__":  # pragma: no cover - manual probe
    from qdr import antnode
    data = antnode.collect()
    print(json.dumps({"task": data.get("task"), "btc_hours": (data.get("btc") or {}).get("hours", [])[-8:]},
                     indent=1))
