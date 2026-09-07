# SIH26183 — Phased Roadmap

**Project:** Fund-Trail Investigation Assistant
**Team size:** 6
**Tracking rule:** a phase is only "done" when its **exit criterion** passes, not when the code is written.

---

## Phase Overview

| Phase | Name | When | Blocking? | Status |
|---|---|---|---|---|
| **P0** | Foundations & Prep | Pre-hackathon | 🔴 Blocks everything | ☐ Not started |
| **P1** | Data Layer | Hours 0–5 | 🔴 Blocks P2 | ☐ Not started |
| **P2** | Graph & Clustering Engine | Hours 3–10 | 🔴 Blocks P3 | ☐ Not started |
| **P3** | Detection & Scoring Engine | Hours 8–16 | 🔴 Core value | ☐ Not started |
| **P4** | Attribution Layer | Hours 12–18 | 🟡 Answers the PS | ☐ Not started |
| **P5** | Frontend Core | Hours 10–24 | 🔴 Demo centerpiece | ☐ Not started |
| **P6** | Workflow & Evidence | Hours 22–30 | 🟡 Deployability | ☐ Not started |
| **P7** | Validation & Metrics | Hours 28–33 | 🔴 Credibility | ☐ Not started |
| **P8** | Demo Hardening & Pitch | Hours 30–36 | 🔴 Everything rides on it | ☐ Not started |
| **P9** | Stretch Features | Only if P0–P8 done | ⚪ Optional | ☐ Not started |

> Phases **overlap deliberately**. P5 (frontend) starts against mocked contracts before P3/P4 are finished — that's why the API contract is frozen in P0.

---

## Dependency Map

```
P0 Foundations
     │
     ├──────────────────────────────────────┐
     ▼                                      ▼
P1 Data Layer                        P5 Frontend Core
     │                               (starts on mocks)
     ▼                                      │
P2 Graph & Clustering                       │
     │                                      │
     ▼                                      │
P3 Detection & Scoring ─────────────────────┤
     │                                      │
     ▼                                      │
P4 Attribution ─────────────────────────────┤
                                            ▼
                                   P6 Workflow & Evidence
                                            │
                                            ▼
                                   P7 Validation & Metrics
                                            │
                                            ▼
                                   P8 Demo Hardening
                                            │
                                            ▼
                                       P9 Stretch
```

---

## PHASE 0 — Foundations & Prep
**Window:** Pre-hackathon (start now)
**Owner:** Whole team, coordinated by #6
**Why it matters:** Every task here has external lead time or is research-heavy. None of it can be done under time pressure. **A team that skips P0 loses the hackathon in P1.**

### Tasks
- [ ] Apply for **Blockchair free non-commercial API key** (email approval — has real lead time)
- [ ] Register **Etherscan API key**; verify current free-tier terms (they tightened in July 2026)
- [ ] Register **Blockscout** key as fallback adapter
- [ ] Freeze the **API contract** (JSON request/response shapes for all endpoints) — unblocks parallel frontend work
- [ ] Scaffold repo: backend, frontend, shared contract types, seed DB migrations
- [ ] Ingest **GraphSense TagPacks** into `attribution_tags` (preserve `source` + `source_url`)
- [ ] Ingest **OFAC SDN crypto address list**
- [ ] Hand-curate a small **India-relevant exchange deposit address list** (quality over quantity)
- [ ] Select **2–3 real demo wallet addresses**: one clean trace, one with a documented laundering pattern, one that dead-ends at a bridge (the deliberate failure demo)
- [ ] Build the **labeled evaluation set** (15–30 publicly documented cases with known laundering paths)
- [ ] Manually trace **one case by hand with a stopwatch** — this is your baseline number for the demo
- [ ] Agree branch/PR conventions and set up the shared dev environment

### Exit Criterion
✅ All API keys work, the attribution DB is populated and queryable, the evaluation set exists as a file, the API contract is committed, and every member can run the scaffold locally.

---

## PHASE 1 — Data Layer
**Window:** Hours 0–5
**Owner:** #1 (Graph & Chain Engine Lead)
**Why it matters:** The cache layer is the single biggest demo-failure preventer. Build it first, not later.

### Tasks
- [ ] `ChainAdapter` interface (chain-agnostic)
- [ ] `BitcoinAdapter` (Blockchair) — address history, transaction detail
- [ ] `EthereumAdapter` (Etherscan V2) — address history, internal txs, token transfers
- [ ] **Cache layer** — every upstream response cached to `api_cache` before use
- [ ] **Token-bucket rate limiter** per provider (30/min Blockchair, 5/sec Etherscan)
- [ ] Graceful degradation — a rate limit returns partial data with a flag, never a raw 429
- [ ] Offline mode — full pipeline runs from cache with the network disabled
- [ ] Normalize both chains into a common internal transaction model

### Exit Criterion
✅ Fetch a real address on both chains, second fetch served from cache, **entire flow works with WiFi off**.

---

## PHASE 2 — Graph & Clustering Engine
**Window:** Hours 3–10
**Owners:** #1 (graph), #2 (clustering)

### Tasks
- [ ] Multi-hop graph builder (configurable depth, default 4–5)
- [ ] Edge weighting: amount + time-decay + hop-depth confidence decay
- [ ] Dust filtering + depth caps (prevent combinatorial blowup)
- [ ] **Common-input-ownership clustering** (BTC)
- [ ] **Change-address detection** (BTC)
- [ ] **Behavioral clustering** (ETH — funding source, timing correlation, contract interaction)
- [ ] CoinJoin-like structure detection → *reduce* cluster confidence rather than assert a false merge
- [ ] Cluster confidence scoring
- [ ] Persist graph + clusters to DB

### Exit Criterion
✅ A real seed address produces a persisted multi-hop graph, and the **"300+ addresses → ~6 clusters"** collapse demonstrably works on at least one demo wallet.

---

## PHASE 3 — Detection & Scoring Engine
**Window:** Hours 8–16
**Owner:** #3 (Pattern & Scoring Engineer)
**Why it matters:** This is the actual technical differentiator. Without it you're a slow Etherscan.

### Tasks
- [ ] Pattern: **rapid fan-out**
- [ ] Pattern: **fan-in / consolidation**
- [ ] Pattern: **peel chain**
- [ ] Pattern: **round-number splitting**
- [ ] Pattern: **timing burst**
- [ ] **Contextual disambiguation features** — cluster age, volume history, attribution match, dormancy (this is what separates laundering from legitimate exchange consolidation)
- [ ] Weighted scoring engine producing a total + **per-signal contribution breakdown**
- [ ] Every finding links to its backing **transaction hashes**
- [ ] Persist findings to DB

### Exit Criterion
✅ On the known-laundering demo wallet, the engine flags the correct patterns, and the "Why?" breakdown returns real per-signal contributions with real tx hashes attached.

---

## PHASE 4 — Attribution Layer
**Window:** Hours 12–18
**Owner:** #6 (Data & Attribution Lead)

### Tasks
- [ ] Match terminal clusters against `attribution_tags`
- [ ] Rank probable exit points with confidence scores
- [ ] Return **provenance** (`source` + `source_url`) with every match
- [ ] Handle unattributed clusters explicitly → "unattributed exit cluster", never a guess
- [ ] Flag **bridge/swap hops as confidence boundaries** ("trail confidence degrades here")
- [ ] Coverage display: "matched against N sources covering M entities"

### Exit Criterion
✅ A traced case returns a ranked exit-point list with a citable source per match, and the bridge-dead-end demo wallet correctly reports degraded confidence instead of a fabricated answer.

---

## PHASE 5 — Frontend Core
**Window:** Hours 10–24 (starts on mocks from hour 10)
**Owners:** #4 (visualization), #5 (workflow screens)

### Tasks
- [ ] Case Intake screen (wallet + chain + case ID)
- [ ] Investigation Dashboard (live counters, risk level, obfuscation Y/N)
- [ ] **Follow-the-Money graph** — force-directed, animated hop-by-hop
- [ ] **Cluster collapse animation** (the money shot: 300 nodes → 6)
- [ ] Colour-coding by node role: victim / suspect / intermediary / exchange
- [ ] Primary-trail highlighting, everything else dimmed
- [ ] Progressive disclosure — clusters by default, detail on click
- [ ] **Why? panel** with per-signal breakdown + tx hashes
- [ ] Progress streaming from `/investigate` so the graph builds live (no 20-second spinner)

### Exit Criterion
✅ Graph renders and animates from **real backend data**, is legible to someone who has never seen it, and no displayed number is hard-coded.

---

## PHASE 6 — Workflow & Evidence
**Window:** Hours 22–30
**Owner:** #5

### Tasks
- [ ] Findings Review UI — **Confirm / Reject / Add Note** per finding
- [ ] Persist review actions with reviewer + timestamp
- [ ] Attribution Result screen (ranked exits, confidence, sources)
- [ ] PDF report generator — case metadata, financial summary, evidence trail, conclusion
- [ ] **SHA-256 evidence hashing** → `evidence_records`
- [ ] Hash verification endpoint + UI action
- [ ] Audit trail on every case action
- [ ] Language audit: **every assertion is probabilistic** ("patterns consistent with…"), never accusatory

### Exit Criterion
✅ Full flow works end-to-end: **address in → PDF out**, with a verifiable evidence hash and human review recorded.

---

## PHASE 7 — Validation & Metrics
**Window:** Hours 28–33
**Owner:** #3
**Why it matters:** This is where you earn technical credibility — and where the temptation to fabricate is highest. **Do not.**

### Tasks
- [ ] Run the pipeline against the full P0 evaluation set
- [ ] Record: patterns correctly flagged, false positives, false negatives
- [ ] Record: attribution hit rate (correct exit in top-3)
- [ ] Record: **time-to-trace** vs. the P0 manual stopwatch baseline
- [ ] Record: graph scale (addresses/txs processed per run)
- [ ] Build the metrics slide using **only measured numbers**
- [ ] Prepare the deliberate **failure-case demo** (bridge dead-end)

### Exit Criterion
✅ Every number destined for a slide traces back to an actual measured run. Zero invented figures anywhere in the deck.

---

## PHASE 8 — Demo Hardening & Pitch
**Window:** Hours 30–36
**Owner:** #6 (owns this from hour zero, not from hour 30)

### Tasks
- [ ] **FEATURE FREEZE at hour 30** — anything broken is cut, not fixed
- [ ] Pre-warm the demo cache; verify the full demo runs offline
- [ ] Replicate cache to the backup machine
- [ ] Error-path audit — no raw errors reachable from any demo path
- [ ] Record a backup demo video (use only if live fails)
- [ ] Finalize slides: problem → solution → differentiation → metrics → limitations
- [ ] Prepare Q&A answers (see master report §19)
- [ ] **Rehearse end-to-end ×3, out loud, on the actual demo machine**
- [ ] Assign roles: one person drives, one narrates

### Exit Criterion
✅ Three clean rehearsed runs on the demo machine, offline, under 5 minutes, with the Q&A prep drilled.

---

## PHASE 9 — Stretch (only if P0–P8 are genuinely complete)

- [ ] Chronological **Investigation Replay** animation
- [ ] Case management list view (multiple saved cases)
- [ ] Harden the weaker chain adapter
- [ ] Lightweight supervised classifier layer (gradient-boosted trees on graph features)

> **Rule:** no stretch work begins until P8's exit criterion has passed once. A polished replay animation on a broken pipeline scores zero.

---

## Progress Tracking

Update this table at every 6-hour integration checkpoint.

| Checkpoint | Phases expected complete | Actual status | Blockers |
|---|---|---|---|
| Pre-event | P0 | | |
| Hour 6 | P1, P2 in progress | | |
| Hour 12 | P2 ✅, P3 in progress, P5 on mocks | | |
| Hour 18 | P3 ✅, P4 ✅, P5 in progress | | |
| Hour 24 | P5 ✅ | | |
| Hour 30 | P6 ✅ — **FEATURE FREEZE** | | |
| Hour 33 | P7 ✅ | | |
| Hour 36 | P8 ✅ | | |

### Integration Rule
**Every 6 hours, everyone merges and the full pipeline runs end-to-end — however ugly.** A system integrated for the first time at hour 30 will not work.

---

## Kill Criteria — What Gets Cut First Under Time Pressure

Cut in this order, top first:

1. Everything in P9
2. Ethereum adapter polish (demo Bitcoin only, state the adapter architecture honestly)
3. Case management / multi-case views
4. Non-essential patterns (keep fan-out, peel chain, timing burst as the minimum viable three)
5. Hash verification **UI** (keep the hashing itself — it's the credibility feature)

**Never cut:** the cache layer, the cluster-collapse animation, the Why? panel, the human-review step, or the honest-limitations slide.
