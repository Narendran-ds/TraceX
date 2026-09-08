"""Pattern 4 — Round-number splitting.

       ₹10,00,000
            ↓
      ₹2L + ₹2L + ₹2L + ₹2L + ₹2L

Definition (master report §13.1): an amount split into near-equal or round-number
pieces.

Meaning: automated or scripted splitting rather than organic transaction
behaviour. A human paying real counterparties produces irregular amounts; a
script produces suspiciously tidy ones. Weight +15.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from backend.config import (
    ROUND_SPLIT_EQUALITY_TOLERANCE,
    ROUND_SPLIT_MIN_PARTS,
    ROUND_SPLIT_WINDOW_SECONDS,
)
from backend.amounts import is_round_amount
from backend.patterns.base import Detection, DetectionContext, short




def _near_equal(amounts: List[float]) -> bool:
    """All parts within the configured tolerance of their mean."""
    if len(amounts) < 2:
        return False
    mean = sum(amounts) / len(amounts)
    if mean <= 0:
        return False
    return all(abs(a - mean) / mean <= ROUND_SPLIT_EQUALITY_TOLERANCE for a in amounts)


def detect(context: DetectionContext) -> List[Detection]:
    detections: List[Detection] = []

    by_sender: Dict[str, List] = defaultdict(list)
    for edge in context.graph.edges:
        if edge.dust:
            continue
        by_sender[edge.from_address.lower()].append(edge)

    for sender, edges in by_sender.items():
        edges = sorted(edges, key=lambda e: e.timestamp)
        if len(edges) < ROUND_SPLIT_MIN_PARTS:
            continue

        start = 0
        best: List = []
        for end in range(len(edges)):
            while (
                edges[end].timestamp - edges[start].timestamp
            ).total_seconds() > ROUND_SPLIT_WINDOW_SECONDS:
                start += 1
            window = edges[start : end + 1]
            if len(window) > len(best):
                best = list(window)

        if len(best) < ROUND_SPLIT_MIN_PARTS:
            continue

        amounts = [e.amount for e in best]
        near_equal = _near_equal(amounts)
        round_count = sum(1 for a in amounts if is_round_amount(a))
        all_round = round_count == len(amounts)

        # Either signature qualifies: near-equal parts, or every part a round
        # figure. Both indicate the split was computed, not negotiated.
        if not (near_equal or all_round):
            continue

        signatures = []
        if near_equal:
            signatures.append("near-equal parts")
        if all_round:
            signatures.append("round-number parts")

        span = (
            max(e.timestamp for e in best) - min(e.timestamp for e in best)
        ).total_seconds()
        sender_display = best[0].from_address

        detections.append(
            Detection(
                pattern_type="round_split",
                title="Round-number splitting",
                description=(
                    f"{short(sender_display, 14)} split "
                    f"{round(sum(amounts), 6)} across {len(best)} transfers in "
                    f"{int(span)} seconds, with {' and '.join(signatures)}. "
                    "Amounts this regular are consistent with scripted splitting "
                    "rather than organic payment activity."
                ),
                evidence_tx_hashes=sorted({e.tx_hash for e in best}),
                evidence_detail={
                    "sender": sender_display,
                    "part_count": len(best),
                    "threshold_parts": ROUND_SPLIT_MIN_PARTS,
                    "amounts": [round(a, 8) for a in amounts],
                    "total_amount": round(sum(amounts), 8),
                    "near_equal": near_equal,
                    "equality_tolerance": ROUND_SPLIT_EQUALITY_TOLERANCE,
                    "round_part_count": round_count,
                    "all_round": all_round,
                    "window_seconds": int(span),
                    "threshold_window_seconds": ROUND_SPLIT_WINDOW_SECONDS,
                    "signatures": signatures,
                },
                subject_addresses=[sender_display]
                + sorted({e.to_address for e in best}),
            )
        )

    return detections
