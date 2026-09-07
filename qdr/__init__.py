"""Qubic Decentralization Report — core library.

Modules:
  client      RPC ingestion (Qubic RPC 2.0) with on-disk caching.
  revenue     Per-computor revenue: epoch-scoped, fully paginated, reconciled.
  metrics     Revenue concentration metrics (Gini, HHI, top-N, Nakamoto).
  clustering  On-chain linkage (primary) + self-reporting registry -> operators.
  store       Persistent history: sealed epochs, live epoch, versioned recompute.
  pipeline    derive -> cluster -> persist, and the sealed-vs-live read path.
  report      Stateless single-epoch assembly (tests / ad-hoc recomputation).
"""

__version__ = "0.2.0"
