"""The ant colony's call for the next BTC hour, rebuilt from public data.

Since epoch 240 the BPP-9000 networks are graded on the hourly BTC direction the
nodes store through the Price oracle (qdr/antask.py). A network has no inputs: it
runs from a fixed start state and emits one trit per step, and the trit at the
position of an hour nobody knows yet is its call for that hour (0 down, 1 up or
level, 2 unsure). The network does not publish it, and a node hands out networks
only to its operator. So this module rebuilds them:

  1. scan  -- every accepted solution of the epoch, from the ANT_SOLU messages in
              a Bob node's tick logs (filtered to log type 255, so a call costs
              ~70 KB per 100 ticks). Each names its parent by (tick, tx index),
              so any network's lineage walks back to its identity's root.
  2. track -- the lineage of the best network (lowest logged error) is replayed
              with qubic/core's own reference scorer (tools/replayer), one walk
              per solution, in a long-running subprocess that keeps the 512 MB
              pool and the rebuilt networks in memory. Every walk must reproduce
              the error the node logged, or nothing it produced is used. Since
              v0.17.0 the best lineages of the next identities are rebuilt too,
              and every verified network's outputs are kept (colony_outputs).
  3. rank  -- each rebuilt network is judged on the recent hours it could not
              have known when it was made (net_record); the best lower bound of
              the hit rate wins (select_network). CFB, 2026-11-24: the score is
              a stopgap, the network that predicts best should call.
  4. call  -- for the coming hour, two calls are recorded once, before the hour
              exists on-chain, and judged when it arrives: rule "score" (the
              complete best-score network, btc_calls) and rule "accuracy" (the
              best-ranked network, or the score network while none has enough
              judged hours, btc_calls_accuracy).

A walk needs, besides the solution itself: the epoch's spectrum digest (seeds the
pool and the control/output neurons), K12(anchorTick || K12(TickData)) of its
anchor tick (raw tick data from a peer), and the targets its anchor tick was
graded on -- the first frame plus the live hours stored in a tick before the
anchor, which follow from the price series' append ticks.

Measured 2026-11-18: one walk takes ~11 s on one core; the leading lineage of
epoch 240 was 111 deep after 2.5 h and grew by ~45 per hour.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import threading
import time
from typing import Optional

from qdr import antask

ANT_SOLU_HEX = "414e545f534f4c55"
ROOT = (0, 0xFFFFFFFF)
ACCEPTED = (0, 1)
CUSTOM_LOG_TYPE = 255

def cpu_has_avx2() -> Optional[bool]:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("flags"):
                    return " avx2" in line
    except OSError:
        return None
    return None


def memory_limit_bytes() -> Optional[int]:
    """The container's memory limit (cgroup v2 or v1), None when unlimited/unknown."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = open(path, encoding="utf-8").read().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < (1 << 60):
            return int(raw)
        return None
    return None


def pick_replayer() -> str:
    """QDR_REPLAYER if set; else the AVX2 build where the CPU has AVX2, the
    portable build where it does not (an AVX2 binary dies on SIGILL there)."""
    env = os.environ.get("QDR_REPLAYER")
    if env:
        return env
    fast, portable = "/usr/local/bin/qdr-replay", "/usr/local/bin/qdr-replay-portable"
    if cpu_has_avx2() is False and os.path.isfile(portable):
        return portable
    return fast


REPLAYER = pick_replayer()
SCAN_CHUNK_TICKS = 500
SCAN_MAX_CALLS = int(os.environ.get("QDR_COLONY_SCAN_CALLS", "40"))
# Walks per tracker step: ~11 s each, so a step stays a few minutes and the
# call for the next hour is never stuck behind a long catch-up.
WALKS_PER_STEP = int(os.environ.get("QDR_COLONY_WALKS", "25"))
SHIFT_CAP = 168

# Which network makes the call (docs/CONCEPT_BTC_RANKING.de.md). CFB, 2026-11-24:
# the score -- the error over the known hours -- is a stopgap; use the network
# that predicts best. So besides the best-score lineage, the best lineages of the
# next identities are rebuilt too (LINES in all, SIDE_WALKS_PER_STEP walks per
# step between them), every rebuilt network's outputs are kept, and each one is
# judged on the hours it could not have known. Over a rolling window: CFB, "we
# cannot use the whole epoch because models are retrained, pick some number from
# practice". "?" is an abstention (CFB), never a hit or a miss.
LINES = int(os.environ.get("QDR_COLONY_LINES", "4"))
SIDE_WALKS_PER_STEP = int(os.environ.get("QDR_COLONY_SIDE_WALKS", "10"))
RECORD_WINDOW_H = int(os.environ.get("QDR_COLONY_WINDOW_H", "48"))
RECORD_MIN_DECIDED = int(os.environ.get("QDR_COLONY_MIN_DECIDED", "12"))
RANKING_SHOWN = 10
SIDE_PEERS = 5


# -- logs ------------------------------------------------------------------------

def parse_solution(entry: dict) -> Optional[dict]:
    """One ANT_SOLU log entry -> the solution, or None for any other message.

    AntSolutionLogMessage (core src/logging/logging.h): 8-byte type, source key,
    nonce, then parentTick, parentSolutionIndexInTick, anchorTick, score, result.
    A solution's own address is (the tick it was published in, its transaction
    index) -- core qubic.cpp sets selfRef = { system.tick, transactionIndex }.
    """
    hexdata = str(entry.get("rawData") or entry.get("hex") or
                  (entry.get("body") or {}).get("hex") or "").lower().removeprefix("0x")
    if not hexdata.startswith(ANT_SOLU_HEX) or len(hexdata) < 184:
        return None
    raw = bytes.fromhex(hexdata[:184])
    pt, pi, anchor, score, result = struct.unpack_from("<IIIII", raw, 72)
    try:
        tick, idx = int(entry["tick"]), int(entry["transactionIndex"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"tick": tick, "tx_index": idx, "pubkey": raw[8:40].hex(), "nonce": raw[40:72].hex(),
            "parent_tick": pt, "parent_index": pi, "anchor_tick": anchor,
            "score": score, "result": result}


def custom_logs(bob, from_tick: int, to_tick: int) -> list[dict]:
    res = bob._call("qubic_getLogs", [{"fromTick": int(from_tick), "toTick": int(to_tick),
                                       "logType": CUSTOM_LOG_TYPE}])
    return res if isinstance(res, list) else []


def scan(store, bob, epoch: int, initial_tick: int, head: int,
         max_calls: int = SCAN_MAX_CALLS, chunk: int = SCAN_CHUNK_TICKS) -> dict:
    """Read the epoch's accepted solutions up to `head`, from where the last scan stopped.

    Starts at the epoch's first tick: a lineage can only be rebuilt when every
    ancestor is known, so the scan never skips ahead. A range Bob cannot serve
    ends the pass there; the next one retries it.
    """
    last = store.colony_scan_tick(epoch)
    tick = (last + 1) if last is not None else int(initial_tick)
    calls = new = 0
    while tick <= head and calls < max_calls:
        end = min(tick + chunk - 1, head)
        try:
            logs = custom_logs(bob, tick, end)
        except Exception:
            break
        calls += 1
        rows = [s for s in (parse_solution(e) for e in logs) if s and s["result"] in ACCEPTED]
        new += store.put_ant_solutions(epoch, rows)
        store.set_colony_scan_tick(epoch, end)
        tick = end + 1
    return {"calls": calls, "new": new, "scanned_to": tick - 1, "behind": max(0, head - tick + 1)}


def lineage(store, tick: int, tx_index: int, limit: int = 100_000) -> Optional[list[dict]]:
    """Root-first list of solutions ending at (tick, tx_index); None if one is missing."""
    chain = []
    ref = (int(tick), int(tx_index))
    while ref != ROOT:
        row = store.ant_solution(*ref)
        if row is None or len(chain) > limit:
            return None
        chain.append(row)
        ref = (row["parent_tick"], row["parent_index"])
    chain.reverse()
    return chain


def indexed_head(bob, node_tick: int) -> int:
    """The last tick Bob has indexed, never beyond the node's own tick.

    A tick Bob has not indexed yet answers with an empty log, which looks
    exactly like "no solutions" -- and a scan that moved past it would lose
    those solutions for good (the burn scan learned this on 2026-09-19).
    Without a status answer the scan keeps a margin of 100 ticks.
    """
    try:
        indexed = int((bob.status() or {}).get("currentIndexingTick") or 0)
    except Exception:
        indexed = 0
    return min(int(node_tick), indexed - 1) if indexed else int(node_tick) - 100


def missing_ref(store, tick: int, tx_index: int) -> Optional[tuple[int, int]]:
    """The first ancestor of (tick, tx_index) that is not stored, or None."""
    ref = (int(tick), int(tx_index))
    for _ in range(100_000):
        if ref == ROOT:
            return None
        row = store.ant_solution(*ref)
        if row is None:
            return ref
        ref = (row["parent_tick"], row["parent_index"])
    return None


def heal(store, bob, epoch: int, tick: int, tx_index: int, max_ticks: int = 60) -> int:
    """Re-read the ticks of missing ancestors, one at a time, until the lineage
    is whole or `max_ticks` reads are spent. Returns solutions recovered."""
    got = 0
    for _ in range(max_ticks):
        ref = missing_ref(store, tick, tx_index)
        if ref is None:
            break
        try:
            logs = custom_logs(bob, ref[0], ref[0])
        except Exception:
            break
        rows = [s for s in (parse_solution(e) for e in logs) if s and s["result"] in ACCEPTED]
        n = store.put_ant_solutions(epoch, rows)
        got += n
        if store.ant_solution(*ref) is None:
            break            # the node does not have it either: stop, report incomplete
    return got


# -- what a walk needs ------------------------------------------------------------

def shift_limit_at(anchor_tick: int, live_append_ticks: list[int], cap: int) -> int:
    """How many live hours a solution anchored at `anchor_tick` is graded on.

    doc 2.3a: the hours stored in a tick BEFORE the anchor, at most SHIFT_CAP.
    """
    return min(cap, sum(1 for t in live_append_ticks if t and t < anchor_tick))


def anchor_digest(store, tick: int, peers: list[str]) -> Optional[str]:
    """K12(tick || K12(TickData)), cached; None if no peer serves the tick data."""
    cached = store.colony_anchor(tick)
    if cached:
        return cached
    from qdr import antnode
    for ip in peers:
        data = antnode.query_tick_data(ip, tick)
        if data is None:
            continue
        tx_digest = bytes.fromhex(antask.k12_hex(data))
        digest = antask.k12_hex(struct.pack("<I", int(tick)) + tx_digest)
        store.put_colony_anchor(tick, digest)
        return digest
    return None


def ref_id(row: dict) -> str:
    return f"{row['tick']}:{row['tx_index']}"


# -- which network predicts best ----------------------------------------------------

def hour_points(task: dict, series: dict) -> list[dict]:
    """The epoch's live hours, oldest first: hour, emit index `j` (the network's
    output for it is trits[j]), target bit, and the tick it was stored in."""
    targets = task["targets"]
    limit = int(task["shift_limit"])
    window = len(targets) - limit
    base = int(task["base_hour"])
    ticks = {int(p["hour"]): int(p.get("append_tick") or 0) for p in (series or {}).get("points") or []}
    return [{"hour": base + 1 + j, "j": j, "up": int(targets[window + j]),
             "append_tick": ticks.get(base + 1 + j, 0)} for j in range(limit)]


def net_record(trits: str, born_tick: int, hours: list[dict], window_h: int = RECORD_WINDOW_H) -> dict:
    """How a network did on the hours it could not have known, within the last
    `window_h` live hours.

    An hour h counts only if the hour two before it was stored in a tick after
    the network was published. That hour is stored a few minutes after h-1
    begins, so the network existed before h began -- and h was never part of
    what it was graded on (only hours stored before its anchor are, and the
    anchor is never after the network's own tick). A "?" or a missing output
    is an abstention: neither a hit nor a miss (CFB, 2026-11-24).
    """
    hits = misses = abstained = 0
    if hours:
        last = hours[-1]["hour"]
        stored = {h["hour"]: h["append_tick"] for h in hours}
        for h in hours:
            if h["hour"] <= last - window_h:
                continue
            prior = stored.get(h["hour"] - 2)
            if not prior or int(born_tick) >= prior:
                continue
            c = trits[h["j"]] if h["j"] < len(trits) else "2"
            if c not in "01":
                abstained += 1
            elif int(c) == h["up"]:
                hits += 1
            else:
                misses += 1
    return {"hits": hits, "misses": misses, "abstained": abstained, "decided": hits + misses}


def wilson_low(k: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the 95 % Wilson interval for k hits in n: what the hit
    rate is at least, allowing for luck. 5 of 5 gives 0.57, 60 of 100 gives 0.50."""
    if n <= 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (centre - margin) / d


def rank_key(c: dict, min_decided: Optional[int] = None) -> tuple:
    need = RECORD_MIN_DECIDED if min_decided is None else min_decided
    return (c["decided"] < need, -c["wilson"], -c["decided"], -int(c.get("shift") or 0),
            int(c.get("error") or 0), -int(c["tick"]))


def select_network(cands: list[dict], min_decided: Optional[int] = None) -> Optional[dict]:
    """The network with the best measured hit rate, or None while no network has
    `min_decided` judged hours in the window (the score then decides).
    Ranked by the Wilson lower bound; ties by more hours, higher shift, fewer
    errors, newer."""
    need = RECORD_MIN_DECIDED if min_decided is None else min_decided
    eligible = [c for c in cands if c["decided"] >= need]
    return min(eligible, key=lambda c: rank_key(c, need)) if eligible else None


# -- the replayer process ------------------------------------------------------------

class ReplayerError(RuntimeError):
    pass


class Replayer:
    """tools/replayer in batch mode, kept running for one epoch (one pool)."""

    def __init__(self, path: str = REPLAYER):
        self.path = path
        self.proc: Optional[subprocess.Popen] = None
        self.seed: Optional[str] = None
        self.known: dict[str, tuple[int, int]] = {}      # ref -> (error, shift)

    @staticmethod
    def available(path: str = REPLAYER) -> bool:
        return bool(path) and (os.path.isfile(path) or shutil.which(path) is not None)

    def _send(self, line: str) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def _read(self) -> str:
        assert self.proc and self.proc.stdout
        out = self.proc.stdout.readline()
        if not out:
            # Say why: -4 is SIGILL (CPU lacks an instruction), -9 SIGKILL (usually
            # the container's memory limit -- the pool alone is 512 MB).
            try:
                rc = self.proc.wait(timeout=5)
                err = (self.proc.stderr.read() or "")[-300:] if self.proc.stderr else ""
            except Exception:
                rc, err = None, ""
            raise ReplayerError(f"replayer exited (code {rc}) {err}".strip())
        return out.rstrip("\n")

    def pool_file(self, seed: str) -> Optional[str]:
        """Where the epoch's 512 MB pool lives on disk, or None to keep it in memory.

        In memory, the pool got the replayer SIGKILLed on report.qubic.tools
        (2026-11-18, exit -9): the host has no room for another 512 MB of
        anonymous memory. A mapped file is page cache the kernel can drop and
        re-read instead. One file per epoch; the previous epoch's is removed.
        """
        base = os.environ.get("QDR_COLONY_POOL_DIR") or (
            os.path.join(os.environ["DATA_DIR"], "raw") if os.environ.get("DATA_DIR") else None)
        if not base:
            return None
        try:
            os.makedirs(base, exist_ok=True)
            keep = f"colony-pool-{seed[:16]}.bin"
            for name in os.listdir(base):
                if name.startswith("colony-pool-") and name != keep:
                    os.remove(os.path.join(base, name))
        except OSError:
            return None
        return os.path.join(base, keep)

    def start(self, seed: str) -> None:
        self.close()
        self.proc = subprocess.Popen([self.path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)
        pool = self.pool_file(seed)
        self._send(f"seed {seed} {pool}" if pool else f"seed {seed}")
        answer = self._read()
        if answer != "ok seed":
            raise ReplayerError(f"seed rejected: {answer}")
        self.seed = seed
        self.known = {}

    def close(self) -> None:
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        self.seed = None
        self.known = {}

    def targets(self, bits: str) -> None:
        self._send(f"targets {bits}")
        self._read()

    def load(self, ref: str, shift: int, ann: str, error: int) -> None:
        self._send(f"load {ref} {shift} {ann}")
        self.known[ref] = (error, shift)

    def walk(self, ref: str, parent: str, pubkey: str, nonce: str, anchor: str, limit: int) -> tuple[int, int]:
        self._send(f"walk {ref} {parent} {pubkey} {nonce} {anchor} {limit}")
        parts = self._read().split()
        if parts[0] != "res":
            raise ReplayerError(" ".join(parts))
        err, shift = int(parts[2]), int(parts[3])
        self.known[ref] = (err, shift)
        return err, shift

    def dump(self, ref: str) -> tuple[int, str]:
        self._send(f"dump {ref}")
        parts = self._read().split()
        if parts[0] != "ann":
            raise ReplayerError(" ".join(parts))
        return int(parts[2]), parts[3]

    def calls(self, ref: str, n: int) -> str:
        self._send(f"calls {ref} {n}")
        parts = self._read().split()
        if parts[0] != "calls":
            raise ReplayerError(" ".join(parts))
        return parts[2] if len(parts) > 2 else ""


# -- the tracker -----------------------------------------------------------------

class Tracker:
    """Follows the colony's best networks of the running epoch and records two
    calls per hour: the best-score network's (rule "score", as since v0.16.0)
    and the best-predicting network's (rule "accuracy", since v0.17.0)."""

    def __init__(self, store, replayer: Optional[Replayer] = None):
        self.store = store
        self.rep = replayer or Replayer()
        self.epoch: Optional[int] = None
        self.verified: Optional[dict] = None     # newest verified node of the best-score lineage
        self.bad: set[str] = set()               # side-line nodes that did not reproduce their error
        self.heal_tried: set[str] = set()        # side-line tips whose gaps were read again once
        self.side_turn = 0
        self.lock = threading.Lock()

    def status(self, **state) -> dict:
        state.setdefault("epoch", self.epoch)
        if self.verified:
            state.setdefault("network", {k: self.verified.get(k) for k in
                                         ("tick", "tx_index", "pubkey", "error", "shift")})
        self.store.set_colony_status(state, int(time.time()))
        return state

    def step(self, bob, peers: list[str]) -> dict:
        with self.lock:
            try:
                return self._step(bob, peers)
            except ReplayerError as e:
                self.rep.close()
                self.verified = None
                mem = memory_limit_bytes()
                return self.status(state="replayer-failed", detail=str(e)[:300],
                                   replayer=self.rep.path, cpu_avx2=cpu_has_avx2(),
                                   memory_limit_mb=round(mem / 2 ** 20) if mem else None)

    def _step(self, bob, peers: list[str]) -> dict:
        from qdr import antnode
        if not Replayer.available(self.rep.path):
            return self.status(state="no-replayer", detail="tools/replayer is not installed in this image")

        ctx = antnode.best_epoch_context(peers)
        info = None
        for ip in peers[:5]:
            info = antnode.query_system_info(ip)
            if info:
                break
        if ctx is None or info is None:
            return self.status(state="no-node")
        task, series = antnode.read_feed(peers, info)
        if task is None or series is None:
            return self.status(state="no-task")
        targets = task["targets"]
        window = len(targets) - int(task["shift_limit"])
        if antask.k12_hex(targets[:window]) != ctx.data_hash:
            return self.status(state="task-unverified")
        epoch = int(task["epoch"])

        if epoch != self.epoch:
            self.rep.close()
            self.epoch, self.verified = epoch, None
            self.bad = set()
            self.heal_tried = set()
            self.store.prune_ant_solutions(keep_from_epoch=epoch - 1)

        scanned = scan(self.store, bob, epoch, info.initial_tick, indexed_head(bob, info.tick))

        tips = self.store.best_ant_solutions_per_identity(epoch, max(1, LINES))
        if not tips:
            return self.status(state="no-solutions", scanned=scanned)
        best = tips[0]
        chain = lineage(self.store, best["tick"], best["tx_index"])
        if chain is None:
            healed = heal(self.store, bob, epoch, best["tick"], best["tx_index"])
            chain = lineage(self.store, best["tick"], best["tx_index"])
            if chain is None:
                return self.status(state="lineage-incomplete", scanned=scanned, healed=healed)

        if self.rep.seed != ctx.spectrum_digest:
            self.rep.start(ctx.spectrum_digest)
        self.rep.targets("".join("1" if b else "0" for b in targets))

        live_ticks = [p["append_tick"] for p in series["points"]]
        adv = self._advance(epoch, chain, peers, live_ticks, WALKS_PER_STEP)
        if adv["state"] == "anchor-unavailable":
            return self.status(state="anchor-unavailable", tick=adv["tick"], scanned=scanned)
        if adv["state"] == "mismatch":
            # A rebuilt network that does not reproduce the node's own score is
            # not the network the colony holds; nothing downstream may use it.
            self.rep.close()
            self.verified = None
            n = adv["node"]
            return self.status(state="mismatch", tick=n["tick"], tx_index=n["tx_index"],
                               logged=n["score"], rebuilt=adv["error"])
        last, walked, walk_time = adv["last"], adv["walked"], adv["walk_time"]
        if last is None:
            return self.status(state="catching-up", scanned=scanned)

        # The score rule's call comes only from a network whose whole lineage has
        # been rebuilt: the newest node of the best lineage. While a lineage is
        # still being caught up, its partial prefix is an older, weaker network
        # (measured 2026-11-18: depth 50 of 142 had 3778 errors where the leaf
        # has 3434 and called the opposite direction), so it never speaks for the
        # rule. A complete network verified earlier keeps calling until the new
        # one is caught up.
        i, node, err, shift = last
        complete = i + 1 == len(chain)
        mine = dict(node, error=err, shift=shift, depth=i + 1, lineage=len(chain), complete=complete)
        prev = self.verified
        if complete or prev is None or not prev.get("complete") or ref_id(prev) not in self.rep.known:
            self.verified = mine
        v = self.verified
        score_net = v if v.get("complete") and ref_id(v) in self.rep.known else None

        # The calls first, from what is rebuilt so far: the side lines below
        # can take minutes (tick data from nodes, walks), and the coming hour's
        # call must never wait on them.
        hours = hour_points(task, series)
        ranking, chosen = self._rank(epoch, hours)
        call = self._call(epoch, task, series, score_net, chosen)

        # The next identities' best lineages, a few walks each step, in turn.
        lines = [{"pubkey": best["pubkey"], "depth": i + 1, "lineage": len(chain), "score": best["score"],
                  "state": "tracking" if complete else "catching-up"}]
        # (The ranking shown is the one the call was made from; networks rebuilt
        # below join it on the next step.)
        lines += self._side_lines(epoch, tips[1:], bob, peers, live_ticks)
        if walked:
            self.walk_s = walk_time / walked
        sel = {"rule": "accuracy" if chosen else "score-fallback", "window_h": RECORD_WINDOW_H,
               "min_decided": RECORD_MIN_DECIDED, "line_count": LINES,
               "chosen": _public_net(chosen) if chosen else None}
        return self.status(state="tracking" if complete else "catching-up",
                           depth=i + 1, lineage=len(chain), walked=walked, scanned=scanned,
                           call=call.get("score"), call_accuracy=call.get("accuracy"),
                           selection=sel, ranking=ranking, lines=lines,
                           walk_s=round(getattr(self, "walk_s", 15.0), 1))

    # -- rebuilding --------------------------------------------------------------

    def _advance(self, epoch: int, chain: list[dict], peers: list[str], live_ticks: list[int],
                 budget: int) -> dict:
        """Rebuild up to `budget` more nodes of one lineage, from the deepest one
        already rebuilt (in this process, or saved). Every node rebuilt has its
        outputs kept. `last` is (index, node, error, shift) of the deepest
        verified node, None if there is none yet."""
        start = 0
        for i in range(len(chain) - 1, -1, -1):
            if ref_id(chain[i]) in self.rep.known:
                start = i + 1
                break
        else:
            saved = {(n["tick"], n["tx_index"]): n for n in self.store.colony_nets(epoch)}
            for i in range(len(chain) - 1, -1, -1):
                n = saved.get((chain[i]["tick"], chain[i]["tx_index"]))
                if n and n["error"] == chain[i]["score"]:
                    self.rep.load(ref_id(chain[i]), n["shift"], n["ann"], n["error"])
                    if not self.store.has_colony_output(chain[i]["tick"], chain[i]["tx_index"]):
                        self._keep_outputs(epoch, chain[i], n["error"], n["shift"], i + 1)
                    start = i + 1
                    break

        cap = SHIFT_CAP
        walked = 0
        walk_time = 0.0
        last = None
        for i in range(start, min(len(chain), start + max(0, budget))):
            node = chain[i]
            anchor = anchor_digest(self.store, node["anchor_tick"], peers)
            if anchor is None:
                return {"state": "anchor-unavailable", "tick": node["anchor_tick"], "walked": walked}
            parent = "root" if i == 0 else ref_id(chain[i - 1])
            limit = shift_limit_at(node["anchor_tick"], live_ticks, cap)
            t0 = time.time()
            err, shift = self.rep.walk(ref_id(node), parent, node["pubkey"], node["nonce"], anchor, limit)
            walk_time += time.time() - t0
            walked += 1
            if err != node["score"]:
                self.rep.known.pop(ref_id(node), None)
                return {"state": "mismatch", "node": node, "error": err, "walked": walked}
            self._keep_outputs(epoch, node, err, shift, i + 1)
            last = (i, node, err, shift)

        if last is None and start > 0:
            node = chain[start - 1]
            err, shift = self.rep.known[ref_id(node)]
            last = (start - 1, node, err, shift)
        if walked and last is not None:
            node = last[1]
            ann_shift, ann = self.rep.dump(ref_id(node))
            self.store.put_colony_net({"tick": node["tick"], "tx_index": node["tx_index"], "epoch": epoch,
                                       "pubkey": node["pubkey"], "shift": ann_shift, "error": last[2],
                                       "ann": ann, "saved_at": int(time.time())})
        return {"state": "ok", "last": last, "walked": walked, "walk_time": walk_time}

    def _keep_outputs(self, epoch: int, node: dict, err: int, shift: int, depth: int) -> None:
        """A verified network's outputs for every live hour of the epoch. They
        are fixed the moment it exists (no inputs), so one rollout now is its
        whole record to come."""
        trits = self.rep.calls(ref_id(node), SHIFT_CAP)
        self.store.put_colony_output({
            "tick": node["tick"], "tx_index": node["tx_index"], "epoch": epoch, "pubkey": node["pubkey"],
            "anchor_tick": node["anchor_tick"], "error": err, "shift": shift, "depth": depth,
            "trits": trits, "saved_at": int(time.time())})

    def _side_lines(self, epoch: int, tips: list[dict], bob, peers: list[str], live_ticks: list[int]) -> list[dict]:
        """The best lineages of the next identities, SIDE_WALKS_PER_STEP walks in
        all per step, a different one first each time. A node that does not
        reproduce its logged error is set aside with everything after it; it
        never stops the best-score lineage."""
        out: list[dict] = []
        if not tips:
            return out
        budget = SIDE_WALKS_PER_STEP
        k = self.side_turn % len(tips)
        self.side_turn += 1
        # Tick data for a side line's anchors from the first few nodes only:
        # each node that lacks a tick costs its 15 s timeout, and the best-score
        # line already asks them all.
        near = peers[:SIDE_PEERS]
        for tip in tips[k:] + tips[:k]:
            row = {"pubkey": tip["pubkey"], "score": tip["score"]}
            chain = lineage(self.store, tip["tick"], tip["tx_index"])
            if chain is None and ref_id(tip) not in self.heal_tried:
                # One attempt per tip: a gap the node cannot fill stays a gap,
                # and retrying it every step would cost Bob calls for nothing.
                self.heal_tried.add(ref_id(tip))
                heal(self.store, bob, epoch, tip["tick"], tip["tx_index"], max_ticks=10)
                chain = lineage(self.store, tip["tick"], tip["tx_index"])
            if chain is None:
                out.append(dict(row, state="lineage-incomplete"))
                continue
            row["lineage"] = len(chain)
            if any(ref_id(n) in self.bad for n in chain):
                out.append(dict(row, state="mismatch"))
                continue
            if budget <= 0:
                depth = max((i + 1 for i, n in enumerate(chain) if ref_id(n) in self.rep.known), default=0)
                out.append(dict(row, depth=depth, state="waiting"))
                continue
            adv = self._advance(epoch, chain, near, live_ticks, budget)
            budget -= adv["walked"]
            if adv["state"] == "mismatch":
                self.bad.add(ref_id(adv["node"]))
                out.append(dict(row, state="mismatch"))
                continue
            if adv["state"] != "ok":
                out.append(dict(row, state=adv["state"]))
                continue
            depth = adv["last"][0] + 1 if adv["last"] else 0
            out.append(dict(row, depth=depth, state="tracking" if depth == len(chain) else "catching-up"))
        return out

    # -- choosing ----------------------------------------------------------------

    def _rank(self, epoch: int, hours: list[dict]) -> tuple[list[dict], Optional[dict]]:
        """Every rebuilt network of the epoch with its record in the window; the
        top of the list, and the one the accuracy rule picks (None: fallback)."""
        cands = []
        for r in self.store.colony_outputs(epoch):
            if f"{r['tick']}:{r['tx_index']}" in self.bad:
                continue
            rec = net_record(r["trits"], r["tick"], hours)
            cands.append(dict(r, **rec, wilson=round(wilson_low(rec["hits"], rec["decided"]), 4)))
        cands.sort(key=rank_key)
        return [_public_net(c) for c in cands[:RANKING_SHOWN]], select_network(cands)

    def _call(self, epoch: int, task: dict, series: dict, score_net: Optional[dict],
              chosen: Optional[dict]) -> dict:
        """Record both rules' outputs for the COMING hour, once, before it begins.

        The hour the oracle stores next is already running (its move from the
        last full hour is under way), so a call for it would be made mid-hour.
        The call is for the hour after it instead: made up to an hour before
        that hour starts, judged when the oracle stores it (~5 min after it
        ends). A network emits every position, so this costs nothing extra.

        Rule "score": the complete best-score network. Rule "accuracy": the
        network with the best record in the window; while no network has
        enough judged hours, the best-score network again (`fallback`).
        """
        out: dict = {}
        if score_net is None and chosen is None:
            return out
        hour = int(series["next_hour"]) + 1
        if time.time() >= antask.hour_at(hour) - antask.HOUR_S:
            return out                           # it has begun: too late to call it
        base = int(task["base_hour"])
        j = hour - base - 1                      # emit WINDOW_WIDTH + j is this hour
        if j < 0 or j >= SHIFT_CAP:
            return out
        # The hour must still be unknown when the call is written down.
        from qdr import antnode
        fresh = None
        for ip in antnode.fetch_peers()[:3]:
            fresh = antnode.query_price_series(ip, hour, 1)
            if fresh:
                break
        if not fresh or int(fresh["next_hour"]) != hour - 1:
            return out
        now = int(time.time())
        score_trit = None
        if score_net is not None:
            trits = self.rep.calls(ref_id(score_net), j + 1)
            if len(trits) > j:
                score_trit = int(trits[j])
                row = {"hour": hour, "epoch": epoch, "call": score_trit, "net_tick": score_net["tick"],
                       "net_index": score_net["tx_index"], "pubkey": score_net["pubkey"],
                       "net_error": score_net["error"], "net_shift": score_net["shift"], "made_at": now}
                out["score"] = {"hour": hour, "call": score_trit, "new": self.store.put_btc_call(row)}
        if chosen is not None:
            t = chosen["trits"][j] if j < len(chosen["trits"]) else "2"
            row = {"hour": hour, "epoch": epoch, "call": int(t) if t in "012" else 2,
                   "net_tick": chosen["tick"], "net_index": chosen["tx_index"], "pubkey": chosen["pubkey"],
                   "net_error": chosen["error"], "net_shift": chosen["shift"],
                   "net_hits": chosen["hits"], "net_decided": chosen["decided"],
                   "net_abstained": chosen["abstained"], "fallback": 0, "made_at": now}
        elif score_trit is not None:
            row = {"hour": hour, "epoch": epoch, "call": score_trit, "net_tick": score_net["tick"],
                   "net_index": score_net["tx_index"], "pubkey": score_net["pubkey"],
                   "net_error": score_net["error"], "net_shift": score_net["shift"],
                   "net_hits": None, "net_decided": None, "net_abstained": None, "fallback": 1, "made_at": now}
        else:
            return out
        out["accuracy"] = {"hour": hour, "call": row["call"], "fallback": bool(row["fallback"]),
                           "new": self.store.put_btc_call_accuracy(row)}
        return out


def _public_net(c: dict) -> dict:
    """A ranked network as the API shows it: no outputs, no network bytes."""
    rate = round(c["hits"] / c["decided"], 4) if c.get("decided") else None
    return {"tick": c["tick"], "tx_index": c["tx_index"], "pubkey": c["pubkey"], "error": c["error"],
            "shift": c["shift"], "depth": c.get("depth"), "hits": c["hits"], "misses": c["misses"],
            "abstained": c["abstained"], "decided": c["decided"], "rate": rate, "wilson": c["wilson"]}


# -- judging -----------------------------------------------------------------------

def judge(calls: list[dict]) -> list[dict]:
    """Each call with `hit`: True/False once its hour is known, None before.

    An "unsure" call (2) is an abstention (CFB, 2026-11-24): `hit` stays None
    and `abstained` is True once its hour is known. It is neither a hit nor a
    miss. (Until v0.17.0 it counted as a miss, the way the scorer counts it.)
    """
    out = []
    for c in calls:
        up = c.get("up")
        unsure = int(c["call"]) == 2
        hit = None if up is None or unsure else (int(c["call"]) == int(up))
        out.append(dict(c, hit=hit, abstained=up is not None and unsure))
    return out


def rate(judged: list[dict]) -> dict:
    """`calls` are the decided ones (hit or miss); `unsure` the abstentions
    beside them, which the rate leaves out."""
    done = [c for c in judged if c["hit"] is not None]
    hits = sum(1 for c in done if c["hit"])
    return {"calls": len(done), "hits": hits,
            "unsure": sum(1 for c in judged if c.get("abstained")),
            "rate": round(hits / len(done), 4) if done else None}


def baselines(hours: list[dict], judged: list[dict]) -> dict:
    """What naive rules would have scored on the same hours: always up, always
    down, and 'same as the hour before' -- so a colony rate can be read against
    them. Always down matters as much as always up: in a falling market a
    network that mostly calls down scores well without any skill."""
    up = {h["hour"]: int(h["up"]) for h in hours}
    done = [c for c in judged if c["hit"] is not None]
    if not done:
        return {"always_up": None, "always_down": None, "repeat_last": None}
    always = sum(1 for c in done if up.get(c["hour"]) == 1) / len(done)
    never = sum(1 for c in done if up.get(c["hour"]) == 0) / len(done)
    rep = [c for c in done if (c["hour"] - 1) in up]
    repeat = (sum(1 for c in rep if up[c["hour"] - 1] == up[c["hour"]]) / len(rep)) if rep else None
    return {"always_up": round(always, 4), "always_down": round(never, 4),
            "repeat_last": round(repeat, 4) if repeat is not None else None}


def streak(judged: list[dict]) -> dict:
    """The longest run of hits in consecutive hours, and the run still going.

    An hour without a judged call breaks a run, the same as a miss: a streak
    means hours in a row, not calls that happen to sit next to each other.
    """
    done = sorted((c for c in judged if c["hit"] is not None), key=lambda c: int(c["hour"]))
    longest = cur = 0
    prev = None
    for c in done:
        if prev is not None and int(c["hour"]) != prev + 1:
            cur = 0
        cur = cur + 1 if c["hit"] else 0
        longest = max(longest, cur)
        prev = int(c["hour"])
    return {"longest": longest, "current": cur}


def run_chance(n: int, k: int, p: float = 0.5) -> Optional[float]:
    """Chance that n independent calls, each right with probability p, hold at
    least one run of k hits -- what luck alone gives a streak of that length.

    Streaks look like skill and mostly are not: over one epoch (168 hours) a
    coin toss gets 5 in a row 94 % of the time.
    """
    if k <= 0:
        return 1.0
    if n < k:
        return 0.0
    # dist[j]: chance that no run of k has happened yet and the current run is j.
    dist = [1.0] + [0.0] * (k - 1)
    for _ in range(n):
        nxt = [0.0] * k
        nxt[0] = sum(dist) * (1 - p)
        for j in range(k - 1):
            nxt[j + 1] = dist[j] * p
        dist = nxt
    return round(1.0 - sum(dist), 4)

