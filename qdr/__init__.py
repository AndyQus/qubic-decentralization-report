"""Qubic Decentralization Report — core library.

Modules:
  client      RPC ingestion (Qubic RPC 2.0) with on-disk caching.
  bob         Bob-node client: the epoch-end payouts the public RPC hides.
  revenue     Per-computor revenue: epoch-scoped, fully paginated, reconciled.
  metrics     Revenue concentration metrics (Gini, HHI, top-N, Nakamoto).
  clustering  On-chain linkage (primary) + self-reporting registry -> operators.
  store       Persistent history: sealed epochs, live epoch, versioned recompute.
  pipeline    derive -> cluster -> persist, and the sealed-vs-live read path.
  report      Stateless single-epoch assembly (tests / ad-hoc recomputation).
"""

# Bumped whenever a change alters a published figure. The ingest worker compares
# this against each stored epoch's code_version on startup and re-derives anything
# computed by an older version, so a deployment can never keep serving numbers its
# own code no longer agrees with.
__version__ = "0.4.3"
