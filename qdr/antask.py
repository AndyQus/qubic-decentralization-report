"""The task the ant colony is mining, described from the file the network checks.

A node reports only a hash of its task (`dataHash` in the epoch context). The task
itself -- how long the series is, how wide the graded frame, how far it may slide,
what the targets look like -- lives in qubic/core: the data in `data/bpp9000.task`,
the parameters in `src/public_settings.h`. The core changes both between epochs
(v1.304 graded 4-week windows, v1.306 a one-year rolling frame), so hard-coding them
here went stale on the first release after it was written.

So the live hash is the key. This module looks for the core ref whose task-file
header carries exactly that hash and reads the parameters from the same ref. `main`
is tried first; the release tags cover the hours around an epoch switch, when `main`
already holds the next epoch's task while the network still mines the current one.

Resolution is cached per hash (in memory and on disk), so GitHub is asked once per
task, not once per node poll. It runs in a background thread: the caller gets
`None` until it is done and never waits on GitHub.
"""
from __future__ import annotations

import ast
import collections
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
TASK_PATH = "data/bpp9000.task"
SETTINGS_PATH = "src/public_settings.h"

# main plus this many of the newest tags. An epoch switch only ever needs the tag
# just before main; the rest is margin for a skipped or hotfix release.
MAX_TAGS = 8

# A hash no ref matched is not retried on every 30 s poll -- that would be a
# GitHub request storm for as long as the core lags behind the network.
RETRY_AFTER_S = 600
FETCH_TIMEOUT_S = 15.0

# Header layout from src/mining/task_file.h (TaskFileHeader, 96 bytes).
TASK_MAGIC = 0x5454554C
HEADER_SIZE = 96
TRITS_PER_BYTE = 5
TRIT_UNKNOWN = 2

_DATA_DIR = os.environ.get("DATA_DIR")
DEFAULT_CACHE_FILE = (
    Path(_DATA_DIR) / "raw" if _DATA_DIR
    else Path(__file__).resolve().parent.parent / "data" / "raw"
) / "ant_tasks.json"

Fetch = Callable[[str], bytes]


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "qubic-report"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return resp.read()


# -- parsing ---------------------------------------------------------------

def _packed(trits: int) -> int:
    return (trits + TRITS_PER_BYTE - 1) // TRITS_PER_BYTE


def parse_task_file(blob: bytes) -> dict:
    """Header fields and the target trit of every row.

    Only the first output trit is decoded: the scorer grades output neuron 0 alone
    (score_bpp9000.h asserts exactly one output neuron).
    """
    if len(blob) < HEADER_SIZE:
        raise ValueError("task file shorter than its header")
    magic, version, n_in, n_out, rows, population, neighbors = struct.unpack_from("<IIIIQII", blob, 0)
    if magic != TASK_MAGIC:
        raise ValueError("not a task file (magic %#x)" % magic)
    topology_hash = blob[32:64].hex()
    data_hash = blob[64:96].hex()
    topo_bytes = (n_in + n_out + 1 + population * neighbors) * 4
    data = blob[HEADER_SIZE + topo_bytes:]
    in_b, out_b = _packed(n_in), _packed(n_out)
    row_b = in_b + out_b
    if len(data) < rows * row_b:
        raise ValueError("task data block truncated")
    targets = [data[i * row_b + in_b] % 3 for i in range(rows)]
    return {
        "version": version,
        "input_trits": n_in,
        "output_trits": n_out,
        "rows": rows,
        "topology_hash": topology_hash,
        "data_hash": data_hash,
        "targets": targets,
    }


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


def describe(task: dict, settings: dict, ref: str) -> dict:
    """The task block the mining page shows, from one file and its settings.

    Two scoring models exist in the core's history. Since v1.306 the score is the
    error count inside one `WINDOW_WIDTH` frame that may slide up to `SHIFT_CAP`
    rows (the "rolling frame"); before, it counted errors over every window after
    the first. Which one applies follows from whether SHIFT_CAP is defined.
    """
    seq = settings.get("BPP9000_SEQUENCE_LENGTH") or task["rows"]
    win = settings.get("BPP9000_WINDOW_WIDTH")
    shift_cap = settings.get("BPP9000_SHIFT_CAP")
    if win is None:
        raise ValueError("BPP9000_WINDOW_WIDTH not found in settings")
    rolling = shift_cap is not None
    targets = task["targets"][:seq]
    counts = collections.Counter(targets)
    # The rows past the first frame are what a sliding frame reaches into. If the
    # core ever left targets there UNKNOWN, the network would be filling in hours
    # nobody knows yet -- a forecast. Counted separately so that shows up.
    tail = targets[win:]
    return {
        "source_ref": ref,
        "scoring": "rolling-frame" if rolling else "sliding-windows",
        "input_trits": task["input_trits"],
        # The rolling-frame scorer loads only the target column (loadTaskFromMemory);
        # the input trits are still in the file but nothing reads them.
        "inputs_used": not rolling,
        "rows": task["rows"],
        "sequence_length": seq,
        "window_width": win,
        "shift_cap": shift_cap,
        "score_max": win if rolling else seq - win,
        "advance_gate": win // 3 if rolling else None,
        "threshold_default": settings.get("BPP9000_SOLUTION_THRESHOLD_DEFAULT"),
        "data_hash": task["data_hash"],
        "target_zeros": counts.get(0, 0),
        "target_ones": counts.get(1, 0),
        "target_unknown": counts.get(TRIT_UNKNOWN, 0),
        "tail_rows": len(tail),
        "tail_unknown": sum(1 for x in tail if x == TRIT_UNKNOWN),
    }


# -- resolution ------------------------------------------------------------

def candidate_refs(fetch: Fetch = _http_get) -> list[str]:
    """main, then the newest release tags."""
    refs = ["main"]
    try:
        tags = json.loads(fetch(TAGS_URL.format(n=MAX_TAGS)))
        refs += [t["name"] for t in tags if t.get("name")][:MAX_TAGS]
    except Exception:
        pass
    return refs


def find_task(data_hash: str, fetch: Fetch = _http_get) -> Optional[dict]:
    """Walk the candidate refs for the task file carrying `data_hash`; describe it."""
    want = data_hash.lower()
    seen: set[str] = set()
    for ref in candidate_refs(fetch):
        try:
            blob = fetch(RAW_URL.format(ref=ref, path=TASK_PATH))
        except Exception:
            continue
        # Several tags usually carry the same file; one parse per distinct file.
        key = blob[64:96].hex()
        if key in seen:
            continue
        seen.add(key)
        if key != want:
            continue
        try:
            task = parse_task_file(blob)
            settings = parse_settings(fetch(RAW_URL.format(ref=ref, path=SETTINGS_PATH)).decode("utf-8", "replace"))
            out = describe(task, settings, ref)
        except Exception:
            continue
        out["resolved_at"] = int(time.time())
        return out
    return None


class TaskResolver:
    """Hash -> task description, cached, resolved off the caller's thread."""

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
        # An unwritable volume costs a GitHub lookup after a restart, nothing more.
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self._known, indent=1), "utf-8")
        except Exception:
            pass

    def _resolve(self, data_hash: str) -> None:
        try:
            found = find_task(data_hash, self.fetch)
        except Exception:
            found = None
        with self._lock:
            self._running.discard(data_hash)
            if found is None:
                self._failed_at[data_hash] = time.time()
            else:
                self._known[data_hash] = found
                self._failed_at.pop(data_hash, None)
                self._save()

    def get(self, data_hash: str) -> Optional[dict]:
        """The description for this hash, or None while it is unknown.

        A miss starts a lookup (unless one is running or one failed less than
        RETRY_AFTER_S ago) and returns None straight away.
        """
        if not data_hash:
            return None
        data_hash = data_hash.lower()
        with self._lock:
            self._load()
            if data_hash in self._known:
                return self._known[data_hash]
            if data_hash in self._running:
                return None
            if time.time() - self._failed_at.get(data_hash, 0.0) < RETRY_AFTER_S:
                return None
            self._running.add(data_hash)
        if self.background:
            threading.Thread(target=self._resolve, args=(data_hash,), daemon=True).start()
            return None
        self._resolve(data_hash)
        with self._lock:
            return self._known.get(data_hash)


_resolver = TaskResolver()


def task_block(data_hash: str, resolver: Optional[TaskResolver] = None) -> dict:
    """What collect() puts under "task": the description, or just the hash.

    `hash_verified` is True only when the description below it was read from the
    core file whose header carries exactly the hash the node reported -- so every
    number shown is the one the network is mining against. None means "not looked
    up yet"; the page says so instead of showing last epoch's numbers.
    """
    found = (resolver or _resolver).get(data_hash)
    if found is None:
        return {"data_hash": data_hash, "hash_verified": None}
    out = dict(found)
    out["data_hash"] = data_hash
    out["hash_verified"] = True
    return out


if __name__ == "__main__":  # pragma: no cover - manual probe
    import sys
    from qdr import antnode
    h = sys.argv[1] if len(sys.argv) > 1 else None
    if h is None:
        ctx = antnode.best_epoch_context()
        if ctx is None:
            raise SystemExit("no node answered")
        h = ctx.data_hash
        print(f"live epoch {ctx.epoch}, threshold {ctx.threshold}")
    print(json.dumps(find_task(h), indent=1))
