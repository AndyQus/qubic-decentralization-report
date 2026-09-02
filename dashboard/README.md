# Dashboard

The reference front-end for the Qubic Decentralization Report. A single self-contained
`index.html` (vanilla JS, no build step, no external dependencies) — embeddable anywhere.

![preview](../docs/preview_dashboard.png)

## Features

- **Operator map** — an animated squarified treemap; each tile is one operator, area =
  share of the epoch. Toggle *by revenue* / *by slots*. Play or scrub through epochs and
  the tiles animate.
- **Concentration over time** — Nakamoto coefficient (⅓) and Gini across epochs, with the
  `Nakamoto = 1` danger line marked.
- **Operator table** — every operator this epoch with slots, basis (declared / unattributed
  / on-chain linked) and revenue share.
- **Dark / light** toggle, dark by default.
- **DE / EN** language switch, English by default.
- Theme and language are saved to `localStorage` (safe fallback if storage is blocked).

## Running it

**Just open it:** double-click `index.html`. It reads bundled data from `data.js` (works
over `file://` because it is a `<script>`, not a `fetch`).

**Against the live API:** append `?api=<base-url>` to the URL, e.g.
`index.html?api=http://localhost:8000`. The page then fetches `/v1/dashboard-data` and
falls back to the bundled `data.js` if the API is unreachable. You can also set
`window.QDR_API` before the app script instead of the query param.

## Data

`data.js` sets `window.QDR_DATA = { report, timeseries, epoch_clusters }`. It is generated
by `scripts/generate_sample.py` (sample data) — in production, regenerate it from live data
with the same `qdr` pipeline, or serve `/v1/dashboard-data` from the API and load via
`?api=`.

## Embedding in an explorer

Drop the `#kpis`, `.treemap`, chart, or table sections into a host page, or iframe the whole
dashboard. The data contract is the API's `/v1/dashboard-data` (and the per-resource
endpoints); nothing here is Qubic-explorer-specific.
