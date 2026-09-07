-- SIH26183 Fund-Trail Investigation Assistant — schema
-- Follows the DDL in docs/MASTER_REPORT §11, adapted to SQLite for the
-- zero-setup demo path. PostgreSQL type mapping is noted per column where the
-- SQLite type differs, so the deploy-ready migration is mechanical:
--   TEXT (uuid)      -> UUID
--   REAL             -> NUMERIC
--   TEXT (json)      -> JSONB
--   TEXT (iso8601)   -> TIMESTAMP
--   TEXT (json array)-> TEXT[]

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- cases
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cases (
    id                  TEXT PRIMARY KEY,              -- UUID
    complaint_id        TEXT NOT NULL,                 -- e.g. CYB-2026-00124
    wallet_address      TEXT NOT NULL,
    chain               TEXT NOT NULL CHECK (chain IN ('bitcoin', 'ethereum')),
    status              TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'running', 'complete', 'failed')),
    risk_level          TEXT,                          -- computed at runtime, never seeded
    suspicion_score     REAL,                          -- computed at runtime, never seeded

    -- Non-negotiable: a case cannot exist without declaring what data it is
    -- made of. Propagates to the dashboard header, the PDF body, and every
    -- evaluation output line. See CLAUDE.md rule 1 and rule 6.
    data_provenance     TEXT NOT NULL
                          CHECK (data_provenance IN ('live_cached', 'synthetic_scenario')),
    provenance_note     TEXT NOT NULL DEFAULT '',      -- human-readable source statement

    -- Graceful degradation flags (CLAUDE.md rule 6). Never a raw error.
    expansion_limited   INTEGER NOT NULL DEFAULT 0,    -- BOOLEAN
    expansion_note      TEXT NOT NULL DEFAULT '',

    -- Converging shapes the engine checked and deliberately did NOT flag, with
    -- the contextual reason. Stored so a suppressed signal is distinguishable
    -- from a signal nobody looked for. JSON array.
    suppressed_signals  TEXT NOT NULL DEFAULT '[]',

    max_hops            INTEGER NOT NULL DEFAULT 4,
    created_at          TEXT NOT NULL,                 -- TIMESTAMP (ISO8601 UTC)
    completed_at        TEXT
);

-- ---------------------------------------------------------------------------
-- clusters  (declared before wallets: wallets.cluster_id references it)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clusters (
    id                  TEXT PRIMARY KEY,              -- UUID
    case_id             TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    cluster_label       TEXT NOT NULL,
    member_count        INTEGER NOT NULL DEFAULT 0,
    clustering_method   TEXT NOT NULL
                          CHECK (clustering_method IN ('common_input', 'change_address',
                                                       'behavioral', 'singleton')),
    cluster_confidence  REAL NOT NULL,                 -- 0..1, computed
    -- Reasons the confidence was reduced (e.g. CoinJoin-like structure). We
    -- lower confidence rather than assert a false merge. JSON array of strings.
    confidence_notes    TEXT NOT NULL DEFAULT '[]',
    attributed_entity   TEXT,                          -- nullable; NULL => unattributed
    attribution_source  TEXT,                          -- tagpack | ofac_sdn | manual_curated
    role                TEXT NOT NULL DEFAULT 'intermediary'
                          CHECK (role IN ('suspect', 'intermediary', 'exchange',
                                          'mixer', 'bridge', 'victim')),
    is_terminal         INTEGER NOT NULL DEFAULT 0,    -- BOOLEAN
    first_seen          TEXT,
    last_seen           TEXT,
    total_in            REAL NOT NULL DEFAULT 0,
    total_out           REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_clusters_case ON clusters(case_id);

-- ---------------------------------------------------------------------------
-- wallets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wallets (
    id                  TEXT PRIMARY KEY,              -- UUID
    case_id             TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    address             TEXT NOT NULL,
    hop_depth           INTEGER NOT NULL,
    first_seen          TEXT,
    last_seen           TEXT,
    total_in            REAL NOT NULL DEFAULT 0,
    total_out           REAL NOT NULL DEFAULT 0,
    tx_count            INTEGER NOT NULL DEFAULT 0,
    cluster_id          TEXT REFERENCES clusters(id) ON DELETE SET NULL,
    UNIQUE (case_id, address)
);

CREATE INDEX IF NOT EXISTS idx_wallets_case ON wallets(case_id);
CREATE INDEX IF NOT EXISTS idx_wallets_cluster ON wallets(cluster_id);

-- ---------------------------------------------------------------------------
-- transactions  (graph edges)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id                  TEXT PRIMARY KEY,              -- UUID
    case_id             TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    tx_hash             TEXT NOT NULL,
    from_address        TEXT NOT NULL,
    to_address          TEXT NOT NULL,
    amount              REAL NOT NULL,                 -- native units (BTC / ETH)
    amount_inr          REAL,                          -- converted at the recorded rate
    fx_rate_source      TEXT,                          -- provenance for the INR conversion
    timestamp           TEXT NOT NULL,                 -- TIMESTAMP (ISO8601 UTC)
    chain               TEXT NOT NULL,
    hop_depth           INTEGER NOT NULL,
    edge_weight         REAL NOT NULL DEFAULT 0,       -- computed: amount x time-decay x depth-decay
    is_dust             INTEGER NOT NULL DEFAULT 0,    -- BOOLEAN
    UNIQUE (case_id, tx_hash, from_address, to_address)
);

CREATE INDEX IF NOT EXISTS idx_tx_case ON transactions(case_id);
CREATE INDEX IF NOT EXISTS idx_tx_from ON transactions(case_id, from_address);
CREATE INDEX IF NOT EXISTS idx_tx_to ON transactions(case_id, to_address);

-- ---------------------------------------------------------------------------
-- findings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS findings (
    id                  TEXT PRIMARY KEY,              -- UUID
    case_id             TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    pattern_type        TEXT NOT NULL,                 -- fan_out|fan_in|peel_chain|
                                                       -- round_split|timing_burst|
                                                       -- sanctioned_match|cluster_context
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,                 -- probabilistic phrasing, always
    score_contribution  REAL NOT NULL,                 -- the documented hand-tuned weight
    -- JSON array of tx hashes. CLAUDE.md: always populated, never empty.
    evidence_tx_hashes  TEXT NOT NULL,
    -- JSON object of the computed observations behind the flag (counts, windows,
    -- ratios). Every number the Why panel shows comes from here.
    evidence_detail     TEXT NOT NULL DEFAULT '{}',
    subject_addresses   TEXT NOT NULL DEFAULT '[]',    -- JSON array
    detected_at         TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'flagged'
                          CHECK (status IN ('flagged', 'confirmed', 'rejected')),
    analyst_note        TEXT,
    reviewed_by         TEXT,
    reviewed_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_findings_case ON findings(case_id);

-- ---------------------------------------------------------------------------
-- attribution_tags
-- Every tag carries source + source_url. Both NOT NULL: an attribution claim
-- that cannot be traced to a public source does not get stored (CLAUDE.md r8).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attribution_tags (
    address             TEXT NOT NULL,
    chain               TEXT NOT NULL,
    entity_name         TEXT NOT NULL,
    entity_type         TEXT NOT NULL
                          CHECK (entity_type IN ('exchange', 'mixer', 'sanctioned',
                                                 'ransomware', 'service', 'bridge')),
    source              TEXT NOT NULL,                 -- graphsense_tagpack|ofac_sdn|manual_curated
    source_url          TEXT NOT NULL,                 -- dereferenceable provenance link
    confidence          REAL NOT NULL,
    last_updated        TEXT NOT NULL,
    PRIMARY KEY (address, chain, source)
);

CREATE INDEX IF NOT EXISTS idx_tags_address ON attribution_tags(address);

-- ---------------------------------------------------------------------------
-- evidence_records
-- payload_hash is SHA-256 over the canonical JSON serialization of payload_json.
-- Never regenerated silently on read; /verify recomputes and compares.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence_records (
    id                  TEXT PRIMARY KEY,              -- UUID
    case_id             TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    payload_json        TEXT NOT NULL,                 -- JSONB
    payload_hash        TEXT NOT NULL,                 -- SHA-256 hex
    hash_algorithm      TEXT NOT NULL DEFAULT 'sha256',
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence_records(case_id);

-- ---------------------------------------------------------------------------
-- reports
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reports (
    id                  TEXT PRIMARY KEY,              -- UUID
    case_id             TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    pdf_path            TEXT NOT NULL,
    evidence_id         TEXT NOT NULL REFERENCES evidence_records(id) ON DELETE CASCADE,
    evidence_hash       TEXT NOT NULL,
    generated_at        TEXT NOT NULL,
    generated_by        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_case ON reports(case_id);

-- ---------------------------------------------------------------------------
-- api_cache
-- Every upstream response lands here before use (CLAUDE.md rule 4). The offline
-- demo path and the cached-live path read from exactly this table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_cache (
    cache_key           TEXT PRIMARY KEY,              -- chain:endpoint:address
    response_json       TEXT NOT NULL,                 -- JSONB
    provider            TEXT NOT NULL,
    fetched_at          TEXT NOT NULL,
    ttl_seconds         INTEGER NOT NULL DEFAULT 604800,
    -- 'live_cached'      : fetched from a real upstream API and stored
    -- 'synthetic_scenario': constructed scenario for the offline demo path
    origin              TEXT NOT NULL
                          CHECK (origin IN ('live_cached', 'synthetic_scenario'))
);

-- ---------------------------------------------------------------------------
-- audit_log — every case action, for the case file
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id                  TEXT PRIMARY KEY,              -- UUID
    case_id             TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    action              TEXT NOT NULL,
    detail              TEXT NOT NULL DEFAULT '{}',    -- JSONB
    actor               TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);
