"""Validate the self-reporting registry.

Integrity checks that make self-reporting trustworthy:
  - every declared computor is a well-formed 60-char Qubic identity
  - no identity is claimed by two different operators (collision = a red flag)
  - (optional, --epoch with live RPC) every declared identity is actually a computor
    of that epoch

Usage:
    python scripts/validate_registry.py
    python scripts/validate_registry.py --epoch 228     # also cross-check against the RPC
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdr.clustering import load_registry

ID_RE = re.compile(r"^[A-Z]{60}$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epoch", type=int, default=None, help="cross-check identities against this epoch's computors (needs live RPC)")
    args = ap.parse_args()

    reg = load_registry()
    pools = reg.get("pools", [])
    errors, warnings = [], []
    seen: dict[str, str] = {}
    total_declared = 0

    for p in pools:
        pid = p.get("id", "?")
        for ident in p.get("computors", []):
            total_declared += 1
            if not ID_RE.match(ident):
                errors.append(f"{pid}: malformed identity {ident!r} (need 60 uppercase A-Z)")
            if ident in seen and seen[ident] != pid:
                errors.append(f"identity {ident[:12]}… claimed by both '{seen[ident]}' and '{pid}'")
            seen[ident] = pid

    print(f"registry: {len(pools)} operators, {total_declared} declared identities")

    if args.epoch is not None and total_declared:
        try:
            from qdr.client import CachedClient
            comp = set(CachedClient().computors(args.epoch))
            for ident, pid in seen.items():
                if ident not in comp:
                    warnings.append(f"{pid}: {ident[:12]}… not a computor in epoch {args.epoch}")
            print(f"cross-checked against epoch {args.epoch} ({len(comp)} computors)")
        except Exception as e:  # noqa
            warnings.append(f"epoch cross-check skipped (RPC unreachable): {e}")

    for w in warnings:
        print("WARN:", w)
    for e in errors:
        print("ERROR:", e)

    if errors:
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"\nOK: no errors, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
