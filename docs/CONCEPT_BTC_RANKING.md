# Concept: choosing the network for the call by how well it predicts (BTC page)

As of 2026-11-24 · implemented in **v0.17.0**, live from epoch 241 (change on 2026-11-25; epoch 240 was the test epoch)
Builds on `docs/CONCEPT_BTC_PREDICTION.de.md` (rebuilding the networks, stage 2).

**Deutsche Fassung:** [CONCEPT_BTC_RANKING.de.md](CONCEPT_BTC_RANKING.de.md). Both versions say
the same; a change to one is made to the other.

This document records **why** the BTC page chooses its network the way it does. It rests on the
exchange with Come-from-Beyond (CFB) on 2026-11-24. Anyone changing something later finds here
what CFB said, what we made of it, and which values are still open.

---

## 1. The exchange with CFB (2026-11-24, Discord)

### CFB's hint (19:56)

> "Hi. I heard you use top 1 model for predictions. You should use another rating to get which
> model is top 1. Instead of best score -- use model which predicts the most accurate. Score
> based on historical data is a kludge, we use it only until we see which model predicts the
> best."

**What he means:** The score is a network's error count over the year-long frame (8,760 hours
plus the shift). It measures how well a network reproduces a **known** sequence. That is curve
fitting. With 1,024 neurons and thousands of mutations per hour, the network that fits best wins,
not the one that predicts best. Qubic uses the score for mining only until it can be seen which
networks really get it right.

**What we did up to v0.16.14:** `Tracker._step` in `qdr/colony.py` took
`best_ant_solutions(epoch, 1)`, the network with the lowest logged error. It rebuilt that
network's lineage, and this one network made the call. That is exactly the "top 1 by score" CFB
means.

### Our reply with the proposal (20:27)

> Thanks for the hint, that makes sense. Here is how we would do it:
>
> - The networks have no inputs, so each network's output for every hour is fixed the moment it
>   is created (anchor tick).
> - For every network we rebuild, we count hits only on hours the oracle stored after that
>   network's anchor tick. These are genuine forecasts, not curve fitting.
> - We rebuild the lineages of the top trees. Every node along them is a candidate.
> - The network with the best measured hit rate makes the call. It only counts after a minimum
>   number of hours, and we rank by the conservative lower bound of the rate, so a network cannot
>   win by luck. Until enough hours are measured, we fall back to the score and say so on the
>   page.
> - Every call is stored before its hour and never changed. Each call records which rule picked
>   it.
>
> Three questions:
> 1. What window do you mean by "most accurate": all live hours of the epoch, or a rolling
>    window, e.g. the last 24 or 72 hours?
> 2. Should a "?" (trit 2) count as a miss, as the scorer does, or as an abstention?
> 3. Is the core going to rank by prediction accuracy itself, or publish the best networks'
>    outputs? Then we would take the data from there and keep our rebuild only as a cross-check.

### CFB's answers (20:30)

> 1. I don't know, we cannot use whole epoch because models are retrained, pick some number from
>    practice
> 2. Abstention
> 3. Core will do that because need it for correct list of future computors

### What follows from it

| Question | CFB's answer | Our decision |
|---|---|---|
| 1. Window | Not the whole epoch, because the models are retrained. The number should come from practice. | **Rolling window of 48 live hours** as a starting value, configurable (`QDR_COLONY_WINDOW_H`). To be checked against the data after the first epochs (section 6). |
| 2. "?" (trit 2) | Abstention | "?" counts as neither hit nor miss, **in the ranking and in every rate on the page**. Up to v0.16.14 it counted as a miss. |
| 3. Ranking in the core | The core will do it, because it needs it for the correct list of future computors. | Our ranking is an interim solution and later a **cross-check**. As soon as the core publishes it (new message types ≥ 82, see the skill `pruefe-btc-vorhersage`), we take the data from there. |

Notable about answer 3: prediction quality is to help decide who becomes a computor. So the
ranking is not only a question for our page but for the protocol.

---

## 2. The insight that makes it feasible

The networks have **no inputs**. `calls()` in `tools/replayer/replay_bpp9000.cpp` runs the
network from its start state and never reads the targets. A network's output for every hour is
therefore fixed **as soon as the network exists**.

It follows that for every rebuilt network we can work out **after the fact and still honestly**
how it would have called every hour it did not know when it was made. We do not have to wait
days to have a record. And whoever rebuilds a lineage gets every ancestor with it. Each node is a
network of its own, verified, and so a candidate.

```
 hour h:           h1    h2    h3    h4    h5    h6    h7
 stored in:       1000  1010  1020  1030  1040  1050  1060   (tick)
 network born in tick 1015
   hour h counts if h-2 was stored AFTER the birth:
   h-2 = h3 (1020) → h5 counts;  h6, h7 … count too;  h1-h4 do not
```

**The rule in detail** (`net_record`): An hour *h* counts for a network only if hour *h−2* was
stored in a tick **after** the network's tick. The oracle stores an hour at the earliest 300 s
after it ends. So *h−2* is stored a few minutes after *h−1* begins, and the network certainly
existed before *h* began. This boundary is stricter than "after the anchor tick" from the message
to CFB. It also stops a miner who already knows the Binance price from submitting a fitting
network just before an hour is stored. The price for it is one hour of record per network.

## 3. The rule

1. **Candidates:** every rebuilt and verified network of the running epoch, i.e. every node of
   the rebuilt lineages.
2. **Lineages:** the best lineage by score (as before) and the best lineages of the next
   identities, each identity's best tree. Together that is `QDR_COLONY_LINES` = 4.
3. **Record:** hits and misses on the hours from section 2, only within the last 48 live hours.
   "?" or a missing output is an abstention.
4. **Rank:** the lower bound of the 95 % Wilson interval of the hit rate. That way 5 of 5 (lower
   bound 57 %) does not automatically beat 60 of 100 (50 %), and small samples are penalised.
   On a tie the network with more hours wins, then the higher shift, then fewer errors, then the
   newer one.
5. **Minimum sample:** a network needs 12 decided hours (hits + misses) to be chosen
   (`QDR_COLONY_MIN_DECIDED`).
6. **Fallback:** as long as no network has 12 hours, roughly the first 14 hours of each epoch,
   the best-score network calls. The call is stored as `fallback` and the page says so.

### Both rules run side by side

Every hour **two** calls are stored. Both are fixed before their hour and never changed.

| Rule | Table | Network |
|---|---|---|
| `score` | `btc_calls` (unchanged since v0.16.0) | the fully rebuilt network with the best score |
| `accuracy` | `btc_calls_accuracy` (new) | the network with the best record, else the score network (`fallback` = 1); plus `net_hits`, `net_decided` and `net_abstained` at the time of the choice |

The page shows the call of the `accuracy` rule. For hours before v0.17.0 there are only calls by
score. Below the hit rate, both rules stand side by side on **the same** hours.

**Why side by side:** If something goes wrong with the new rule, the old one keeps running
unchanged and the data has no gap. And we can show whether CFB's rule really calls better. That
is also the answer we can give CFB later.

**Against fooling ourselves:** Whoever picks the network with the best record out of many often
catches the luckiest one (winner's curse). So what is judged is not the best record in hindsight
but the **selection rule**: every call of the rule is fixed before its hour.

## 4. Implementation (v0.17.0)

| Part | What |
|---|---|
| `qdr/colony.py` | `hour_points`, `net_record`, `wilson_low`, `select_network`. `Tracker._advance` rebuilds a lineage incrementally and stores the outputs of every verified network, `_side_lines` the other lineages in turn, `_rank` builds the ranking, `_call` writes both calls. A network on a side line that misses its error is set aside with its descendants. The score line does not stop for it. |
| `qdr/store.py` | Tables `colony_outputs` (168 trits per network) and `btc_calls_accuracy`. Plus `best_ant_solutions_per_identity`. |
| `judge`/`rate` | "?" is an abstention: `hit` = null, `abstained` = true. `rate.calls` counts only decided calls. |
| `/v1/btc` | `call.rule`, `call.fallback`, `call.network.record`; `call.rules` (both rules); `call.selection` (rule, window, ranking of the best 10, lineages); `stats.rules` (both rates on the same hours). |
| `dashboard/btc.html` | The sentence under the call names the network's record or the fallback. The ranking sits under "How the calls are made", the rule comparison under the hit rate. "?" is an abstention everywhere. |
| Tests | `tests/test_colony.py`: the "could not have known" boundary, the window, abstention, Wilson, selection and fallback, a second lineage, a side line with a wrong error, the API with both rules. |

## 5. Compute budget

One walk takes about 11 s on one core. The score line gets up to 25 walks per step, as before;
the other lineages share 10 (`QDR_COLONY_SIDE_WALKS`), with a different lineage first each time.
A new lineage is 100 to 150 nodes deep, so catching up takes a few hours. During that time its
nodes so far are already candidates. A network's outputs cost a single rollout (milliseconds).
In all, that is up to about half an hour of one core per hour.

## 6. Open and to watch

- **Window (48 h) and minimum sample (12):** both are starting values "from practice", as CFB
  wanted. Check after one or two epochs against `stats.rules` and the ranking: if the chosen
  network changes too often, the window is too short; if old networks that are long overtaken
  win, it is too long.
- **Number of lineages (4):** more lineages mean more candidates, but also more compute. Check
  that the side lines keep up (`call.selection.lines`, state `waiting`).
- **Core ranking:** as soon as the core publishes prediction quality itself (CFB, answer 3), take
  its data and show our ranking as a cross-check. It is coming with v1.314.0, see section 9.
- **Report back to CFB:** after the first full epoch, send the numbers "by record vs. by score".

---

## 7. First measurement: live test before the deploy (2026-11-24, epoch 240)

**Setup:** image v0.17.0 built locally, the tracker in the container with the real replayer
against the live network. The database was a throwaway file, the script lived in a scratch
folder. To keep the test short, the scan read only the first 6,000 ticks of the epoch, with 6 + 6
walks allowed per step. The lineages were therefore short, but every network was real and
verified.

**Result:**

| Step | Time | What happened |
|---|---|---|
| 0 | 278 s | 6 walks on the score line (depth 6 of 145), 6 walks on side line 2. Lines 3 and 4 `waiting`. Ranking built, **accuracy call by record** written (no fallback). |
| 1 | 272 s | Score line at depth 12. This time side line 3 went first, as intended. A new network at the top. The call for the same hour was **not** overwritten (`new: false`). |
| 2 | 20 s | `no-task`: a node did not answer the task query. Known, transient behaviour; the next step tries again. |

- 24 networks from 3 identities stored, each with exactly 168 trits.
- There was no score call, because the score line was not fully rebuilt yet. That is intended:
  the score call comes only from the complete leaf.
- The top of the ranking after step 1:

| Network (tick) | Depth | Errors (score) | Right | Rate | At least (Wilson) |
|---|---|---|---|---|---|
| 91,912,523 | 8 | 4146 | 34 / 46 (2 ?) | 73.9 % | 59.7 % |
| 91,912,518 | 7 | 4157 | 33 / 47 (1 ?) | 70.2 % | 56.0 % |
| 91,912,513 | 6 | 4170 | 31 / 47 (1 ?) | 66.0 % | 51.7 % |
| 91,911,185 | 1 | 4265 | 29 / 46 (2 ?) | 63.0 % | 48.6 % |

**What it shows:**

- Early networks with a **poor** score (over 4,100 errors; the lineage's leaf has 3,431) call the
  hours they did not know clearly better than 50 %. Score and prediction are not the same thing.
  That supports CFB's hint.
- **Caution:** with many candidates the best one almost always lies above 50 %, by luck as well
  (winner's curse). Whether the rule is really worth anything only shows in the comparison of
  both rules' calls fixed in advance (`stats.rules`), not in this ranking.
- A step with 12 walks took about 4.5 minutes, i.e. a little over 11 s per walk, as measured.

## 8. Operation: how it runs and how to tell

**One tracker step** (`scripts/ingest.py`, thread `colony-tracker`, about every minute or as
soon as the previous step is done):

1. Read the task and price series from the node, check `dataHash`.
2. Read new solutions from the Bob logs (`scan`).
3. Score line: up to 25 walks. Every verified network gets its 168 trits in `colony_outputs`.
4. Ranking from every stored network of the epoch (`_rank`).
5. Write both calls for the coming hour, provided it has not begun and the oracle does not know
   it yet.
6. Side lines: up to 10 walks together, a different one first each time. Their new networks join
   the ranking on the next step (since v0.17.2; before, the side lines came before the call).

**On deploy (restart):** the replayer starts afresh and loads, per lineage, the network saved last
(`colony_nets`). The score call therefore continues without a gap. Networks from before v0.17.0
have no trits yet. For the loaded network they are computed at once, for older ancestors not. The
side lines are rebuilt from the start, but their early networks already have a record.

**On the epoch change:** new spectrum digest → new pool (512 MB, as a file under `DATA_DIR/raw`),
replayer restart, old networks and trits are deleted (one epoch is kept). In the first roughly 14
hours no network has 12 judged hours. The accuracy rule then falls back to the score
(`fallback` = 1), and the page says so.

**How to tell it is running** (`GET /v1/btc`):

| Field | Healthy | If not |
|---|---|---|
| `call.tracker` | `tracking` or `catching-up` | reason in `call.reason` / `call.detail` |
| `call.rule` | `accuracy` | `score`: an hour before v0.17.0 or no call by the accuracy rule |
| `call.fallback` | `false`, once a network has 12 hours | `true` at the start of an epoch is normal |
| `call.selection.chosen` | a network with `decided` ≥ 12 | `null` = fallback to the score |
| `call.selection.lines[].state` | `tracking` / `catching-up` | `waiting` for good = too few walks; `mismatch` = lineage set aside; `lineage-incomplete` = ancestors missing from the logs |
| `stats.rules` | both rates on the same hours | `null` = no accuracy call yet |

**Settings** (environment variables; changed only through a new image, because we have no server
access): `QDR_COLONY_LINES`, `QDR_COLONY_SIDE_WALKS`, `QDR_COLONY_WINDOW_H`,
`QDR_COLONY_MIN_DECIDED`, see the README.

**Incident on the first deploy (v0.17.0, 2026-11-24):** on report.qubic.tools the new tracker
completed no step for over 30 minutes. There was no ranking and no call for the next hour. The
cause was the query "best tip per identity" (`best_ant_solutions_per_identity`): as a window
function it sorted every accepted solution of the epoch on every step, and an epoch holds millions
of them. Measured at 3 M rows: 54 s on a desktop, against 0.04 s for the old query. The live test
before had read only the first 6,000 ticks and could not show it. **Fixed in v0.17.2:** the query
reads the (epoch, score) index in score order and stops at the fourth identity (0.055 s, same
result). In addition the tracker now writes the calls **before** the side lines, so they can never
hold up the call. Side lines ask at most 5 nodes for tick data and read missing ancestors again
only once per line. **Lesson:** run live tests with the data volume of a full epoch, not only its
beginning.

**Way back:** push image v0.16.14 again. The new tables do not disturb the old version. The calls
of both rules stay stored.

---

## 9. Official count in the core from v1.314.0 (as of 2026-11-25)

**Occasion:** on 25 Nov QubicChurch asked on Discord whether we had seen the v1.314.0 update: the
core now keeps correct and counted hours per ID itself and ranks by accuracy from 12 correct, so
there is one official way to count.

**State of the code:** there is no v1.314.0 release yet, but `develop` holds PR #1077 "Bpp9000:
live feed with prediction" (merged 23 Nov) and the parameters for epoch 241 (tick 92260000).
Rules per `doc/ant_colony_mining.md` §2.4a:

- **Qualifying:** an identity with an accepted solution at shift ≥ 1, or at shift 0 with at most
  3504 errors.
- **Switch:** the epoch switches from training to prediction once 226 computors have qualified,
  but not when 24 hours or less are left. One switch per epoch.
- **Answer:** for each hour H the network's output at the position for H (the same 168 hours as
  ours). After the switch every accepted solution of the identity replaces its answers for the
  open hours, also a weaker one. Deadline: 30 minutes before the **end** of H.
- **Counting:** per identity, over the whole prediction phase. No answer (?) is counted but not
  correct. Ranked by accuracy from 12 correct hours. Ties: more counted hours, then shift, error,
  tick.
- **Seats are given from this ranking** (451 computors stay, 225 seats for the best of the
  remaining computors and candidates). From v1.314.0 on, who becomes a computor depends on
  prediction quality; that touches the main subject of this report.
- **Readable:** messages 82/83 (phase) and 84/85 (state of one identity: `hours`, `correct`),
  operator command 21 (whole ranking, not public), event logs `PRED_SWI`, `PRED_ANS` (answer per
  identity and hour, with the answering solution), `PRED_RES` (result per hour).

**Differences to our rule (v0.17):**

| | core v1.314.0 | ours v0.17 |
|---|---|---|
| Unit | identity, counters carry over network changes | single network |
| Period | whole prediction phase | last 48 hours |
| Threshold | 12 correct | 12 judged hours |
| "?" | counts as wrong | abstention (as CFB said on 24 Nov) |
| Deadline | 30 min before the hour ends, i.e. mid-move | before the hour begins |
| Who calls | every qualified identity | one network, chosen by record |

**Open question to the core devs:** the deadline lies in the middle of the hour whose move is
predicted. Since every new accepted solution replaces the open answers, a miner could publish at
:29 a network whose answer matches the first half hour. Is that intended? (Asked in the Discord
reply drafted on 25 Nov.)

**When we switch (recommendation):**

1. **Nothing now.** Until epoch 241 switches there are no official numbers; our tracker keeps
   running. After the epoch change, check that it starts cleanly under v1.314.0.
2. **Once epoch 241 has switched** (check message 82/83 live on a node; then there is real data
   to test against): read the official counters and show them on the page **next to** our
   ranking, as a cross-check. Add the seat question to the report.
   **Prepared in v0.17.3:** `antnode` reads message 82/83 (phase) and 84/85 (state of one
   identity), but only from epoch 241 on, because older nodes do not answer and every silent node
   would cost the full timeout. `/v1/btc` carries `prediction`; the page shows below the status
   line "in training: x of 226 computors qualified" or "in prediction since …", with a tooltip
   (de + en). Before the change (25 Nov, 09:50 UTC) no node answered yet (all v1.313), so the wire
   format is only checked live after 12:00 UTC.
3. **From the start of epoch 242:** the official top identity gives the page's call, our rule
   runs alongside as a cross-check. Switching at an epoch boundary keeps every epoch consistent in
   itself. Our deadline stays: what counts is the answer fixed before the hour began, not one
   handed in up to :30.
4. **Align "?":** count "?" as wrong in our rate like the core, or show both numbers. That goes
   against CFB's word of 24 Nov, which came before the core code. Decide with step 3.
