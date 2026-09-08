"""Entity clustering heuristics.

These are the heuristics real forensic analysts use, implemented as written in
the literature — not invented for this project:

* common-input-ownership (Bitcoin): addresses spent together as inputs to one
  transaction are almost always controlled by the same entity. The single most
  powerful clustering tool in blockchain forensics.
* change-address detection (Bitcoin): identify which output is the sender's own
  change, so the trail follows the actor rather than the payment.
* behavioral (Ethereum): common-input does not apply to an account-model chain,
  so we lean on funding source and timing correlation — which is how
  professional analysts handle the account model.

CoinJoin-like structures deliberately *reduce cluster confidence* rather than
asserting a merge. A false merge in a case file is worse than an un-merged
graph, because it attributes someone else's money to the suspect.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from backend.amounts import is_round_amount

from backend.adapters.base import NormalizedTx


class UnionFind:
    """Disjoint-set over addresses (lowercased keys)."""

    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}
        self.rank: Dict[str, int] = {}

    def add(self, item: str) -> None:
        key = item.lower()
        if key not in self.parent:
            self.parent[key] = key
            self.rank[key] = 0

    def find(self, item: str) -> str:
        key = item.lower()
        self.add(key)
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]  # path compression
            key = self.parent[key]
        return key

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1

    def groups(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = defaultdict(list)
        for item in self.parent:
            out[self.find(item)].append(item)
        return dict(out)


# --- CoinJoin-like structure ----------------------------------------------

def is_coinjoin_like(tx: NormalizedTx, min_participants: int = 3) -> bool:
    """Detect the equal-output, many-participant shape of a CoinJoin.

    A CoinJoin has many inputs and many outputs, with a large subset of outputs
    carrying identical values. Applying common-input-ownership across it would
    merge unrelated people into one "entity", so we refuse to.
    """
    if len(tx.inputs) < min_participants or len(tx.outputs) < min_participants:
        return False

    buckets: Dict[float, int] = defaultdict(int)
    for out in tx.outputs:
        buckets[round(out.amount, 8)] += 1
    if not buckets:
        return False

    largest = max(buckets.values())
    # Several participants receiving an identical amount in the same transaction
    # is the defining signature.
    return largest >= min_participants


# --- common-input-ownership ------------------------------------------------

def cluster_common_input(
    transactions: Iterable[NormalizedTx],
) -> Tuple[UnionFind, Set[str], Dict[str, List[str]]]:
    """Union the input addresses of each transaction.

    Returns the union-find, the set of addresses touched by a CoinJoin-like
    transaction, and per-address notes explaining any confidence reduction.
    """
    uf = UnionFind()
    coinjoin_touched: Set[str] = set()
    notes: Dict[str, List[str]] = defaultdict(list)

    for tx in transactions:
        addresses = [a for a in tx.input_addresses if a]
        for address in addresses:
            uf.add(address)
        if len(addresses) < 2:
            continue

        if is_coinjoin_like(tx):
            for address in addresses:
                coinjoin_touched.add(address.lower())
                notes[address.lower()].append(
                    f"Transaction {tx.tx_hash[:16]}… has the equal-output shape of a "
                    "CoinJoin-like structure; common-input ownership was not applied "
                    "to it and cluster confidence is reduced."
                )
            continue

        first = addresses[0]
        for other in addresses[1:]:
            uf.union(first, other)

    return uf, coinjoin_touched, dict(notes)


# --- change-address detection ---------------------------------------------

# A payment tends to be a round figure; change tends not to be. Shared with the
# round-split detector so the two cannot drift apart.
_is_round = is_round_amount


def detect_change_outputs(
    transactions: Sequence[NormalizedTx],
) -> Dict[str, str]:
    """Map a change address -> one of the input addresses it belongs with.

    Conservative, and deliberately so. All of these must hold:
      * the transaction has exactly two outputs (payment + change),
      * the candidate goes to an address not seen anywhere before this
        transaction — a freshly generated change address,
      * the candidate's value is not a round number while every other output is
        a round number: a payment tends to be a round figure and the change is
        whatever awkward remainder is left over,
      * exactly one output satisfies both, so there is no ambiguity about which
        one it is.

    When the signals disagree we decline to merge rather than guess. A false
    merge in a case file attributes someone else's money to the suspect.
    """
    seen_before: Set[str] = set()
    result: Dict[str, str] = {}

    ordered = sorted(transactions, key=lambda t: t.timestamp)
    for tx in ordered:
        inputs = [a for a in tx.input_addresses if a]
        outputs = tx.outputs

        if len(outputs) == 2 and inputs and not is_coinjoin_like(tx):
            input_set = {a.lower() for a in inputs}
            candidates = [
                o for o in outputs
                if o.address.lower() not in seen_before
                and o.address.lower() not in input_set
                and not _is_round(o.amount)
            ]
            if len(candidates) == 1:
                candidate = candidates[0]
                others = [o for o in outputs if o is not candidate]
                if all(_is_round(o.amount) for o in others):
                    result[candidate.address.lower()] = inputs[0]

        for address in inputs:
            seen_before.add(address.lower())
        for out in outputs:
            seen_before.add(out.address.lower())

    return result


# --- behavioral (account model) -------------------------------------------

def cluster_behavioral(
    transactions: Sequence[NormalizedTx],
    funding_window_seconds: int = 3600,
) -> Tuple[UnionFind, Dict[str, List[str]]]:
    """Group account-model addresses by shared funding source and timing.

    Two addresses that were first funded by the same address inside a short
    window behave like two arms of one operation. This is a weaker signal than
    common-input ownership, and its cluster confidence reflects that.
    """
    uf = UnionFind()
    notes: Dict[str, List[str]] = defaultdict(list)

    # address -> (funder, timestamp) for the first funding transfer seen.
    first_funding: Dict[str, Tuple[str, float]] = {}

    for tx in sorted(transactions, key=lambda t: t.timestamp):
        if len(tx.inputs) != 1:
            continue
        funder = tx.inputs[0].address
        for out in tx.outputs:
            key = out.address.lower()
            uf.add(key)
            if key not in first_funding and key != funder.lower():
                first_funding[key] = (funder.lower(), tx.timestamp.timestamp())

    by_funder: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for address, (funder, ts) in first_funding.items():
        by_funder[funder].append((address, ts))

    for funder, members in by_funder.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m[1])
        # Chain members together while consecutive fundings stay inside the window.
        run: List[str] = [members[0][0]]
        for previous, current in zip(members, members[1:]):
            if current[1] - previous[1] <= funding_window_seconds:
                run.append(current[0])
            else:
                _union_run(uf, run, funder, notes, funding_window_seconds)
                run = [current[0]]
        _union_run(uf, run, funder, notes, funding_window_seconds)

    return uf, dict(notes)


def _union_run(
    uf: UnionFind,
    run: List[str],
    funder: str,
    notes: Dict[str, List[str]],
    window_seconds: int,
) -> None:
    if len(run) < 2:
        return
    for other in run[1:]:
        uf.union(run[0], other)
    minutes = window_seconds // 60
    for address in run:
        notes[address].append(
            f"Grouped behaviourally: first funded by {funder[:16]}… together with "
            f"{len(run) - 1} other address(es) inside a {minutes}-minute window. "
            "Behavioural grouping is weaker evidence than common-input ownership."
        )
