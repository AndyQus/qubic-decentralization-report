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

- Arbitrator identity (payout source) — confirm the exact public ID against boundary-tick
  transfers.
- Active computor count (676) vs. the `identities` array length returned by the endpoint.
- Epoch length in ticks (varies; `initialTick` + observed range give it per epoch).
