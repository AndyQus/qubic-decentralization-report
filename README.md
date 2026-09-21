# Qubic Decentralization Report

> ## 📄 Start here: the concept
>
> **[`docs/CONCEPT.md`](docs/CONCEPT.md) (English) · [`docs/CONCEPT.de.md`](docs/CONCEPT.de.md) (Deutsch)**
>
> The concept is the heart of this project — it explains *what* is measured, *why* those
> metrics, and *how* slots are attributed to operators (and where that attribution stops
> being provable). Read it before the code: everything here implements that document.
> Both language versions are kept in sync — any change to one is mirrored in the other.

![Dashboard preview](docs/preview_dashboard.png)

A tool that measures **how decentralized Qubic actually is** and publishes the result as an
API that explorers can embed, plus a reference dashboard.

It answers the request made by CFB in `#computor-operator`: a report showing **revenue
metrics and their dynamics** and **clustering with the number of computors in each
cluster** — built on Qubic's **self-reporting** anti-Sybil approach and extended with
on-chain and behavioral signals to quantify concentration/collusion among the 676
Computors.

It has since grown two further measurements, both of figures the public RPC does not
publish at the resolution they need, and each with its own page:

| Page | Question it answers | Why it exists |
|---|---|---|
| **Report** (`/`) | How concentrated is the network, per epoch and over time? | The original request. |
| **Burn** (`/dashboard/burn.html`) | How much QU is burned per day, and by which contract? | The RPC's `burnedQus` is a cumulative epoch aggregate — it does not move between boundaries, so it cannot answer "today". This counts the burn events themselves. |
| **Mining Live** (`/dashboard/mining.html`) | What does the ant colony look like right now? | Live mining state is served from a node's peer port, not the RPC, and a node only answers for *now* — a reading not taken is gone. |
| **How it works** (`/how-it-works`) | What is measured, and how? | Generated from `docs/CONCEPT*.md` at build time, so the page cannot disagree with the concept it was built from. |

**Run it** (details under [Docker](#docker--publishing)). Two services share one volume:
the ingest worker writes the store, the API serves it.

```bash
docker compose up -d
docker compose logs -f qubic_decentralization_report_ingest   # watch the first backfill
```

The API alone starts too, but without the worker nothing fills the store and the page
stays on "building the report" — the worker is what makes it self-updating.

### What works today, and what is still open

| | |
|---|---|
| ✅ **Revenue per computor slot** | From a Bob node's end-epoch log. Epochs 220-230 sealed, 676/676 computors paid in each — the public RPC does not expose these payouts at all (they are protocol emission, not transfers). |
| ✅ **Concentration metrics + dynamics** | Gini, HHI, Nakamoto ⅓/½, top-N share, per epoch and over time. |
| ✅ **Keeps itself current** | The worker backfills on first start, seals each epoch as it closes, and re-derives any epoch an older code version computed — a deploy retires the figures it corrects. |
| ✅ **Burned supply, per day and per contract** | Counted from a Bob node's tick logs, because the RPC's `burnedQus` only steps once per epoch. Backfills the days before the worker existed by dating past ticks from measurement. Measured 2026-09-17: 194,520,597 QU of contract burns in one day, dominated by QEarn. |
| ✅ **Live mining state, with history** | The colony's figures come from a node's peer port and are not replayable, so the worker samples them on a timer and keeps a rolling two-epoch window. |
| ✅ **Honest about its limits** | No sample data, ever. An unfilled store answers 503 and the page says it is building. Every figure is labelled for what it measures — a partial day says so, and the chart refuses to aggregate one into a period. |
| 🔶 **Operator attribution: 0%** | **The one thing still missing, and it needs the community, not more code.** See below. |
| 🔶 **Explorer embedding** | Widget, iframe and raw API are shipped (`docs/EMBEDDING.md`); no explorer has adopted it yet. |

### The open question: who operates the 676 slots?

CFB's request was clustering — "number of computors in each cluster". The machinery for
that is built and runs on every epoch. It currently produces **one cluster: 676
unattributed slots**, because neither attribution layer can resolve anything:

- **On-chain linkage** finds nothing provable. Every computor is credited by the same
  null address (protocol emission, not a wallet), and their outgoing transfers are
  uniformly 1,000,000 QU burns to that same address. No transfer graph links two
  computors, so the ledger discloses no ownership. Reported as `linkage_coverage: 0`
  rather than dressed up as independence.
- **Self-reporting** — CFB's own anti-Sybil point, "let's use it to the fullest" — is
  empty: no pool has declared its slots yet.

So the report currently measures revenue **per slot**, and says so on every figure
("slots, not operators"). A Nakamoto coefficient of 222 means 222 of 676 *slots*, not 222
independent operators; where one operator holds several, the real concentration is higher.

Ready-to-post announcement text for `#computor-operator` — long and short — is in
[`docs/ANNOUNCEMENT.md`](docs/ANNOUNCEMENT.md).

**This is the finding, not a gap in the tool.** It measures that self-reporting adoption is
currently zero, and it is the infrastructure for changing that: a pool opens a pull request
against [`data/self_reporting/pools.json`](data/self_reporting/pools.json), and the
operator view — treemap, per-operator table, operator-level Nakamoto — turns itself on for
that pool with no code change. Git history is the audit trail.

## How the pieces fit together

```
  Data sources                Tool (this repo)                 Consumers
  ────────────                ────────────────                 ─────────
  Qubic RPC 2.0        ┐                       ┌── store ──┐
  Pool self-reporting  ├──▶ ingest → analyze ──▶│  sealed   │──▶ JSON API ──▶ Explorers
  On-chain ledger      ┘        │               │  history  │        │        (embed it)
  (behavioral signals)          │               └───────────┘        │
                                └── reference dashboard (animated + DE/EN + dark/light)

  Closed epochs are computed once and SEALED in the store; the running epoch is
  recomputed live. History survives restarts and RPC outages.
```

**We are the data provider.** The tool computes the report and serves it as an API.
**Explorers are the consumers** — they call the API and display the report on their sites.
The dashboard in this repo is our own reference front-end (and the home of the animated
views).

## Repository layout

```
docs/         Concept, data-source mapping, API spec
api/          The service that serves the computed report (JSON)
analysis/     Revenue-metrics and clustering engines
data/         Cached raw pulls (data/raw), the store (qdr.db) + the self-reporting registry
dashboard/    Reference front-end: charts, animations, DE/EN i18n, dark/light
scripts/      CLI: ingest worker, build reports, validate the registry, verify the dashboard
tests/        Metric + report unit tests
Dockerfile, docker-compose.yaml, .env.example
```

## Dashboard requirements (front-end)

- Layout inspired by existing Qubic community apps (e.g. *Qubic Dividends*).
- Dark / light mode toggle, dark by default.
- German / English language switch, English by default.
- Both choices persisted in `localStorage` (with a safe fallback if storage is blocked).
- Animations only where motion carries meaning (cluster evolution, Nakamoto timeline,
  fund-flow graph) — see `docs/CONCEPT.md` §6.

## Quickstart

```
pip install -r requirements.txt

# 1) run the tests
python3 -m pytest tests/ -q

# 2) sample data (no live RPC needed) -> api/sample/ + dashboard/data.js
python3 scripts/generate_sample.py

# 2b) LIVE data (run where rpc.qubic.org is reachable) -> store + snapshots
python3 scripts/build_report.py --check         # test connectivity
python3 scripts/ingest.py --snapshot            # balance snapshots (fallback revenue)
python3 scripts/build_report.py                 # last 9 epochs into the store

# 2c) keep it current (production): snapshot + refresh + seal epochs as they close
python3 scripts/ingest.py --watch               # <- the one you actually run
python3 scripts/ingest.py --status              # what the store holds
python3 scripts/ingest.py --backfill 30         # fill history

# 3) view it
#   a) just open dashboard/index.html  (bundled sample data)
#   b) or serve API + dashboard together:
uvicorn api.server:app --port 8000
#      then open http://localhost:8000/  (dashboard auto-fetches the live API)
```

### In VS Code (F5)

Press **F5** → *Dashboard (Chrome · sample data)* opens it in Chrome instantly (no setup).
The *Dashboard + live API (Chrome)* config starts the API (`uvicorn`) first and opens the
live dashboard. Tasks for install / tests / sample-data are in the Command Palette →
*Run Task*. (Configs live in `.vscode/`.)

> Note on live data: `rpc.qubic.org` must be reachable from wherever the API/`build_report`
> runs. It is blocked inside the Cowork sandbox, but works from a normal machine — so live
> pulls run fine in your own VS Code / on a server.

### API reference (Swagger)

The API is FastAPI, so the interactive docs come with it — no extra setup:

| URL | What |
|---|---|
| `http://localhost:8000/docs` | **Swagger UI** — every endpoint with *Try it out* |
| `http://localhost:8000/redoc` | ReDoc — the same spec, easier to read |
| `http://localhost:8000/openapi.json` | OpenAPI spec (import into Postman/Insomnia, generate clients) |
| `http://localhost:8000/api` | plain JSON index of the endpoints |

**The endpoints, grouped by what they answer:**

| Group | Endpoints |
|---|---|
| **Report** | `/v1/report/latest`, `/v1/report/{epoch}`, `/v1/report/{epoch}/versions`, `/v1/report/{epoch}/snapshot.json`, `/v1/epochs`, `/v1/clusters/{epoch}` |
| **Metrics** | `/v1/metrics/timeseries`, `/v1/dashboard-data` |
| **Burn** | `/v1/burn/latest`, `/v1/burn/series?by=day\|epoch\|year`, `/v1/burn/contracts`, `/v1/burn/coverage` |
| **Mining** | `/v1/mining` (live reading), `/v1/mining/series` (stored history) |
| **Service** | `/v1/pulse`, `/v1/store`, `/v1/diagnostics` (*why is the report empty?*), `/v1/log` (worker tail), `/health` |

Every endpoint answers `503` rather than inventing a figure the store cannot back
(`tests/test_burn_page.py`, `tests/test_mining.py`). The API also never *writes*:
one writer, many readers — a rule with a history, since an API process running an
older build used to recompute epochs on request and overwrite the worker's
corrections behind its back (`tests/test_api_is_read_only.py`).

### Embedding in an explorer

A one-line widget, an iframe, or the raw JSON API — see [`docs/EMBEDDING.md`](docs/EMBEDDING.md)
and the live demo at `examples/embed-example.html` (or `http://localhost:8000/examples/embed-example.html`).


## Docker / Publishing

```bash
docker build -t andyqus/qubic_decentralization_report:latest .
docker push andyqus/qubic_decentralization_report:latest
```

The image bundles the API, the dashboard and the embed example, so one container
serves everything on port `8000`. It needs **no secrets** — the report is built purely
from public RPC data. The RPC cache lives in the volume under `/data`.

### Deployment for admins (Docker)

**1. Pull the image**

```bash
docker pull andyqus/qubic_decentralization_report:latest
```

(Alternatively build it yourself: `docker build -t andyqus/qubic_decentralization_report:latest .`)

**2. Put `docker-compose.yaml` + `.env.example` on the server**

Both files are in the repo and must reside in the same directory on the server.

**3. Optional settings** – there are **no mandatory secrets**: the report reads public RPC
data only, and has no login, no database and no uploads. Create the `.env` only if you
want to deviate from the defaults:

```bash
cp .env.example .env
```

```env
QUBIC_RPC_BASE=https://rpc.qubic.org
#QDR_REGISTRY=/data/pools.json
```

**4. Start**

```bash
docker compose up -d
```

Then open `http://<host>:8000/` — the dashboard is served directly and fetches the API
next to it. Health check: `http://<host>:8000/health`.

**5. HTTPS via reverse proxy** – explorers embed the widget from HTTPS pages, so the API
must be reachable over HTTPS too (a browser blocks mixed content otherwise). Minimal
Caddy setup next to the compose file:

```caddyfile
# /etc/caddy/Caddyfile
report.example.org {
    encode gzip
    reverse_proxy localhost:8000
}
```

```bash
sudo apt install -y caddy          # Debian/Ubuntu
sudo systemctl reload caddy        # certificate is obtained automatically
```

Caddy handles the Let's Encrypt certificate on its own. With nginx use a normal
`proxy_pass http://127.0.0.1:8000;` plus certbot.

**6. Keep it updated**

```bash
docker compose pull && docker compose up -d
docker image prune -f              # remove the superseded image
```

**Important for operation:**

- **One writer, many readers.** Only the ingest worker writes to the store; the
  API is a pure reader and never recomputes on request. That separation is load-
  bearing: an API process running an older build would otherwise rebuild epochs
  behind the worker's back and stamp them with its own `code_version`, undoing
  corrections the worker had just applied.
- **Two services, one volume.** `qubic_decentralization_report` serves the API and
  dashboard; `qubic_decentralization_report_ingest` keeps the store current — it
  backfills history on first start, refreshes the running epoch every
  `QDR_INGEST_INTERVAL` seconds, and seals each epoch as it closes. The API only ever
  reads what the worker writes, so without the worker the report never advances.
- **Network access:** both containers must reach `rpc.qubic.org` (or whatever
  `QUBIC_RPC_BASE` points at), and the worker additionally reaches `QDR_BOB_URL` for
  the epoch-end payouts. There is **no sample fallback**: figures shaped like real
  measurements are indistinguishable from them once rendered, and this report is read
  as a statement about the network. An unfilled store answers `503` and the dashboard
  says it is still building — it never shows a number nobody measured.
- **A deploy corrects itself.** Sealed epochs are normally immutable, but a fix
  that changes a published figure makes the epochs computed before it wrong, not
  done. Every worker start compares each epoch's `code_version` against the
  running code and re-derives whatever disagrees, so shipping new code is all it
  takes to retire the numbers it corrects — no `--force` by hand, nothing stale
  left on the page. The superseded computation is kept beside the new one
  (reports are keyed by `(epoch, code_version)`), so a changed number has a
  recorded before and after. Run it manually with
  `python scripts/ingest.py --refresh-stale`.
- **First start** takes a few minutes: the worker seals `QDR_BACKFILL_EPOCHS` (default
  10) epochs before the dashboard has anything to show. Watch it with
  `docker compose logs -f qubic_decentralization_report_ingest`.
- **Persistent data** lives in the named volume `qdr-data` → `/data`: the RPC cache and
  the SQLite store of sealed epochs. Inspect it with `docker volume inspect qdr-data`.
  Sealed epochs are re-derivable from a Bob node, so a backup is nice-to-have rather
  than critical — but re-deriving costs a backfill.
- The app listens on port `8000` inside the container (mapped to host `8000`). A reverse
  proxy (e.g. Caddy/Nginx) for HTTPS belongs in front of it, especially since explorers
  embedding the widget will load it over HTTPS.
- **CORS is open (`*`) by design** — the point is for third-party explorers to embed the
  report. The API is read-only (`GET` only), so there is nothing to protect against
  writes; do put a rate limit in the reverse proxy if you expose it publicly.
- **The registry** (`data/self_reporting/pools.json`) is baked into the image. To run
  your own without rebuilding, mount it and set `QDR_REGISTRY` — see the commented
  lines in `docker-compose.yaml`.
- The container runs as a **non-root user** (uid 10001). The named volume above gets the
  correct ownership automatically. If you switch to a host bind mount instead, run
  `sudo chown -R 10001:10001 <path>` once, otherwise the cache cannot be written.

## Configuration (environment variables)

| Variable | Purpose | Default |
|----------|---------|---------|
| `QUBIC_RPC_BASE` | Qubic RPC endpoint the report is built from | https://rpc.qubic.org |
| `DATA_DIR` | Storage for the RPC cache (`$DATA_DIR/raw`) | (local: `data/raw`) |
| `QDR_REGISTRY` | Path to the self-reporting registry | bundled `data/self_reporting/pools.json` |
| `QUBIC_ARBITRATOR` | Arbitrator identity used for revenue derivation | built-in default (**unverified** — run `ingest.py --verify-arbitrator`) |
| `QDR_DB` | Persistent store holding sealed epochs and history | `$DATA_DIR/qdr.db` (local: `data/qdr.db`) |
| `QDR_LIVE_MAX_AGE` | Seconds the running epoch may be stale before a request recomputes it | `300` |
| `QDR_BOB_URL` | Bob node carrying the epoch-end payouts (use your own node) | `https://bob.qubic.li/qubic` |
| `QDR_PULSE_TTL` | Seconds the live pulse is cached | `10` |
| `QDR_MINING_TTL` | Seconds a live mining reading is cached before a node is queried again | `30` |
| `QDR_MINING_SAMPLE_INTERVAL` | Seconds between mining samples written to the store by the ingest worker | `30` |
| `QDR_BURN_BACKFILL_DAYS` | Days of burn history to count on start, from before the worker existed (see below) | `4` (one epoch; `0` disables) |
| `QDR_BURN_BACKFILL_CALLS` | Call budget for that backfill | `days x 300 + 100` (~270 calls buy one day) |
| `QDR_BURN_BACKFILL_INTERVAL` | Seconds between re-checks for a finished day that is still half-measured (an idle check costs no network call) | `3600` |

### Burn history: why the live scan alone cannot fill it

Tick logs carry no timestamp. The live scan therefore dates a burn by the moment
it watched it happen, which is only true near the chain head — so a fresh
deployment's burn series begins at its own cold start, and the burn page shows a
single partial day (the first pass covers ~10,000 ticks, roughly an hour, rather
than issuing ~1,400 heavy calls against a public node before the page shows
anything).

Older days are not lost, only unfilled: `/v1/ticks/{t}/tick-data` carries a real
wall-clock time, so a past tick can be dated by measurement rather than assumed
from a tick rate. The worker therefore keeps the last **4 days** (one epoch)
filled, and `QDR_BURN_BACKFILL_DAYS` changes or disables that:

```bash
docker run -e QDR_BURN_BACKFILL_DAYS=7 ...        # more history
docker run -e QDR_BURN_BACKFILL_DAYS=0 ...        # off
python scripts/ingest.py --burn-backfill --burn-backfill-days 4   # locally
```

It defaults to **on** despite costing a few hundred calls against a public node,
because the deployment this image serves is installed by a watcher that pulls the
image and sets no environment. An opt-in flag there could never be switched on,
and the burn page would stay at a single partial day permanently — a default
nobody can reach is not a choice. The cost is paid once per day of history, not
once per restart.

How much history is available at all is the node's decision, not this setting's:
measured 2026-09-20, `bob.qubic.li` retained **4.2 days** (its `initialTick` to
its current indexing tick). Asking for more is not an error — the window is
clamped to what the node still holds — but it cannot conjure ticks the node has
dropped.

A day of chain is ~134,000 ticks at the measured 1.55 ticks/s and one call
covers 500, so **a day costs ~270 calls**; the budget scales with the days asked
for unless `QDR_BURN_BACKFILL_CALLS` overrides it. A run that exhausts its budget
stops partway and is *not* recorded as done, so the next pass continues it.

It does not re-scan work already done, and it does not stop watching either.
Two questions, asked in that order:

- **"Is the last complete day in the report?"** — checked against the measurement
  itself: a day counts as done when its stored buckets cover ≥95% of that day's
  own measured tick span, the same threshold the chart uses for its `partial`
  flag, so the two can never disagree. Today is excluded, because it is still
  running and the live scan owns it.
- **"Did we already scan this stretch of chain?"** — a completed tick window is
  recorded in the store, so a host that reinstalls the image on every push does
  not re-scan the same ticks.

The day question has to come first, and not only for correctness: a new day lies
outside every recorded tick window, so the window marker alone would never notice
that midnight had turned the running day into a half-measured finished one. The
worker therefore re-checks every hour (`QDR_BURN_BACKFILL_INTERVAL`). An idle
check costs **one local SQLite query and no network call at all** — the gap check
runs before the tick window is resolved, which is itself ~20 RPC lookups.

A run that stopped early — at a gap Bob could not serve, or on its call budget —
is *not* recorded, so the next pass resumes it rather than leaving a silent hole.
`--burn-backfill-force` recounts a window already recorded as done, and
`--burn-status` prints what each day actually holds.

Until one whole day has been measured, the chart's Epoch and Year views stay
disabled: aggregating a partial day would draw an hour as though it were a full
period, and a bar is believed faster than the footnote under it is read.

### Mining history: why it is stored

Everything else in this report can be rebuilt from the chain. Live mining state
cannot: the colony figures are served from a node's peer port, not the RPC, and a
node answers what it looks like **right now**. A reading not taken is gone.

So the ingest worker samples it on a timer and writes it to the store, and the
mining page draws that stored series rather than whatever the browser happened to
see. Two consequences worth knowing:

* **Raw samples are windowed to the last two epochs** (~2 MB total, measured at
  0.8 MB per epoch). Two and not one, because `solution_count` resets to zero at
  an epoch boundary — a one-epoch window would delete the previous peak at exactly
  the moment the drop appears.
* **`mining_epochs` keeps one row per epoch forever** (~100 bytes per 4.4 days):
  final count, peak, mean threshold. Pruning costs resolution, never history.
  Same split as `burn_buckets` (detail, windowed) and `burn_totals` (anchor,
  permanent).

Nothing is interpolated and nothing is back-filled before the first sample. A
gap in the curve is a gap in the measurement, and is shown as one.

## Status

Running live at **https://report.qubic.tools** — four pages, 230 tests, version 0.6.0.

**v0.6** added the burn measurement and moved the design into one stylesheet:

- **Burned supply per day and per contract.** The RPC publishes one cumulative counter
  that only steps at an epoch boundary, so a daily figure has to be counted from a node's
  own burn events. Two event shapes are counted and reported separately, because they
  answer different questions: transfers to the burn address (netted, since most
  1,000,000 QU round trips come straight back) and `BURNING` events, which are the only
  source that says what a burn was *for*.
- **Historical backfill.** Tick logs carry no timestamp, so the live scan can only date
  what it watches happen and a new deployment's series began at its own cold start.
  `/v1/ticks/{t}/tick-data` carries a real wall-clock time, so past ticks are dated by
  bisection — ~20 lookups per day boundary, no tick-rate assumption anywhere. The worker
  fills the last 4 days on start and re-checks hourly, so a finished day cannot be left
  half-measured.
- **Coverage is a measurement over a measurement.** A day's denominator is that day's own
  measured tick span, not a constant: the real rate averages 1.55 ticks/s and varies 29%
  between days, which used to make complete days report 50-65% coverage.

**v0.5** added the live mining page and the worker's mining sampler. **v0.4** made the
image run its own ingest and follow the host's container conventions, and kept the service
serving when the data volume cannot be written. **v0.3** made a deploy retire the figures
its own code corrects: every start re-derives epochs computed by an older `code_version`.
`/v1/log` and `/log.html` came alongside, so an operator without shell access can tell
"still building" from "broken".

**v0.2** reworked the analysis after community feedback (see
[`docs/CONCEPT.md`](docs/CONCEPT.md) §7.1):

- **On-chain linkage is now the primary attribution layer**, not an optional hook. Slots
  are `unattributed` only when the ledger shows no link — every report states its
  `linkage_coverage` so a reader can tell resolved from assumed.
- **Full transaction tracking** for revenue: epoch-scoped tick windows, exhaustive
  pagination, reconciliation, and a `revenue_provenance` block so a third party can locate
  a divergence rather than argue about totals.
- **Persistent history**: closed epochs are sealed in a SQLite store and served from disk;
  only the running epoch touches the network. Recomputes under a new `code_version` land
  beside the old ones instead of overwriting them.

- **Forward-compatible by design**: no network constant is hardcoded (the UI language files
  use `{slots}` / `{epoch}` placeholders filled from live data, so translations never go
  stale), new pools need no code change, unknown confidence levels degrade honestly, and API
  responses stay a constant size as history grows. Covered by `tests/test_forward_compat.py`.

### Live-chain findings (2026-09-07)

Running the pipeline against the live network produced substantive results, recorded in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) §6–§8.

**Per-computor revenue is not exposed by the public RPC.** The arbitrator identity carried
since v0.1 paid **0 of 676** computors, and scanning ~4,500 transactions per computor across a
full epoch found no inbound payments — what computor identities emit is `amount = 0` solution
submissions. Revenue is credited by protocol-level emission, so "track the arbitrator's
payouts" cannot work against the public RPC however completely it paginates.

**A Bob node has it.** Bob keeps the full event log, including the virtual end-epoch tick
where the protocol credits every computor. One call per epoch
(`qubic_getEndEpochLogs`) returns the settlement, and history reaches back to at least epoch
220 — so the report starts with real history instead of from zero:

| Epoch | Computors paid | Revenue |
|---|---|---|
| 225 | 676 / 676 | 357,094,835,754 QU |
| 226 | 676 / 676 | 360,401,553,845 QU |
| 227 | 676 / 676 | 180,603,819,833 QU |
| 228 | 676 / 676 | 178,477,462,349 QU |

Set `QDR_BOB_URL` to your own node (RPC port 40420) rather than depending on the public one.
Balance deltas from our own snapshots remain the fallback when no Bob node is reachable.

**No on-chain linkage between computors is currently provable.** All 676 payouts come from
the same null address (protocol emission, not a wallet), and computors' own outgoing
transfers are uniformly 1,000,000 QU burns to that address. Both are traps for a naive payout
graph — one of them cost a bug that claimed 100% on-chain coverage while proving nothing. The
report states `linkage_coverage: 0` rather than implying independence, and the dashboard
warns that the Nakamoto figure is an **upper bound** on decentralization. Pool self-reports
are what would sharpen it.

**Update cadence, measured:** the tick advances ~2.7/s while the analysis changes once per
epoch (~4.4 days, though epoch length varies 1.08M–2.29M ticks). Hence two clocks —
`/v1/pulse` for the live view (poll every 15 s) and the report only when the epoch turns.

Remaining for production: filling the self-reporting registry with real pool declarations
(the single biggest lever now), a `qubic.li` Score API token as an independent cross-check,
and comparing methods with the implementation that reports converging numbers. See the
roadmap in [`docs/CONCEPT.md`](docs/CONCEPT.md) §9.

## Bounty

A community bounty (~2B QU) has been offered for a decentralization report that gets
accepted and embedded by explorers. This project targets that deliverable.

## License

MIT — see [`LICENSE`](LICENSE). Use it, fork it, embed it, ship it commercially; just keep
the copyright notice. Pool self-reports and other contributions are accepted under the same
terms via pull request.
