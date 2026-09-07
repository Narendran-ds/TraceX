/**
 * Frozen API contract — TypeScript mirror of backend/api/contracts.py.
 *
 * These shapes are frozen (CLAUDE.md "API Contract"). If a shape ever has to
 * change, both files change together and both frontend owners are told.
 *
 * Note that every number here is computed by the backend from the actual case.
 * The UI renders what it is given and never substitutes a placeholder.
 */

export type Chain = 'bitcoin' | 'ethereum';
export type Provenance = 'live_cached' | 'synthetic_scenario';
export type CaseStatus = 'pending' | 'running' | 'complete' | 'failed';
export type FindingStatus = 'flagged' | 'confirmed' | 'rejected';
export type NodeRole =
  | 'victim'
  | 'suspect'
  | 'intermediary'
  | 'exchange'
  | 'mixer'
  | 'bridge';

export interface ProvenanceInfo {
  kind: Provenance;
  label: string;
  note: string;
}

/* -- POST /api/cases ----------------------------------------------------- */

export interface CaseCreateRequest {
  complaint_id: string;
  wallet_address: string;
  chain: Chain;
  max_hops?: number | null;
}

export interface CaseCounts {
  wallets_discovered: number;
  transactions_analysed: number;
  clusters_identified: number;
  findings_flagged: number;
  findings_confirmed: number;
  findings_rejected: number;
  hops_traversed: number;
}

export interface CaseSummary {
  id: string;
  complaint_id: string;
  wallet_address: string;
  chain: Chain;
  status: CaseStatus;
  created_at: string;
  completed_at: string | null;
  provenance: ProvenanceInfo;
  risk_level: string | null;
  suspicion_score: number | null;
  score_cap: number;
  raw_score_total: number | null;
  score_was_capped: boolean;
  obfuscation_detected: boolean | null;
  counts: CaseCounts;
  total_amount_traced: number;
  amount_unit: string;
  expansion_limited: boolean;
  expansion_note: string;
  max_hops: number;
}

/* -- POST /api/cases/{id}/investigate (NDJSON stream) -------------------- */

export type ProgressEventName =
  | 'started'
  | 'hop_complete'
  | 'clustering'
  | 'scoring'
  | 'attribution'
  | 'complete'
  | 'error';

export interface ProgressEvent {
  event: ProgressEventName;
  message: string;
  hop: number | null;
  percent: number;
  counts: CaseCounts | null;
  detail: Record<string, unknown>;
}

/* -- GET /api/cases/{id}/graph ------------------------------------------ */

export interface GraphNode {
  id: string;
  label: string;
  kind: 'address' | 'cluster';
  role: NodeRole;
  hop_depth: number;
  total_in: number;
  total_out: number;
  tx_count: number;
  member_count: number;
  cluster_id: string | null;
  attributed_entity: string | null;
  attribution_source: string | null;
  on_primary_trail: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  amount: number;
  tx_count: number;
  edge_weight: number;
  hop_depth: number;
  first_timestamp: string;
  last_timestamp: string;
  tx_hashes: string[];
  on_primary_trail: boolean;
}

export interface GraphResponse {
  case_id: string;
  collapsed: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  amount_unit: string;
  address_count: number;
  cluster_count: number;
  collapse_ratio: number | null;
  expansion_limited: boolean;
  expansion_note: string;
}

/* -- GET /api/cases/{id}/clusters --------------------------------------- */

export interface ClusterView {
  id: string;
  label: string;
  member_count: number;
  members: string[];
  clustering_method: 'common_input' | 'change_address' | 'behavioral' | 'singleton';
  cluster_confidence: number;
  confidence_notes: string[];
  attributed_entity: string | null;
  attribution_source: string | null;
  role: NodeRole;
  is_terminal: boolean;
  total_in: number;
  total_out: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface ClustersResponse {
  case_id: string;
  clusters: ClusterView[];
  address_count: number;
  cluster_count: number;
  collapse_ratio: number | null;
  amount_unit: string;
}

/* -- GET /api/cases/{id}/findings --------------------------------------- */

export type PatternType =
  | 'fan_out'
  | 'fan_in'
  | 'peel_chain'
  | 'round_split'
  | 'timing_burst'
  | 'sanctioned_match'
  | 'cluster_context';

export interface FindingView {
  id: string;
  pattern_type: PatternType;
  title: string;
  description: string;
  score_contribution: number;
  evidence_tx_hashes: string[];
  evidence_detail: Record<string, unknown>;
  subject_addresses: string[];
  detected_at: string;
  status: FindingStatus;
  analyst_note: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface ScoreBreakdown {
  contributions: FindingView[];
  raw_total: number;
  cap: number;
  capped_total: number;
  was_capped: boolean;
  excluded_rejected_total: number;
  risk_level: string;
  weights_note: string;
}

/**
 * A shape that matched a pattern but was deliberately not reported (currently
 * the fan-in disambiguation). Surfaced because silence about a suppressed
 * signal is indistinguishable from never having looked for it.
 */
export interface SuppressedSignal {
  pattern_type: PatternType;
  subject: string;
  reason: string;
  detail: Record<string, unknown>;
}

export interface FindingsResponse {
  case_id: string;
  findings: FindingView[];
  score: ScoreBreakdown;
  suppressed_signals: SuppressedSignal[];
}

/* -- POST /api/cases/{id}/findings/{fid}/review ------------------------- */

export interface ReviewRequest {
  action: 'confirm' | 'reject';
  note?: string | null;
  reviewed_by?: string | null;
}

export interface ReviewResponse {
  finding: FindingView;
  score: ScoreBreakdown;
  case_status: CaseStatus;
}

/* -- GET /api/cases/{id}/attribution ------------------------------------ */

export interface AttributionSource {
  source: string;
  source_url: string;
  entity_name: string;
  entity_type: string;
  confidence: number;
  matched_address: string;
  last_updated: string;
}

export interface ExitCandidate {
  cluster_id: string;
  cluster_label: string;
  entity_name: string | null;
  entity_type: string | null;
  attributed: boolean;
  confidence: number;
  confidence_basis: string[];
  amount_received: number;
  member_count: number;
  hop_depth: number;
  sources: AttributionSource[];
  confidence_boundary: boolean;
  boundary_note: string | null;
}

export interface AttributionCoverage {
  source_count: number;
  entity_count: number;
  tag_count: number;
  sources: string[];
  statement: string;
}

export interface AttributionResponse {
  case_id: string;
  candidates: ExitCandidate[];
  coverage: AttributionCoverage;
  amount_unit: string;
  unattributed_terminal_clusters: number;
  trail_degraded: boolean;
  trail_degraded_note: string;
}

/* -- POST /api/cases/{id}/report ---------------------------------------- */

export interface ReportRequest {
  generated_by?: string | null;
}

export interface ReportResponse {
  report_id: string;
  case_id: string;
  pdf_path: string;
  download_url: string;
  evidence_id: string;
  evidence_hash: string;
  hash_algorithm: string;
  generated_at: string;
  generated_by: string;
}

/* -- GET /api/cases/{id}/evidence/{eid}/verify -------------------------- */

export interface VerifyResponse {
  evidence_id: string;
  case_id: string;
  stored_hash: string;
  recomputed_hash: string;
  match: boolean;
  hash_algorithm: string;
  created_at: string;
  verified_at: string;
}

/* -- GET /api/health ---------------------------------------------------- */

export interface AdapterStatus {
  name: string;
  chain: string;
  mode: string;
  configured: boolean;
  note: string;
}

export interface HealthResponse {
  status: string;
  api_version: string;
  database: string;
  database_path: string;
  cache_entries: number;
  cache_origins: Record<string, number>;
  seeded_cases: number;
  attribution_tags: number;
  adapters: AdapterStatus[];
  adapter_mode: string;
  offline_capable: boolean;
}
