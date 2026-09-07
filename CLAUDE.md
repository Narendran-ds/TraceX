# CLAUDE.md

Project context for Claude Code. Read this before making changes.

---

## Project

**SIH26183 — Fund-Trail Investigation Assistant**
Smart India Hackathon 2026 · Ministry of Home Affairs · Blockchain & Cybersecurity · Software track

A cybercrime investigator's forensic assistant. Takes a victim-reported suspect wallet address, automatically traces and clusters the transaction graph, detects laundering patterns with an explainable rule engine, attributes the likely cash-out exchange with a confidence score, and produces a tamper-evident PDF report — with human confirmation required on every finding.

**Not** a general crypto analytics tool. **Not** an automated accusation engine. It is decision support for a specific, victim-initiated complaint with a case ID attached.

---

## Non-Negotiable Project Rules

These are correctness requirements, not style preferences. Violating them breaks the project's core claims.

### 1. Never fabricate numbers
No hard-coded confidence percentages, detection rates, counts, or metrics anywhere — not in the UI, not in seed data, not as placeholders. **Every displayed number must be computed at runtime from the actual case.** A placeholder that survives to demo day is a fabricated metric in front of judges.

### 2. Confidence, never certainty
All output language is probabilistic. Write "patterns consistent with layering" — never "this wallet is criminal" or "this wallet belongs to a scammer." Applies to UI copy, report text, API field names, and code comments.

### 3. Explainability is the architecture
The detection engine is a **transparent rule system**, deliberately chosen over a black-box model because output goes into a legal case file. Every score must decompose into per-signal contributions, and every signal must link to the backing transaction hashes. Do not introduce an unexplainable model into the scoring path.

### 4. Cache before you fetch
Every upstream API response is cached to `api_cache` before use. Graph expansion re-queries the same addresses constantly; uncached expansion burns the rate-limit quota in seconds. **The full pipeline must run offline from cache.**

### 5. Rate limits are hard constraints
All upstream calls go through a token-bucket limiter matched to the provider's documented limit. Never fire uncapped requests.

### 6. Fail honestly, degrade gracefully
Rate limit hit → return the partial graph with an "expansion limited" flag. Trail hits a bridge/mixer → report "trail confidence degrades here." Cluster unattributed → say "unattributed exit cluster." **Never guess to fill a gap, never surface a raw error.**

### 7. Human-in-the-loop is mandatory
Every finding requires investigator Confirm / Reject / Note. Do not add any path that finalizes a conclusion without human review.

### 8. Attribution needs provenance
Every tag in `attribution_tags` carries `source` and `source_url`. Any attribution claim in a report must be traceable to a public source.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python + FastAPI |
| Graph engine | networkx |
| Database | SQLite (demo) / PostgreSQL (deploy-ready) |
| Cache | `api_cache` table (SQLite) or Redis |
| Frontend | React + Vite + Tailwind |
| Graph viz | react-force-graph / D3 |
| PDF | server-side generation |

**Deliberate non-requirements:** no GPU, no model training, no cloud dependency. The whole system runs on one laptop, offline, from cache. This is a robustness feature — don't introduce dependencies that break it.

---

## Repository Layout

```
/backend
  /adapters        chain adapters (ChainAdapter interface, bitcoin, ethereum, blockscout)
  /cache           cache layer + token-bucket rate limiter
  /graph           multi-hop graph builder, edge weighting, dust filtering
  /clustering      common-input, change-address, behavioral, CoinJoin detection
  /patterns        the 5-pattern detection library
  /scoring         weighted scoring engine + per-signal breakdown
  /attribution     tagpack/OFAC matching, provenance, coverage reporting
  /evidence        SHA-256 hashing, audit records
  /reports         PDF generation
  /api             FastAPI routes
  /models          DB models + migrations
/frontend
  /screens         intake, dashboard, graph, why-panel, findings, attribution, report
  /components
  /contracts       shared API types (frozen — see below)
/data
  /tagpacks        ingested GraphSense TagPacks
  /ofac            OFAC SDN crypto addresses
  /curated         hand-verified India-relevant exchange addresses
  /evaluation      labeled test set of documented cases
/docs
  MASTER_REPORT.md full project report
  ROADMAP.md       phased plan + progress tracking
```

---

## API Contract

**Frozen in Phase 0.** Frontend builds against these shapes on mocks before the backend is ready. Do not change a shape without updating `/frontend/contracts` and telling both frontend owners.

```
POST   /api/cases                              create case
POST   /api/cases/{id}/investigate             run pipeline (streams progress events)
GET    /api/cases/{id}                         case summary
GET    /api/cases/{id}/graph?collapsed=bool    nodes + edges
GET    /api/cases/{id}/clusters                clusters + confidence
GET    /api/cases/{id}/findings                patterns + score contributions + tx hashes
POST   /api/cases/{id}/findings/{fid}/review   {action: confirm|reject, note}
GET    /api/cases/{id}/attribution             ranked exits + sources
POST   /api/cases/{id}/report                  generate PDF, return path + evidence hash
GET    /api/cases/{id}/evidence/{eid}/verify   recompute hash, return match
GET    /api/health                             API + cache + adapter status
```

`/investigate` **must stream progress** (hop complete, clustering, scoring) so the frontend animates the graph building live rather than showing a spinner.

---

## Core Data Model

Tables: `cases`, `wallets`, `transactions`, `clusters`, `findings`, `attribution_tags`, `evidence_records`, `reports`, `api_cache`.

Key relationships:
- `wallets.cluster_id → clusters.id`
- `findings.evidence_tx_hashes` — array, always populated
- `attribution_tags` — keyed by address, always carries `source` + `source_url`
- `evidence_records.payload_hash` — SHA-256, never regenerate silently on read

Full DDL in `/docs/MASTER_REPORT.md` §11.

---

## Upstream API Constraints

| Provider | Limit | Notes |
|---|---|---|
| **Blockchair** (BTC) | 30 req/min free; 1,000 calls/day without a key; soft 5 req/sec under load | Free non-commercial key by email application. Overage can get the IP blocked. |
| **Etherscan V2** (ETH) | 5 calls/sec, ~100k/day; historical endpoints 2 calls/sec | Limits tightened July 2026 — max records per request dropped to 1,000; some endpoints removed from free tier. Returns 429 under load. |
| **Blockscout** | Fallback adapter | Migration is usually a base-URL + key swap; free tier covers all supported chains. |

Verify current terms before relying on them — they've been moving.

---

## Detection Patterns

Five patterns in `/backend/patterns`, each returning a score contribution + backing tx hashes:

| Pattern | Weight | Signal |
|---|---:|---|
| Rapid fan-out | +20 | One address → N≥5 new addresses in a short window |
| Peel chain | +20 | Chain forwarding a remainder while peeling small amounts |
| Known mixer/sanctioned match | +20 | Direct attribution hit |
| Fan-in consolidation | +15 | N≥5 addresses → one, short window |
| Round-number splitting | +15 | Split into near-equal/round pieces |
| Timing burst | +12 | Abnormally tight tx timing among related addresses |
| Cluster new + burst + dormant | +15 | Disambiguates laundering from legitimate service activity |

**Weights are a documented hand-tuned heuristic.** Do not describe them in code, docs, or UI as "learned" or "trained" — they aren't.

**Critical:** fan-in looks identical for a laundering consolidation and a legitimate exchange deposit. The contextual disambiguation features (cluster age, volume history, attribution match, dormancy) do the real work here. Don't ship fan-in detection without them.

---

## Current Phase

See `/docs/ROADMAP.md` for the full phased plan, exit criteria, and progress table.

**Phases:** P0 Foundations → P1 Data Layer → P2 Graph & Clustering → P3 Detection & Scoring → P4 Attribution → P5 Frontend Core → P6 Workflow & Evidence → P7 Validation → P8 Demo Hardening → P9 Stretch

**A phase is done when its exit criterion passes, not when the code is written.**

Integration checkpoint every 6 hours: everyone merges, full pipeline runs end-to-end.

---

## When Working On This Project

**Do:**
- Compute every displayed value at runtime
- Add tests for pattern detection against the evaluation set in `/data/evaluation`
- Keep the offline-from-cache path working — verify with the network disabled
- Preserve provenance on every attribution claim
- Use probabilistic language in all user-facing strings

**Don't:**
- Hard-code demo numbers "temporarily"
- Add a model to the scoring path that can't explain itself
- Bypass the cache or the rate limiter for convenience
- Introduce a cloud/GPU dependency
- Write accusatory copy anywhere
- Start Phase 9 work before Phase 8's exit criterion passes

---

## Scope Boundaries

**Out of scope, deliberately:**
- Multi-chain breadth beyond BTC + ETH (adapter architecture is stated honestly instead)
- From-scratch GNN (wrong tool for a legal-evidence context)
- Automated exchange reporting or freezing (legally inappropriate to automate)
- Broad chain monitoring (scope explosion + crosses into surveillance framing)

If a proposed feature falls in this list, it doesn't get built — the constraint is intentional, not an oversight.
