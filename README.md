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

# 2) sample data (no live RPC needed) -> api/sample/ + dashboard/data.js
python3 scripts/generate_sample.py

# 2b) LIVE data (run where rpc.qubic.org is reachable) -> real snapshots
python3 scripts/build_report.py --check     # test connectivity first
python3 scripts/build_report.py             # last 9 epochs

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

### Embedding in an explorer

A one-line widget, an iframe, or the raw JSON API — see [`docs/EMBEDDING.md`](docs/EMBEDDING.md)
and the live demo at `examples/embed-example.html` (or `http://localhost:8000/examples/embed-example.html`).

![dashboard preview](docs/preview_dashboard.png)

## Status

Working end-to-end on sample data: research + core engine + API + dashboard are in place.
Remaining for production: live RPC pulls (needs network access to `rpc.qubic.org`),
filling the self-reporting registry with real pool declarations, and enriching the
on-chain-linkage layer. See the roadmap in [`docs/CONCEPT.md`](docs/CONCEPT.md) §9.

## Bounty

A community bounty (~2B QU) has been offered for a decentralization report that gets
accepted and embedded by explorers. This project targets that deliverable.
