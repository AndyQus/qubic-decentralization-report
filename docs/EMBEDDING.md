# Embedding the report in an explorer

The report is explorer-agnostic: you provide the data as an open JSON API, an explorer
consumes it. There are three integration levels, cheapest first.

## 1. The widget (one script tag)

A compact, dependency-free summary card (operators, Nakamoto ⅓, top operator, proportion
bar). Good for a homepage or a sidebar.

```html
<div id="qdr" data-api="https://report.example" data-lang="en" data-theme="dark"></div>
<script src="https://report.example/dashboard/embed.js"></script>
<script>QDRWidget.render(document.getElementById('qdr'));</script>
```

Or fully declarative — add `data-qdr-auto` to the container and just include the script;
every `[data-qdr-auto]` element renders on load. Options: `data-api`, `data-lang`
(`en`/`de`), `data-theme` (`dark`/`light`). See `examples/embed-example.html`.

## 2. The iframe (full dashboard)

Embed the whole interactive dashboard (treemap, timeline, table):

```html
<iframe src="https://report.example/dashboard/?api=https://report.example"
        style="width:100%;height:1200px;border:0" loading="lazy"></iframe>
```

If the dashboard is served from the same host as the API (it is, at `/dashboard/`), you can
drop the `?api=` — it defaults to the same origin.

## 3. The raw API (build your own UI)

Consume the JSON directly and render it in your own design. CORS is open, everything is
versioned under `/v1`, and every response names its inputs so you can show provenance.

| Endpoint | Use |
|---|---|
| `GET /v1/report/latest` | headline + clusters for the current epoch |
| `GET /v1/report/{epoch}` | a specific epoch |
| `GET /v1/clusters/{epoch}` | just the operator clusters |
| `GET /v1/metrics/timeseries` | concentration indices across epochs (charts) |
| `GET /v1/dashboard-data` | one bundle: report + timeseries + per-epoch operator bubbles |
| `GET /v1/report/{epoch}/snapshot.json` | frozen archivable snapshot |

### Cluster object

```jsonc
{
  "cluster_id": "jetski",
  "label": "JetSki Pool",
  "confidence": "declared",      // declared | unattributed | linked | flagged
  "computor_count": 198,
  "revenue": 277000000000,
  "revenue_share": 0.294,
  "evidence": ["self-reported registry"]
}
```

### Headline metrics (`concentration_by_revenue` / `concentration_by_slots`)

`gini`, `hhi_normalized`, `top1_share` / `top3_share` / `top5_share`,
`nakamoto_one_third`, `nakamoto_half`. The Nakamoto coefficient is the headline
decentralization number: how many operators must collude to cross the threshold.

## Provenance & trust

Every report carries `data_sources`, `code_version`, `generated_at`, and
`reproducible: true`. Nothing is opaque — a skeptic can re-run the pipeline from public
Qubic RPC data plus the self-reporting registry and get the same numbers. That is the point
of the report: it measures concentration neutrally rather than asserting it.
