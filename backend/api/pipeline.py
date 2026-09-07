"""The investigation pipeline.

Runs as a generator so POST /investigate can stream progress and the frontend
animates the graph building live, rather than showing a spinner for twenty
seconds (master report §10).

Order matters and is not arbitrary: attribution is loaded *before* the detectors
run, because the fan-in disambiguation needs to know whether the destination is
a known exchange. Running detection first and attributing afterwards would flag
every exchange deposit in the country.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from backend.api.contracts import CaseCounts, ProgressEvent
from backend.attribution.matcher import (
    apply_attribution_to_clusters,
    load_attribution,
)
from backend.clustering.engine import cluster_graph, persist_clusters
from backend.graph.builder import build_graph, persist_graph
from backend.models.db import record_audit, utc_now_iso
from backend.patterns.base import DetectionContext
from backend.patterns.engine import (
    persist_findings,
    run_detectors,
    suppression_report,
)
from backend.scoring.engine import apply_score_to_case


def _counts(conn: sqlite3.Connection, case_id: str, hops: int) -> CaseCounts:
    """Counts read from the database. Nothing here is a constant."""
    def scalar(sql: str, *params) -> int:
        return conn.execute(sql, params).fetchone()[0]

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
            "SELECT COUNT(*) FROM findings WHERE case_id = ? AND status = 'flagged'",
            case_id,
        ),
        findings_confirmed=scalar(
            "SELECT COUNT(*) FROM findings WHERE case_id = ? AND status = 'confirmed'",
            case_id,
        ),
        findings_rejected=scalar(
            "SELECT COUNT(*) FROM findings WHERE case_id = ? AND status = 'rejected'",
            case_id,
        ),
        hops_traversed=hops,
    )


def run_investigation(
    conn: sqlite3.Connection,
    case_id: str,
    now: Optional[datetime] = None,
    mode: Optional[str] = None,
) -> Iterator[ProgressEvent]:
    """Execute the pipeline, yielding progress as it goes.

    The caller owns the connection and the commit. Every yield point leaves the
    database in a consistent state, so a client that disconnects mid-stream
    does not leave a half-written case.
    """
    now = now or datetime.now(timezone.utc)

    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        yield ProgressEvent(
            event="error",
            message=f"No case with id {case_id} exists.",
            percent=0,
        )
        return

    seed = case["wallet_address"]
    chain = case["chain"]
    max_hops = case["max_hops"]

    conn.execute("UPDATE cases SET status = 'running' WHERE id = ?", (case_id,))
    record_audit(conn, case_id, "investigation_started", "system",
                 {"seed": seed, "chain": chain, "max_hops": max_hops})
    conn.commit()

    yield ProgressEvent(
        event="started",
        message=f"Tracing {seed} on {chain}, up to {max_hops} hops.",
        percent=2,
        detail={"seed": seed, "chain": chain, "max_hops": max_hops},
    )

    # --- 1. graph expansion -------------------------------------------------
    hop_events: List[Dict[str, Any]] = []

    def collect(event: str, message: str, hop: int, detail: dict) -> None:
        hop_events.append({"event": event, "message": message, "hop": hop,
                           "detail": detail})

    graph = build_graph(
        seed, chain, max_hops=max_hops, conn=conn, mode=mode, now=now,
        on_progress=collect,
    )

    # Hop events are emitted after the walk so the DB write is atomic per hop;
    # the frontend animates them in order regardless.
    total_hops = max(1, len(hop_events))
    for index, event in enumerate(hop_events, start=1):
        yield ProgressEvent(
            event="hop_complete",
            message=(
                f"Hop {event['hop']}: {event['detail']['addresses']} addresses, "
                f"{event['detail']['transactions']} transactions so far."
            ),
            hop=event["hop"],
            percent=int(5 + 45 * index / total_hops),
            detail=event["detail"],
        )

    persist_graph(conn, case_id, graph)
    conn.execute(
        "UPDATE cases SET expansion_limited = ?, expansion_note = ? WHERE id = ?",
        (1 if graph.expansion_limited else 0, graph.expansion_note, case_id),
    )
    conn.commit()

    # --- 2. clustering ------------------------------------------------------
    yield ProgressEvent(
        event="clustering",
        message="Grouping addresses into entity clusters.",
        percent=58,
        detail={"addresses": graph.address_count},
    )

    clustering = cluster_graph(graph)

    # --- 3. attribution (before detection: fan-in needs it) -----------------
    yield ProgressEvent(
        event="attribution",
        message="Matching clusters against attribution sources.",
        percent=70,
        detail={"clusters": clustering.cluster_count},
    )

    attribution = load_attribution(
        conn, chain, [w.address for w in graph.wallets.values()]
    )
    apply_attribution_to_clusters(clustering, attribution)
    persist_clusters(conn, case_id, clustering)
    conn.commit()

    yield ProgressEvent(
        event="clustering",
        message=(
            f"{clustering.address_count} addresses grouped into "
            f"{clustering.cluster_count} clusters."
        ),
        percent=78,
        detail={
            "address_count": clustering.address_count,
            "cluster_count": clustering.cluster_count,
            "collapse_ratio": clustering.collapse_ratio,
        },
    )

    # --- 4. detection and scoring ------------------------------------------
    yield ProgressEvent(
        event="scoring",
        message="Running the pattern library and scoring engine.",
        percent=85,
    )

    context = DetectionContext(
        graph=graph,
        clustering=clustering,
        chain=chain,
        now=now,
        attribution=attribution,
    )
    detections = run_detectors(context)
    persist_findings(conn, case_id, detections)

    # Shapes that matched a pattern but were deliberately not reported, with the
    # contextual reason. Persisted so the investigator can see what the engine
    # chose not to flag — silence would be indistinguishable from not looking.
    from backend.models.db import dumps

    conn.execute(
        "UPDATE cases SET suppressed_signals = ? WHERE id = ?",
        (dumps(suppression_report(context)), case_id),
    )

    score = apply_score_to_case(conn, case_id)

    conn.execute(
        "UPDATE cases SET status = 'complete', completed_at = ? WHERE id = ?",
        (utc_now_iso(), case_id),
    )
    record_audit(
        conn, case_id, "investigation_completed", "system",
        {
            "addresses": graph.address_count,
            "clusters": clustering.cluster_count,
            "findings": len(detections),
            "score": score.capped_total,
            "risk_level": score.risk_level,
        },
    )
    conn.commit()

    yield ProgressEvent(
        event="complete",
        message=(
            f"Investigation complete. {len(detections)} finding(s) flagged for review."
        ),
        percent=100,
        counts=_counts(conn, case_id, graph.hops_traversed),
        detail={
            "risk_level": score.risk_level,
            "suspicion_score": score.capped_total,
            "raw_score_total": score.raw_total,
            "score_was_capped": score.was_capped,
            "collapse_ratio": clustering.collapse_ratio,
            "expansion_limited": graph.expansion_limited,
            "expansion_note": graph.expansion_note,
        },
    )
