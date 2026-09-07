"""Signal — cluster newly created + burst + dormant.

Weight +15 (CLAUDE.md scoring table). Its stated job is disambiguation: it is
what separates laundering from legitimate service activity.

The shape it looks for is the life-cycle of a throwaway laundering cluster:

    created recently  ->  short intense burst of activity  ->  goes quiet

A real exchange or service does not look like this. It has a long history, and
it never goes quiet. All three conditions must hold together; any one of them
alone is ordinary.
"""
from __future__ import annotations

from typing import List

from backend.config import (
    CLUSTER_DORMANT_MIN_DAYS,
    CLUSTER_NEW_MAX_AGE_DAYS,
    TIMING_BURST_MIN_TXS,
    TIMING_BURST_WINDOW_SECONDS,
)
from backend.patterns.base import Detection, DetectionContext
from backend.patterns.burst import largest_burst

SERVICE_ENTITY_TYPES = {"exchange", "service"}


def detect(context: DetectionContext) -> List[Detection]:
    detections: List[Detection] = []

    for cluster in context.clustering.clusters:
        if cluster.first_seen is None or cluster.last_seen is None:
            continue
        # An attributed service is expected to be busy; this signal is not about it.
        if any(
            context.entity_types_for(m) & SERVICE_ENTITY_TYPES
            for m in cluster.members
        ):
            continue

        active_days = (cluster.last_seen - cluster.first_seen).total_seconds() / 86400.0
        dormant_days = (context.now - cluster.last_seen).total_seconds() / 86400.0

        is_new = active_days <= CLUSTER_NEW_MAX_AGE_DAYS
        is_dormant = dormant_days >= CLUSTER_DORMANT_MIN_DAYS
        if not (is_new and is_dormant):
            continue

        members = {m.lower() for m in cluster.members}
        edges = [
            e
            for e in context.graph.edges
            if not e.dust and e.from_address.lower() in members
        ]

        # Counted over distinct transactions, not edges — see patterns/burst.py.
        hashes, _edges_in_window, _span = largest_burst(
            edges, TIMING_BURST_WINDOW_SECONDS
        )
        if len(hashes) < TIMING_BURST_MIN_TXS:
            continue

        detections.append(
            Detection(
                pattern_type="cluster_context",
                title="Short-lived cluster: created, burst, then dormant",
                description=(
                    f"{cluster.label} was active for only "
                    f"{round(active_days, 1)} day(s), moved funds in a burst of "
                    f"{len(hashes)} transactions, and has been inactive for "
                    f"{int(dormant_days)} day(s) since. This life-cycle is consistent "
                    "with a purpose-built pass-through rather than an ongoing service."
                ),
                evidence_tx_hashes=hashes,
                evidence_detail={
                    "cluster_id": cluster.id,
                    "cluster_label": cluster.label,
                    "member_count": cluster.member_count,
                    "active_days": round(active_days, 2),
                    "new_threshold_days": CLUSTER_NEW_MAX_AGE_DAYS,
                    "dormant_days": round(dormant_days, 2),
                    "dormant_threshold_days": CLUSTER_DORMANT_MIN_DAYS,
                    "burst_transaction_count": len(hashes),
                    "burst_threshold": TIMING_BURST_MIN_TXS,
                    "first_seen": cluster.first_seen.isoformat(),
                    "last_seen": cluster.last_seen.isoformat(),
                },
                subject_addresses=sorted(cluster.members),
            )
        )

    return detections
