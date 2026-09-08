# Announcement drafts

Copy-paste text for `#computor-operator`. The long version is the post; the short
one is for a follow-up or a reply. Both set the same expectation deliberately:
revenue is measured, attribution is not — and that is the finding.

---

## Long version (the post)

**Qubic Decentralization Report — live, and asking pools to declare their slots**

CFB asked for a report showing revenue metrics and their dynamics, plus clustering with
the number of computors in each cluster. Here is the first part, running on live data —
and an honest account of why the second part is still empty.

**What it measures today**

Revenue per computor slot, per epoch, from a Bob node's end-epoch log. Epochs 225-228 are
sealed with 676/676 computors paid each. This matters because the public RPC does not
expose these payouts at all: computor revenue is protocol emission, not a transfer, so
"track the arbitrator's payouts" cannot work however completely it paginates. A Bob node
keeps the full event log including the virtual end-epoch tick, and one call per epoch
returns the whole settlement.

On top of that: Gini, HHI, Nakamoto ⅓ and ½, top-N share, per epoch and as a time series.

One measured result worth stating on its own — **61.8% of slots in epoch 228 were paid
exactly the same amount** (268,701,925 QU), and the highest-earning slot took 3.02× the
lowest. Payouts are close to flat per slot.

**What it does not measure, and why**

Who operates those slots. Both attribution layers currently resolve nothing:

- *On-chain linkage* is implemented and finds nothing provable. Every computor is credited
  by the same null address, and computors' outgoing transfers are uniformly 1,000,000 QU
  burns to that same address. No transfer graph links two computors. The report says
  `linkage_coverage: 0` rather than dressing that up as independence.
- *Self-reporting* — CFB's own anti-Sybil point — is empty. No pool has declared its slots.

So every figure is labelled for what it actually counts: **slots, not operators**. A
Nakamoto coefficient of 222 means 222 of 676 *slots*. If one operator holds several, real
concentration is higher than the number shown. The report will not claim otherwise.

**This is where you come in**

The clustering machinery is built and runs every epoch. It needs declarations, not more
code. A pool opens a pull request against `data/self_reporting/pools.json` listing the
identities it controls; the operator view — treemap, per-operator table, operator-level
Nakamoto — turns itself on for that pool with no code change. Git history is the audit
trail, and entries can be corrected or completed at any time.

Entries are ready and waiting for qubic.li, Apool and MinerLab/Solutions. Any other
operator can add one.

Until then the report measures something real and uncomfortable: **self-reporting adoption
is currently zero.** Making that visible is the point — the tool does not accuse anyone, it
makes concentration measurable so the community can judge for itself.

Repo: <link> · Live: <link> · Explorers: embed format in `docs/EMBEDDING.md`

Feedback welcome, especially on the revenue derivation — the method is documented in
`docs/DATA_SOURCES.md` so it can be checked against your own numbers rather than argued
about.

---

## Short version (reply / follow-up)

The decentralization report is live on real data: revenue per computor slot per epoch from
a Bob node, epochs 225-228 sealed at 676/676 computors paid, with Gini / Nakamoto /
top-share and their dynamics.

What it cannot show yet is *who* runs those slots. On-chain linkage finds nothing provable
(payouts are protocol emission from a null address; computor outflows are uniform burns),
and no pool has self-declared. So every figure says "slots, not operators" — Nakamoto 222
means 222 of 676 slots, and real concentration is higher wherever one operator holds
several.

The clustering is built and waiting on declarations, not code. Pools: one pull request
against `data/self_reporting/pools.json` and the operator view turns on for you.

Repo: <link>

---

## X / Twitter

### Thread (5 posts)

**1/**
The Qubic Decentralization Report is live.

Revenue per computor slot, per epoch, measured from the chain. Epochs 225-228 sealed,
676/676 computors paid each.

And an honest answer to the harder question: we still cannot tell you who operates
those slots. 🧵

**2/**
Why this was hard: the public RPC does not expose computor payouts at all.

They are protocol emission, not transfers — ~4,500 txs per computor per epoch show no
inbound payments, yet balances grow by 0.5-1.6B QU.

A Bob node's end-epoch log carries it.

**3/**
One measured result, epoch 228:

61.8% of all 676 slots were paid exactly the same amount — 268,701,925 QU.
Highest-earning slot: 3.02× the lowest.

Payouts are close to flat per slot. Gini 0.014.

**4/**
What it will NOT tell you: how many independent operators there are.

Every computor is credited by the same null address, and their outflows are uniform
1,000,000 QU burns. No transfer graph links two computors.

The ledger discloses no ownership.

**5/**
Every figure is therefore labelled slots, not operators.

Nakamoto ⅓ = 222 means 222 of 676 *slots*. If one operator holds several, real
concentration is higher.

Pools: declare your slots via PR and the operator view turns on for you.

<repo link>

### Single post (if you prefer one)

Qubic Decentralization Report — live on chain data.

Revenue per computor slot, per epoch. In epoch 228, 61.8% of slots were paid
identically.

What it won't claim: who operates them. The ledger discloses no ownership — so every
figure says "slots, not operators".

<repo link>

---

## Discord — short version for a general channel

**Qubic Decentralization Report is live** 📊

Revenue per computor slot, per epoch, straight from chain data — epochs 225-228
sealed with 676/676 computors paid each. Plus Gini, Nakamoto ⅓/½ and how they move
over time.

One thing it measured that I did not expect: **61.8% of slots in epoch 228 were paid
exactly the same amount** (268,701,925 QU), with the top slot earning 3.02× the
lowest. Payouts are close to flat per slot.

One thing it deliberately does **not** claim: how many independent operators are
behind those 676 slots. Every computor is credited by the same null address and their
outflows are uniform burns, so the ledger discloses no ownership — and no pool has
self-declared yet. Every figure on the page is labelled **slots, not operators**:
Nakamoto ⅓ = 222 means 222 of 676 slots, and wherever one operator holds several, the
real concentration is higher.

That last part is where the community comes in. The clustering is built and runs every
epoch — it needs declarations, not more code. One PR against
`data/self_reporting/pools.json` and the operator view switches on for your pool.

Repo: <link> · Live: <link>
