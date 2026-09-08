"""Shared types for the pattern library.

Every detector returns Detections, never a score. Weights live in one place
(backend/config.PATTERN_WEIGHTS) and are applied by the scoring engine, so a
detector cannot quietly invent its own weight.

Language rule (CLAUDE.md rule 2): every title and description here is
probabilistic. "Patterns consistent with layering", never "this wallet is
criminal".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from backend.clustering.engine import Cluster, ClusteringResult
from backend.graph.builder import BuiltGraph


@dataclass
class Detection:
    """One flagged observation, with the evidence that produced it."""

    pattern_type: str
    title: str
    description: str
    evidence_tx_hashes: List[str]
    evidence_detail: Dict[str, Any] = field(default_factory=dict)
    subject_addresses: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # CLAUDE.md: findings.evidence_tx_hashes is always populated. A finding
        # an investigator cannot trace to transactions is not a finding.
        if not self.evidence_tx_hashes:
            raise ValueError(
                f"Detection {self.pattern_type} has no backing transaction hashes"
            )


@dataclass
class AttributionMatch:
    """An attribution tag that matched an address in this case."""

    address: str
    entity_name: str
    entity_type: str
    source: str
    source_url: str
    confidence: float
    last_updated: str


@dataclass
class DetectionContext:
    """Everything the detectors read. Deliberately read-only."""

    graph: BuiltGraph
    clustering: ClusteringResult
    chain: str
    now: datetime
    # address (lowercased) -> matching attribution tags
    attribution: Dict[str, List[AttributionMatch]] = field(default_factory=dict)

    def cluster_for(self, address: str) -> Optional[Cluster]:
        cluster_id = self.clustering.address_to_cluster.get(address.lower())
        if not cluster_id:
            return None
        for cluster in self.clustering.clusters:
            if cluster.id == cluster_id:
                return cluster
        return None

    def tags_for(self, address: str) -> List[AttributionMatch]:
        return self.attribution.get(address.lower(), [])

    def entity_types_for(self, address: str) -> set:
        return {t.entity_type for t in self.tags_for(address)}


def short(value: str, keep: int = 10) -> str:
    """Shorten a hash or address for display inside a sentence."""
    return value if len(value) <= keep + 2 else f"{value[:keep]}…"


def canonical_entity_names(names: Iterable[str]) -> List[str]:
    """Collapse variant spellings of the same entity to one name.

    Two sources can tag the same address with different name strings — a
    TagPack's "Binance" and a curated list's "Binance (India-serving venue)" are
    the same venue. Listing both reads as two separate entities and makes the
    system look like it cannot count. Where one name is a prefix of another, the
    shorter canonical form wins.
    """
    unique = sorted({n.strip() for n in names if n and n.strip()}, key=len)
    kept: List[str] = []
    for name in unique:
        lowered = name.lower()
        if any(lowered.startswith(k.lower()) for k in kept):
            continue
        kept.append(name)
    return sorted(kept)
