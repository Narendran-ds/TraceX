"""API routes — the frozen contract from CLAUDE.md, implemented exactly.

No endpoint here is added, renamed or reshaped relative to that contract.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from backend import config
from backend.api.contracts import (
    AdapterStatus,
    AttributionResponse,
    CaseCreateRequest,
    CaseSummary,
    ClustersResponse,
    FindingsResponse,
    GraphResponse,
    HealthResponse,
    ReportRequest,
    ReportResponse,
    ReviewRequest,
    ReviewResponse,
    VerifyResponse,
)
from backend.api.errors import (
    CaseNotFound,
    EvidenceNotFound,
    FindingNotFound,
    InvalidRequest,
)
from backend.api.pipeline import run_investigation
from backend.api.views import (
    attribution_response,
    case_summary,
    clusters_response,
    finding_view,
    findings_response,
    get_case_row,
    graph_response,
    score_breakdown,
)
from backend.evidence.hashing import verify_evidence
from backend.models.db import DB_PATH, connect, new_id, record_audit, utc_now_iso
from backend.reports.generator import generate_report
from backend.scoring.engine import apply_score_to_case

router = APIRouter(prefix="/api")

API_VERSION = "0.1.0"

VALID_CHAINS = {"bitcoin", "ethereum"}
DEFAULT_REVIEWER = "investigator"


# ===========================================================================
# POST /api/cases
# ===========================================================================

@router.post("/cases", response_model=CaseSummary)
def create_case(request: CaseCreateRequest) -> CaseSummary:
    if request.chain not in VALID_CHAINS:
        raise InvalidRequest(
            f"Chain must be one of {sorted(VALID_CHAINS)}; got {request.chain!r}.",
            {"chain": request.chain},
        )

    address = request.wallet_address.strip()
    if not address:
        raise InvalidRequest("A wallet address is required to open a case.")

    max_hops = request.max_hops or config.DEFAULT_MAX_HOPS
    if not 1 <= max_hops <= config.MAX_HOPS_CEILING:
        raise InvalidRequest(
            f"max_hops must be between 1 and {config.MAX_HOPS_CEILING}.",
            {"max_hops": max_hops},
        )

    conn = connect()
    try:
        # Provenance is decided by what is actually in the cache for this
        # address, not by what the caller claims. A case cannot be opened
        # without the system stating what its data is.
        from backend.cache import store

        entry = store.get_any(request.chain, address, conn=conn)
        if entry is not None:
            provenance = entry.origin
            note = (
                "Transactions for this case are served from cached responses "
                f"fetched from {entry.provider}. Every transaction hash is real and "
                "can be checked on a public block explorer."
                if entry.origin == "live_cached"
                else (
                    "Transactions for this case come from a constructed scenario used "
                    "to exercise the detection engine offline. They are not real "
                    "on-chain activity. The detection, clustering, attribution and "
                    "scoring applied to them are the same code that runs on live data."
                )
            )
        elif config.ADAPTER_MODE == "live":
            provenance = "live_cached"
            note = (
                "This address is not cached; the system will attempt a live fetch "
                "and cache the response before use."
            )
        else:
            raise InvalidRequest(
                "No cached data exists for this address and the system is running "
                "from cache (offline mode), so there is nothing to trace. Seed this "
                "address first, or start the API with FUNDTRAIL_ADAPTER_MODE=live.",
                {"address": address, "chain": request.chain},
            )

        case_id = new_id()
        conn.execute(
            "INSERT INTO cases (id, complaint_id, wallet_address, chain, status,"
            " data_provenance, provenance_note, max_hops, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (
                case_id,
                request.complaint_id.strip(),
                address,
                request.chain,
                provenance,
                note,
                max_hops,
                utc_now_iso(),
            ),
        )
        record_audit(conn, case_id, "case_created", DEFAULT_REVIEWER, {
            "complaint_id": request.complaint_id,
            "wallet_address": address,
            "chain": request.chain,
            "data_provenance": provenance,
        })
        conn.commit()
        return case_summary(conn, case_id)
    finally:
        conn.close()


# ===========================================================================
# POST /api/cases/{id}/investigate  — streams progress as NDJSON
# ===========================================================================

@router.post("/cases/{case_id}/investigate")
def investigate(case_id: str) -> StreamingResponse:
    """Run the pipeline, streaming progress events one JSON object per line.

    NDJSON over the POST body rather than SSE: the frozen contract puts this on
    POST, and EventSource cannot issue a POST. The frontend reads it with
    fetch() + a ReadableStream reader.
    """
    conn = connect()
    get_case_row(conn, case_id)  # raises CaseNotFound before the stream opens
    conn.close()

    def stream() -> Iterator[bytes]:
        conn = connect()
        try:
            for event in run_investigation(conn, case_id):
                yield (event.model_dump_json() + "\n").encode("utf-8")
        except Exception as exc:  # degrade gracefully, never leak a traceback
            import logging

            logging.getLogger("fundtrail").exception("Investigation failed")
            failure = {
                "event": "error",
                "message": (
                    "The investigation stopped early: "
                    f"{exc.__class__.__name__}. Any results produced before this "
                    "point were saved and are shown below."
                ),
                "hop": None,
                "percent": 100,
                "counts": None,
                "detail": {},
            }
            try:
                conn.execute(
                    "UPDATE cases SET status = 'failed' WHERE id = ?", (case_id,)
                )
                conn.commit()
            except Exception:
                pass
            yield (json.dumps(failure) + "\n").encode("utf-8")
        finally:
            conn.close()

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===========================================================================
# GET /api/cases/{id}
# ===========================================================================

@router.get("/cases/{case_id}", response_model=CaseSummary)
def get_case(case_id: str) -> CaseSummary:
    conn = connect()
    try:
        return case_summary(conn, case_id)
    finally:
        conn.close()


# ===========================================================================
# GET /api/cases/{id}/graph?collapsed=bool
# ===========================================================================

@router.get("/cases/{case_id}/graph", response_model=GraphResponse)
def get_graph(case_id: str, collapsed: bool = Query(True)) -> GraphResponse:
    conn = connect()
    try:
        return graph_response(conn, case_id, collapsed)
    finally:
        conn.close()


# ===========================================================================
# GET /api/cases/{id}/clusters
# ===========================================================================

@router.get("/cases/{case_id}/clusters", response_model=ClustersResponse)
def get_clusters(case_id: str) -> ClustersResponse:
    conn = connect()
    try:
        return clusters_response(conn, case_id)
    finally:
        conn.close()


# ===========================================================================
# GET /api/cases/{id}/findings
# ===========================================================================

@router.get("/cases/{case_id}/findings", response_model=FindingsResponse)
def get_findings(case_id: str) -> FindingsResponse:
    conn = connect()
    try:
        return findings_response(conn, case_id)
    finally:
        conn.close()


# ===========================================================================
# POST /api/cases/{id}/findings/{fid}/review
# ===========================================================================

@router.post("/cases/{case_id}/findings/{finding_id}/review",
             response_model=ReviewResponse)
def review_finding(
    case_id: str, finding_id: str, request: ReviewRequest
) -> ReviewResponse:
    if request.action not in ("confirm", "reject"):
        raise InvalidRequest(
            "action must be 'confirm' or 'reject'.", {"action": request.action}
        )

    conn = connect()
    try:
        get_case_row(conn, case_id)
        row = conn.execute(
            "SELECT * FROM findings WHERE id = ? AND case_id = ?",
            (finding_id, case_id),
        ).fetchone()
        if row is None:
            raise FindingNotFound(finding_id)

        status = "confirmed" if request.action == "confirm" else "rejected"
        reviewer = (request.reviewed_by or DEFAULT_REVIEWER).strip() or DEFAULT_REVIEWER
        reviewed_at = utc_now_iso()

        conn.execute(
            "UPDATE findings SET status = ?, analyst_note = ?, reviewed_by = ?,"
            " reviewed_at = ? WHERE id = ?",
            (status, request.note, reviewer, reviewed_at, finding_id),
        )

        # The score is recomputed server-side, so rejecting a finding visibly
        # moves the case score. The client re-fetches GET /api/cases/{id};
        # no extra endpoint is invented for this.
        score = apply_score_to_case(conn, case_id)

        record_audit(conn, case_id, f"finding_{status}", reviewer, {
            "finding_id": finding_id,
            "pattern_type": row["pattern_type"],
            "note": request.note,
            "score_after": score.capped_total,
        })
        conn.commit()

        updated = conn.execute(
            "SELECT * FROM findings WHERE id = ?", (finding_id,)
        ).fetchone()
        case = get_case_row(conn, case_id)

        return ReviewResponse(
            finding=finding_view(updated),
            score=score_breakdown(conn, case_id, score),
            case_status=case["status"],
        )
    finally:
        conn.close()


# ===========================================================================
# GET /api/cases/{id}/attribution
# ===========================================================================

@router.get("/cases/{case_id}/attribution", response_model=AttributionResponse)
def get_attribution(case_id: str) -> AttributionResponse:
    conn = connect()
    try:
        return attribution_response(conn, case_id)
    finally:
        conn.close()


# ===========================================================================
# POST /api/cases/{id}/report
# ===========================================================================

@router.post("/cases/{case_id}/report", response_model=ReportResponse)
def create_report(case_id: str, request: Optional[ReportRequest] = None) -> ReportResponse:
    conn = connect()
    try:
        get_case_row(conn, case_id)
        generated_by = (
            (request.generated_by if request else None) or DEFAULT_REVIEWER
        ).strip() or DEFAULT_REVIEWER

        result = generate_report(conn, case_id, generated_by)

        report_id = new_id()
        conn.execute(
            "INSERT INTO reports (id, case_id, pdf_path, evidence_id, evidence_hash,"
            " generated_at, generated_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                report_id,
                case_id,
                result["pdf_path"],
                result["evidence_id"],
                result["evidence_hash"],
                result["generated_at"],
                generated_by,
            ),
        )
        record_audit(conn, case_id, "report_generated", generated_by, {
            "report_id": report_id,
            "evidence_id": result["evidence_id"],
            "evidence_hash": result["evidence_hash"],
        })
        conn.commit()

        return ReportResponse(
            report_id=report_id,
            case_id=case_id,
            pdf_path=result["pdf_path"],
            download_url=f"/api/cases/{case_id}/report/{report_id}/download",
            evidence_id=result["evidence_id"],
            evidence_hash=result["evidence_hash"],
            hash_algorithm="sha256",
            generated_at=result["generated_at"],
            generated_by=generated_by,
        )
    finally:
        conn.close()


@router.get("/cases/{case_id}/report/{report_id}/download")
def download_report(case_id: str, report_id: str):
    """Serve a generated PDF. Not a contract endpoint — it backs `download_url`."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM reports WHERE id = ? AND case_id = ?", (report_id, case_id)
        ).fetchone()
        if row is None:
            raise CaseNotFound(case_id)
        path = Path(row["pdf_path"])
        if not path.exists():
            raise InvalidRequest(
                "The generated PDF is no longer on disk. Generate the report again.",
                {"report_id": report_id},
            )
        return FileResponse(
            str(path), media_type="application/pdf", filename=path.name
        )
    finally:
        conn.close()


# ===========================================================================
# GET /api/cases/{id}/evidence/{eid}/verify
# ===========================================================================

@router.get("/cases/{case_id}/evidence/{evidence_id}/verify",
            response_model=VerifyResponse)
def verify(case_id: str, evidence_id: str) -> VerifyResponse:
    conn = connect()
    try:
        get_case_row(conn, case_id)
        result = verify_evidence(conn, case_id, evidence_id)
        if result is None:
            raise EvidenceNotFound(evidence_id)
        record_audit(conn, case_id, "evidence_verified", DEFAULT_REVIEWER, {
            "evidence_id": evidence_id, "match": result["match"],
        })
        conn.commit()
        return VerifyResponse(**result)
    finally:
        conn.close()


# ===========================================================================
# GET /api/health
# ===========================================================================

def _adapter_statuses() -> list[AdapterStatus]:
    from backend.adapters.bitcoin import BlockchairAdapter
    from backend.adapters.ethereum import BlockscoutAdapter, EtherscanAdapter
    from backend.adapters.fixture import FixtureAdapter

    fixture = FixtureAdapter("bitcoin,ethereum")
    blockscout = BlockscoutAdapter()
    etherscan = EtherscanAdapter()
    blockchair = BlockchairAdapter()

    return [
        AdapterStatus(name="fixture", chain="bitcoin,ethereum", mode="cache",
                      configured=True, note=fixture.status_note()),
        AdapterStatus(name="blockscout", chain="ethereum", mode="live",
                      configured=blockscout.is_configured(),
                      note=blockscout.status_note()),
        AdapterStatus(name="etherscan", chain="ethereum", mode="live",
                      configured=etherscan.is_configured(),
                      note=etherscan.status_note()),
        AdapterStatus(name="blockchair", chain="bitcoin", mode="live",
                      configured=blockchair.is_configured(),
                      note=blockchair.status_note()),
    ]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """API + cache + adapter status. Every count is read from the live DB."""
    database = "ok"
    cache_entries = 0
    cache_origins: dict = {}
    seeded_cases = 0
    tag_count = 0

    try:
        conn = connect()
        try:
            cache_entries = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
            for row in conn.execute(
                "SELECT origin, COUNT(*) AS n FROM api_cache GROUP BY origin"
            ):
                cache_origins[row["origin"]] = row["n"]
            seeded_cases = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            tag_count = conn.execute(
                "SELECT COUNT(*) FROM attribution_tags"
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # surfaced as degraded, never raised at the client
        database = f"unavailable: {exc.__class__.__name__}"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        api_version=API_VERSION,
        database=database,
        database_path=str(DB_PATH),
        cache_entries=cache_entries,
        cache_origins=cache_origins,
        seeded_cases=seeded_cases,
        attribution_tags=tag_count,
        adapters=_adapter_statuses(),
        adapter_mode=config.ADAPTER_MODE,
        offline_capable=cache_entries > 0,
    )
