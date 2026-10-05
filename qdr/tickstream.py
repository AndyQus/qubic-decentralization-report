"""Live ticks, relayed from a Bob node to any number of browsers.

The ticks page shows every tick of the network as it is produced. The data is
there — a Bob node pushes it over WebSocket — but not in a shape a browser should
receive directly. Measured against bob.qubic.li on 2026-10-05, ~1.8 ticks/s:

    subscription   per tick    per hour, per viewer
    tickStream     ~20 KB      ~130 MB   (transactions incl. input data + logs)
    newTicks       ~720 KB     —         (carries all 676 quorum votes)

So this module holds ONE upstream connection, condenses each tick to well under
a kilobyte, keeps the last few hundred in memory, and fans them out. However many
browsers watch, the node sees one client; when none watch, the connection is
closed after QDR_TICKS_IDLE seconds and the node sees none.

Nothing here is stored: like the pulse, this is live state, and the API stays a
reader of the store (tests/test_api_is_read_only.py).

Two upstream modes, chosen automatically:

  * ``bob-ws``   — ``qubic_subscribe ["tickStream"]`` on ``/ws/qubic``. Full
                   transaction detail (from, to, amount, input type).
  * ``bob-poll`` — when the node offers no WebSocket (or it keeps failing):
                   ``qubic_getTickNumber`` once a second, then
                   ``qubic_getTickByNumber`` + ``qubic_getLogs`` for the new
                   ticks. Transactions arrive as hashes only, so the
                   per-transaction view is thinner; the counts, burns and
                   solutions — all read from the logs — are the same.

Facts the condensing depends on, each measured on the live node:

  * A mining solution is a transaction that sends 1,000,000 QU to the null
    address and gets it straight back in the same transaction; its log carries a
    custom message (type 255) whose payload starts with the ASCII bytes
    ``ANT_SOLU``. It is counted as a solution, never as a burn — the netting in
    ``qdr.burn.burn_events`` is what guarantees the latter.
  * A skipped tick arrives with ``isSkipped`` and ``hasNoTickData`` both true and
    the timestamp ``2000-00-00T00:00:00Z``; that timestamp is not a time.
  * A transaction's ``inputType`` is the procedure id of the contract it is sent
    to, which the published contract registry names.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

import requests

from qdr import burn

BOB_URL = os.environ.get("QDR_BOB_URL", "https://bob.qubic.li/qubic")
BUFFER_TICKS = int(os.environ.get("QDR_TICKS_BUFFER", "300"))
IDLE_S = float(os.environ.get("QDR_TICKS_IDLE", "60"))

# A Qubic identity: exactly 60 upper-case letters.
_IDENTITY = re.compile(r"[A-Z]{60}")

# The ASCII bytes "ANT_SOLU" in hex: the prefix of a mining-solution message.
ANT_SOLU_HEX = "414e545f534f4c55"
CUSTOM_MESSAGE_TYPE = 255
NULL_ADDRESS = burn.NULL_ADDRESS

# How many transactions a summary names. The rest are counted; the detail view
# lists them all.
TOP_N = 5
# Ceiling on events in a detail response. A busy tick has a few hundred.
MAX_EVENTS = 400

# Poll mode: one tick-number read per second, and never more than this many new
# ticks fetched per round, so a node that fell behind cannot be hammered by a
# relay trying to catch up all at once.
POLL_INTERVAL_S = 1.0
POLL_MAX_TICKS = 6
# After this many WebSocket failures in a row the relay polls for a while before
# trying the socket again.
WS_FAILURES_BEFORE_POLL = 3
POLL_BEFORE_WS_RETRY_S = 300.0

# Ranking for the transactions a summary names: what changed the supply first,
# then what called a contract, then plain transfers by size.
_KIND_RANK = {"burn": 0, "contract": 1, "transfer": 2, "call": 3, "unknown": 4}

# Workers for the on-demand detail read, which needs one call per transaction.
DETAIL_WORKERS = 8
# A tick with more transactions than this is listed by hash beyond it.
DETAIL_MAX_TX_LOOKUPS = 128


def ws_url_for(bob_url: str) -> str:
    """``https://host/qubic`` → ``wss://host/ws/qubic`` (and http → ws).

    The public node serves JSON-RPC under /qubic and the socket under /ws/qubic.
    A bare own node (``http://10.0.0.5:40420``) gets ``ws://10.0.0.5:40420/ws/qubic``.
    """
    explicit = os.environ.get("QDR_BOB_WS")
    if explicit:
        return explicit
    url = bob_url.rstrip("/")
    if url.endswith("/qubic"):
        url = url[: -len("/qubic")]
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return url + "/ws/qubic"


# ── Condensing ────────────────────────────────────────────────────────────────

def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def short_id(identity: Optional[str]) -> Optional[str]:
    """``ZTZEAQ…UEJJ`` — enough to recognise an identity, not to copy it."""
    if not identity or len(identity) < 14:
        return identity
    return identity[:6] + "…" + identity[-4:]


def _norm_log(entry: dict) -> dict:
    """One log entry in the shape qdr.burn understands, from either Bob shape.

    tickStream nests the payload under ``body`` and spells ``logTypename`` and
    ``txHash``; ``qubic_getLogs`` is flat with ``source``/``destination``,
    ``logTypeName`` and ``transactionHash``.
    """
    body = entry.get("body")
    if not isinstance(body, dict):
        body = {
            "from": entry.get("source"),
            "to": entry.get("destination"),
            "amount": entry.get("amount"),
            "hex": (entry.get("rawData") or "").removeprefix("0x"),
        }
    return {
        "logTypeName": entry.get("logTypename") or entry.get("logTypeName"),
        "type": entry.get("type", entry.get("logType")),
        "tick": entry.get("tick"),
        "transactionHash": entry.get("txHash") or entry.get("transactionHash"),
        "body": body,
    }


def _is_solution(log: dict) -> bool:
    if _int(log.get("type")) != CUSTOM_MESSAGE_TYPE and log.get("logTypeName"):
        return False
    hexdata = str((log.get("body") or {}).get("hex") or "").lower()
    return hexdata.startswith(ANT_SOLU_HEX)


def _timestamp(raw: dict) -> Optional[int]:
    """Unix seconds, or None for a tick that carries no real time (skipped)."""
    ts = raw.get("timestamp")
    if isinstance(ts, (int, float)):
        return int(ts) if ts > 1_000_000_000 else None
    if isinstance(ts, str) and ts and not ts.startswith("2000-00"):
        try:
            return int(datetime.fromisoformat(ts.replace("Z", "+00:00"))
                       .astimezone(timezone.utc).timestamp())
        except ValueError:
            return None
    return None


def condense(raw: dict, contracts_by_address: Optional[dict] = None) -> tuple[dict, dict]:
    """One Bob tick → (summary for the stream, detail for the tick view).

    ``raw`` is a tickStream event, or the equivalent the poll mode assembles.
    ``contracts_by_address`` maps a contract's address to its registry entry
    (``qdr.contracts.by_address()``); without it, contract calls show as calls to
    an address, which is true, just less readable.
    """
    contracts_by_address = contracts_by_address or {}
    tick = _int(raw.get("tick") or raw.get("tickNumber"))
    skipped = bool(raw.get("isSkipped"))
    empty = bool(raw.get("hasNoTickData"))
    state = "skipped" if skipped else ("empty" if empty else "ok")

    logs = [_norm_log(e) for e in (raw.get("logs") or []) if isinstance(e, dict)]

    # Per transaction: solutions, and what reached / left the burn address.
    solution_txs: set[str] = set()
    solutions = 0
    sink_in: dict[str, int] = {}
    sink_out: dict[str, int] = {}
    qu_moved = 0
    events: list[dict] = []
    for log in logs:
        h = log["transactionHash"] or ""
        body = log["body"]
        if _is_solution(log):
            solutions += 1
            solution_txs.add(h)
            continue
        name = log["logTypeName"]
        if name == burn.QU_TRANSFER:
            amount = _int(body.get("amount"))
            src, dst = body.get("from"), body.get("to")
            if burn.is_burn_sink(dst):
                sink_in[h] = sink_in.get(h, 0) + amount
            if burn.is_burn_sink(src):
                sink_out[h] = sink_out.get(h, 0) + amount
            if not burn.is_burn_sink(src) and not burn.is_burn_sink(dst):
                qu_moved += amount
                events.append({"type": name, "tx": h or None, "from": src, "to": dst,
                               "qu": amount})
        elif name:
            ev = {"type": name, "tx": h or None}
            ev.update({k: v for k, v in body.items() if k != "hex"})
            events.append(ev)

    # Total burned: the same netting the burn report uses, so this page and the
    # burn page can never disagree about what a transaction burned.
    burned = sum(e["amount"] for e in burn.burn_events(logs))
    tx_burn = {h: sink_in.get(h, 0) - sink_out.get(h, 0)
               for h in sink_in if sink_in.get(h, 0) > sink_out.get(h, 0)}

    txs: list[dict] = []
    contract_calls: dict[int, dict] = {}
    tx_failed = 0
    for t in raw.get("transactions") or []:
        if isinstance(t, str):          # poll mode: a hash and nothing else
            t = {"hash": t}
        h = t.get("hash") or ""
        ok = t.get("status", "success") == "success" and t.get("executed", True) is not False
        if not ok:
            tx_failed += 1
        to = t.get("to")
        row = {"h": h, "from": t.get("from"), "to": to,
               "qu": _int(t.get("amount")) if "amount" in t else None, "ok": ok}
        contract = contracts_by_address.get(to) if to else None
        if h in solution_txs:
            row["kind"] = "solution"
        elif h in tx_burn:
            row["kind"] = "burn"
            row["burned"] = tx_burn[h]
        elif contract:
            row["kind"] = "contract"
        elif burn.is_burn_sink(to) and not row["qu"]:
            # 0 QU to the null address and no solution message: a computor's
            # own protocol transaction. Counted, but never "interesting".
            row["kind"] = "system"
        elif to is None:
            row["kind"] = "unknown"
        elif row["qu"]:
            row["kind"] = "transfer"
        else:
            row["kind"] = "call"
        if contract:
            idx = int(contract["index"])
            proc_id = _int(t.get("inputType"))
            procs = contract.get("procedures") or {}
            row["contract"] = {"index": idx, "name": contract.get("label") or contract.get("name"),
                               "proc": procs.get(proc_id) or procs.get(str(proc_id))}
            c = contract_calls.setdefault(idx, {"index": idx,
                                                "name": row["contract"]["name"],
                                                "calls": 0})
            c["calls"] += 1
        if "inputType" in t:
            row["input_type"] = _int(t.get("inputType"))
        txs.append(row)

    top = sorted((r for r in txs if r["kind"] not in ("solution", "system")),
                 key=lambda r: (_KIND_RANK.get(r["kind"], 9),
                                -(r.get("burned") or r.get("qu") or 0)))[:TOP_N]

    summary = {
        "tick": tick,
        "epoch": _int(raw.get("epoch")) or None,
        "ts": _timestamp(raw),
        "state": state,
        "catch_up": bool(raw.get("isCatchUp")),
        "leader": {"index": raw.get("computorIndex")},
        "tx": len(txs) or _int(raw.get("totalTxs") or raw.get("transactionCount")),
        "tx_failed": tx_failed,
        "logs": len(logs) or _int(raw.get("totalLogs")),
        "qu_moved": qu_moved,
        "burned": burned,
        "solutions": solutions,
        "contracts": sorted(contract_calls.values(), key=lambda c: -c["calls"]),
        # Shortened identities and no hashes: the summary is what streams twice a
        # second to every viewer, and 60-character strings were most of its bytes.
        # The detail view carries them in full.
        "top": [{**{k: v for k, v in r.items() if k in
                    ("kind", "qu", "burned", "contract", "ok")},
                 "from": short_id(r.get("from")), "to": short_id(r.get("to"))}
                for r in top],
    }
    detail = dict(summary)
    detail["transactions"] = txs
    detail["events"] = events[:MAX_EVENTS]
    detail["events_truncated"] = len(events) > MAX_EVENTS
    return summary, detail


def condense_polled(tick_data: dict, logs: Iterable[dict],
                    contracts_by_address: Optional[dict] = None) -> tuple[dict, dict]:
    """The poll mode's equivalent: ``qubic_getTickByNumber`` + its logs."""
    raw = dict(tick_data or {})
    raw["tick"] = raw.get("tickNumber") or raw.get("tick")
    raw["logs"] = list(logs)
    raw.setdefault("isCatchUp", False)
    return condense(raw, contracts_by_address)


# ── Bob JSON-RPC (poll mode and the detail view) ──────────────────────────────

class BobRPC:
    """The three tick reads, over the same JSON-RPC as qdr.bob."""

    def __init__(self, url: str = BOB_URL, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()

    def call(self, method: str, params: list) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        resp = self.session.post(self.url, json=body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"{method}: {data['error']}")
        return data.get("result")

    def tick_number(self) -> int:
        return int(self.call("qubic_getTickNumber", []))

    def tick(self, tick: int) -> dict:
        return self.call("qubic_getTickByNumber", [int(tick)]) or {}

    def logs(self, from_tick: int, to_tick: int) -> list[dict]:
        res = self.call("qubic_getLogs", [{"fromTick": int(from_tick), "toTick": int(to_tick)}])
        return res if isinstance(res, list) else []

    def computors(self, epoch: int) -> list[str]:
        """The epoch's 676 computors in index order.

        Bob returns each identity with trailing bytes after the 60 characters —
        usually U+00F7, sometimes more (measured 2026-10-05: ``…EVKc``),
        apparently whatever followed the string in memory. An identity is
        exactly 60 letters A-Z, so that is what is kept; anything else becomes
        None rather than a near-identity that matches nobody.
        """
        res = self.call("qubic_getComputors", [int(epoch)]) or {}
        ids = res.get("computors") if isinstance(res, dict) else None
        out: list = []
        for raw in ids or []:
            m = _IDENTITY.match(str(raw))
            out.append(m.group(0) if m else None)
        return out

    def transaction(self, tx_hash: str) -> Optional[dict]:
        res = self.call("qubic_getTransactionByHash", [tx_hash])
        if not isinstance(res, dict):
            return None
        res.pop("inputData", None)
        res.pop("signature", None)
        return res

    def fetch_tick(self, tick: int, contracts_by_address: Optional[dict] = None) -> dict:
        """One tick's detail, read on demand (for ticks no longer in the buffer).

        ``qubic_getTickByNumber`` names the transactions by hash only, so each is
        looked up — in parallel, and capped, because a detail view is one click
        and should not turn into a few hundred sequential calls.
        """
        from concurrent.futures import ThreadPoolExecutor

        data = self.tick(tick)
        logs = [e for e in self.logs(tick, tick) if _int(e.get("tick")) == int(tick)]
        hashes = [h for h in (data.get("transactions") or []) if isinstance(h, str)]

        def lookup(h: str):
            try:
                return self.transaction(h) or {"hash": h}
            except Exception:
                return {"hash": h}

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            looked = list(pool.map(lookup, hashes[:DETAIL_MAX_TX_LOOKUPS]))
        data = dict(data)
        data["transactions"] = looked + hashes[DETAIL_MAX_TX_LOOKUPS:]
        return condense_polled(data, logs, contracts_by_address)[1]


# ── The hub ───────────────────────────────────────────────────────────────────

class TickHub:
    """One upstream, a ring buffer, and any number of subscribers.

    Lives on the API's event loop. ``ensure_running()`` starts the upstream task
    on demand; the task ends itself once nobody has subscribed or polled for
    ``idle_s`` seconds.
    """

    def __init__(
        self,
        bob_url: str = BOB_URL,
        buffer_ticks: int = BUFFER_TICKS,
        idle_s: float = IDLE_S,
        contracts_fn: Optional[Callable[[], dict]] = None,
        leaders_fn: Optional[Callable[[int], list]] = None,
    ) -> None:
        self.bob_url = bob_url
        self.ws_url = ws_url_for(bob_url)
        self.idle_s = idle_s
        self.buffer: deque[dict] = deque(maxlen=buffer_ticks)
        self.details: OrderedDict[int, dict] = OrderedDict()
        self.max_details = buffer_ticks
        self.subscribers: set[asyncio.Queue] = set()
        self.contracts_fn = contracts_fn
        self.leaders_fn = leaders_fn
        self._contracts: dict = {}
        self._contracts_at = 0.0
        self._leaders: dict[int, list] = {}
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_seen = time.time()
        self.source: Optional[str] = None
        self.connected = False
        self.last_event_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.ticks_seen = 0

    # -- lifecycle --------------------------------------------------------

    def touch(self) -> None:
        self._last_seen = time.time()

    def _idle(self) -> bool:
        return not self.subscribers and time.time() - self._last_seen > self.idle_s

    async def ensure_running(self) -> None:
        self.touch()
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._loop is loop:
            return
        self._loop = loop
        # Starting again after an idle shutdown: what the buffer holds is from
        # before the gap. Served as "recent", it would put minutes-old ticks in
        # front of a new viewer and make the tick rate read as a crawl.
        if self.last_event_at and time.time() - self.last_event_at > 30:
            self.buffer.clear()
            self.details.clear()
        self._task = loop.create_task(self._run())

    def status(self) -> dict:
        now = time.time()
        last = self.buffer[-1] if self.buffer else None
        age = round(now - self.last_event_at, 1) if self.last_event_at else None
        return {
            "source": self.source,
            "connected": self.connected,
            "upstream": self.ws_url if self.source == "bob-ws" else self.bob_url,
            "last_tick": last["tick"] if last else None,
            "last_event_age_s": age,
            # Ticks come ~2/s; ten silent seconds means the node, not the network.
            "stale": age is None or age > 10,
            "buffered": len(self.buffer),
            "viewers": len(self.subscribers),
            "error": self.last_error,
        }

    # -- consumers --------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(q)
        self.touch()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)
        self.touch()

    def recent(self, after: Optional[int] = None, limit: int = 30) -> list[dict]:
        """Ticks newer than ``after`` (oldest first), at most ``limit`` — the newest
        ones if more are waiting, because a viewer that fell behind wants now."""
        rows = [s for s in self.buffer if after is None or s["tick"] > after]
        return rows[-limit:] if limit else rows

    def detail(self, tick: int) -> Optional[dict]:
        return self.details.get(int(tick))

    # -- ingest -----------------------------------------------------------

    def _contracts_map(self) -> dict:
        return self._contracts

    async def _refresh_contracts(self) -> None:
        if self.contracts_fn is None or time.time() - self._contracts_at < 3600:
            return
        self._contracts_at = time.time()
        try:
            self._contracts = await asyncio.to_thread(self.contracts_fn) or {}
        except Exception:
            pass        # names are a nicety; an address is still the truth

    async def _refresh_leaders(self, epoch: Optional[int]) -> None:
        if not epoch or self.leaders_fn is None or epoch in self._leaders:
            return
        self._leaders[epoch] = []        # one attempt per epoch, not per tick
        try:
            self._leaders[epoch] = await asyncio.to_thread(self.leaders_fn, epoch) or []
        except Exception as e:
            self.last_error = f"leaders: {e}"

    def ingest(self, summary: dict, detail: dict) -> bool:
        """Add one condensed tick. Returns False for a tick already seen."""
        tick = summary["tick"]
        if not tick or tick in self.details:
            return False
        if self.buffer and tick < self.buffer[-1]["tick"] - self.buffer.maxlen:
            return False                 # far behind the window: not live any more
        leaders = self._leaders.get(summary.get("epoch") or 0) or []
        idx = summary["leader"].get("index")
        if isinstance(idx, int) and 0 <= idx < len(leaders) and leaders[idx]:
            info = leaders[idx]
            detail["leader"] = {"index": idx, **info}
            summary["leader"] = {"index": idx, "id": short_id(info.get("id")),
                                 "cluster": info.get("cluster")}
        self.buffer.append(summary)
        if len(self.buffer) > 1 and self.buffer[-2]["tick"] > tick:
            # Out of order (a catch-up burst): keep the buffer sorted, so
            # `recent(after=…)` never skips a tick a viewer has not had.
            ordered = sorted(self.buffer, key=lambda s: s["tick"])
            self.buffer.clear()
            self.buffer.extend(ordered)
        self.details[tick] = detail
        while len(self.details) > self.max_details:
            self.details.popitem(last=False)
        self.ticks_seen += 1
        self.last_event_at = time.time()
        for q in list(self.subscribers):
            try:
                q.put_nowait(summary)
            except asyncio.QueueFull:
                # A consumer that cannot keep up is dropped; its browser
                # reconnects with Last-Event-ID and resumes from the buffer.
                self.subscribers.discard(q)
        return True

    async def _ingest_raw(self, raw: dict) -> None:
        await self._refresh_leaders(_int(raw.get("epoch")) or None)
        summary, detail = condense(raw, self._contracts_map())
        self.ingest(summary, detail)

    # -- upstream ---------------------------------------------------------

    async def _run(self) -> None:
        ws_failures = 0
        poll_until = 0.0
        try:
            while not self._idle():
                await self._refresh_contracts()
                if time.time() < poll_until:
                    await self._poll_once()
                    await asyncio.sleep(POLL_INTERVAL_S)
                    continue
                try:
                    await self._run_ws()
                    ws_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    ws_failures += 1
                    self.last_error = f"websocket: {type(e).__name__}: {e}"
                    self.connected = False
                    if ws_failures >= WS_FAILURES_BEFORE_POLL:
                        poll_until = time.time() + POLL_BEFORE_WS_RETRY_S
                        ws_failures = 0
                    else:
                        await asyncio.sleep(min(2 ** ws_failures, 10))
        finally:
            self.connected = False

    async def _run_ws(self) -> None:
        import websockets      # ships with uvicorn[standard]

        async with websockets.connect(self.ws_url, open_timeout=15, max_size=None,
                                      ping_interval=20) as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1,
                                      "method": "qubic_subscribe",
                                      "params": ["tickStream"]}))
            self.source = "bob-ws"
            self.connected = True
            self.last_error = None
            while not self._idle():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    if self.last_event_at and time.time() - self.last_event_at > 30:
                        raise RuntimeError("no tick for 30 s")
                    continue
                data = json.loads(msg)
                if "error" in data:
                    raise RuntimeError(str(data["error"]))
                result = (data.get("params") or {}).get("result")
                if isinstance(result, dict) and "tick" in result:
                    await self._ingest_raw(result)

    async def _poll_once(self) -> None:
        rpc = getattr(self, "_rpc", None) or BobRPC(self.bob_url)
        self._rpc = rpc
        try:
            head = await asyncio.to_thread(rpc.tick_number)
            last = self.buffer[-1]["tick"] if self.buffer else head - 1
            if head <= last:
                return
            first = max(last + 1, head - POLL_MAX_TICKS + 1)
            logs = await asyncio.to_thread(rpc.logs, first, head)
            by_tick: dict[int, list] = {}
            for e in logs:
                by_tick.setdefault(_int(e.get("tick")), []).append(e)
            for t in range(first, head + 1):
                data = await asyncio.to_thread(rpc.tick, t)
                if not data:
                    continue
                await self._refresh_leaders(_int(data.get("epoch")) or None)
                s, d = condense_polled(data, by_tick.get(t, []), self._contracts_map())
                self.ingest(s, d)
            self.source = "bob-poll"
            self.connected = True
            self.last_error = None
        except Exception as e:
            self.connected = False
            self.last_error = f"poll: {type(e).__name__}: {e}"
