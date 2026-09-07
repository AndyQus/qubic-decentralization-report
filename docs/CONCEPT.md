# Qubic Decentralization Report — Concept

*Working concept for a tooling project that produces a standing "how decentralized is
Qubic" report from self-reported and on-chain data, and exposes it as an API that
explorers can embed.*

Status: draft v0.2.1 · Owner: (Qubic community project) · Language: English (spec is for
the Qubic community / bounty reviewers; happy to keep a German copy alongside)

---

## 1. Background — what CFB actually asked for

In the `#computor-operator` discussion, Come-from-Beyond (CFB) framed a specific,
buildable deliverable:

> "Prepare a report showing revenue metrics and their dynamics. And clustering with
> number of computors in each cluster."
>
> "Explorers should add this report to their sites. It's a very important report,
> showing how decentralized Qubic is."

Two things sit underneath that request:

**The problem — collusion / Sybil among computors.** Qubic's network is run by 676
Computors. On paper that looks like 676 independent operators. In practice a single
entity (a pool) can control many Computor slots at once and coordinate them. That is a
textbook **Sybil situation**: one entity, many identities. AndreiBLR's "we see smoke and
know there's a fire, but the investigators are the ones who set it" is exactly this — the
signals of concentration are visible, but the parties who could investigate are the same
parties benefiting from it.

**The proposed defense — self-reporting.** CFB's earlier point:

> "Qubic was the first implementing such theoretical anti-Sybil technique as
> self-reporting … Let's make it look more solid by using it to the fullest."

Self-reporting is a recognized-but-rarely-implemented anti-Sybil idea: operators
voluntarily declare which identities (Computor slots) they control. Qubic already has the
raw material for this because pools publish which of their members hold slots. "Use it to
the fullest" = turn that self-declared ownership into a **public, continuously updated
decentralization metric** that lives on the explorers.

CFB's own stance on the stakes (paraphrased from the thread): if Computors collude, the
worst-case is *suboptimal service / performance*, not a broken chain — and if the
community can *see* the concentration clearly, it can decide for itself. So the tool's job
is not to accuse anyone; it is to **make concentration measurable and visible**.

A bounty of ~2B QU (Eko 1B + Broms 1B) has been offered for a report that gets accepted
and embedded by explorers.

---

## 2. What the tool produces

A single logical artifact — the **Decentralization Report** — available in three forms:

1. **A machine-readable API** (`/report/latest`, `/report/{epoch}`, plus sub-resources).
   This is the primary deliverable: explorers (qubic.org explorer, qubic.li, jetski's ANN
   explorer, etc.) call it and render it however they like. *We provide the data; the
   explorer is the consumer.*
2. **A reference dashboard** — our own front-end that renders the same data, including the
   **animated views** (see §6). Doubles as the "reference implementation" so an explorer
   can copy the presentation if they want.
3. **A periodic static snapshot** (JSON + rendered PNG/SVG) per epoch, so the report is
   archivable and citable even if the live service is down.

Every number in the report is **reproducible from public data** and ships with its inputs,
so nobody has to trust us — an explorer or a skeptic can re-run it.

---

## 3. Data sources

| Layer | Source | What we get | Role |
|---|---|---|---|
| Consensus / slots | Qubic RPC 2.0 (`rpc.qubic.org`) + core node data | Computor list per epoch (676 IDs), tick data, quorum info | Ground truth |
| Revenue | RPC / archiver, full transaction tracking (§4.3) | Per-Computor revenue per epoch, epoch-scoped and paginated to exhaustion | Ground truth |
| **On-chain linkage** | Qubic ledger via RPC | Payout destinations, fund flows between identities, the payout graph | **Primary attribution** |
| Self-reporting | Pool APIs & public declarations (qubic.li and others), plus a curated registry in this repo | Which slots a pool/operator claims | Labels + supplements linkage |
| Behavioral (optional) | Solution-submission timing/patterns where observable | Signals that flag possible undeclared clusters | Flagging only |

Exact endpoint names are verified against the live RPC during implementation; see
`docs/DATA_SOURCES.md` (to be filled in during the data-mapping step).

---

## 4. The analytical engines

### 4.1 Revenue metrics & dynamics

Per epoch, per Computor: revenue earned; aggregated to per-cluster totals. Concentration
measured with standard, defensible indices so the result is not a matter of opinion:

- **Gini coefficient** of revenue across operators (0 = perfectly even, 1 = one entity
  takes all).
- **Herfindahl–Hirschman Index (HHI)** of operator revenue share.
- **Top-N share** (e.g. share held by the largest 1 / 3 / 5 operators).
- **Nakamoto coefficient** — how many operators must collude to control >⅓ / >½ of slots
  or revenue (the headline "how decentralized" number).

"Dynamics" = all of the above as a **time series over epochs**, so trends (is
concentration rising or falling?) are visible.

### 4.2 Clustering — slots → operators

This is the core. **On-chain linkage is the default attribution layer, not a fallback.**
Self-reporting is what operators *claim*; on-chain payout linkage is what the ledger
*shows*. Both run on every epoch, independently, and the report publishes both plus their
delta. A slot is only "unattributed" when the ledger itself shows no link — never merely
because nobody filed a self-report.

The three layers, and what each is allowed to conclude:

1. **On-chain linkage (default, always computed).** Derived from the full payout graph:
   every epoch-boundary distribution to a computor identity is tracked to where the funds
   go next. Slots whose payouts converge on a shared destination, or that are funded from a
   common source, are one economic owner. This runs with no registry at all and is what
   produces the baseline clustering.
2. **Self-reported (declaration layer, per CFB).** Pools declare their slots into a
   versioned registry. This *labels* clusters (a linkage cluster becomes "Qubic.li Pool"
   instead of "Linked (ABCD…)") and can merge slots the ledger has not yet linked. It never
   *splits* what the chain has linked — a declaration cannot un-prove a payout path.
3. **Behavioral fingerprints (flagging only).** Correlated submission timing / source
   patterns can *flag* slots that behave as one — surfaced as "possible undeclared
   cluster," never as a hard accusation.

**Confidence levels** now reflect that ordering:

| Level | Meaning |
|---|---|
| `declared+linked` | Self-reported **and** confirmed by the payout graph — strongest |
| `linked` | Proven on-chain, not (yet) declared — the undeclared concentration |
| `declared` | Declared, no on-chain confirmation yet — trust-only |
| `flagged` | Behavioral correlation only, no proof |
| `unattributed` | The ledger shows no link and nobody declared it — a genuine unknown |

**Why this ordering matters for the headline numbers.** Treating every undeclared slot as
its own operator does not produce a neutral result — it produces a *systematically
optimistic* one, because an unlinked singleton inflates the operator count and pushes
Nakamoto, Gini and HHI toward "more decentralized than reality." The previous design made
that the default and the on-chain layer an optional enrichment; that is backwards, and the
report is stated with a **linkage-coverage figure** so a reader can see how much of the
676 is actually resolved rather than assumed.

Output per cluster: number of computors, revenue, share of Top-451, confidence, the
evidence used, and a **declared-vs-detected delta** that quantifies the "smoke."

### 4.3 Revenue derivation — full transaction tracking

Concentration numbers are only as good as the revenue figures underneath them, and this is
where independent reimplementations diverge. The derivation is therefore specified exactly
and made auditable rather than left to a best-effort scan:

- **Epoch-scoped.** Payouts are attributed to the epoch they settle for, using the epoch's
  tick range (`initialTick` → next epoch's `initialTick`), not to whenever a transfer
  happens to appear. Qubic pays with a one-epoch lag, so an unscoped scan mixes two epochs.
- **Complete.** The transfer history is read with **full pagination** to exhaustion. A
  truncated first page silently under-counts the largest operators, which biases
  concentration *downward*.
- **Anchored on a verified arbitrator identity.** The payout source is confirmed against
  boundary-tick transfers and pinned in the repo with the tick evidence, not carried as an
  unverified constant.
- **Cross-checkable.** Each epoch's derived revenue ships with the totals and tick range it
  was derived from, so a third party can recompute it and see exactly where a divergence
  comes from — which epoch, which tick window, which transfers.
- **Reconciled.** Derived totals are checked against the known distribution model
  (per-computor cap after the operator fee, performance/slashing factors). A mismatch is
  reported in the payload as a warning instead of being averaged away.

Where an epoch cannot be derived completely, it is marked `partial` and excluded from the
headline metrics rather than published as if it were solid.

**Which source, in practice.** The public RPC does not expose these payouts at all
(DATA_SOURCES §6.2), so the pipeline reads them from a **Bob node**, which keeps the full
event log including the virtual end-epoch tick where the protocol credits every computor:

1. **Bob's end-epoch log (primary).** One call per epoch returns every settlement transfer.
   Verified on epoch 228: 676 of 676 computors paid, 178 477 462 349 QU, with history back
   to at least epoch 220 — so the report starts with real history rather than from zero.
2. **Balance deltas (fallback).** Where no Bob node is reachable, revenue is the change in
   each computor's cumulative `incomingAmount` across the epoch boundary, from snapshots we
   take ourselves. Self-contained, but only forward from the first boundary observed.

Point `QDR_BOB_URL` at your own node rather than depending on a public one.

### 4.3.1 Why the payout source proves nothing about ownership

All 676 payouts come from the **same null address** — the credit is protocol emission, not a
wallet. So "shares a payout source" is true of the whole network and must produce *no*
linkage; and computors' own outgoing transfers are uniformly 1 000 000 QU burns to that same
address. Both facts are traps for a naive payout-graph implementation (one cost us a bug that
reported 100 % on-chain coverage while proving nothing). Linkage therefore ignores null/burn
destinations, needs at least two computors sharing a destination, and discards destinations
used by more than half the network.

**Current finding: no on-chain linkage between computors is provable from the public ledger
today.** The report states that as `linkage_coverage: 0` instead of implying independence,
and the dashboard warns that the Nakamoto figure is an *upper bound* on decentralization.
Self-reports are what would sharpen it — which is exactly CFB's point about using
self-reporting "to the fullest".

### 4.4 Slot transitions — surviving operator exits and re-identification

Every layer above describes a **single epoch**. That is not enough for the question the
community actually asks when a pool shuts down: *where did its slots go?* An operator that
leaves does not take its 676-slot share with it — the slots are re-issued, and the
concentration effect of that hand-over is invisible to any per-epoch snapshot, because each
epoch on its own looks internally consistent.

This is the failure mode a per-epoch report has by construction. Concretely, when a large
pool closes there are three outcomes and they are **numerically indistinguishable** in a
snapshot:

1. Slots go to genuinely new, independent operators → real decentralization *gain*.
2. Slots are absorbed by the remaining large operators → concentration *rises sharply*.
3. The same operator returns under fresh identities and no new declaration → concentration
   is **unchanged in reality but appears to fall**, because the successor slots enter the
   report as unlinked singletons.

Case 3 is the dangerous one: a departing operator re-entering under new IDs makes the
headline numbers *improve* at the exact moment the network has learned nothing. A report
that cannot separate case 1 from case 3 will systematically flatter the network after every
pool exit, which is precisely when readers rely on it most.

**The epoch-over-epoch diff is therefore a first-class output, not a derived view.** For
each epoch boundary the report computes the identity delta against the previous epoch and
classifies it:

| Class | Meaning |
|---|---|
| `retained` | Identity present in both epochs |
| `departed` | Present in epoch *n-1*, absent in *n* |
| `entered` | Absent in epoch *n-1*, present in *n* |
| `succeeded` | An `entered` slot linked by evidence to a `departed` operator |

**Successor detection.** An `entered` identity is tested against recently `departed`
operators using the same ledger evidence §4.2 already relies on, so this adds a dimension
rather than a new trust assumption:

- **Shared payout destination** — the new identity's distributions converge on an address
  the departed cluster also paid into. This is the strongest signal and reuses the payout
  graph unchanged.
- **Funding lineage** — the new identity's slot was funded from, or its early outflows
  return to, the departed cluster's known addresses.
- **Continuity in timing** — the entered slot resumes the departed cluster's submission
  pattern at the boundary tick, without the ramp-up an unrelated new operator shows.

A match promotes the cluster to `succeeded` and the **operator identity is carried across
the boundary**, so an operator's history is continuous even when every one of its computor
IDs changed. Without this, a rename silently resets an operator's entire time series to
zero, and the roll-up rewards it for doing so.

**Confidence is inherited conservatively.** A `succeeded` link is evidence of continuity,
never a declaration: it is reported at `linked` confidence at best, and drops to `flagged`
when only the timing signal supports it. Cluster continuity claimed on timing alone is
always surfaced as a hypothesis with its evidence attached, so a reader can disagree with a
specific link rather than with the whole number.

**Churn is published as a metric in its own right.** Per epoch boundary the report states
how many slots changed hands, what share of Top-451 revenue moved, how much of the movement
resolved to `succeeded` versus genuinely `entered`, and — the honest caveat — **how much of
the delta remains unexplained**. A high unexplained churn is itself the finding: it means
the report's confidence in that epoch's headline numbers should be lower, and it is stated
that way rather than smoothed over.

**Announced exits are recorded before they happen.** The self-reporting registry gains an
optional per-operator `status` (`active` / `winding_down` / `closed`, with an `effective`
epoch), so a known shutdown is on record *ahead* of the boundary and the diff for that epoch
can be interpreted rather than reconstructed after the fact. This turns a pool closure from
a retrospective puzzle into a pre-registered event with an expected slot count to account
for.

---


## 5. API shape (draft)

```
GET /report/latest                 → summary: epoch, #clusters, Nakamoto coeff, Gini, HHI, top clusters
GET /report/{epoch}                → same, historical
GET /clusters/{epoch}              → full cluster list: id, label, computor_count, revenue, share, confidence
GET /computors/{epoch}            → per-slot: id, cluster_id, revenue, evidence
GET /metrics/timeseries           → concentration indices across epochs (feeds the charts)
GET /transitions/{epoch}          → slot diff vs. epoch-1: retained / departed / entered / succeeded,
                                    churn share of Top-451 revenue, unexplained-delta figure (§4.4)
GET /operators/{id}/history       → one operator across epochs, continuous through ID changes (§4.4)
GET /report/{epoch}/snapshot.json → frozen, archivable snapshot
```

Design principles: read-only, cacheable, CORS-open (explorers embed it client-side),
versioned (`/v1/`), and every response carries `generated_at`, `data_sources`, and a
`reproducible: true` block naming the inputs.

---

## 5.1 Persistence — a store, not a rebuild

The report is a **time series**, so history is the product, not a by-product. Rebuilding
every answer from the RPC on each request (the previous design) has three defects: it makes
the API slow and dependent on upstream uptime, it loses any epoch the RPC no longer serves,
and it means two runs can silently disagree with no record of which was which. Overwriting
`api/sample/*.json` on each build discarded the past outright.

**Storage model.** This follows the pattern already proven in the sibling Qubic projects
(`qubic_doge_stats`, `qubic_spotlight`): a single embedded, file-backed database in a
mounted volume, located via the same `DATA_DIR` environment variable this repo's RPC cache
already honours — no database server to operate, and the file is trivially copyable and
archivable. Those projects use LiteDB because they are .NET; the direct Python equivalent
is **SQLite** (standard library, no new dependency, same single-file-in-a-volume
semantics). We adopt the architecture, not the library.

Concretely, the parts carried over from `qubic_doge_stats`:

- an `UpdateLive()` / `FinalizeEpoch()` split — the running epoch is refreshed on every
  poll, a closed epoch is finalized once (this is exactly the sealed-vs-live rule below);
- **upsert with dedupe** rather than blind insert, so a re-poll never duplicates rows;
- **"once correctly set, immutable"** — a finalized epoch's values are not overwritten by a
  later poll;
- a **backfill service** for filling history and for re-deriving under a new code version;
- polling **workers** on independent intervals, so a slow revenue pull never blocks the
  cheap tick/epoch pointer.

Tables:

| Table | Holds |
|---|---|
| `epochs` | one row per epoch: tick range, status (`sealed` / `partial` / `live`), when it was first and last computed |
| `computor_revenue` | per epoch, per identity: derived revenue + the tick window it came from |
| `transfers` | the tracked payout transfers backing that revenue — the audit trail that makes a divergence explainable |
| `clusters` | per epoch: cluster membership, confidence, evidence |
| `reports` | the finished report JSON per epoch, with `code_version` |
| `linkage` | the payout-graph edges, so linkage is incrementally extendable rather than recomputed from zero |

**Sealed vs. live — the core rule.** A **closed** epoch is immutable: computed once,
written, and served from the store forever after. Its inputs cannot change, so recomputing
it only risks drift. The **current** epoch is explicitly *not* sealed — it is recomputed on
a short interval (and on demand) and served with `status: "live"` plus the timestamp of the
last refresh, so consumers always see the running epoch's latest state and can tell it
apart from settled history.

An epoch is sealed only when its successor has started **and** its revenue derivation is
complete (full pagination, reconciliation passed). An epoch that closes with gaps stays
`partial` and is retried, rather than being frozen wrong.

**Recompute and versioning.** When the pipeline's logic changes (a corrected arbitrator, a
better linkage rule), sealed epochs are *not* silently rewritten: a backfill runs under a
new `code_version`, and the store keeps the prior computation. That way a number that
changes has a visible reason — which matters directly for the "our numbers don't match"
problem.

**Ingest is incremental.** Raw pulls stay cached (`data/raw/`) as today, but the derived
layer is now written once and read many times. A restart, an RPC outage, or an explorer
hammering the API all read the store; only the live epoch touches the network.

**API impact.** `/v1/report/{epoch}` becomes a store lookup and can serve *any* historical
epoch, not just whatever snapshot was last built. Responses carry `status` (`sealed` /
`partial` / `live`), `computed_at`, and `code_version`. The static `api/sample/*.json`
snapshots stay only as a cold-start fallback for a fresh install with an empty database.

---

## 5.1.1 Two clocks: what is live, and what is not

Measured against the live network: the tick advances **~2.7/s**, while the computor list,
revenue and clustering change **once per epoch** — roughly every 4.4 days (epoch length
itself varies, 1.08M-2.29M ticks). Polling the analysis every minute would burn RPC budget to
produce an identical answer for four days.

So the service runs two cadences:

- **`/v1/pulse`** — tick, epoch progress, tick quality, active addresses. Cheap, cached ~10 s,
  designed to be polled every 15 s. This is what the dashboard animates.
- **The report** — recomputed only when the epoch turns (the dashboard watches the pulse's
  epoch number rather than polling the report on a timer).

The running epoch also has *no revenue yet* — it is credited when the epoch closes — so the
headline report is the newest **settled** epoch, with the running one shown separately as the
live panel. Presenting the running epoch as the report would show an empty one while a
complete one sat right behind it.

---

## 5.2 Built for a moving network

The network does not hold still: epochs arrive continuously, pools appear, merge and
disappear, the analysis layer will gain new evidence types, and even "676 computors" is a
current constant rather than a law. Anything pinned to today's shape becomes a lie later,
silently. The rules that keep this honest:

- **No network constant is hardcoded.** Slot counts, epoch numbers and operator counts are
  read from the data on every render. In particular the **UI language files carry no
  numbers**: strings use placeholders (`{slots}`, `{epoch}`, `{operators}`) that are filled
  from the live payload, so a translation written today still tells the truth when the
  network changes. A language file must never be tied to one epoch's values.
- **New operators need no code change.** A pool added to the registry, or a cluster the
  payout graph newly reveals, simply appears in the next epoch's report — clustering is
  data-driven, never an enumerated list.
- **Unknown values degrade honestly.** A confidence level this dashboard version has never
  seen is rendered neutrally *under its own name*, never silently relabelled as an existing
  level, and it is not counted as on-chain-linked. Being wrong in the direction of "we
  cannot confirm this" is the safe direction.
- **Old readers, new data.** Report payloads are additive: consumers ignore fields they do
  not know, so an explorer running an older embed keeps working when the report grows.
- **History is bounded in transit, not on disk.** The store keeps every epoch forever, but
  the default API views return a recent window (52 epochs for the timeseries, 26 for the
  dashboard bundle) so responses stay a constant size as history accumulates. The full range
  stays available on request (`?epochs=`).
- **Forward compatibility is tested, not assumed.** `tests/test_forward_compat.py` runs the
  analysis at 100 / 676 / 1 000 / 2 048 slots, across a growing epoch range, with pools added
  mid-history and an unknown confidence level injected.

---

## 6. Dashboard & animation

The reference dashboard renders the report and — where there is genuinely something to
animate — animates it. Animation is used only where motion carries meaning, not decoration:

- **Cluster evolution over epochs** — an animated treemap / bubble chart where each bubble
  is an operator, size = slots or revenue; play the epochs and watch clusters grow, shrink,
  merge, or split. This makes "one pool creeping toward the Top-451 threshold" visible at a
  glance.
- **Nakamoto coefficient timeline** — an animated line that draws across epochs, with the
  danger thresholds (⅓, ½) marked.
- **Fund-flow / linkage graph** — a force-directed graph of slots and payout links that
  settles into clusters, so on-chain linkage is literally watchable.
- **Declared vs. detected** — a transition that morphs the "official" self-reported
  clustering into the detected one, so the gap ("the smoke") is the animation itself.

Static fallbacks are always available (each animated view has a still image), because the
explorers may embed the static form.

### 6.1 Dashboard UX requirements

- **Layout / visual language:** take cues from existing Qubic community front-ends
  (e.g. *Qubic Dividends*) so the report feels native to the ecosystem — same general
  card/table/dark aesthetic, so an explorer can drop it in without a jarring restyle.
- **Dark / light mode:** a toggle in the header. Dark is the default (matches the
  ecosystem). Fully theme-aware — both modes are first-class, not an afterthought.
- **Language switch DE / EN:** all UI strings run through an i18n layer with German and
  English. English is the default; German is a full first-class translation. Structured so
  more languages can be added later (simple key → string dictionaries).
- **Persistence:** both the theme choice and the language choice are saved to
  `localStorage` and restored on next visit. Reads/writes are wrapped in `try/catch` so the
  page still renders correctly if storage is unavailable (private mode, blocked cookies),
  falling back to defaults (EN + dark).

---

## 7. Why this satisfies the ask

- Delivers **exactly** the two things CFB named: revenue metrics + dynamics, and clustering
  with computor counts per cluster.
- Built on **self-reporting as the primary layer**, which is the anti-Sybil technique CFB
  wants used "to the fullest," while adding on-chain/behavioral layers to quantify the gap.
- Ships as an **API for explorers to embed**, which is his stated distribution requirement.
- Neutral and reproducible — it measures concentration, it does not accuse; anyone can
  re-run it, which answers the "no qualified/neutral investigator" problem.

---

## 7.1 Community feedback and what changed (v0.2)

Four pieces of feedback on the v0.1 draft, and how the concept answers each. The first
three turned out to point at the same weakness: a solid metrics layer sitting on an unproven
data layer. The fourth points at a different one: a report that only ever looks at one epoch
at a time.

**1. "Mine doesn't converge with the numbers you have — I've had full transaction tracking
running for about 6 epochs."** (Kevarms)

Correct — and investigating it turned up something bigger than a bug in our pagination.

v0.1 derived revenue with a single unpaginated pass over the arbitrator's transfers, with no
epoch/tick scoping and an unverified arbitrator identity. §4.3 specifies the fix (epoch tick
windows, exhaustive pagination, reconciliation). But when that was run against the live RPC
on 2026-09-07, it returned **zero**, and the reason is fundamental (full evidence in
`docs/DATA_SOURCES.md` §6):

- the arbitrator identity we carried paid **0 of 676** computors — it was simply wrong;
- scanning ~4 500 transactions per computor across the entire epoch-228 window found **no
  inbound payments at all**. What computor identities actually emit is a stream of
  `amount = 0` transactions to the null address — those are **solution submissions**, not
  payouts;
- yet `/v1/balances` reports 0.5–1.6 B QU of inbound value per computor. The value is real,
  but **the transfers carrying it are not exposed** by the transaction endpoints.

**Conclusion: computor revenue is credited by protocol-level emission, not by a transfer the
public RPC exposes.** "Track the arbitrator's payouts" cannot work no matter how completely
it paginates — the records are not there. That is almost certainly the root of the
divergence, and it means neither implementation can be checked against the other until both
state which source they use.

What we do instead (§4.3, implemented): revenue is measured as the **delta of each
computor's cumulative `incomingAmount` across the epoch boundary**, using balance snapshots
we take ourselves — which is exactly what the store (§5.1) exists for. Verified working
against the live chain. It is self-contained, reproducible, and starts producing correct
numbers from the next boundary forward.

Two things still worth doing, and the second needs Kevarms directly:

1. Request a `qubic.li` Score API token — the fastest route to *historical* revenue and an
   independent cross-check.
2. **Compare methods, not just numbers.** Since the public transfer endpoints do not carry
   these payments, a 6-epoch dataset that converges must be reading a different source (a
   node-level feed, a pool API, or balance deltas as we now do). Establishing which is more
   valuable than arguing about totals — and it is the actual acceptance test for this layer.

Alongside this, three concrete endpoint corrections came out of the same session (wrong
paths, a **250-row pagination cap**, and where epoch tick windows actually live); all are
recorded in `docs/DATA_SOURCES.md` §6.1 so the next implementer does not repeat them.

**2. "Good tooling, rough input; on-chain linkage should be the default, not the fallback
bucket labeled unattributed."** (Jure Ursic Cergol)

Accepted as an architectural correction. In v0.1 `apply_onchain_linkage()` existed but was
never called by the report path, so the only real attribution came from a registry whose
`computors` lists were deliberately empty — meaning every one of the 676 slots became its
own "unattributed" singleton and the headline decentralization numbers described 676
fictional independent operators. §4.2 inverts the layering: the payout graph is computed on
every epoch and produces the baseline clustering; self-reporting labels and supplements it
but can never split what the chain has linked. "Unattributed" now means *the ledger shows no
link*, not *nobody filed a form*, and the report states its linkage coverage so a reader can
see how much of the network is resolved versus assumed.

**3. "Don't you need to persist anything?"** (admin)

Yes. v0.1 rebuilt every response from the RPC and overwrote its only snapshot files on each
build, so history was lost and no two runs were comparable. §5.1 introduces a SQLite store
with the explicit split the project needs: **the current epoch is always recomputed live**
so the running epoch is never stale, while **closed epochs are sealed and served from the
store** so history is immutable, archivable, and no longer dependent on the RPC still
serving old epochs. Logic changes trigger a versioned backfill rather than a silent
rewrite — so when a number changes, there is a recorded reason.

**4. "Apool closing — where are the IDs they were using going?"** (Vaintor)

The concept had no answer, because every layer in §4.2 reasons within a single epoch. A pool
shutting down is the exact event a per-epoch report cannot interpret: the departing
operator's slots are re-issued, and whether they land with new independent operators, get
absorbed by the remaining large pools, or come back under the same operator's fresh
identities, each of those produces an internally consistent snapshot. Worse, the third case
makes the headline numbers *improve* — the successor slots enter as unlinked singletons and
inflate the operator count — so the report would have flattered the network at precisely the
moment it should have raised a flag.

§4.4 adds the missing dimension: an epoch-over-epoch identity diff
(`retained`/`departed`/`entered`/`succeeded`) as a first-class output, successor detection
that reuses the existing payout graph to carry an operator's identity across an ID change,
a published churn metric that includes an explicit *unexplained* share, and an optional
`status` field in the registry so an announced shutdown is on record before the boundary
rather than reconstructed afterwards. The question is also a concrete acceptance test: when
Apool's exit epoch closes, the report must be able to state where those slots went, and say
plainly how much of the movement it could not explain.

---

## 8. Open questions to resolve next

- Confirm the arbitrator identity against boundary-tick transfers and pin it with evidence
  (blocks §4.3; currently an unverified constant).
- Reconcile derived per-epoch revenue against an independent implementation (Kevarms' 6-epoch
  dataset) before publishing headline numbers.
- Rate limits for the full-pagination transfer pulls — the exhaustive scan is a much heavier
  RPC load than v0.1's single page; confirm politeness budget or mirror.
- Format/authority of pool self-reporting — is there a canonical feed, or do we curate a
  registry and let pools PR their entries?
- Successor-detection thresholds (§4.4) — how much shared-destination overlap constitutes a
  `succeeded` link, and how many epochs a `departed` operator stays in the candidate pool
  before an entering slot is treated as genuinely new.
- Apool's exit epoch — pin the boundary and capture the pre-exit slot set *before* it is
  gone, so the transition diff has a baseline to compare against (§4.4).
- Whether a departing operator will declare its wind-down (`status: winding_down`) at all,
  or whether exits must always be detected after the fact.
- Which explorer(s) are the first integration target, and their preferred embed format.
- Bounty acceptance criteria — what specifically must the report contain to be "accepted"
  and added to a site.

---

## 9. Roadmap

1. **Concept** (this doc) ✅
2. **Data mapping** — endpoints confirmed against live RPC, documented in `docs/DATA_SOURCES.md` ✅
3. **Revenue engine** — Gini / HHI / top-N / Nakamoto implemented + unit-tested (`qdr/metrics.py`) ✅
4. **Clustering engine** — registry + linkage implemented (`qdr/clustering.py`); **on-chain linkage promoted from optional hook to the default attribution layer** (§4.2) ✅
4b. **Revenue derivation** — solved. Payout tracking proved impossible against the public
   RPC (revenue is protocol emission, not an exposed transfer — DATA_SOURCES §6.2); a **Bob
   node's end-epoch log** carries it, with history. Epochs 225-228 built from live data:
   676/676 computors paid each. Balance deltas remain the no-Bob fallback (§4.3) ✅
4b-ii. **Live view** — `/v1/pulse` on a fast clock (tick, epoch progress, quality) beside the
   report on its slow one, cadence measured rather than guessed (§5.1.1) ✅
4c. **Persistence** — SQLite store, sealed vs. live epochs, versioned recompute (§5.1) ✅
4d. **Forward compatibility** — no hardcoded network constants, data-driven i18n, bounded
   API windows, tested at 100–2048 slots (§5.2) ✅
4e. **On-chain linkage** — implemented, but currently finds nothing provable: computors only
   receive protocol emission and burn fees (§4.3.1). Reported honestly as
   `linkage_coverage: 0` rather than as independence 🔶
4f. **Slot transitions** — epoch-over-epoch identity diff, successor detection, churn metric
   with an explicit unexplained share, registry `status` field (§4.4) 🔶
5. **API** — FastAPI service serving the report (`api/server.py`), CORS-open, with a
   `/v1/dashboard-data` bundle and a static-sample fallback ✅
6. **Dashboard** — self-contained SPA (`dashboard/index.html`): animated operator treemap,
   Nakamoto/Gini timeline, operator table, DE/EN + dark/light with localStorage ✅
7. **Explorer integration** — embed format shipped (widget + iframe + raw API,
   `docs/EMBEDDING.md`, `dashboard/embed.js`); first-partner rollout pending 🔶

Implemented so far: `qdr/` (client, metrics, clustering, report); a self-reporting
registry with a validator (`data/self_reporting/`, `scripts/validate_registry.py`); tests
(`tests/`, all passing); the API service (`api/`, which also serves the dashboard and
examples); the reference dashboard (`dashboard/`); an embeddable widget (`dashboard/embed.js`);
a live-pull CLI (`scripts/build_report.py`); and VS Code F5→Chrome configs (`.vscode/`).
Live runs need network access to `rpc.qubic.org` (blocked in the Cowork sandbox, fine from a
normal machine); everything else runs today on generated sample data.
