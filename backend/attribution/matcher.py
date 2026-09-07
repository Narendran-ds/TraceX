"""Attribution matching and exit-point ranking.

Answers the actual question in the problem statement: which exchange did the
money leave through? With three hard rules:

  * Every match carries its source and a dereferenceable source URL.
  * A terminal cluster with no match is reported as an "unattributed exit
    cluster" — never upgraded to a guess.
  * A bridge or mixer hop is a confidence boundary. Past it, deterministic
    tracing stops and the system says so.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.clustering.engine import Cluster, ClusteringResult
from backend.graph.builder import BuiltGraph
from backend.patterns.base import AttributionMatch

BOUNDARY_ENTITY_TYPES = {"bridge", "mixer"}
EXIT_ENTITY_TYPES = {"exchange", "service"}

BOUNDARY_NOTE = (
    "The trail reaches a {kind} here. Value that passes through it cannot be "
    "followed deterministically on-chain, so tracing confidence degrades at this "
    "point rather than continuing. Anything beyond this hop needs a different "
    "investigative route (an exchange records request, or the operator's own logs)."
)


@dataclass
class ExitCandidate:
    cluster: Cluster
    entity_name: Optional[str]
    entity_type: Optional[str]
    attributed: bool
    confidence: float
    confidence_basis: List[str]
    amount_received: float
    hop_depth: int
    sources: List[AttributionMatch] = field(default_factory=list)
    confidence_boundary: bool = False
    boundary_note: Optional[str] = None


def load_attribution(
    conn: sqlite3.Connection, chain: str, addresses: List[str]
) -> Dict[str, List[AttributionMatch]]:
    """Look up every traced address against attribution_tags in one pass."""
    if not addresses:
        return {}

    result: Dict[str, List[AttributionMatch]] = {}
    lowered = [a.lower() for a in addresses]

    # Chunked to stay under SQLite's parameter limit on large graphs.
    for start in range(0, len(lowered), 500):
        chunk = lowered[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT * FROM attribution_tags WHERE chain = ? AND address IN ({placeholders})",
            [chain] + chunk,
        ).fetchall()
        for row in rows:
            result.setdefault(row["address"], []).append(
                AttributionMatch(
                    address=row["address"],
                    entity_name=row["entity_name"],
                    entity_type=row["entity_type"],
                    source=row["source"],
                    source_url=row["source_url"],
                    confidence=row["confidence"],
                    last_updated=row["last_updated"],
                )
            )
    return result


def apply_attribution_to_clusters(
    clustering: ClusteringResult, attribution: Dict[str, List[AttributionMatch]]
) -> None:
    """Name clusters that matched, and set the roles attribution is entitled to set."""
    for cluster in clustering.clusters:
        matches: List[AttributionMatch] = []
        for member in cluster.members:
            matches.extend(attribution.get(member.lower(), []))
        if not matches:
            continue

        # Prefer the most specific designation: a sanctions hit outranks an
        # ordinary exchange label on the same cluster.
        priority = {"sanctioned": 0, "mixer": 1, "ransomware": 2, "bridge": 3,
                    "exchange": 4, "service": 5}
        best = sorted(
            matches, key=lambda m: (priority.get(m.entity_type, 9), -m.confidence)
        )[0]

        cluster.attributed_entity = best.entity_name
        cluster.attribution_source = best.source
        if best.entity_type in ("exchange", "service"):
            cluster.role = "exchange"
        elif best.entity_type in ("mixer", "sanctioned", "ransomware"):
            cluster.role = "mixer"
        elif best.entity_type == "bridge":
            cluster.role = "bridge"


def rank_exit_candidates(
    graph: BuiltGraph,
    clustering: ClusteringResult,
    attribution: Dict[str, List[AttributionMatch]],
) -> List[ExitCandidate]:
    """Rank probable cash-out points.

    Confidence is built from stated components, each of which is shown to the
    investigator:
      * the attribution tag's own confidence,
      * the cluster's confidence (how sure we are these addresses are one actor),
      * a decay for hop distance from the victim-reported address.
    """
    candidates: List[ExitCandidate] = []

    for cluster in clustering.clusters:
        matches: List[AttributionMatch] = []
        for member in cluster.members:
            matches.extend(attribution.get(member.lower(), []))

        amount = sum(
            e.amount
            for e in graph.edges
            if not e.dust and e.to_address.lower() in {m.lower() for m in cluster.members}
        )

        is_boundary = any(m.entity_type in BOUNDARY_ENTITY_TYPES for m in matches)
        is_exit = any(m.entity_type in EXIT_ENTITY_TYPES for m in matches)

        # A cluster is a candidate exit if it is attributed as a venue, or if it
        # is terminal (the traced value stops there) and moved value.
        if not (is_exit or is_boundary or (cluster.is_terminal and amount > 0)):
            continue

        basis: List[str] = []
        if matches:
            best = sorted(matches, key=lambda m: -m.confidence)[0]
            tag_confidence = best.confidence
            basis.append(
                f"Attribution tag confidence {tag_confidence:.2f} from {best.source}."
            )
            entity_name: Optional[str] = best.entity_name
            entity_type: Optional[str] = best.entity_type
        else:
            # Unattributed. We report it as an exit we cannot name — we do not
            # invent one. The confidence reflects only the clustering.
            tag_confidence = 0.0
            entity_name = None
            entity_type = None
            basis.append(
                "No attribution match in the loaded sources. Reported as an "
                "unattributed exit cluster rather than assigned a probable name."
            )

        basis.append(
            f"Cluster confidence {cluster.confidence:.2f} "
            f"({cluster.method.replace('_', ' ')})."
        )

        from backend.graph.weighting import depth_decay

        distance_factor = depth_decay(cluster.hop_depth)
        basis.append(
            f"Hop-depth decay {distance_factor:.2f} at {cluster.hop_depth} hop(s) "
            "from the reported address."
        )

        if matches:
            confidence = tag_confidence * cluster.confidence * distance_factor
        else:
            # Without a tag there is no entity claim to be confident about; what
            # remains is only our confidence that this is where the trail ends.
            confidence = cluster.confidence * distance_factor * 0.5
            basis.append(
                "Confidence is halved for an unattributed cluster: we can say the "
                "trail ends here, not who controls it."
            )

        boundary_note = None
        if is_boundary:
            kind = next(
                m.entity_type for m in matches if m.entity_type in BOUNDARY_ENTITY_TYPES
            )
            boundary_note = BOUNDARY_NOTE.format(
                kind="cross-chain bridge" if kind == "bridge" else "mixing service"
            )
            basis.append(
                "This is a confidence boundary; the figure above describes reaching "
                "it, not what happened after it."
            )

        candidates.append(
            ExitCandidate(
                cluster=cluster,
                entity_name=entity_name,
                entity_type=entity_type,
                attributed=bool(matches),
                confidence=round(min(1.0, confidence), 4),
                confidence_basis=basis,
                amount_received=round(amount, 8),
                hop_depth=cluster.hop_depth,
                sources=matches,
                confidence_boundary=is_boundary,
                boundary_note=boundary_note,
            )
        )

    # Rank by confidence, then by how much value actually arrived.
    candidates.sort(key=lambda c: (-c.confidence, -c.amount_received))
    return candidates


def trail_degraded(candidates: List[ExitCandidate]) -> bool:
    return any(c.confidence_boundary for c in candidates)


def degraded_note(candidates: List[ExitCandidate]) -> str:
    boundaries = [c for c in candidates if c.confidence_boundary]
    if not boundaries:
        return ""
    names = ", ".join(sorted({c.entity_name or "an unnamed service" for c in boundaries}))
    return (
        f"Trail confidence degrades at {names}. Funds passing through a bridge or "
        "mixing service cannot be followed deterministically on-chain, so this "
        "report stops there rather than presenting an inferred destination as a "
        "traced one."
    )


def unattributed_terminal_count(clustering: ClusteringResult) -> int:
    return sum(
        1
        for c in clustering.clusters
        if c.is_terminal and not c.attributed_entity
    )
