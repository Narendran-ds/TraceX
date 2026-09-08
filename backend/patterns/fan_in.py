"""Pattern 2 — Fan-in / consolidation, with contextual disambiguation.

      B ──┐
      C ──┤
      D ──┼──► Z
      E ──┤
      F ──┘

Definition (master report §13.1): N >= 5 addresses converge on a single address
within a short window. Meaning: re-aggregation before cash-out. Weight +15.

The caveat that makes this the hardest detector in the library, stated in both
CLAUDE.md and the master report: **a legitimate exchange deposit consolidation
looks exactly the same**. Shape alone cannot tell them apart, so this detector
does not report on shape alone.

Four contextual features decide whether a converging shape is reported:

    attribution_match  is the destination a known exchange or service?
    volume_history     does the destination behave like a busy service?
    cluster_age        was the destination cluster created recently?
    dormancy           was the destination inactive before this convergence?

A destination that is an attributed service, or that is both high-volume and
long-lived, is consolidating deposits — not laundering — and is suppressed. The
computed features travel with the finding either way, so the investigator sees
the reasoning rather than a bare verdict.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from backend.config import (
    CLUSTER_NEW_MAX_AGE_DAYS,
    FAN_IN_MIN_SENDERS,
    FAN_IN_WINDOW_SECONDS,
    HIGH_VOLUME_TX_COUNT,
)
from backend.patterns.base import (
    Detection,
    DetectionContext,
    canonical_entity_names,
    short,
)

SERVICE_ENTITY_TYPES = {"exchange", "service"}


@dataclass
class DisambiguationFeatures:
    """The contextual evidence behind a fan-in decision. Always computed."""

    attributed_as_service: bool
    attributed_entities: List[str]
    destination_tx_count: int
    high_volume: bool
    high_volume_threshold: int
    cluster_age_days: Optional[float]
    cluster_is_new: bool
    cluster_new_threshold_days: int
    dormant_before_days: Optional[float]
    was_dormant_before: bool

    @property
    def looks_like_a_service(self) -> bool:
        """The documented suppression rule.

        Either signal on its own is enough when it is an attribution hit; volume
        alone is not, because a busy laundering consolidation is also busy. It
        takes volume *and* a long history to look like a real service.
        """
        return self.attributed_as_service or (
            self.high_volume and not self.cluster_is_new
        )

    def explain(self) -> str:
        if self.attributed_as_service:
            names = ", ".join(self.attributed_entities) or "a known service"
            return (
                f"Not reported as suspicious: the destination is attributed to {names}, "
                "so this convergence is consistent with ordinary deposit consolidation."
            )
        if self.high_volume and not self.cluster_is_new:
            return (
                f"Not reported as suspicious: the destination has "
                f"{self.destination_tx_count} transactions and a history longer than "
                f"{self.cluster_new_threshold_days} days, which is consistent with a "
                "high-volume service rather than a laundering consolidation."
            )
        return "Contextual features do not indicate ordinary service activity."


def _features(context: DetectionContext, destination: str) -> DisambiguationFeatures:
    tags = context.tags_for(destination)
    service_tags = [t for t in tags if t.entity_type in SERVICE_ENTITY_TYPES]

    wallet = context.graph.wallets.get(destination.lower())
    tx_count = wallet.tx_count if wallet else 0

    cluster = context.cluster_for(destination)
    age_days: Optional[float] = None
    if cluster and cluster.first_seen and cluster.last_seen:
        age_days = (cluster.last_seen - cluster.first_seen).total_seconds() / 86400.0

    dormant_before: Optional[float] = None
    if wallet and wallet.first_seen and wallet.last_seen:
        # How long the destination existed before this activity began. A freshly
        # created collection point has no prior life.
        dormant_before = (wallet.last_seen - wallet.first_seen).total_seconds() / 86400.0

    return DisambiguationFeatures(
        attributed_as_service=bool(service_tags),
        attributed_entities=canonical_entity_names(
            t.entity_name for t in service_tags
        ),
        destination_tx_count=tx_count,
        high_volume=tx_count >= HIGH_VOLUME_TX_COUNT,
        high_volume_threshold=HIGH_VOLUME_TX_COUNT,
        cluster_age_days=round(age_days, 2) if age_days is not None else None,
        cluster_is_new=(age_days is None or age_days <= CLUSTER_NEW_MAX_AGE_DAYS),
        cluster_new_threshold_days=CLUSTER_NEW_MAX_AGE_DAYS,
        dormant_before_days=round(dormant_before, 2) if dormant_before is not None else None,
        was_dormant_before=(dormant_before is not None and dormant_before <= 1.0),
    )


def detect(context: DetectionContext) -> List[Detection]:
    detections: List[Detection] = []

    by_destination: Dict[str, List] = defaultdict(list)
    for edge in context.graph.edges:
        if edge.dust:
            continue
        by_destination[edge.to_address.lower()].append(edge)

    for destination, edges in by_destination.items():
        edges = sorted(edges, key=lambda e: e.timestamp)
        if len(edges) < FAN_IN_MIN_SENDERS:
            continue

        start = 0
        best: List = []
        for end in range(len(edges)):
            while (
                edges[end].timestamp - edges[start].timestamp
            ).total_seconds() > FAN_IN_WINDOW_SECONDS:
                start += 1
            window = edges[start : end + 1]
            if len(window) > len(best):
                best = list(window)

        senders = {e.from_address.lower(): e for e in best}
        senders.pop(destination, None)
        if len(senders) < FAN_IN_MIN_SENDERS:
            continue

        edges_used = list(senders.values())
        features = _features(context, destination)

        # The disambiguation gate. CLAUDE.md is explicit that fan-in must not
        # ship without this.
        if features.looks_like_a_service:
            continue

        span = (
            max(e.timestamp for e in edges_used)
            - min(e.timestamp for e in edges_used)
        ).total_seconds()
        total = sum(e.amount for e in edges_used)
        destination_display = edges_used[0].to_address

        detections.append(
            Detection(
                pattern_type="fan_in",
                title="Fan-in consolidation",
                description=(
                    f"{len(senders)} addresses converged on "
                    f"{short(destination_display, 14)} within {int(span)} seconds. "
                    "This shape is consistent with re-aggregation before cash-out. "
                    "The contextual checks below did not indicate ordinary exchange "
                    "deposit activity, which produces the same shape."
                ),
                evidence_tx_hashes=sorted({e.tx_hash for e in edges_used}),
                evidence_detail={
                    "destination": destination_display,
                    "sender_count": len(senders),
                    "threshold_senders": FAN_IN_MIN_SENDERS,
                    "window_seconds": int(span),
                    "threshold_window_seconds": FAN_IN_WINDOW_SECONDS,
                    "total_amount": round(total, 8),
                    "senders": sorted(e.from_address for e in edges_used),
                    "disambiguation": asdict(features),
                    "disambiguation_verdict": features.explain(),
                },
                subject_addresses=[destination_display]
                + sorted(e.from_address for e in edges_used),
            )
        )

    return detections


def explain_suppressions(context: DetectionContext) -> List[Dict[str, object]]:
    """Converging shapes that were checked and deliberately not reported.

    Surfaced so the investigator can see what the system chose not to flag, and
    why. Silence about a suppressed signal is indistinguishable from not having
    looked.
    """
    out: List[Dict[str, object]] = []
    by_destination: Dict[str, List] = defaultdict(list)
    for edge in context.graph.edges:
        if edge.dust:
            continue
        by_destination[edge.to_address.lower()].append(edge)

    for destination, edges in by_destination.items():
        senders = {e.from_address.lower() for e in edges}
        senders.discard(destination)
        if len(senders) < FAN_IN_MIN_SENDERS:
            continue
        features = _features(context, destination)
        if not features.looks_like_a_service:
            continue
        out.append(
            {
                "destination": edges[0].to_address,
                "sender_count": len(senders),
                "reason": features.explain(),
                "features": asdict(features),
            }
        )
    return out
