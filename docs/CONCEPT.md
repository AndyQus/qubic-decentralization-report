# Qubic Decentralization Report — Concept

*Working concept for a tooling project that produces a standing "how decentralized is
Qubic" report from self-reported and on-chain data, and exposes it as an API that
explorers can embed.*

Status: draft v0.1 · Owner: (Qubic community project) · Language: English (spec is for
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

| Layer | Source | What we get |
|---|---|---|
| Consensus / slots | Qubic RPC 2.0 (`rpc.qubic.org`) + core node data | Computor list per epoch (676 IDs), tick data, quorum info |
| Revenue | RPC / archiver | Per-Computor revenue per epoch, payout flows, Top-451 distribution |
| Self-reporting | Pool APIs & public declarations (qubic.li and others), plus a curated registry in this repo | Which slots a pool/operator claims |
| On-chain | Qubic ledger via RPC | Payout destination addresses, fund flows between identities |
| Behavioral (optional) | Solution-submission timing/patterns where observable | Signals to confirm or challenge self-reported clusters |

Exact endpoint names are verified against the live RPC during implementation; see
`docs/DATA_SOURCES.md` (to be filled in during the data-mapping step).

---

## 4. The two analytical engines

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

This is the core, and it is layered from most trustworthy to most inferential. Each
Computor slot gets assigned to a cluster with a **confidence level and the evidence used**,
so the report is transparent about what is declared vs. inferred:

1. **Self-reported (primary, per CFB).** Pools declare their slots; we ingest those
   declarations into a versioned registry. This is the baseline "official" clustering.
2. **On-chain linkage.** Shared payout destinations, reused identities across epochs, and
   fund-flow graphs merge slots that provably share an economic owner.
3. **Behavioral fingerprints (flagging only).** Correlated submission timing / source
   patterns can *flag* slots that behave as one even if declared separately — surfaced as
   "possible undeclared cluster," never as a hard accusation.

Output per cluster: number of Computors, revenue, share of Top-451, and a
**declared-vs-detected** delta that quantifies the "smoke."

---

## 5. API shape (draft)

```
GET /report/latest                 → summary: epoch, #clusters, Nakamoto coeff, Gini, HHI, top clusters
GET /report/{epoch}                → same, historical
GET /clusters/{epoch}              → full cluster list: id, label, computor_count, revenue, share, confidence
GET /computors/{epoch}            → per-slot: id, cluster_id, revenue, evidence
GET /metrics/timeseries           → concentration indices across epochs (feeds the charts)
GET /report/{epoch}/snapshot.json → frozen, archivable snapshot
```

Design principles: read-only, cacheable, CORS-open (explorers embed it client-side),
versioned (`/v1/`), and every response carries `generated_at`, `data_sources`, and a
`reproducible: true` block naming the inputs.

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

## 8. Open questions to resolve next

- Exact RPC endpoints and rate limits for per-Computor revenue history (map in
  `docs/DATA_SOURCES.md`).
- Format/authority of pool self-reporting — is there a canonical feed, or do we curate a
  registry and let pools PR their entries?
- Which explorer(s) are the first integration target, and their preferred embed format.
- Bounty acceptance criteria — what specifically must the report contain to be "accepted"
  and added to a site.

---

## 9. Roadmap

1. **Concept** (this doc) ✅
2. **Data mapping** — endpoints confirmed against live RPC, documented in `docs/DATA_SOURCES.md` ✅
3. **Revenue engine** — Gini / HHI / top-N / Nakamoto implemented + unit-tested (`qdr/metrics.py`) ✅
4. **Clustering engine** — self-reported registry + on-chain-linkage hook implemented + tested (`qdr/clustering.py`); on-chain layer to be enriched 🔶
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
