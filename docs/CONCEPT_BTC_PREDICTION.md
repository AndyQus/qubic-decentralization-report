# Concept: the ant colony's BTC prediction (BPP-9000, from epoch 240)

**Deutsche Fassung:** [CONCEPT_BTC_PREDICTION.de.md](CONCEPT_BTC_PREDICTION.de.md). Both versions
say the same; a change to one is made to the other.

> **Status (2026-11-18): stages 0, 1 and 2 implemented.** Stage 2 is described in §5a. The mining
> page reads the task live (message 80, K12-verified). The page `dashboard/btc.html` is linked and
> shows directions only, no prices (decision §7.1). The request to the core developers is in
> [`BTC_CALL_REQUEST.md`](BTC_CALL_REQUEST.md) (decision §7.2: both). Supersedes the conclusion
> "no forecast" from [`CONCEPT_BTC_ORACLE.de.md`](CONCEPT_BTC_ORACLE.de.md).
>
> **Since v0.17.0 (2026-11-24):** the network that makes the call is chosen by how well it
> predicts, no longer by its score — see [`CONCEPT_BTC_RANKING.md`](CONCEPT_BTC_RANKING.md).

Created: 2026-11-18 · Checked against: `qubic/core` @ `2fee231` (release v1.313.0), live node,
epoch 240, tick 91 910 766

---

## 0. The result in three sentences

1. **The network now trains on real BTC hours as they arrive live.** The fixed task file no
   longer exists. Every hour the nodes fetch the BTC/USDT price on-chain through the price oracle
   (Binance). Measured live, see §3.
2. **The prediction exists implicitly but is published nowhere.** Every network emits a sequence
   of trits. The output after the last known hour is its "call" for the next hour. No node hands
   out that call.
3. **The networks themselves are public only indirectly.** Fetching one directly
   (`REQUEST_ANT_PARENT_ANN`) needs an operator signature. But everything needed to **rebuild**
   them is public. Our own rebuild is the core of this concept (§5).

Answer to "Can we show the forecast now?": **Partly.** We can show the live feed, the task and the
colony's activity right away. The call itself we can only show once we rebuild the networks or
Qubic provides a public query.

---

## 1. How BPP-9000 works now (verified against the code)

Source: `doc/ant_colony_mining.md`, `src/mining/price_feed.h`, `src/mining/score_bpp9000.h`.

```
  Binance BTC/USDT ──► price oracle ──► every node stores hour H (on-chain, consensus)
                                              │
                         target[H] = 1 if price(H) ≥ price(H−1), else 0  (rounded to whole USDT)
                                              │
        ┌─────────────────────────────────────┴─────────────────────────────────────┐
        │  8760 targets (1 year up to epoch start = baseHour) │ +1 target per new hour │
        │◄──────────────── frame at shift 0 ─────────────────►│  … up to SHIFT_CAP = 168│
        └─────────────────────────────────────────────────────┴────────────────────────┘
                                              ▲
   Network: 1024 ternary neurons, 3 neighbours + LUT each, **no inputs**.
   It runs from a fixed start state and emits one trit per step:
   0 = down · 1 = up · 2 = "don't know" (counts as an error in the scorer)
```

- **Error** = number of wrong trits in the 8,760-hour frame. Fewer is better.
- **Acceptance threshold** (epoch 240): 4,350 errors, i.e. just better than a coin toss.
- **Shift**: if a network has at most 3,504 errors in the frame (`ADVANCE_THRESHOLD`, at least
  60 % right over a year), the frame moves on by one hour and is graded again there. But the frame
  can only move as far as real new hours have already been stored (`shiftLimit`). A higher shift
  always beats a lower error.
- **Colony**: every mining identity has a tree. A child inherits its parent's network, mutates the
  wiring (1,000 steps, seed from pubkey, nonce and anchor tick) and has to beat the parent. That
  holds **on the child's data**, i.e. including the new hours.
- **New hours**: the leader of every 5th tick may ask the oracle for the next missing hour, at the
  earliest 300 s after the full hour. After 24 h of outage the hour is filled with the previous
  hour's price (flag bit 0) and counted as "up".

(Since v0.17.0 our page counts a "don't know" as an abstention, on CFB's word — see
`CONCEPT_BTC_RANKING.md`.)

### What is prediction about it and what is not

The network sees **no prices**. It is a sequence generator that reproduces a year-long sequence
of up/down bits as closely as it can. Its call for the next hour is the continuation of that
sequence. Whether that is more than chance can be tested honestly in only one way:

> **A call counts only if the network was fixed before the hour.** For hour *h*, only a network
> whose anchor tick lies before the tick in which *h* was stored (`appendTick`) is judged. Then
> *h* is truly "out of sample".

The protocol already enforces this for the shift: a network only advances over hours it did not
know, provided it was minted before them. Our page measures exactly that, against the 50 % bar.

---

## 2. What is publicly readable

| Source | Path | Signature | Content | Status |
|---|---|---|---|---|
| Epoch context (76/77) | peer port 21841 | none | threshold, solution count, spectrum digest (= root seed), `dataHash` | already used |
| **Price series (78/79)** | peer port | none | hourly BTC prices, `baseHour`, `nextHour`, outage counter, digest | **new, checked live** |
| **Task data (80/81)** | peer port | none | targets and `shiftLimit` for an anchor tick | **new, checked live** |
| Solution tx (`inputType` 12) | tick data (Bob/RPC) | – | pubkey, parent ref, anchor tick, nonce, claimed error | public |
| Log `ANT_SOLU` (custom message 255) | Bob logs | – | result per solution (valid/reject…) and recomputed error, **without shift** | public, `qdr/tickstream.py` knows type 255 |
| Identity tree (72/73), parent ANN (74/75) | peer port | **operator** | shift per node, network bytes | closed to us |
| `antColonySolutions.eoe` | file on the node | – | best 676 networks at epoch end | not retrievable |

---

## 3. Live measurement (2026-11-18, about 12:45 UTC, node 82.197.173.133)

```
Price series: epoch=240 synced=1 source=1 (bootstrap) baseHour=40316 (08:00 UTC) nextHour=40321 (13:00 UTC)
              4 oracle queries succeeded, 0 failures, 0 filled
  09:00  64 685.33  appendTick 91 910 016   flags 0
  10:00  64 650.84  appendTick 91 910 026
  11:00  64 373.18  appendTick 91 910 040
  12:00  64 231.52  appendTick 91 910 051
  (Hours ≤ 08:00 carry flags = 2, so they come from the bootstrap file. Bit 1 is undocumented.)
Task data:    anchor=91 910 761 valid=1 shiftLimit=4 targets=8764 (4370 × up)
Colony:       solution_count = 0 (the epoch is a few hours old)
```

This shows: since this morning the network receives real new BTC hours. Up to 168 hours are
added by the end of the epoch.

---

## 4. Page idea: "Colony vs Bitcoin"

An animated page (`dashboard/btc.html`) in the style of Ticks Live: dark, calm, with motion that
means something. Three layers, unlocked one after the other.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  COLONY VS BITCOIN                               Epoch 240 · hour 5 of 168   │
│                                                                              │
│        ╭───────── next oracle hour in 23:41 ───────────────╮                 │
│        │      ◉  Colony call for 14:00 UTC:  ▲ UP          │  ← stage 2      │
│        │      Best network ▲ · swarm 412 ▲ / 263 ▼ / 1 ?   │                 │
│        ╰───────────────────────────────────────────────────╯                 │
│                                                                              │
│  HOUR BAND (moves left as soon as an hour arrives)                           │
│  … ▲ ▲ ▼ ▼ ▲ ▼ ▲ ▲ │ ▼  ▼  ▼  ▼  │ ?                                         │
│     previous year (frame) │ live since 08:00 │ open                          │
│                    call:  ✓  ✗  ✓  ✓     ← stage 2: hits green, misses red    │
│                                                                              │
│  HIT RATE      ━━━━━━━━━━━━━━━━━ 50 % coin toss ┈┈┈┈┈┈┈┈┈┈┈┈  ← stage 2      │
│  (rolling, per epoch, with confidence band)                                  │
│                                                                              │
│  COLONY        solutions 1 284 · accepted 71 % · best error 4 102            │
│                ● ● ●  (one pulse per accepted solution, live from the logs)  │
│  ORACLE        4/4 hours on time · 0 filled · digest 64e8…                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Animation with meaning:**
- **Countdown ring** to the next oracle hour (full hour + ≥ 300 s). When the hour arrives, the
  band moves one cell to the left and the cell is revealed green or red.
- **Pulse** for every accepted solution (from the `ANT_SOLU` logs over the existing tick stream),
  dimmed for rejected ones.
- **Growing frame**: a bar shows `shiftLimit`, i.e. how far into the "future" the colony may be
  graded (0 → 168).

**History:** one record per hour (hour, up/down, the best network's call, swarm distribution,
hit). From these come the hit rate per epoch and an archive of the epochs. The oracle hours can
also be fetched retroactively through message 78, 256 per request. The **calls**, however, exist
only from our first run, as everywhere in this project. The page reports that itself (`ready`,
`unwatched`).

---

## 5. Implementation in stages

### Stage 0 – repair the mining page (mandatory, independent of the rest)

`qdr/antask.py` looks for the task file by its live hash. That file no longer exists since epoch
240: `/v1/mining` returns `hash_verified: null`, the check script reports `task_unresolved`. New:
read the task through message 80. Cross-check with `dataHash` = K12 of the first 8,760 targets.
Switch the terminal line "Forecast" / "N open hours" (`tail_unknown`) to `shiftLimit`.

### Stage 1 – live feed without networks (feasible right away)

- `qdr/antnode.py`: `query_price_series()` (78) and `query_task_data()` (80). The prototype is in
  the scratchpad and checked.
- Worker job every 5 min: store new hours, oracle counters and `shiftLimit`. Plus the `ANT_SOLU`
  logs from the tick stream (count, result, error distribution per hour).
- Endpoints `/v1/btc/series`, `/v1/btc/colony`.
- Page with hour band, countdown, colony pulse and oracle health. Instead of the call it says
  honestly: "The colony's networks are not public – call to follow".

### Stage 2 – rebuild the networks and show the call

Everything needed is public:

| Building block | Source |
|---|---|
| Random pool (≈ 512 MB, `POOL_VEC_SIZE`) | from the spectrum digest (message 76), `generateRandom2Pool` |
| Root network of an identity | `deriveRootANN(pubkey, pool)` |
| Child network | parent network + walk with seed `K12(pubkey ‖ nonce ‖ anchorTickDigest)` |
| `anchorTickDigest` | `K12(anchorTick ‖ K12(TickData))`, TickData public |
| The child's targets | message 80 for the anchor tick |
| Check | recomputed error == `claimedScore` and log result `Valid` |

A **replayer** in C++ takes the scorer unchanged from `qubic/core` (reference:
`test/score_bpp9000_reference.h`, 409 lines, portable, or the AVX2 version from
`src/mining/score_bpp9000.h`). It rebuilds the lineages of the best trees and lets the best
network run beyond `8760 + shiftLimit`. The next trit is the call. Alongside, all rebuilt networks
vote (swarm).

Effort and risk: the scorer must be **bit-exact**, and every solution costs a full walk (1,000
evaluations). But it is enough to rebuild only the lineages of the leaders. The pool needs about
512 MB of RAM on the host. Before building it in comes an offline proof: rebuild a network of a
finished epoch and check its error against the log.

**In parallel, as a shortcut:** ask the core developers to add a public query "output of the best
network for hour h", or to make message 74 public (model: `docs/POOL_REQUEST.de.md`). If that
comes, the replayer is no longer needed.

### 5a. Stage 2 implemented: how the call is made (2026-11-18)

- **Rebuild:** `tools/replayer` compiles the reference scorer from `qubic/core` (tag from
  `QUBIC_CORE_TAG` in the Dockerfile) into a static program in the image. It is checked
  bit-exact: the core's test data (chain 11, depth 0 → 5966/0) and a live solution from epoch 240
  (tick 91,912,670 → 4332, as logged). A walk takes about 11 s on one core.
- **Solutions:** `qdr/colony.py` reads all `ANT_SOLU` logs from the epoch's first tick (Bob,
  `logType: 255`) and stores the accepted solutions. A solution's address is (tick, transaction
  index).
- **Best network:** the one with the lowest logged error. Its lineage is rebuilt incrementally:
  the process holds the pool and the networks, and the newest state lives in the store. **Every
  walk must hit the logged error exactly**, or there is no call.
- **Call:** for the hour the oracle stores next, the output of the best verified network is
  recorded once (`btc_calls`). That happens only while that hour is not yet on the network. A call
  is never overwritten.
- **Judging:** against the hour's target bit (`btc_hours.up`). "Unsure" (2) counted as an error,
  as in the scorer. Rates for 6 h, 24 h, today, per day and per epoch, each beside "always ▲" and
  "as hour before".
- **Endpoints:** `/v1/btc` (hours with call and ✓/✗, current call, rates), `/v1/btc/summary`
  (small, for e-ink and widgets).

Measured on 2026-11-18: 6,707 solutions in the first 2.5 h, the leading lineage 111 deep, best
error 3,489. That is below the advance limit of 3,504, so the networks move into the live hours.

Limit: the rank from the logs only considers the error. The shift is only known after the
rebuild. The network for the call is the best verified one by (shift, error).

> **Superseded since v0.17.0 (2026-11-24):** on a hint from Come-from-Beyond the page chooses the
> network by its measured prediction, no longer by its score. The score call keeps being stored in
> parallel, and "?" is now an abstention instead of an error. The conversation, the reasons and
> the rule are in `docs/CONCEPT_BTC_RANKING.md`.

### Stage 3 – archive and evaluation

Hit rate per epoch with confidence interval, "best network vs. swarm vs. coin toss vs. 'as last
hour'". This naive benchmark belongs with it, so that a rate above 50 % does not promise more than
it delivers.

---

## 6. Rules for the page

- No call without a rebuilt and verified network. No estimate, no placeholder value.
- Calls are stored **before** the hour and never changed afterwards. The page shows when each was
  stored.
- A "?" (trit 2) is shown as a category of its own, not as a hit.
- A clear note: "A research experiment of the Qubic network, not investment advice".

## 7. Decisions (made on 2026-11-18)

Result: (1) direction only, (2) both, i.e. rebuild and ask Qubic, (3) linked right away.
The original questions:

1. **Show the BTC price or only ▲/▼?** The prices come from Binance but arrive through Qubic's
   on-chain consensus. Our rule so far: do not republish exchange data (memory "Price source
   licensing"). My proposal: show only the direction bits and the hit rate, the price itself only
   after checking Binance's terms.
2. **Build stage 2 ourselves or ask Qubic first?** My proposal: both. Do the offline proof and
   write the request.
3. **Page linked or unlinked at first?**

## Sources

- `qubic/core` @ `2fee231`: `doc/ant_colony_mining.md` §2.3a, §2.7d–e; `src/mining/price_feed.h`;
  `src/mining/score_bpp9000.h:2974–3021` (output, shift); `src/mining/rating.h`;
  `src/logging/logging.h:195` (`AntSolutionLogMessage`); `src/qubic.cpp:3568` (log content);
  `src/public_settings.h:123–134` (constants); `test/score_bpp9000_reference.h`
- Live query of messages 78/80 on peer port 21841, see §3
- https://qubic.org/blog-detail/qubic-bpp-9000-mining-algorithm-explained (as of October, still
  describes the file variant)
- https://qubic.org/blog-detail/qubic-all-hands-recap-september-2026-neuraxon-arc-agi-3
  (announcement "live Bitcoin prediction prototype … through the new oracle")
