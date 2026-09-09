"""Ant-colony mining data, read straight from a Qubic node over TCP.

The public RPC (rpc.qubic.org) does not expose mining state at all -- no scores, no
threshold, no solution count. That data only exists on the peer-to-peer port, so this
module speaks the node protocol directly.

Two things are needed and neither is in the RPC:

  * a set of reachable node IPs -- there is no official endpoint, so peers come from
    qubic.li's public list with the core's bootstrap IPs as the fallback;
  * REQUEST_ANT_EPOCH_CONTEXT (76), which is *public*: no signature, no operator key.

What is deliberately NOT here: per-identity scores. REQUEST_ANT_IDENTITY_TREE (72) is
operator-signed -- a node answers it only for identities whose operator key signed the
request. Verified against a live node: an unsigned request draws no response at all.
So the network-wide best score is not readable from outside, and this module does not
pretend otherwise.

Protocol reference: qubic/core doc/ant_colony_mining.md section 2.7a.
"""
from __future__ import annotations

import json
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass

# The peer-to-peer port every node listens on.
PEER_PORT = 21841

# Message types from src/network_messages/network_message_type.h.
REQUEST_SYSTEM_INFO = 46
RESPOND_SYSTEM_INFO = 47
REQUEST_REVENUE_DATA = 70
RESPOND_REVENUE_DATA = 71
REQUEST_ANT_EPOCH_CONTEXT = 76
RESPOND_ANT_EPOCH_CONTEXT = 77

# Fixed packed struct sizes, asserted in the core's own headers.
EPOCH_CONTEXT_SIZE = 120
SYSTEM_INFO_SIZE = 128
NUMBER_OF_COMPUTORS = 676
# tick + dogeK + ipc, then three per-computor score arrays.
REVENUE_DATA_SIZE = 4 + 2 + 8 + 3 * 8 * NUMBER_OF_COMPUTORS

# Bootstrap peers from qubic/go-qubic-nodes. Its own README warns that this list is
# not maintained, so it is the fallback, not the primary source.
BOOTSTRAP_PEERS = ("5.39.222.64", "82.197.173.130", "82.197.173.129")

PEER_LIST_URL = "https://api.qubic.li/Public/Peers"

# A node that has not answered in this long is not worth waiting for; the fast ones
# answer in well under 150 ms.
DEFAULT_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class AntEpochContext:
    """The public per-epoch mining parameters, as one node reports them."""

    epoch: int
    threshold: int
    freshness_window: int
    solution_count: int
    free_ann_slots: int
    max_children_per_parent: int
    spectrum_digest: str
    topology_hash: str
    data_hash: str
    source_ip: str
    latency_ms: float

    @property
    def max_children_is_unbounded(self) -> bool:
        """The doc encodes "no cap" as 0, which would otherwise read as "no children"."""
        return self.max_children_per_parent == 0


@dataclass(frozen=True)
class SystemInfo:
    """What the node reports about itself and the epoch it is running."""

    version: int
    epoch: int
    tick: int
    initial_tick: int
    latest_created_tick: int
    number_of_entities: int
    number_of_transactions: int
    source_ip: str
    latency_ms: float

    @property
    def ticks_into_epoch(self) -> int:
        return max(0, self.tick - self.initial_tick)


@dataclass(frozen=True)
class RevenueData:
    """Per-computor revenue scores for all 676 slots, as the node has them right now.

    Approximate mid-epoch, exact only at epoch end (see the core's revenue_data.h).
    """

    tick: int
    doge_k: int
    ipc: int
    tx_score: list[int]
    oracle_score: list[int]
    doge_score: list[int]
    source_ip: str
    latency_ms: float

    def totals(self) -> list[int]:
        """The three dimensions summed per slot -- a single number to rank slots by."""
        return [t + o + d for t, o, d in zip(self.tx_score, self.oracle_score, self.doge_score)]


def _header(payload_size: int, msg_type: int, dejavu: int = 1) -> bytes:
    """RequestResponseHeader: 3-byte little-endian size (header included), type, dejavu."""
    size = 8 + payload_size
    return bytes((size & 0xFF, (size >> 8) & 0xFF, (size >> 16) & 0xFF, msg_type)) + struct.pack("<I", dejavu)


def _frames(buf: bytes):
    """Split a receive buffer into (type, payload) frames, ignoring a trailing partial."""
    off = 0
    while off + 8 <= len(buf):
        size = int.from_bytes(buf[off:off + 3], "little")
        msg_type = buf[off + 3]
        if size < 8 or off + size > len(buf):
            break
        yield msg_type, buf[off + 8:off + size]
        off += size


def fetch_peers(timeout: float = 10.0) -> list[str]:
    """Reachable node IPs, best-effort, most current tick first.

    qubic.li publishes a live list with each node's tick; sorting by that puts the
    least-lagged nodes first. On any failure this falls back to the bootstrap IPs
    rather than raising -- an empty peer list would be indistinguishable from a
    network outage, and the caller can only act on "no data" either way.
    """
    try:
        with urllib.request.urlopen(PEER_LIST_URL, timeout=timeout) as resp:
            peers = json.load(resp)
        ranked = sorted(peers, key=lambda p: -int(p.get("currentTick", 0)))
        ips = [p["ipAddress"] for p in ranked if p.get("ipAddress")]
        if ips:
            return ips
    except Exception:
        pass
    return list(BOOTSTRAP_PEERS)


def _ask(ip: str, req_type: int, resp_type: int, min_size: int,
         timeout: float = DEFAULT_TIMEOUT_S) -> tuple[bytes, float] | None:
    """Send an empty, unsigned request and wait for one frame of `resp_type`.

    All three requests used here take no payload and no signature. A node answers with
    a peer-exchange frame first, so frames have to be walked rather than assuming the
    reply is the first thing on the wire. Returns (payload, latency_ms) or None.
    """
    sock = socket.socket()
    sock.settimeout(timeout)
    started = time.monotonic()
    try:
        sock.connect((ip, PEER_PORT))
        sock.sendall(_header(0, req_type, dejavu=int(time.time()) & 0xFFFF or 1))
        buf = b""
        while time.monotonic() - started < timeout:
            try:
                chunk = sock.recv(32768)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            for msg_type, payload in _frames(buf):
                if msg_type == resp_type and len(payload) >= min_size:
                    return payload, (time.monotonic() - started) * 1000
    except OSError:
        return None
    finally:
        sock.close()
    return None


def query_epoch_context(ip: str, timeout: float = DEFAULT_TIMEOUT_S) -> AntEpochContext | None:
    """Ask one node for the epoch context. None if it does not answer in time."""
    got = _ask(ip, REQUEST_ANT_EPOCH_CONTEXT, RESPOND_ANT_EPOCH_CONTEXT, EPOCH_CONTEXT_SIZE, timeout)
    if got is None:
        return None
    payload, latency = got
    return _parse_context(payload, ip, latency)


def query_system_info(ip: str, timeout: float = DEFAULT_TIMEOUT_S) -> SystemInfo | None:
    """Epoch, tick range and spectrum size, straight from the node. Unsigned."""
    got = _ask(ip, REQUEST_SYSTEM_INFO, RESPOND_SYSTEM_INFO, SYSTEM_INFO_SIZE, timeout)
    if got is None:
        return None
    p, latency = got
    version, epoch, tick, initial_tick, latest_tick = struct.unpack_from("<hHIII", p, 0)
    entities, transactions = struct.unpack_from("<II", p, 20)
    return SystemInfo(
        version=version,
        epoch=epoch,
        tick=tick,
        initial_tick=initial_tick,
        latest_created_tick=latest_tick,
        number_of_entities=entities,
        number_of_transactions=transactions,
        source_ip=ip,
        latency_ms=latency,
    )


def query_revenue_data(ip: str, timeout: float = DEFAULT_TIMEOUT_S) -> RevenueData | None:
    """Per-computor revenue scores for all 676 slots. Unsigned, no operator key.

    The core's own header calls these approximate mid-epoch: the sliding-window tail is
    only finalized at epoch end. Treat a mid-epoch read as a live indication, not a
    sealed figure -- which is exactly the distinction the report already draws between
    `live` and `sealed`.
    """
    got = _ask(ip, REQUEST_REVENUE_DATA, RESPOND_REVENUE_DATA, REVENUE_DATA_SIZE, timeout)
    if got is None:
        return None
    p, latency = got
    tick, doge_k, ipc = struct.unpack_from("<IHq", p, 0)
    off = 14
    n = NUMBER_OF_COMPUTORS
    tx = list(struct.unpack_from("<%dQ" % n, p, off))
    oracle = list(struct.unpack_from("<%dQ" % n, p, off + 8 * n))
    doge = list(struct.unpack_from("<%dQ" % n, p, off + 16 * n))
    return RevenueData(
        tick=tick,
        doge_k=doge_k,
        ipc=ipc,
        tx_score=tx,
        oracle_score=oracle,
        doge_score=doge,
        source_ip=ip,
        latency_ms=latency,
    )


def _parse_context(payload: bytes, ip: str, latency_ms: float) -> AntEpochContext:
    threshold, freshness, solutions, free_slots, max_children = struct.unpack_from("<IIIII", payload, 96)
    (epoch,) = struct.unpack_from("<H", payload, 116)
    return AntEpochContext(
        epoch=epoch,
        threshold=threshold,
        freshness_window=freshness,
        solution_count=solutions,
        free_ann_slots=free_slots,
        max_children_per_parent=max_children,
        spectrum_digest=payload[0:32].hex(),
        topology_hash=payload[32:64].hex(),
        data_hash=payload[64:96].hex(),
        source_ip=ip,
        latency_ms=latency_ms,
    )


def best_epoch_context(
    peers: list[str] | None = None,
    attempts: int = 5,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> AntEpochContext | None:
    """Try peers in order until one answers; that answer wins.

    Peers arrive sorted by tick, so the first responder is already among the most
    current. Measured across the live list: 8 of 10 nodes answered, all agreeing on
    epoch, threshold and task hashes, so a single good answer is enough -- there is
    nothing to reconcile between them.
    """
    for ip in (peers or fetch_peers())[:attempts]:
        ctx = query_epoch_context(ip, timeout=timeout)
        if ctx is not None:
            return ctx
    return None


# The task the network is mining against. These are compile-time constants in the
# core (src/public_settings.h), not something a node reports, so they are mirrored
# here -- and the live dataHash is what proves the mirror is still accurate.
TASK_INPUT_TRITS = 18
TASK_SEQUENCE_LENGTH = 24 * 365
TASK_WINDOW_WIDTH = 24 * 28
TASK_GRADED_WINDOWS = TASK_SEQUENCE_LENGTH - TASK_WINDOW_WIDTH
CANONICAL_DATA_HASH = "979cdc2247d2ca4ed3d614bf27896384cb1c9c3d804af6ede6b59fc52c0e3dfa"
CANONICAL_TOPOLOGY_HASH = "1dcc19941bb525e8a81fbdac612d3da92aba06e8753afb2974bcbea6a686d5da"

# Target-trit distribution decoded from data/bpp9000.task (see the concept doc). The
# point of carrying it is the zero: UNKNOWN is never a target, so the task is binary.
TASK_TARGET_ZEROS = 4344
TASK_TARGET_ONES = 4417
TASK_TARGET_UNKNOWN = 0


def collect(peers: list[str] | None = None, attempts: int = 5) -> dict:
    """One bundle of live mining state for the dashboard, or a reason it is missing.

    Each of the three queries is tried against peers in order until one answers;
    different queries may end up served by different nodes, which is fine -- they are
    independent reads of the same network state. Whichever node answered the colony
    query is reported as *the* node, since that is the headline data.
    """
    peer_list = peers or fetch_peers()
    probed = peer_list[:attempts]

    ctx = None
    responding = 0
    for ip in probed:
        got = query_epoch_context(ip)
        if got is not None:
            responding += 1
            if ctx is None:
                ctx = got

    if ctx is None:
        return {
            "status": "unavailable",
            "reason": "no Qubic node answered REQUEST_ANT_EPOCH_CONTEXT",
            "fetched_at": int(time.time()),
            "node": {"peers_total": len(peer_list), "peers_responding": 0},
        }

    info = None
    for ip in probed:
        info = query_system_info(ip)
        if info is not None:
            break

    rev = None
    for ip in probed:
        rev = query_revenue_data(ip)
        if rev is not None:
            break

    bundle: dict = {
        "status": "live",
        "fetched_at": int(time.time()),
        "node": {
            "ip": ctx.source_ip,
            "latency_ms": round(ctx.latency_ms),
            "version": info.version if info else None,
            "peers_total": len(peer_list),
            "peers_responding": responding,
        },
        "colony": {
            "solution_count": ctx.solution_count,
            "threshold": ctx.threshold,
            "free_ann_slots": ctx.free_ann_slots,
            "max_children_per_parent": ctx.max_children_per_parent,
        },
        "task": {
            "input_trits": TASK_INPUT_TRITS,
            "sequence_length": TASK_SEQUENCE_LENGTH,
            "window_width": TASK_WINDOW_WIDTH,
            "graded_windows": TASK_GRADED_WINDOWS,
            "data_hash": ctx.data_hash,
            # If this ever goes false the task changed and the mirrored constants
            # above (and the concept doc's analysis) no longer describe reality.
            "hash_verified": ctx.data_hash == CANONICAL_DATA_HASH
            and ctx.topology_hash == CANONICAL_TOPOLOGY_HASH,
            "target_zeros": TASK_TARGET_ZEROS,
            "target_ones": TASK_TARGET_ONES,
            "target_unknown": TASK_TARGET_UNKNOWN,
        },
    }

    if info is not None:
        bundle["epoch"] = {
            "epoch": info.epoch,
            "tick": info.tick,
            "initial_tick": info.initial_tick,
            "ticks_into_epoch": info.ticks_into_epoch,
        }
    else:
        bundle["epoch"] = {"epoch": ctx.epoch}

    if rev is not None:
        totals = rev.totals()
        total_sum = sum(totals)
        top10 = sum(sorted(totals, reverse=True)[:10])
        bundle["revenue"] = {
            "tick": rev.tick,
            "ipc": rev.ipc,
            "slots": NUMBER_OF_COMPUTORS,
            "tx_nonzero": sum(1 for x in rev.tx_score if x),
            "oracle_nonzero": sum(1 for x in rev.oracle_score if x),
            "doge_nonzero": sum(1 for x in rev.doge_score if x),
            "top10_share_pct": round(top10 / total_sum * 100, 2) if total_sum else 0.0,
            "histogram": totals,
            # The core's revenue_data.h says so itself: exact only at epoch end.
            "approximate": True,
        }

    return bundle


if __name__ == "__main__":  # pragma: no cover - manual probe
    peers = fetch_peers()
    ctx = best_epoch_context(peers)
    if ctx is None:
        raise SystemExit("no node answered")
    print("-- ant colony (epoch context) ------------------------")
    print(f"epoch           {ctx.epoch}")
    print(f"threshold       {ctx.threshold}")
    print(f"solutions       {ctx.solution_count}")
    print(f"free ANN slots  {ctx.free_ann_slots}")
    print(f"child cap       {'unbounded' if ctx.max_children_is_unbounded else ctx.max_children_per_parent}")
    print(f"data hash       {ctx.data_hash}")
    print(f"from            {ctx.source_ip} ({ctx.latency_ms:.0f} ms)")

    for ip in peers[:5]:
        info = query_system_info(ip)
        if info:
            print("\n-- node / epoch -------------------------------------")
            print(f"node version    {info.version}")
            print(f"tick            {info.tick} ({info.ticks_into_epoch} into epoch)")
            print(f"entities        {info.number_of_entities:,}")
            print(f"transactions    {info.number_of_transactions:,}")
            break

    for ip in peers[:5]:
        rev = query_revenue_data(ip)
        if rev:
            totals = rev.totals()
            ranked = sorted(totals, reverse=True)
            top_share = sum(ranked[:10]) / sum(totals) * 100 if sum(totals) else 0
            print("\n-- revenue scores (all 676 slots, unsigned) ----------")
            print(f"tick            {rev.tick}")
            print(f"ipc (cap)       {rev.ipc:,}")
            print(f"tx  nonzero     {sum(1 for x in rev.tx_score if x)}/676")
            print(f"oracle nonzero  {sum(1 for x in rev.oracle_score if x)}/676")
            print(f"doge nonzero    {sum(1 for x in rev.doge_score if x)}/676")
            print(f"top-10 share    {top_share:.1f}%")
            break
