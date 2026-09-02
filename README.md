# Qubic Decentralization Report

A tool that measures **how decentralized Qubic actually is** and publishes the result as an
API that explorers can embed, plus a reference dashboard.

It answers the request made by CFB in `#computor-operator`: a report showing **revenue
metrics and their dynamics** and **clustering with the number of computors in each
cluster** — built on Qubic's **self-reporting** anti-Sybil approach and extended with
on-chain and behavioral signals to quantify concentration/collusion among the 676
Computors.

> Full rationale and design: [`docs/CONCEPT.md`](docs/CONCEPT.md) (English) ·
> [`docs/CONCEPT.de.md`](docs/CONCEPT.de.md) (Deutsch). Both versions are kept in sync —
> any change to one is mirrored in the other.

## How the pieces fit together

```
  Data sources                Tool (this repo)                 Consumers
  ────────────                ────────────────                 ─────────
  Qubic RPC 2.0        ┐
  Pool self-reporting  ├──▶  ingest → analyze ──▶  JSON API ──▶  Explorers (embed it)
  On-chain ledger      ┘        │                     │
  (behavioral signals)          └── reference dashboard (own UI, animated + DE/EN + dark/light)
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
data/         Cached raw pulls (data/raw) + the self-reporting registry
dashboard/    Reference front-end: charts, animations, DE/EN i18n, dark/light
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
python3 tests/test_metrics.py && python3 tests/test_report.py

# 2) generate sample data (no live RPC needed) -> api/sample/ + dashboard/data.js
python3 scripts/generate_sample.py

# 3a) open the dashboard: just open dashboard/index.html in a browser
# 3b) or run the API and point the dashboard at it:
uvicorn api.server:app --port 8000
#     then open dashboard/index.html?api=http://localhost:8000
```

![dashboard preview](docs/preview_dashboard.png)

## Status

Working end-to-end on sample data: research + core engine + API + dashboard are in place.
Remaining for production: live RPC pulls (needs network access to `rpc.qubic.org`),
filling the self-reporting registry with real pool declarations, and enriching the
on-chain-linkage layer. See the roadmap in [`docs/CONCEPT.md`](docs/CONCEPT.md) §9.

## Bounty

A community bounty (~2B QU) has been offered for a decentralization report that gets
accepted and embedded by explorers. This project targets that deliverable.
