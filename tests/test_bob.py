"""Bob-node revenue parsing and the linkage rules that live data forced.

Bob is the only public source carrying the protocol's epoch-end computor payouts
(DATA_SOURCES §6). These tests pin the parsing and, more importantly, the two
rules that live data taught us:

  * every computor is credited by the SAME null address, so "shares a payout
    source" links the whole network and must prove nothing;
  * computors' own outgoing transfers are 1,000,000 QU burns to the null address,
    so a naive forward-trace would cluster all 676 into one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdr.bob import computor_revenue, iter_qu_transfers, payout_sources
from qdr.revenue import RevenueResult, forward_linkage, is_uninformative_identity, payout_linkage

NULL = "A" * 56 + "FXIB"
C1, C2, C3 = "C1" + "X" * 58, "C2" + "X" * 58, "C3" + "X" * 58
WALLET = "W1" + "Y" * 58


def end_epoch_entry(frm, to, amount, tick=100):
    """The end-epoch shape: payload under `body`, type as `logTypename`."""
    return {"logTypename": "QU_TRANSFER", "tick": tick, "txHash": f"tx{frm}{to}{amount}",
            "body": {"from": frm, "to": to, "amount": str(amount)}}


def tick_entry(frm, to, amount, tick=200):
    """The tick-log shape: flat, `source`/`destination`, `logTypeName`."""
    return {"logTypeName": "QU_TRANSFER", "tick": tick, "logId": f"{frm}{to}{amount}",
            "source": frm, "destination": to, "amount": amount}


def test_parses_both_log_shapes():
    """One parser has to cover end-epoch and tick logs; they differ in shape."""
    both = iter_qu_transfers([end_epoch_entry(NULL, C1, 500),
                              tick_entry(C1, WALLET, 700)])
    assert len(both) == 2
    assert both[0]["sourceId"] == NULL and both[0]["amount"] == 500
    assert both[1]["destId"] == WALLET and both[1]["amount"] == 700


def test_non_transfer_and_zero_amount_entries_are_ignored():
    logs = [end_epoch_entry(NULL, C1, 0),
            {"logTypename": "BURNING", "body": {"from": NULL, "to": C1, "amount": "9"}},
            end_epoch_entry(NULL, C1, 5)]
    assert len(iter_qu_transfers(logs)) == 1


def test_computor_revenue_covers_every_slot():
    """An unpaid computor must be a zero, not a missing key — otherwise it is
    indistinguishable from a slot we failed to read."""
    rev, matched = computor_revenue([end_epoch_entry(NULL, C1, 100)], [C1, C2])
    assert rev == {C1: 100, C2: 0}
    assert len(matched) == 1
    assert payout_sources(matched) == {NULL: 1}


def test_epoch_payout_leg_yields_no_linkage():
    """Measured on epoch 228: all 676 computors are credited by the same null
    address. Grouping on that would 'link' the entire network — or, as the first
    implementation did, link each computor to itself and claim 100% coverage."""
    _, matched = computor_revenue(
        [end_epoch_entry(NULL, C1, 10), end_epoch_entry(NULL, C2, 10)], [C1, C2])
    result = RevenueResult(epoch=228, revenue={C1: 10, C2: 10}, transfers=matched)
    assert payout_linkage(result) == {}


def test_forward_linkage_groups_computors_sharing_a_destination():
    """The real signal: two computors paying out to one wallet are one owner."""
    moves = iter_qu_transfers([tick_entry(C1, WALLET, 900), tick_entry(C2, WALLET, 800),
                               tick_entry(C3, "Z" * 60, 700)])
    linkage = forward_linkage(moves, [C1, C2, C3])
    assert linkage.get(C1) == WALLET and linkage.get(C2) == WALLET
    assert C3 not in linkage          # single sender: no shared owner shown


def test_forward_linkage_ignores_burns_and_universal_destinations():
    """Computors emit 1,000,000 QU burns to the null address every few ticks; a
    destination shared by (nearly) everyone is infrastructure, not an operator."""
    assert is_uninformative_identity(NULL)
    burns = iter_qu_transfers([tick_entry(c, NULL, 1_000_000) for c in (C1, C2, C3)])
    assert forward_linkage(burns, [C1, C2, C3]) == {}
    # a normal wallet that every single computor pays is also not a finding
    everyone = iter_qu_transfers([tick_entry(c, WALLET, 5) for c in (C1, C2, C3)])
    assert forward_linkage(everyone, [C1, C2, C3]) == {}


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} bob tests passed")
