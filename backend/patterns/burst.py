"""Shared burst-window helper.

Used by both the timing-burst pattern and the cluster-context signal so they
cannot drift apart.

The important subtlety: the definition says "multiple related **transactions** in
a tight window". A single fan-out transaction with six outputs is six graph
edges but *one* transaction, and counting edges would report it as a burst of
six — inflating a case that already scores for fan-out. Bursts are therefore
counted over distinct transaction hashes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Sequence, Set, Tuple


def largest_burst(
    edges: Sequence, window_seconds: int
) -> Tuple[List[str], List, float]:
    """Find the densest window by distinct transaction count.

    Returns (tx_hashes, edges_in_window, span_seconds).
    """
    if not edges:
        return [], [], 0.0

    ordered = sorted(edges, key=lambda e: e.timestamp)

    # Collapse to one entry per transaction, at that transaction's timestamp.
    by_tx: Dict[str, List] = {}
    for edge in ordered:
        by_tx.setdefault(edge.tx_hash, []).append(edge)

    transactions: List[Tuple[str, datetime]] = sorted(
        ((tx_hash, group[0].timestamp) for tx_hash, group in by_tx.items()),
        key=lambda item: item[1],
    )

    start = 0
    best: List[Tuple[str, datetime]] = []
    for end in range(len(transactions)):
        while (
            transactions[end][1] - transactions[start][1]
        ).total_seconds() > window_seconds:
            start += 1
        window = transactions[start : end + 1]
        if len(window) > len(best):
            best = list(window)

    if not best:
        return [], [], 0.0

    hashes: List[str] = [tx_hash for tx_hash, _ in best]
    hash_set: Set[str] = set(hashes)
    edges_in_window = [e for e in ordered if e.tx_hash in hash_set]
    span = (best[-1][1] - best[0][1]).total_seconds()
    return sorted(hashes), edges_in_window, span
