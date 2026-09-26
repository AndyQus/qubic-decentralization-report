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

# Bumped whenever a change alters a published figure, and at a release. The
# ingest worker compares this against each stored epoch's code_version on startup
# and re-derives anything computed by an older version, so a deployment can never
# keep serving numbers its own code no longer agrees with.
#
# A release bump therefore costs one recompute of every sealed epoch even when no
# figure changed — ~11 epochs against the RPC at the time of writing, producing
# the same numbers again. That is the price of the version being both a release
# marker and the recompute trigger; it is paid once per deployment, and the
# alternative (a second, untriggering version string) is a second thing to keep
# in sync and get wrong.
__version__ = "0.11.0"
