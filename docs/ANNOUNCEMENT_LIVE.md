# Announcement — the report is live at report.qubic.tools

Copy-paste text for the moment the page is actually reachable, as distinct from
`docs/ANNOUNCEMENT.md`, which announced the project itself.

All figures below were read from the live deployment before writing, not carried
over from the earlier draft:

| | |
|---|---|
| Epochs sealed | 220–228 (9) |
| Latest sealed | 228 |
| Computors | 676 |
| Revenue, epoch 228 | 178,477,462,349 QU |
| Identical payouts | 418 of 676 slots (61.8%) at 268,701,925 QU |
| Spread | 3.02× highest over lowest |
| Top slot | 0.15% of revenue |
| Gini | 0.0139 |
| Nakamoto ⅓ / ½ | 222 / 333 slots |
| Declared operators | 0 |

Replace `<link>` with https://report.qubic.tools/dashboard/ before posting.

---

## X / Twitter

### Single post (recommended)

> Qubic Decentralization Report — live: report.qubic.tools
>
> Revenue per computor slot, per epoch, measured from the chain. 9 epochs
> sealed, 676/676 computors paid in each.
>
> In epoch 228, 61.8% of slots were paid exactly the same amount.
>
> What it won't claim: who operates them.

*(280/280 with X counting the URL as 23 characters.)*

### Thread (4 posts)

**1/**

> The Qubic Decentralization Report is live.
>
> report.qubic.tools
>
> Revenue per computor slot, per epoch, from the chain. Epochs 220-228 sealed,
> 676/676 computors paid in each.
>
> And the harder question, answered honestly: we cannot tell you who operates
> those slots. 🧵

**2/**

> Why that took work: the public RPC does not expose computor payouts at all.
>
> They are protocol emission, not transfers — a computor shows no inbound
> payment, yet its balance grows by hundreds of millions of QU per epoch.
>
> A Bob node's end-epoch log carries the settlement.

**3/**

> One measured result, epoch 228:
>
> 418 of 676 slots — 61.8% — were paid exactly the same amount, 268,701,925 QU.
> The highest-earning slot took 3.02× the lowest, and holds 0.15% of revenue.
>
> Gini 0.0139. Payouts are close to flat per slot.

**4/**

> What it will not tell you: how many independent operators there are.
>
> So every figure says slots, not operators. Nakamoto ⅓ = 222 means 222 of 676
> *slots*. If one operator holds several, real concentration is higher.
>
> Pools: declare yours by PR and the operator view turns on.

---

## Discord

### General channel

> **The Qubic Decentralization Report is live** 📊
> https://report.qubic.tools/dashboard/
>
> Revenue per computor slot, per epoch, straight from chain data. Nine epochs
> are sealed (220–228), each with 676/676 computors paid, alongside Gini,
> Nakamoto ⅓/½ and how they move across epochs.
>
> One result worth stating on its own: in epoch 228, **418 of 676 slots were paid
> exactly the same amount** — 268,701,925 QU. The highest-earning slot took 3.02×
> the lowest and holds 0.15% of revenue. Payouts are close to flat per slot, and
> Gini sits at 0.0139.
>
> One thing the report deliberately does **not** claim: how many independent
> operators sit behind those 676 slots. Computor revenue is protocol emission
> rather than a transfer, so the ledger discloses no ownership — and no pool has
> self-declared yet. Every figure on the page is therefore labelled **slots, not
> operators**. Nakamoto ⅓ = 222 means 222 of 676 *slots*; wherever one operator
> holds several, real concentration is higher than the number shown.
>
> That last part is where the community comes in. The clustering already runs
> every epoch — it needs declarations, not more code. One PR against
> `data/self_reporting/pools.json` and the operator view switches on for your
> pool.
>
> Repo: <link>

### #computor-operator (follow-up to the earlier post)

> The report is now reachable: https://report.qubic.tools/dashboard/
>
> Since the last post it has sealed epochs 220–228, 676/676 computors paid in
> each. Epoch 228: 61.8% of slots paid identically at 268,701,925 QU, 3.02×
> between highest and lowest, Gini 0.0139, Nakamoto ⅓ at 222 of 676 slots.
>
> Self-reporting is still at zero, so the attribution layer stays empty and every
> figure remains labelled slots, not operators. Entries are ready for qubic.li,
> Apool and MinerLab/Solutions — one PR against `data/self_reporting/pools.json`
> switches the operator view on for your pool, no code change needed.
>
> The revenue derivation is documented in `docs/DATA_SOURCES.md` if you want to
> check it against your own numbers.

---

## Notes for whoever posts this

- **Do not round 61.8% to "most" or "nearly two thirds".** The exact figure is
  the point: it is measured, and a reader can verify it on the page.
- **Keep "slots, not operators" verbatim.** The dashboard uses that phrasing, so
  someone arriving from a post finds the page making the same claim.
- The single X post is preferred over the thread unless there is appetite for a
  longer read — the finding survives compression, the thread mostly adds context.
- Every X post was counted, not estimated (URL as 23 chars, X's own rule):
  single 280, 1/ 269, 2/ 272, 3/ 236, 4/ 276. The single post is exactly at the
  limit, so any edit to it has to remove as much as it adds.
- Figures move at each epoch boundary (~4.4 days). If posting more than a few
  days after this file was written, re-read them from
  `https://report.qubic.tools/v1/dashboard-data` rather than trusting the table
  above.
