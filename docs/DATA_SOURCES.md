# Data Sources — verified endpoints & open-question answers

*Technical appendix to the concept. Endpoint reference is language-neutral; a short German
summary is at the top. The conceptual docs (`CONCEPT.md` / `CONCEPT.de.md`) stay fully
bilingual.*

Verified live against `https://rpc.qubic.org` on 2026-09-02 — network was at **epoch 228**.

## Zusammenfassung (DE)

Die zentralen Datenquellen der Qubic-RPC 2.0 sind bestätigt: Computor-Liste pro Epoche,
Tick-/Epoche-Info, Netzwerk-Statistiken und Rich-List funktionieren ohne Auth. Das
**Revenue pro Computor** wird nicht als fertiger Endpoint geliefert; wir leiten es aus den
**Arbitrator-Auszahlungen am Epochenende** (On-chain-Transfers) ab — das ist reproduzierbar
und liefert zugleich die Payout-Adressen fürs Clustering. `qubic.li` hätte fertige
Score-/Revenue-Daten, braucht aber ein API-Token. Self-Reporting hat **keinen kanonischen
Maschinen-Feed** — wir kuratieren ein Registry im Repo (Pools per PR), initial befüllt aus
öffentlichen Pool-Angaben.

---

## 1. Verified RPC endpoints (no auth)

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /v1/tick-info` | `{tickInfo:{tick,duration,epoch,initialTick}}` | Cheap epoch/tick pointer. `initialTick` = first tick of the current epoch. |
| `GET /v1/latest-stats` | `{data:{epoch,currentTick,ticksInCurrentEpoch,emptyTicksInCurrentEpoch,epochTickQuality,circulatingSupply,activeAddresses,price,marketCap,burnedQus,timestamp}}` | Network-level health & supply. |
| `GET /v1/epochs/{epoch}/computors` | `{computors:{epoch,identities:[...],signatureHex}}` | The list of computor identities for the epoch, signed by the arbitrator. This is the **slot → identity** ground truth per epoch. (Array length observed larger than 676 via proxy read — must be re-counted against a direct pull; Qubic's active-computor constant is 676.) |
| `GET /v1/rich-list?page={n}&pageSize={m}` | `{pagination:{totalRecords,currentPage,totalPages,pageSize},epoch,richList:{entities:[{identity,balance}]}}` | Paginated balances — used for on-chain linkage / holdings context. |
| `GET /v1/status` | system status | Liveness. |
| `GET /v1/balances/{publicId}` | account balance | Per-identity balance. |

Archiver-style historical endpoints (from `qubic/go-archiver`, now EOL — a successor
archiver serves the same shapes behind the RPC):

| Endpoint | Returns |
|---|---|
| `GET /v1/epochs/{epoch}/computors` | computor list (as above) |
| `GET /ticks/{tick}/transactions` | all transactions in a tick |
| `GET /ticks/{tick}/transfer-transactions` | currency transfers in a tick |
| `GET /identities/{identity}/transfer-transactions` | historical transfers for an identity |
| `GET /ticks/{tick}/quorum-tick-data` | quorum / consensus data |

Full interactive reference: `https://qubic.github.io/integration/Partners/swagger/qubic-rpc-doc.html`
(Swagger UI — JS-rendered, so read it in a browser, not via a plain fetch.)

## 2. Per-computor revenue — how we get it

There is **no single "revenue per computor" endpoint** in the public RPC. Two viable paths:

**A. Derive from arbitrator payouts (primary, reproducible).** At each epoch boundary the
Arbitrator distributes revenue to the epoch's computor identities. Per-computor revenue =
the payout transfer received by each identity in `/v1/epochs/{epoch}/computors` around the
epoch transition, read from `/identities/{identity}/transfer-transactions` (or by scanning
the boundary ticks' `transfer-transactions`). This is fully reproducible from public data
and, as a bonus, yields the **payout linkage** used by the clustering engine. The
distribution model (one-epoch lag, ~1.48B QU max per computor after the 10% operator fee,
performance/slashing factors) is documented by pools and the Qubic academy and matches the
on-chain amounts.

**B. `qubic.li` Score/stats API (convenience, needs auth).** `api.qubic.li` exposes
per-computor score/revenue but requires an API token (`/Score/Get` returns 401 without one).
Usable as a cross-check once a token is available; not required for the core pipeline.

We build on **A** and treat **B** as optional validation.

## 3. Answers to the concept's open questions (§8)

**Q1 — RPC endpoints & rate limits for per-computor revenue history.**
No dedicated endpoint; we derive revenue from arbitrator epoch-end transfers (path A above).
Historical computor lists are available per epoch via `/v1/epochs/{epoch}/computors`, so we
can rebuild the full time series epoch by epoch. Rate limits are not published; we cache
every raw pull under `data/raw/` and are polite (one pass per epoch, incremental).

**Q2 — Self-reporting format / authority.**
There is **no canonical machine-readable self-reporting feed**. Decision: we maintain a
**curated registry in this repo** (`data/self_reporting/pools.json`), versioned in git, where
each pool/operator declares the computor identities it controls. Pools contribute via pull
request (auditable history = itself an anti-Sybil property). It is seeded from public pool
information (qubic.li pool pages, community disclosures). The registry is the "declared"
layer; on-chain linkage is the "detected" layer; their delta is the headline signal.

**Q3 — First explorer integration target.**
Recommendation: ship an **embeddable widget + open JSON API** so any explorer can adopt it,
and approach the most active explorers first (the qubic.org explorer, `qubic.li`, and
jetski's ANN explorer). Final target to be confirmed with the community/bounty sponsors; the
API is explorer-agnostic by design (CORS-open, versioned).

**Q4 — Bounty acceptance criteria.**
Restating the target the report must hit to be "accepted and embedded": (a) revenue metrics
**and their dynamics** over epochs; (b) clustering with the **number of computors per
cluster**; (c) fully **reproducible** from public data; (d) **embeddable** by explorers
(API + widget). Exact sign-off wording to be confirmed with Eko/Broms; this is the concrete
spec we build to.

## 4. Identities & constants to confirm on a direct pull

- **Arbitrator identity (payout source)** — the bundled default is a *candidate*, not a
  verified value, and nothing in the pipeline trusts it blindly. Confirm it empirically:

  ```
  python scripts/ingest.py --verify-arbitrator
  ```

  This counts, for each candidate identity, how many of the epoch's computors it actually
  paid within the epoch's tick window. The real arbitrator pays a large share of the 676; a
  candidate that pays none is wrong. Set the confirmed value via `QUBIC_ARBITRATOR` and
  record the tick evidence here. Until it is confirmed, derived revenue carries an explicit
  warning in the payload rather than silently returning zeros.
- Active computor count (676) vs. the `identities` array length returned by the endpoint.
- Epoch length in ticks (varies; `initialTick` + observed range give it per epoch).

## 5. Revenue derivation — the exact procedure (v0.2)

Implemented in `qdr/revenue.py`. The three rules that make two implementations agree:

1. **Epoch scoping.** Payouts for epoch *N* are searched from *N*'s `initialTick` through the
   end of epoch *N+1* (Qubic pays with a one-epoch lag). Transfers outside that window are
   discarded even if the RPC returns them, because the client cannot assume the endpoint
   honoured its tick parameters.
2. **Exhaustive pagination.** `fetch_identity_transfers()` pages until a short page, an empty
   page, or a page with no new transaction ids. If it cannot prove exhaustion (page ceiling,
   RPC error, or an endpoint that only serves the unpaged v1 shape) the epoch is returned
   `complete=False` and the pipeline marks it `partial` — excluded from headline metrics.
3. **Reconciliation.** Derived totals are checked against the documented model (per-computor
   cap after the 10% operator fee). Violations — a computor above the cap, a negative amount,
   or more than half the computors at zero — are attached to the report as warnings rather
   than averaged away.

Every report carries a `revenue_provenance` block naming the arbitrator, the tick range, the
number of matched transfers, the computors paid and the totals. That block is what lets a
third party locate a divergence instead of arguing about it.


---

## 6. Live findings, 2026-09-07 — what the RPC actually does

Verified by direct pulls against `rpc.qubic.org` (network was at **epoch 229**). These
correct several assumptions in §1–§5 and in CONCEPT v0.1. Where a claim below contradicts an
earlier section, **this section is the verified one.**

### 6.1 Endpoints — corrections

| Claim | Verified result |
|---|---|
| `/v1/epochs/{e}/computors` returns >676 identities | **False.** Returns exactly **676** for epoch 228. The earlier note was a proxy artefact. |
| That endpoint carries an epoch tick pointer | **False.** It returns only `{epoch, identities, signatureHex}` — no `initialTick`. |
| `/identities/{id}/transfer-transactions` (v1) works | **False.** Does not resolve. The working form is **`/v2/identities/{id}/transfers`**. |
| `/v1/ticks/{tick}/transfer-transactions` works | **False.** Returns **503**. Use `/v1/ticks/{tick}/transactions` or `/v2/ticks/{tick}/transactions`. |

**Epoch tick windows** (needed for epoch-scoped revenue) come from two other endpoints:

* first tick — `GET /v2/epochs/{e}/ticks?page=0&pageSize=1` → `ticks[0].tickNumber`
* last tick  — `GET /v1/status` → `lastProcessedTicksPerEpoch[{e}]`

Measured: epoch 228 = ticks **76 550 000 … 77 560 787**; epoch 229 starts at **77 700 000**.
(Epoch 227's first tick is no longer served — the archive window is limited, so older epochs
resolve as `partial` by design.)

**Pagination limit.** `/v2/identities/{id}/transfers` rejects `pageSize > 250` with
HTTP 400 (`"Invalid page size 500 (maximum is 250)"`). Implementations that request more get
an error, not a truncated page — and code that treats an error as "no data" will silently
report zero revenue. `qdr/revenue.py` pins `MAX_PAGE_SIZE = 250`.

The endpoint **does** honour `startTick`/`endTick` (epoch-228 window: 4 249 records vs.
10 000 unfiltered), and returns an explicit `pagination` block that should be used to detect
exhaustion. Note that the unfiltered `totalRecords` caps at exactly 10 000 — an unscoped scan
is therefore truncated by the server, which is precisely the under-counting failure mode
described in CONCEPT §4.3.

### 6.2 The blocker: per-computor revenue is not visible as a transfer

CONCEPT v0.1 (and §2 above) assumed computor revenue arrives as an epoch-boundary transfer
from an arbitrator identity to each computor identity. **Direct measurement contradicts
this:**

* `python scripts/ingest.py --verify-arbitrator` reports the bundled arbitrator paid
  **0 of 676** computors — the constant is wrong, and no candidate was found.
* Scanning ~4 500 transactions per computor across the full epoch-228 window found
  **zero inbound payments**. What the computor identities actually emit is a stream of
  `amount = 0` transactions to the null address with `inputType` 1/9 — these are
  **solution submissions**, not payments.
* `/v1/balances/{id}` nonetheless reports substantial inbound value per computor
  (0.5–1.6 B QU, 70–570 incoming transfers), i.e. the value *is* real…
* …but the transfers that carry it are **not returned** by
  `/v2/identities/{id}/transfers`, not even when querying a ±2 000-tick window around the
  `latestIncomingTransferTick` the balance endpoint itself reports. The corresponding tick
  (`/v2/ticks/{t}/transactions`) contains only unrelated transactions.

**Conclusion.** Computor revenue is credited by protocol-level emission at the epoch
boundary, not by an ordinary transfer that the transaction endpoints expose. Deriving it by
"tracking arbitrator payouts" is therefore **not implementable against the public RPC as it
stands** — no amount of pagination fixes it, because the records are not there.

### 6.3 Viable paths for revenue (to decide)

1. **Balance deltas across the epoch boundary.** Snapshot each computor's balance at the
   epoch's last tick and at the successor's, and treat the credited difference as revenue.
   Self-contained and reproducible, but needs a historical balance read (`/v1/balances` is
   current-state only), so it requires *our own* per-epoch snapshots — which the store
   (CONCEPT §5.1) is exactly the right place for. **This is the recommended path**: it starts
   producing correct data from the next epoch boundary forward, without waiting on anyone.
2. **`qubic.li` Score API.** `api.qubic.li` exposes per-computor score/revenue but needs a
   token (`/Score/Get` → 401 without one). Fastest route to *historical* numbers and the
   natural cross-check — worth requesting a token.
3. **Reconcile against an existing implementation.** Kevarms reports ~6 epochs of full
   transaction tracking that converges. Since the public transfer endpoints do not expose
   these payments, comparing method notes is the cheapest way to find out whether they use a
   non-public source, a node-level feed, or balance deltas as in (1).

Until one of these lands, the pipeline reports revenue-derived metrics with an explicit
warning and marks the epoch `partial` rather than publishing zeros as if they were real.
**Slot-based concentration and on-chain clustering are unaffected** — they do not depend on
revenue and work against live data today.