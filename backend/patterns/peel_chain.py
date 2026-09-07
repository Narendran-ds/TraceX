"""Pattern 3 — Peel chain.

    A ──► B ──► C ──► D ──► E ──► F
          │     │     │     │
          ▼     ▼     ▼     ▼
        small small small small   (small amounts peeled off; remainder continues)

Definition (master report §13.1): a chain where each hop forwards a large
remainder and peels a small amount to a side address.

Meaning: one of the most recognizable deliberate laundering structures — the
bulk keeps moving while small slices are shaved off to separate destinations.
Weight +20.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from backend.config import (
    PEEL_CHAIN_MIN_HOPS,
    PEEL_REMAINDER_MIN_RATIO,
    PEEL_SMALL_MAX_RATIO,
)
from backend.patterns.base import Detection, DetectionContext, short


def _peel_step(context: DetectionContext, address: str) -> Optional[Tuple[str, dict]]:
    """If this address performs one peel, return (next_address, step_detail).

    A peel step is a single transaction that splits the incoming value into a
    dominant remainder that carries on, plus one or more small slices that leave
    the chain.
    """
    edges = [e for e in context.graph.outgoing_from(address) if not e.dust]
    if len(edges) < 2:
        return None

    # Group this address's outgoing edges by the transaction they belong to.
    by_tx: Dict[str, List] = {}
    for edge in edges:
        by_tx.setdefault(edge.tx_hash, []).append(edge)

    for tx_hash, group in by_tx.items():
        if len(group) < 2:
            continue
        total = sum(e.amount for e in group)
        if total <= 0:
            continue

        ordered = sorted(group, key=lambda e: e.amount, reverse=True)
        remainder, peels = ordered[0], ordered[1:]

        remainder_ratio = remainder.amount / total
        largest_peel_ratio = max(p.amount for p in peels) / total

        if (
            remainder_ratio >= PEEL_REMAINDER_MIN_RATIO
            and largest_peel_ratio <= PEEL_SMALL_MAX_RATIO
        ):
            return (
                remainder.to_address,
                {
                    "tx_hash": tx_hash,
                    "from": remainder.from_address,
                    "remainder_to": remainder.to_address,
                    "remainder_amount": round(remainder.amount, 8),
                    "remainder_ratio": round(remainder_ratio, 4),
                    "peeled_to": [p.to_address for p in peels],
                    "peeled_amount": round(sum(p.amount for p in peels), 8),
                    "largest_peel_ratio": round(largest_peel_ratio, 4),
                    "timestamp": remainder.timestamp.isoformat(),
                },
            )
    return None


def detect(context: DetectionContext) -> List[Detection]:
    detections: List[Detection] = []
    consumed: set = set()

    # Start from the seed and from any address that is not already part of a
    # longer chain, so the same peel run is reported once.
    starts = [context.graph.seed] + [
        w.address for w in context.graph.wallets.values()
    ]

    for start in starts:
        if start.lower() in consumed:
            continue

        steps: List[dict] = []
        path: List[str] = [start]
        current = start
        visited = {start.lower()}

        while True:
            step = _peel_step(context, current)
            if step is None:
                break
            next_address, detail = step
            if next_address.lower() in visited:
                break
            steps.append(detail)
            path.append(next_address)
            visited.add(next_address.lower())
            current = next_address

        if len(steps) < PEEL_CHAIN_MIN_HOPS:
            continue

        for address in path:
            consumed.add(address.lower())

        tx_hashes = sorted({s["tx_hash"] for s in steps})
        peeled_total = sum(s["peeled_amount"] for s in steps)
        remainder_final = steps[-1]["remainder_amount"]

        detections.append(
            Detection(
                pattern_type="peel_chain",
                title="Peel chain",
                description=(
                    f"A chain of {len(steps)} hops beginning at {short(start, 14)} "
                    f"forwarded a large remainder at each step while peeling smaller "
                    f"amounts to side addresses ({round(peeled_total, 6)} peeled in "
                    f"total, {round(remainder_final, 6)} still carried at the end). "
                    "This structure is consistent with deliberate obfuscation."
                ),
                evidence_tx_hashes=tx_hashes,
                evidence_detail={
                    "chain_length": len(steps),
                    "threshold_hops": PEEL_CHAIN_MIN_HOPS,
                    "remainder_min_ratio": PEEL_REMAINDER_MIN_RATIO,
                    "peel_max_ratio": PEEL_SMALL_MAX_RATIO,
                    "path": path,
                    "steps": steps,
                    "total_peeled": round(peeled_total, 8),
                    "final_remainder": round(remainder_final, 8),
                },
                subject_addresses=path,
            )
        )

    return detections
