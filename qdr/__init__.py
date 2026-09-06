"""Qubic Decentralization Report — core library.

Modules:
  client      RPC ingestion (Qubic RPC 2.0) with on-disk caching.
  metrics     Revenue concentration metrics (Gini, HHI, top-N, Nakamoto).
  clustering  Self-reporting registry + slot-to-operator clustering.
  report      Assemble the decentralization report for one epoch / a range.
"""

__version__ = "0.1.1"
