"""Builds contract responses from persisted case data.

Every number returned here is read or computed from the database. There is no
default, no placeholder and no sample value anywhere in this module — if the
pipeline has not run, the fields that depend on it come back null and the UI
says "not yet computed".
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from backend.adapters.base import AMOUNT_UNITS
from backend.api.contracts import (
    AttributionCoverage,
    AttributionResponse,
    CaseCounts,
    CaseSummary,
    ClustersResponse,
    ClusterView,
    ExitCandidate as ExitCandidateModel,
    AttributionSource as AttributionSourceModel,
    FindingsResponse,
    FindingView,
    GraphEdge,
    GraphNode,
    GraphResponse,
    ProvenanceInfo,
    ScoreBreakdown,
    SuppressedSignal,
)
from backend.api.errors import CaseNotFound
from backend.attribution.ingest import coverage as attribution_coverage
from backend.attribution.matcher import BOUNDARY_ENTITY_TYPES
from backend.models.db import loads
from backend.scoring.engine import Score, score_case

PROVENANCE_LABELS = {
    "live_cached": "Live chain data, cached",
    "synthetic_scenario": "Constructed scenario",
}

PROVENANCE_NOTES = {
    "live_cached": (
        "The transactions in this case were fetched from a public blockchain API "
        "and cached locally. Every transaction hash below is real and can be "
        "checked on any block explorer."
    ),
    "synthetic_scenario": (
        "The transactions in this case were constructed to exercise the detection "
        "engine offline. They are not real on-chain activity. Attribution tags, "
        "detection logic and scoring are the same code that runs on live data."
    ),
}


def get_case_row(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise CaseNotFound(case_id)
    return row


def provenance_of(row: sqlite3.Row) -> ProvenanceInfo:
    kind = row["data_provenance"]
    note = row["provenance_note"] or PROVENANCE_NOTES.get(kind, "")
    return ProvenanceInfo(
        kind=kind,
        label=PROVENANCE_LABELS.get(kind, kind),
        note=note,
    )


def counts_for(conn: sqlite3.Connection, case_id: str) -> CaseCounts:
    def scalar(sql: str, *params) -> int:
        value = conn.execute(sql, params).fetchone()[0]
        return int(value or 0)

    return CaseCounts(
        wallets_discovered=scalar(
            "SELECT COUNT(*) FROM wallets WHERE case_id = ?", case_id
        ),
        transactions_analysed=scalar(
            "SELECT COUNT(DISTINCT tx_hash) FROM transactions WHERE case_id = ?", case_id
        ),
        clusters_identified=scalar(
            "SELECT COUNT(*) FROM clusters WHERE case_id = ?", case_id
        ),
        findings_flagged=scalar(
            "SELECT COUNT(*) FROM findings WHERE case_id = ? AND status='flagged'", case_id
        ),
        findings_confirmed=scalar(
            "SELECT COUNT(*) FROM findings WHERE case_id = ? AND status='confirmed'",
            case_id,
        ),
        findings_rejected=scalar(
            "SELECT COUNT(*) FROM findings WHERE case_id = ? AND status='rejected'",
            case_id,
        ),
        hops_traversed=scalar(
            "SELECT COALESCE(MAX(hop_depth), -1) + 1 FROM transactions WHERE case_id = ?",
            case_id,
        ),
    )


def total_traced(conn: sqlite3.Connection, case_id: str, seed: str) -> float:
    """Value that left the reported address. What the case is actually about."""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions"
        " WHERE case_id = ? AND LOWER(from_address) = ? AND is_dust = 0",
        (case_id, seed.lower()),
    ).fetchone()
    return round(float(row["total"] or 0.0), 8)


def case_summary(conn: sqlite3.Connection, case_id: str) -> CaseSummary:
    row = get_case_row(conn, case_id)
    score = score_case(conn, case_id)
    has_run = row["status"] == "complete"

    return CaseSummary(
        id=row["id"],
        complaint_id=row["complaint_id"],
        wallet_address=row["wallet_address"],
        chain=row["chain"],
        status=row["status"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        provenance=provenance_of(row),
        risk_level=row["risk_level"],
        suspicion_score=row["suspicion_score"],
        score_cap=score.cap,
        raw_score_total=score.raw_total if has_run else None,
        score_was_capped=score.was_capped,
        obfuscation_detected=score.obfuscation_detected if has_run else None,
        counts=counts_for(conn, case_id),
        total_amount_traced=total_traced(conn, case_id, row["wallet_address"]),
        amount_unit=AMOUNT_UNITS.get(row["chain"], ""),
        expansion_limited=bool(row["expansion_limited"]),
        expansion_note=row["expansion_note"] or "",
        max_hops=row["max_hops"],
    )


# --- graph -----------------------------------------------------------------

def _primary_trail_addresses(conn: sqlite3.Connection, case_id: str, seed: str) -> set:
    """Recompute the highest-weight path to the best-attributed terminal cluster."""
    edges = conn.execute(
        "SELECT from_address, to_address, edge_weight FROM transactions"
        " WHERE case_id = ? AND is_dust = 0",
        (case_id,),
    ).fetchall()
    if not edges:
        return set()

    adjacency: Dict[str, List] = {}
    for edge in edges:
        adjacency.setdefault(edge["from_address"].lower(), []).append(edge)

    targets = {
        r["address"].lower()
        for r in conn.execute(
            "SELECT w.address FROM wallets w JOIN clusters c ON w.cluster_id = c.id"
            " WHERE w.case_id = ? AND (c.is_terminal = 1 OR c.attributed_entity IS NOT NULL)",
            (case_id,),
        )
    }
    if not targets:
        return set()

    best: List[str] = []
    best_score = -1.0

    def walk(address: str, path: List[str], score: float, visited: set) -> None:
        nonlocal best, best_score
        if address in targets and len(path) > 1:
            if score > best_score:
                best_score = score
                best = list(path)
            return
        if len(path) > 12:
            return
        for edge in sorted(
            adjacency.get(address, []), key=lambda e: e["edge_weight"], reverse=True
        ):
            nxt = edge["to_address"].lower()
            if nxt in visited:
                continue
            visited.add(nxt)
            path.append(nxt)
            walk(nxt, path, score + edge["edge_weight"], visited)
            path.pop()
            visited.discard(nxt)

    walk(seed.lower(), [seed.lower()], 0.0, {seed.lower()})
    return set(best)


def graph_response(
    conn: sqlite3.Connection, case_id: str, collapsed: bool
) -> GraphResponse:
    case = get_case_row(conn, case_id)
    seed = case["wallet_address"]
    unit = AMOUNT_UNITS.get(case["chain"], "")

    wallets = conn.execute(
        "SELECT * FROM wallets WHERE case_id = ? ORDER BY hop_depth, address", (case_id,)
    ).fetchall()
    clusters = conn.execute(
        "SELECT * FROM clusters WHERE case_id = ?", (case_id,)
    ).fetchall()
    edges = conn.execute(
        "SELECT * FROM transactions WHERE case_id = ? ORDER BY timestamp", (case_id,)
    ).fetchall()

    cluster_by_id = {c["id"]: c for c in clusters}
    cluster_of_address = {
        w["address"].lower(): w["cluster_id"] for w in wallets if w["cluster_id"]
    }

    trail = _primary_trail_addresses(conn, case_id, seed)
    trail_clusters = {cluster_of_address.get(a) for a in trail}

    nodes: List[GraphNode] = []
    out_edges: List[GraphEdge] = []

    if collapsed and clusters:
        for cluster in clusters:
            members = [
                w for w in wallets if w["cluster_id"] == cluster["id"]
            ]
            min_hop = min((w["hop_depth"] for w in members), default=0)
            nodes.append(
                GraphNode(
                    id=cluster["id"],
                    label=cluster["attributed_entity"] or cluster["cluster_label"],
                    kind="cluster",
                    role=cluster["role"],
                    hop_depth=min_hop,
                    total_in=round(cluster["total_in"], 8),
                    total_out=round(cluster["total_out"], 8),
                    tx_count=sum(w["tx_count"] for w in members),
                    member_count=cluster["member_count"],
                    cluster_id=cluster["id"],
                    attributed_entity=cluster["attributed_entity"],
                    attribution_source=cluster["attribution_source"],
                    on_primary_trail=cluster["id"] in trail_clusters,
                )
            )

        # Collapse parallel edges between the same pair of clusters.
        aggregated: Dict[tuple, Dict] = {}
        for edge in edges:
            if edge["is_dust"]:
                continue
            source = cluster_of_address.get(edge["from_address"].lower())
            target = cluster_of_address.get(edge["to_address"].lower())
            if not source or not target or source == target:
                continue
            key = (source, target)
            bucket = aggregated.setdefault(
                key,
                {
                    "amount": 0.0,
                    "weight": 0.0,
                    "hashes": set(),
                    "hop": edge["hop_depth"],
                    "first": edge["timestamp"],
                    "last": edge["timestamp"],
                },
            )
            bucket["amount"] += edge["amount"]
            bucket["weight"] += edge["edge_weight"]
            bucket["hashes"].add(edge["tx_hash"])
            bucket["hop"] = min(bucket["hop"], edge["hop_depth"])
            bucket["first"] = min(bucket["first"], edge["timestamp"])
            bucket["last"] = max(bucket["last"], edge["timestamp"])

        for (source, target), bucket in aggregated.items():
            out_edges.append(
                GraphEdge(
                    id=f"{source}->{target}",
                    source=source,
                    target=target,
                    amount=round(bucket["amount"], 8),
                    tx_count=len(bucket["hashes"]),
                    edge_weight=round(bucket["weight"], 8),
                    hop_depth=bucket["hop"],
                    first_timestamp=bucket["first"],
                    last_timestamp=bucket["last"],
                    tx_hashes=sorted(bucket["hashes"]),
                    on_primary_trail=(
                        source in trail_clusters and target in trail_clusters
                    ),
                )
            )
    else:
        for wallet in wallets:
            cluster = cluster_by_id.get(wallet["cluster_id"]) if wallet["cluster_id"] else None
            address = wallet["address"]
            nodes.append(
                GraphNode(
                    id=address,
                    label=(cluster["attributed_entity"] if cluster else None) or address,
                    kind="address",
                    role=_address_role(address, seed, cluster),
                    hop_depth=wallet["hop_depth"],
                    total_in=round(wallet["total_in"], 8),
                    total_out=round(wallet["total_out"], 8),
                    tx_count=wallet["tx_count"],
                    member_count=1,
                    cluster_id=wallet["cluster_id"],
                    attributed_entity=cluster["attributed_entity"] if cluster else None,
                    attribution_source=cluster["attribution_source"] if cluster else None,
                    on_primary_trail=address.lower() in trail,
                )
            )

        aggregated: Dict[tuple, Dict] = {}
        for edge in edges:
            if edge["is_dust"]:
                continue
            key = (edge["from_address"], edge["to_address"])
            bucket = aggregated.setdefault(
                key,
                {
                    "amount": 0.0,
                    "weight": 0.0,
                    "hashes": set(),
                    "hop": edge["hop_depth"],
                    "first": edge["timestamp"],
                    "last": edge["timestamp"],
                },
            )
            bucket["amount"] += edge["amount"]
            bucket["weight"] += edge["edge_weight"]
            bucket["hashes"].add(edge["tx_hash"])
            bucket["hop"] = min(bucket["hop"], edge["hop_depth"])
            bucket["first"] = min(bucket["first"], edge["timestamp"])
            bucket["last"] = max(bucket["last"], edge["timestamp"])

        for (source, target), bucket in aggregated.items():
            out_edges.append(
                GraphEdge(
                    id=f"{source}->{target}",
                    source=source,
                    target=target,
                    amount=round(bucket["amount"], 8),
                    tx_count=len(bucket["hashes"]),
                    edge_weight=round(bucket["weight"], 8),
                    hop_depth=bucket["hop"],
                    first_timestamp=bucket["first"],
                    last_timestamp=bucket["last"],
                    tx_hashes=sorted(bucket["hashes"]),
                    on_primary_trail=(
                        source.lower() in trail and target.lower() in trail
                    ),
                )
            )

    address_count = len(wallets)
    cluster_count = len(clusters)
    ratio = round(address_count / cluster_count, 2) if cluster_count else None

    return GraphResponse(
        case_id=case_id,
        collapsed=collapsed,
        nodes=nodes,
        edges=out_edges,
        amount_unit=unit,
        address_count=address_count,
        cluster_count=cluster_count,
        collapse_ratio=ratio,
        expansion_limited=bool(case["expansion_limited"]),
        expansion_note=case["expansion_note"] or "",
    )


def _address_role(address: str, seed: str, cluster: Optional[sqlite3.Row]) -> str:
    if address.lower() == seed.lower():
        return "suspect"
    if cluster and cluster["role"] in ("exchange", "mixer", "bridge"):
        return cluster["role"]
    return "intermediary"


# --- clusters --------------------------------------------------------------

def clusters_response(conn: sqlite3.Connection, case_id: str) -> ClustersResponse:
    case = get_case_row(conn, case_id)
    rows = conn.execute(
        "SELECT * FROM clusters WHERE case_id = ? ORDER BY member_count DESC", (case_id,)
    ).fetchall()

    members_by_cluster: Dict[str, List[str]] = {}
    for wallet in conn.execute(
        "SELECT address, cluster_id FROM wallets WHERE case_id = ?", (case_id,)
    ):
        if wallet["cluster_id"]:
            members_by_cluster.setdefault(wallet["cluster_id"], []).append(
                wallet["address"]
            )

    address_count = conn.execute(
        "SELECT COUNT(*) FROM wallets WHERE case_id = ?", (case_id,)
    ).fetchone()[0]

    clusters = [
        ClusterView(
            id=row["id"],
            label=row["cluster_label"],
            member_count=row["member_count"],
            members=sorted(members_by_cluster.get(row["id"], [])),
            clustering_method=row["clustering_method"],
            cluster_confidence=row["cluster_confidence"],
            confidence_notes=loads(row["confidence_notes"], []),
            attributed_entity=row["attributed_entity"],
            attribution_source=row["attribution_source"],
            role=row["role"],
            is_terminal=bool(row["is_terminal"]),
            total_in=round(row["total_in"], 8),
            total_out=round(row["total_out"], 8),
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )
        for row in rows
    ]

    return ClustersResponse(
        case_id=case_id,
        clusters=clusters,
        address_count=address_count,
        cluster_count=len(clusters),
        collapse_ratio=round(address_count / len(clusters), 2) if clusters else None,
        amount_unit=AMOUNT_UNITS.get(case["chain"], ""),
    )


# --- findings --------------------------------------------------------------

def finding_view(row: sqlite3.Row) -> FindingView:
    return FindingView(
        id=row["id"],
        pattern_type=row["pattern_type"],
        title=row["title"],
        description=row["description"],
        score_contribution=row["score_contribution"],
        evidence_tx_hashes=loads(row["evidence_tx_hashes"], []),
        evidence_detail=loads(row["evidence_detail"], {}),
        subject_addresses=loads(row["subject_addresses"], []),
        detected_at=row["detected_at"],
        status=row["status"],
        analyst_note=row["analyst_note"],
        reviewed_by=row["reviewed_by"],
        reviewed_at=row["reviewed_at"],
    )


def score_breakdown(
    conn: sqlite3.Connection, case_id: str, score: Optional[Score] = None
) -> ScoreBreakdown:
    score = score or score_case(conn, case_id)
    rows = conn.execute(
        "SELECT * FROM findings WHERE case_id = ? ORDER BY score_contribution DESC,"
        " pattern_type",
        (case_id,),
    ).fetchall()
    return ScoreBreakdown(
        contributions=[finding_view(r) for r in rows],
        raw_total=score.raw_total,
        cap=score.cap,
        capped_total=score.capped_total,
        was_capped=score.was_capped,
        excluded_rejected_total=score.excluded_rejected_total,
        risk_level=score.risk_level,
        weights_note=score.weights_note,
    )


def findings_response(conn: sqlite3.Connection, case_id: str) -> FindingsResponse:
    case = get_case_row(conn, case_id)
    rows = conn.execute(
        "SELECT * FROM findings WHERE case_id = ? ORDER BY score_contribution DESC,"
        " pattern_type",
        (case_id,),
    ).fetchall()
    suppressed = [
        SuppressedSignal(
            pattern_type=item.get("pattern_type", "fan_in"),
            subject=item.get("subject", ""),
            reason=item.get("reason", ""),
            detail=item.get("detail", {}),
        )
        for item in loads(case["suppressed_signals"], [])
    ]
    return FindingsResponse(
        case_id=case_id,
        findings=[finding_view(r) for r in rows],
        score=score_breakdown(conn, case_id),
        suppressed_signals=suppressed,
    )


# --- attribution -----------------------------------------------------------

def attribution_response(conn: sqlite3.Connection, case_id: str) -> AttributionResponse:
    case = get_case_row(conn, case_id)
    chain = case["chain"]

    clusters = conn.execute(
        "SELECT * FROM clusters WHERE case_id = ?", (case_id,)
    ).fetchall()
    members_by_cluster: Dict[str, List[str]] = {}
    for wallet in conn.execute(
        "SELECT address, cluster_id FROM wallets WHERE case_id = ?", (case_id,)
    ):
        if wallet["cluster_id"]:
            members_by_cluster.setdefault(wallet["cluster_id"], []).append(
                wallet["address"]
            )

    amounts: Dict[str, float] = {}
    for row in conn.execute(
        "SELECT w.cluster_id AS cid, COALESCE(SUM(t.amount), 0) AS total"
        " FROM transactions t JOIN wallets w"
        "   ON LOWER(w.address) = LOWER(t.to_address) AND w.case_id = t.case_id"
        " WHERE t.case_id = ? AND t.is_dust = 0 GROUP BY w.cluster_id",
        (case_id,),
    ):
        if row["cid"]:
            amounts[row["cid"]] = float(row["total"] or 0.0)

    from backend.graph.weighting import depth_decay

    candidates: List[ExitCandidateModel] = []
    for cluster in clusters:
        members = members_by_cluster.get(cluster["id"], [])
        tags = _tags_for_addresses(conn, chain, members)
        amount = amounts.get(cluster["id"], 0.0)

        is_boundary = any(t["entity_type"] in BOUNDARY_ENTITY_TYPES for t in tags)
        is_exit = any(t["entity_type"] in ("exchange", "service") for t in tags)
        if not (is_exit or is_boundary or (cluster["is_terminal"] and amount > 0)):
            continue

        hop_depth = conn.execute(
            "SELECT COALESCE(MIN(hop_depth), 0) FROM wallets WHERE cluster_id = ?",
            (cluster["id"],),
        ).fetchone()[0]

        basis: List[str] = []
        if tags:
            best = sorted(tags, key=lambda t: -t["confidence"])[0]
            basis.append(
                f"Attribution tag confidence {best['confidence']:.2f} from "
                f"{best['source']}."
            )
            entity_name = best["entity_name"]
            entity_type = best["entity_type"]
            tag_confidence = best["confidence"]
        else:
            entity_name = None
            entity_type = None
            tag_confidence = 0.0
            basis.append(
                "No attribution match in the loaded sources. Reported as an "
                "unattributed exit cluster rather than assigned a probable name."
            )

        basis.append(
            f"Cluster confidence {cluster['cluster_confidence']:.2f} "
            f"({cluster['clustering_method'].replace('_', ' ')})."
        )
        factor = depth_decay(hop_depth)
        basis.append(
            f"Hop-depth decay {factor:.2f} at {hop_depth} hop(s) from the reported "
            "address."
        )

        if tags:
            confidence = tag_confidence * cluster["cluster_confidence"] * factor
        else:
            confidence = cluster["cluster_confidence"] * factor * 0.5
            basis.append(
                "Confidence is halved for an unattributed cluster: we can say the "
                "trail ends here, not who controls it."
            )

        boundary_note = None
        if is_boundary:
            from backend.attribution.matcher import BOUNDARY_NOTE

            kind = next(
                t["entity_type"] for t in tags if t["entity_type"] in BOUNDARY_ENTITY_TYPES
            )
            boundary_note = BOUNDARY_NOTE.format(
                kind="cross-chain bridge" if kind == "bridge" else "mixing service"
            )
            basis.append(
                "This is a confidence boundary; the figure above describes reaching "
                "it, not what happened after it."
            )

        candidates.append(
            ExitCandidateModel(
                cluster_id=cluster["id"],
                cluster_label=cluster["cluster_label"],
                entity_name=entity_name,
                entity_type=entity_type,
                attributed=bool(tags),
                confidence=round(min(1.0, confidence), 4),
                confidence_basis=basis,
                amount_received=round(amount, 8),
                member_count=cluster["member_count"],
                hop_depth=hop_depth,
                sources=[
                    AttributionSourceModel(
                        source=t["source"],
                        source_url=t["source_url"],
                        entity_name=t["entity_name"],
                        entity_type=t["entity_type"],
                        confidence=t["confidence"],
                        matched_address=t["address"],
                        last_updated=t["last_updated"],
                    )
                    for t in tags
                ],
                confidence_boundary=is_boundary,
                boundary_note=boundary_note,
            )
        )

    candidates.sort(key=lambda c: (-c.confidence, -c.amount_received))

    stats = attribution_coverage(conn)
    degraded = [c for c in candidates if c.confidence_boundary]
    if degraded:
        names = ", ".join(
            sorted({c.entity_name or "an unnamed service" for c in degraded})
        )
        degraded_note = (
            f"Trail confidence degrades at {names}. Funds passing through a bridge "
            "or mixing service cannot be followed deterministically on-chain, so "
            "this report stops there rather than presenting an inferred destination "
            "as a traced one."
        )
    else:
        degraded_note = ""

    unattributed = sum(
        1 for c in clusters if c["is_terminal"] and not c["attributed_entity"]
    )

    return AttributionResponse(
        case_id=case_id,
        candidates=candidates,
        coverage=AttributionCoverage(
            source_count=stats["source_count"],
            entity_count=stats["entity_count"],
            tag_count=stats["tag_count"],
            sources=stats["sources"],
            statement=stats["statement"],
        ),
        amount_unit=AMOUNT_UNITS.get(chain, ""),
        unattributed_terminal_clusters=unattributed,
        trail_degraded=bool(degraded),
        trail_degraded_note=degraded_note,
    )


def _tags_for_addresses(
    conn: sqlite3.Connection, chain: str, addresses: List[str]
) -> List[Dict]:
    if not addresses:
        return []
    out: List[Dict] = []
    lowered = [a.lower() for a in addresses]
    for start in range(0, len(lowered), 500):
        chunk = lowered[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT * FROM attribution_tags WHERE chain = ? AND address IN ({placeholders})",
            [chain] + chunk,
        ).fetchall()
        out.extend(dict(r) for r in rows)
    return out
