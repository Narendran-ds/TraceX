"""Frozen API contract.

These shapes are frozen (CLAUDE.md "API Contract"). The TypeScript mirror lives
at frontend/src/contracts/api.ts and must be updated in lockstep if a shape ever
changes — which requires telling both frontend owners first.

Every numeric field here is computed at runtime from the case. None of them has
a default that could survive to a demo as a fabricated figure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# --- shared ----------------------------------------------------------------

Chain = str  # 'bitcoin' | 'ethereum'
Provenance = str  # 'live_cached' | 'synthetic_scenario'


class ProvenanceInfo(BaseModel):
    """What the case's underlying data actually is.

    Carried on every case-level response, rendered in the dashboard header and
    in the body of the PDF. A synthetic scenario is honest when it is labelled;
    it is not honest when it is silent.
    """

    kind: Provenance
    label: str
    note: str


# --- POST /api/cases -------------------------------------------------------

class CaseCreateRequest(BaseModel):
    complaint_id: str = Field(..., min_length=1, max_length=64)
    wallet_address: str = Field(..., min_length=1, max_length=128)
    chain: Chain
    max_hops: Optional[int] = None


class CaseCounts(BaseModel):
    wallets_discovered: int
    transactions_analysed: int
    clusters_identified: int
    findings_flagged: int
    findings_confirmed: int
    findings_rejected: int
    hops_traversed: int


class CaseSummary(BaseModel):
    """GET /api/cases/{id} and the POST /api/cases response."""

    id: str
    complaint_id: str
    wallet_address: str
    chain: Chain
    status: str
    created_at: str
    completed_at: Optional[str] = None

    provenance: ProvenanceInfo

    # Null until the pipeline has run. The UI renders "not yet computed" rather
    # than a placeholder number.
    risk_level: Optional[str] = None
    suspicion_score: Optional[float] = None
    score_cap: float
    raw_score_total: Optional[float] = None
    score_was_capped: bool = False

    obfuscation_detected: Optional[bool] = None
    counts: CaseCounts

    total_amount_traced: float
    amount_unit: str

    expansion_limited: bool
    expansion_note: str

    max_hops: int


# --- POST /api/cases/{id}/investigate (NDJSON stream) ----------------------

class ProgressEvent(BaseModel):
    """One line of the NDJSON progress stream.

    Streamed rather than returned in one block so the frontend animates the
    graph building live instead of showing a spinner (master report §10).
    """

    event: str  # started|hop_complete|clustering|scoring|attribution|complete|error
    message: str
    hop: Optional[int] = None
    percent: int
    counts: Optional[CaseCounts] = None
    detail: Dict[str, Any] = Field(default_factory=dict)


# --- GET /api/cases/{id}/graph --------------------------------------------

class GraphNode(BaseModel):
    id: str            # address, or cluster id when collapsed=true
    label: str
    kind: str          # 'address' | 'cluster'
    role: str          # victim|suspect|intermediary|exchange|mixer|bridge
    hop_depth: int
    total_in: float
    total_out: float
    tx_count: int
    member_count: int  # 1 for an address node
    cluster_id: Optional[str] = None
    attributed_entity: Optional[str] = None
    attribution_source: Optional[str] = None
    on_primary_trail: bool = False


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    amount: float
    tx_count: int
    edge_weight: float
    hop_depth: int
    first_timestamp: str
    last_timestamp: str
    tx_hashes: List[str]
    on_primary_trail: bool = False


class GraphResponse(BaseModel):
    case_id: str
    collapsed: bool
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    amount_unit: str
    # Computed, not asserted: raw address count vs. cluster count for this case.
    address_count: int
    cluster_count: int
    collapse_ratio: Optional[float] = None
    expansion_limited: bool
    expansion_note: str


# --- GET /api/cases/{id}/clusters -----------------------------------------

class ClusterView(BaseModel):
    id: str
    label: str
    member_count: int
    members: List[str]
    clustering_method: str
    cluster_confidence: float
    confidence_notes: List[str]
    attributed_entity: Optional[str] = None
    attribution_source: Optional[str] = None
    role: str
    is_terminal: bool
    total_in: float
    total_out: float
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class ClustersResponse(BaseModel):
    case_id: str
    clusters: List[ClusterView]
    address_count: int
    cluster_count: int
    collapse_ratio: Optional[float] = None
    amount_unit: str


# --- GET /api/cases/{id}/findings -----------------------------------------

class FindingView(BaseModel):
    id: str
    pattern_type: str
    title: str
    description: str
    score_contribution: float
    evidence_tx_hashes: List[str]
    evidence_detail: Dict[str, Any]
    subject_addresses: List[str]
    detected_at: str
    status: str
    analyst_note: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None


class ScoreBreakdown(BaseModel):
    """The Why panel's data.

    contributions sum to raw_total; raw_total then meets the cap. Both are shown
    so the arithmetic on screen is verifiable by anyone reading it.
    """

    contributions: List[FindingView]
    raw_total: float
    cap: float
    capped_total: float
    was_capped: bool
    excluded_rejected_total: float
    risk_level: str
    weights_note: str


class SuppressedSignal(BaseModel):
    """A shape that matched a pattern but was deliberately not reported.

    Currently the fan-in disambiguation. Surfaced because silence about a
    suppressed signal is indistinguishable from never having looked for it —
    and this is the evidence that the contextual features actually work.
    """

    pattern_type: str
    subject: str
    reason: str
    detail: Dict[str, Any] = Field(default_factory=dict)


class FindingsResponse(BaseModel):
    case_id: str
    findings: List[FindingView]
    score: ScoreBreakdown
    suppressed_signals: List[SuppressedSignal] = Field(default_factory=list)


# --- POST /api/cases/{id}/findings/{fid}/review ---------------------------

class ReviewRequest(BaseModel):
    action: str  # 'confirm' | 'reject'
    note: Optional[str] = None
    reviewed_by: Optional[str] = None


class ReviewResponse(BaseModel):
    finding: FindingView
    score: ScoreBreakdown
    case_status: str


# --- GET /api/cases/{id}/attribution --------------------------------------

class AttributionSource(BaseModel):
    source: str
    source_url: str
    entity_name: str
    entity_type: str
    confidence: float
    matched_address: str
    last_updated: str


class ExitCandidate(BaseModel):
    cluster_id: str
    cluster_label: str
    entity_name: Optional[str] = None
    entity_type: Optional[str] = None
    attributed: bool
    confidence: float
    confidence_basis: List[str]
    amount_received: float
    member_count: int
    hop_depth: int
    sources: List[AttributionSource]
    # Set when the trail reaches a bridge/mixer: we report the boundary rather
    # than guessing what is on the other side.
    confidence_boundary: bool = False
    boundary_note: Optional[str] = None


class AttributionCoverage(BaseModel):
    """Never implies exhaustive coverage — the numbers are counted from the DB."""

    source_count: int
    entity_count: int
    tag_count: int
    sources: List[str]
    statement: str


class AttributionResponse(BaseModel):
    case_id: str
    candidates: List[ExitCandidate]
    coverage: AttributionCoverage
    amount_unit: str
    unattributed_terminal_clusters: int
    trail_degraded: bool
    trail_degraded_note: str


# --- POST /api/cases/{id}/report ------------------------------------------

class ReportRequest(BaseModel):
    generated_by: Optional[str] = None


class ReportResponse(BaseModel):
    report_id: str
    case_id: str
    pdf_path: str
    download_url: str
    evidence_id: str
    evidence_hash: str
    hash_algorithm: str
    generated_at: str
    generated_by: str


# --- GET /api/cases/{id}/evidence/{eid}/verify ----------------------------

class VerifyResponse(BaseModel):
    evidence_id: str
    case_id: str
    stored_hash: str
    recomputed_hash: str
    match: bool
    hash_algorithm: str
    created_at: str
    verified_at: str


# --- GET /api/health ------------------------------------------------------

class AdapterStatus(BaseModel):
    name: str
    chain: str
    mode: str
    configured: bool
    note: str


class HealthResponse(BaseModel):
    status: str
    api_version: str
    database: str
    database_path: str
    cache_entries: int
    cache_origins: Dict[str, int]
    seeded_cases: int
    attribution_tags: int
    adapters: List[AdapterStatus]
    adapter_mode: str
    offline_capable: bool
