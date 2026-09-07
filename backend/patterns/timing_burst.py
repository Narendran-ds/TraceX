"""Pattern 5 — Timing burst.

    10:01:04  ─┐
    10:01:11   │
    10:02:33   ├─  multiple related txs in a tight window
    10:02:47   │
    10:03:02  ─┘

Definition (master report §13.1): transactions from related addresses clustered
in an abnormally tight time window.

"Related" means belonging to the same entity cluster, which is what makes this
different from "a busy minute on the chain". Meaning: scripted movement,
characteristic of automated laundering rather than human activity. Weight +12.
"""
from __future__ import annotations

from typing import List

from backend.config import TIMING_BURST_MIN_TXS, TIMING_BURST_WINDOW_SECONDS
from backend.patterns.base import Detection, DetectionContext
from backend.patterns.burst import largest_burst


def detect(context: DetectionContext) -> List[Detection]:
    detections: List[Detection] = []

    for cluster in context.clustering.clusters:
        members = {m.lower() for m in cluster.members}
        edges = [
            e
            for e in context.graph.edges
            if not e.dust and e.from_address.lower() in members
        ]

        hashes, edges_in_window, span = largest_burst(
            edges, TIMING_BURST_WINDOW_SECONDS
        )
        if len(hashes) < TIMING_BURST_MIN_TXS:
            continue

        involved = sorted({e.from_address for e in edges_in_window})

        detections.append(
            Detection(
                pattern_type="timing_burst",
                title="Timing burst",
                description=(
                    f"{len(hashes)} transactions from {len(involved)} address(es) in "
                    f"{cluster.label} occurred within {int(span)} seconds. Timing this "
                    "tight is consistent with scripted movement rather than "
                    "human-paced activity."
                ),
                evidence_tx_hashes=hashes,
                evidence_detail={
                    "cluster_id": cluster.id,
                    "cluster_label": cluster.label,
                    "transaction_count": len(hashes),
                    "threshold_transactions": TIMING_BURST_MIN_TXS,
                    "window_seconds": int(span),
                    "threshold_window_seconds": TIMING_BURST_WINDOW_SECONDS,
                    "addresses_involved": involved,
                    "first_timestamp": min(
                        e.timestamp for e in edges_in_window
                    ).isoformat(),
                    "last_timestamp": max(
                        e.timestamp for e in edges_in_window
                    ).isoformat(),
                },
                subject_addresses=involved,
            )
        )

    return detections
