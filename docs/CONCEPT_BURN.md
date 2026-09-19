# Burn Report — Concept

*A standing, per-day view of Qubic's burned supply: what is actually measurable, where
the numbers come from, and how the page shows them without inventing resolution the data
does not have.*

Status: draft v0.1 · Language: English (a German copy is kept in sync at
[`CONCEPT_BURN.de.md`](CONCEPT_BURN.de.md))

---

## 1. What prompted this

`explorer.qubic.org` shows a **Burned Supply** figure. It is a single cumulative number —
53,662,829,138,067 QU when this concept was written (2026-09-19, epoch 231). It answers
"how much has been burned in total" and nothing else. There is no per-day figure, no
per-epoch figure, no trend, and no breakdown of *what* was burned for.

The ask: store the value by day and show it — by day, epoch, and year — as a chart, or as
something better than a chart.

## 2. The finding that shapes this whole design

**The public RPC's burn counter does not move between epoch boundaries.**

Measured directly, 2026-09-19:

| Sample | `burnedQus` | tick |
|---|---|---|
| t₀ | 53,662,829,138,067 | 80,822,754 |
| +94 s | 53,662,829,138,067 | 80,822,884 |
| +120 s | 53,662,829,138,067 | 80,822,919 |
| +180 s | 53,662,829,138,067 | 80,823,001 |
| +214 s | 53,662,829,138,067 | 80,823,048 |
| +275 s | 53,662,829,138,067 | 80,823,129 |

375 ticks passed over ~4.6 minutes. The counter did not change by a single QU
(`circulatingSupply` held identically flat across the same samples). Meanwhile a Bob node,
asked for the log of ticks 80,820,000–80,820,500 in the same period, reported **913 burn
transfers of exactly 1,000,000 QU each** — 913,000,000 QU burned in 500 ticks.

So burning is happening continuously; `latest-stats.burnedQus` just doesn't report it
continuously. It is an **epoch-boundary aggregate**. An epoch lasts ~4.4 days.

**The consequence for this feature.** Polling `burnedQus` every hour and storing it "by
day" would produce a table of identical numbers for four days, then one jump. Drawing that
as a daily chart and labelling the flat stretch "0 QU burned today" would be false — it
burned ~426 billion QU that day, the counter simply had not published yet. Interpolating
the jump backwards across the days would be worse: an invented curve presented as
measurement.

This project's standing rule is that nothing on the page is estimated or invented
(`README`: "No sample data, ever"). So the daily resolution has to be *earned* by counting
the events ourselves.

## 3. Where the data actually comes from

Two sources, doing two different jobs.

### 3.1 Bob node — the measurement

A Bob node's `qubic_getLogs` over a tick range carries the individual burn events. The
project already talks to Bob for computor revenue (`qdr/bob.py`), so this is an extension
of an existing integration, not a new dependency.

Two event shapes matter, and they are **not** the same thing:

**(a) `QU_TRANSFER` to the null address.** Destination
`AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAFXIB`. Measured over 500 ticks:
913 transfers, **each exactly 1,000,000 QU**. These are the computor burns this project
already documents (`DATA_SOURCES` §6, `CONCEPT` §4.2) — the uniform outflow every computor
emits. Extrapolated: ~426 G QU/day, ~2.56 T QU/epoch.

> **Implementation trap, already paid for once.** A Qubic identity is **60 characters**.
> Matching the null address on a 40-character prefix silently matches nothing and the burn
> total comes out as a clean, plausible zero. `qdr/revenue.py` has `BURN_PREFIXES` and
> `_is_burn_address()` for exactly this — reuse them, do not re-derive them.

**(b) `BURNING` log events.** A distinct log type carrying `amount` and — the valuable
part — **`contractIndexBurnedFor`**. This is the only source that says *what* a burn was
for. Measured in `qubic_getEndEpochLogs`:

| Epoch | Events | Total | By contract index |
|---|---|---|---|
| 225 | 5 | 385,481 QU | 27: 35,001 · 19: 350,000 · 12: 480 · 10: 0 · 9: 0 |
| 226 | 5 | 322,652 QU | 27: 10,009 · 19: 300,000 · 12: 0 · 10: 0 · 9: 12,643 |
| 227 | 5 | 338,122 QU | 27: 44,006 · 19: 250,000 · 12: 604 · 10: 0 · 9: 43,512 |
| 228 | 4 | 478,850 QU | 19: 450,000 · 12: 0 · 10: 0 · 9: 28,850 |

Note the scale: these are ~0.3–0.5 **million** QU per epoch, against ~2.56 **trillion** QU
from the computor burns. Both are real burns; they differ by six orders of magnitude. The
page must not add them into one bar and let the contract burns vanish — they are
interesting precisely because they are attributable.

The `BURNING` events appeared in the end-epoch logs and **not** in the normal tick sample
that was checked. Whether contract burns also occur mid-epoch is an open question (§9,
Q1) — the ingest will answer it empirically rather than the concept assuming it.

### 3.2 RPC `burnedQus` — the anchor

`GET /v1/latest-stats` → `data.burnedQus`, the same number the explorer shows. It is the
authoritative cumulative total. Its job here is **reconciliation, not resolution**: at each
epoch boundary, compare our summed events against the official delta.

```
official_delta  = burnedQus(end of epoch N) − burnedQus(end of epoch N−1)
measured_delta  = Σ our burn events in epoch N
coverage        = measured_delta / official_delta
```

Coverage is published on every response. If our event scan misses a burn category, coverage
drops below 1.0 and says so — the same discipline `linkage_coverage: 0` already applies to
operator attribution. A silent undercount is the failure mode worth engineering against.

### 3.3 Cost, honestly

Bob tick logs are heavy: **~2.6 MB for 500 ticks**, and the node caps a request at ~1000
ticks whatever range is asked for. A full 1.4 M-tick epoch is ~1,400 calls and gigabytes of
JSON. That is not a per-request operation and not even a casual per-epoch one.

Two consequences, both already the project's pattern:

1. **The worker does it, incrementally.** Each pass scans only from the last scanned tick to
   the current tick and stores the aggregate. `scan_tick_transfers()` already pages around
   the node cap; it needs a `max_calls` budget per pass and a resume pointer.
2. **Only aggregates are stored, never the raw events.** A tick-bucket row, not 913 rows
   per 500 ticks.

## 4. What gets stored

One new table, following the store's existing conventions (`qdr/store.py`): immutable
observations, upsert-with-dedupe, no silent overwrite.

```sql
-- Burn totals per tick window. The raw events are NOT kept: at ~900 burns per
-- 500 ticks the event log is gigabytes per epoch and says nothing the aggregate
-- does not. The window is what makes a figure recomputable by a third party.
CREATE TABLE IF NOT EXISTS burn_buckets (
    from_tick     INTEGER NOT NULL,   -- inclusive
    to_tick       INTEGER NOT NULL,   -- inclusive
    epoch         INTEGER NOT NULL,
    day           TEXT NOT NULL,      -- UTC date, 'YYYY-MM-DD', from event timestamps
    burned        INTEGER NOT NULL DEFAULT 0,   -- Σ QU_TRANSFER to null
    burn_events   INTEGER NOT NULL DEFAULT 0,
    contract_burned INTEGER NOT NULL DEFAULT 0, -- Σ BURNING events
    by_contract   TEXT,               -- json {contractIndex: amount}
    scanned_at    INTEGER NOT NULL,
    PRIMARY KEY (from_tick, to_tick)
);
CREATE INDEX IF NOT EXISTS ix_burn_epoch ON burn_buckets(epoch);
CREATE INDEX IF NOT EXISTS ix_burn_day   ON burn_buckets(day);

-- The official cumulative counter, sampled at epoch boundaries. This is the
-- anchor the measured buckets are reconciled against, and the only figure that
-- is directly comparable to what explorer.qubic.org shows.
CREATE TABLE IF NOT EXISTS burn_totals (
    epoch         INTEGER PRIMARY KEY,
    burned_total  INTEGER NOT NULL,   -- burnedQus as reported at epoch close
    circulating   INTEGER,
    tick          INTEGER,
    observed_at   INTEGER NOT NULL
);
```

**Why a day column and not just ticks.** "Per day" is what was asked for, and a tick number
is not a date. Bob's log entries carry a `timestamp` (`"26-09-02 12:00:05"` in the observed
data), so the day is read off the events rather than computed from an assumed tick rate.
Where a bucket straddles midnight it is split at the boundary — a bucket is a scan unit,
not a reporting unit.

**Aggregation for the views.** Day / epoch / year are all `SUM(burned) GROUP BY` over this
one table. Nothing is stored three times.

### 4.1 The honesty rule for history

The worker can only measure burns from **the first tick it ever scanned**. Everything before
that has no per-day figure and never will — Bob's history is finite and rescanning a year of
ticks is not proportionate.

So the series has two clearly separated regimes, and the API labels every point:

| Regime | Source | Resolution | Label |
|---|---|---|---|
| Before first scan | `burnedQus` deltas at epoch boundaries | per epoch (~4.4 d) | `measured: false` |
| From first scan on | Bob events, aggregated | per day / per hour | `measured: true` |

The chart draws the older regime as epoch-wide steps and the newer as a daily curve, and
says which is which. It does **not** smooth the old regime into fake daily values. A reader
must be able to see where real measurement begins.

## 5. API

Three endpoints, matching the existing `/v1` conventions (read-only, CORS-open, every
response naming its provenance).

```
GET /v1/burn/latest
    { burned_total, circulating, burned_share, epoch, tick,
      rate: { per_day, per_epoch, measured_over_ticks },
      last_24h: { burned, events, measured },
      coverage: { measured_delta, official_delta, ratio },
      source: "rpc:latest-stats + bob:getLogs", observed_at }

GET /v1/burn/series?by=day|epoch|year&limit=90
    { by, series: [ { key, from_tick, to_tick, burned, events,
                      contract_burned, cumulative, measured } ],
      first_measured_day, generated_at, code_version }

GET /v1/burn/contracts?epoch=231
    { epoch, total, by_contract: [ { index, burned, events, share } ] }
```

`/v1/burn/latest` is the cheap one a live page polls; it is cached like `/v1/pulse`
(TTL a few seconds) so many viewers collapse into one upstream read. The series endpoint is
windowed by default — history only grows, and `DEFAULT_TIMESERIES_EPOCHS` already sets the
precedent.

The dashboard bundle (`/v1/dashboard-data`) stays untouched. Burn is its own page with its
own fetches; folding it into the bundle would make the main report's payload grow for data
it does not show.

## 6. The page: `dashboard/burn.html`

A sibling of `mining.html` — same design tokens, same theme handling, same i18n mechanism,
no build step, no CDN, no external library. It is linked from the header next to
"Mining Live".

### 6.1 Hero — the furnace

The top of the page is a live burn visualization, and its rule is: **every particle is a
measured event, never decoration.**

- A counter showing the cumulative burned total, animated as an odometer rolling up.
- Beneath it, a furnace: each 1,000,000 QU burn event the last scan actually recorded
  drops in as a spark and is consumed. 913 events per 500 ticks is roughly 5/second —
  enough to look genuinely alive without being faked.
- When the scan reports nothing, **nothing falls**. A dead furnace is a true statement
  about the data, and is infinitely better than a decorative loop that burns pretend QU
  while the node is unreachable.
- The burn rate (QU/day) and the share of total supply burned sit next to it as plain
  figures, because that is what people will quote.

Motion is capped and `prefers-reduced-motion` turns the particles off entirely, leaving the
counter and figures — the page must be fully readable without animation.

### 6.2 The series — chart

Below the hero, the part that was actually asked for: the stored history, with a
segmented control for **Day · Epoch · Year** and a toggle for **burned per period** vs.
**cumulative**.

Drawn as inline SVG, hand-rolled, like the rest of the dashboard's charts:

- Bars for per-period, a line for cumulative.
- The pre-measurement regime renders in a visibly different treatment (hatched / lower
  contrast) with a marker at "measurement starts here". The legend names both.
- Hover/tap gives a tooltip with the exact figure, the tick window behind it, and whether
  that point was measured or derived from the epoch counter.
- On a phone the chart keeps its own horizontal scroll container; the page body never
  scrolls sideways.

### 6.3 Contract burns

A small separate panel, not mixed into the main bars — the six-orders-of-magnitude scale
difference would make them invisible. A ranked list per contract index with its share, and
the honest note that a contract index is not yet a contract *name*: mapping index → name
needs a source we do not have yet (§9, Q2). Until then the index is shown as the index.

### 6.4 Mobile

Same approach as the existing pages: one column below ~700 px, the hero counter scaling
down with `clamp()`, the segmented control becoming full-width, touch targets ≥ 44 px, and
the particle count reduced on small screens (a phone GPU does not need 900 sprites).

### 6.5 Languages

DE and EN, via the `data-i18n` attribute dictionary the other pages already use
(`mining.html` has ~90 keys, `index.html` ~46). Language follows the same persistence and
`navigator.language` fallback. Both dictionaries ship complete — a missing key must never
fall back to showing a raw key name to a reader.

## 7. How it runs

The ingest worker gains one more job, alongside the ones `scripts/worker.sh` already owns:

```
scripts/ingest.py --burn-scan          # advance the burn scan to the current tick
scripts/ingest.py --burn-scan --from-tick N
scripts/ingest.py --burn-status        # what has been scanned, and coverage
```

In the watch loop it runs after the live-epoch refresh, with a per-pass call budget so one
pass cannot monopolise the worker or the public Bob node. It records its resume pointer, so
a restart continues rather than rescanning.

At each epoch boundary it samples `burnedQus` into `burn_totals` and computes coverage for
the epoch that just closed. A coverage figure that drifts away from 1.0 is a finding, not
an error to hide — it means a burn category exists that the scan does not yet recognise,
and it belongs on the page and in `/v1/burn/latest`.

## 8. Build order

Each step is independently useful and independently verifiable.

| # | Step | Done when |
|---|---|---|
| 1 | `burn_totals` + epoch-boundary sampling in the worker | `/v1/burn/latest` returns the official total and matches the explorer |
| 2 | `burn_buckets` + Bob scan (incremental, budgeted, resumable) | a day's burn total is measured from events, tests cover the 60-char address trap |
| 3 | `/v1/burn/series` with day/epoch/year + the measured/derived split | series answers all three granularities, labels every point |
| 4 | `dashboard/burn.html` — chart first, DE/EN, mobile | the chart is correct and readable on a phone before any animation exists |
| 5 | The furnace hero, driven by real event counts | particles stop when the data stops |
| 6 | Contract-burn panel | per-index breakdown with shares |

Chart before animation is deliberate: the chart is the deliverable, the furnace is what
makes people look at it. Shipping them in the other order risks a beautiful page with a
wrong number in it.

## 9. Open questions

**Q1 — Do `BURNING` events occur mid-epoch?** Observed in `qubic_getEndEpochLogs` (4–5 per
epoch); the 500-tick sample of normal ticks contained none. The scan will count them
wherever they appear; the answer gets recorded here once a full epoch has been scanned.
Until then, `contract_burned` may only reflect epoch-boundary events.

**Q2 — What are the contract indices?** Measured: 9, 10, 12, 19, 27. Index 19 is the
largest and steadiest (250k–450k QU/epoch). Mapping index → contract name needs a source
(core source, or the community). Until there is one, the page shows indices, not guessed
names.

**Q3 — Is the computor burn the *whole* non-contract burn?** Every null-address transfer in
the sample was exactly 1,000,000 QU, which matches the known uniform computor outflow. If
coverage (§3.2) lands near 1.0 the answer is yes; if it lands short, another burn path
exists and the gap will point at it. This is precisely why coverage is published rather
than assumed.

**Q4 — How far back can Bob serve?** Determines how much of the "before measurement" regime
could be converted to measured data by a one-off backfill. Worth one experiment before
deciding it is not proportionate.

## 10. What this is not

It does not claim to explain *why* Qubic burns what it burns, it does not forecast, and it
does not present the burn rate as a valuation signal. It measures a published quantity at a
finer resolution than it is published, states how it did so, and shows where its own
measurement starts. That is the same contract the rest of this report operates under.
