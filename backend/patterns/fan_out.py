"""Pattern 1 — Rapid fan-out.

            A
      ┌─────┼─────┬─────┬─────┐
      ▼     ▼     ▼     ▼     ▼
      B     C     D     E     F     (within a short time window)

Definition (master report §13.1): one address sends to N >= 5 previously-unseen
addresses within a configurable time window.

Meaning: the classic first step of layering — splitting funds to fragment the
trail. Weight +20.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from backend.config import FAN_OUT_MIN_RECIPIENTS, FAN_OUT_WINDOW_SECONDS
from backend.patterns.base import Detection, DetectionContext, short


def _first_appearance(context: DetectionContext) -> Dict[str, float]:
    """Earliest moment each address is seen anywhere in the traced graph."""
    first: Dict[str, float] = {}
    for tx in context.graph.transactions.values():
        ts = tx.timestamp.timestamp()
        for address in tx.input_addresses + tx.output_addresses:
            key = address.lower()
            if key not in first or ts < first[key]:
                first[key] = ts
    return first


def detect(context: DetectionContext) -> List[Detection]:
    first_seen = _first_appearance(context)
    detections: List[Detection] = []

    # Group each sender's non-dust outgoing edges by transaction time.
    by_sender: Dict[str, List] = defaultdict(list)
    for edge in context.graph.edges:
        if edge.dust:
            continue
        by_sender[edge.from_address.lower()].append(edge)

    for sender, edges in by_sender.items():
        edges = sorted(edges, key=lambda e: e.timestamp)
        if len(edges) < FAN_OUT_MIN_RECIPIENTS:
            continue

        # Sliding window over the sender's outgoing transfers.
        start = 0
        best: List = []
        for end in range(len(edges)):
            while (
                edges[end].timestamp - edges[start].timestamp
            ).total_seconds() > FAN_OUT_WINDOW_SECONDS:
                start += 1
            window = edges[start : end + 1]
            if len(window) > len(best):
                best = list(window)

        if not best:
            continue

        window_start = min(e.timestamp for e in best).timestamp()

        # "Previously unseen": the recipient has no earlier appearance in the
        # traced graph than this window. An address the suspect already dealt
        # with is not a freshly created layering hop.
        fresh_recipients = {}
        for edge in best:
            key = edge.to_address.lower()
            if key == sender:
                continue
            if first_seen.get(key, window_start) >= window_start:
                fresh_recipients.setdefault(key, edge)

        if len(fresh_recipients) < FAN_OUT_MIN_RECIPIENTS:
            continue

        edges_used = list(fresh_recipients.values())
        span = (
            max(e.timestamp for e in edges_used)
            - min(e.timestamp for e in edges_used)
        ).total_seconds()
        total = sum(e.amount for e in edges_used)
        sender_display = next(
            (e.from_address for e in edges_used), sender
        )

        detections.append(
            Detection(
                pattern_type="fan_out",
                title="Rapid fan-out",
                description=(
                    f"{short(sender_display, 14)} distributed funds to "
                    f"{len(fresh_recipients)} addresses that had no prior activity in "
                    f"this trace, within {int(span)} seconds. This shape is consistent "
                    "with layering — splitting funds to fragment the trail."
                ),
                evidence_tx_hashes=sorted({e.tx_hash for e in edges_used}),
                evidence_detail={
                    "sender": sender_display,
                    "recipient_count": len(fresh_recipients),
                    "threshold_recipients": FAN_OUT_MIN_RECIPIENTS,
                    "window_seconds": int(span),
                    "threshold_window_seconds": FAN_OUT_WINDOW_SECONDS,
                    "total_amount": round(total, 8),
                    "recipients": sorted(e.to_address for e in edges_used),
                },
                subject_addresses=[sender_display]
                + sorted(e.to_address for e in edges_used),
            )
        )

    return detections
