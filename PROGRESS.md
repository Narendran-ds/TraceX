# PROGRESS — SIH26183 Fund-Trail Investigation Assistant

Working prototype for the college selection round. **The whole thing runs offline
from cache.** No API key is required, and no network call is made on the demo
path.

Last updated: 2026-09-07

---

## Run it

Three commands. Each is a separate terminal for the last two.

```bash
# 0. one-time setup (from the repo root, E:\Nari\SIH)
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
npm --prefix frontend install

# 1. seed the database (schema + attribution sources + demo scenarios)
.venv/Scripts/python.exe scripts/seed.py --reset

# 2. start the API                      → http://127.0.0.1:8000
.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8000

# 3. start the frontend                 → http://localhost:5173
npm --prefix frontend run dev
```

Then open **http://localhost:5173** and pick a prepared case. Nothing else is
needed — no terminal work during the demo.

> **`python` on this machine is 3.9; the project needs 3.11.** Always use
> `py -3.11` or `.venv/Scripts/python.exe`. In Git Bash the venv python is
> `.venv/Scripts/python.exe`; in PowerShell it is `.venv\Scripts\python.exe`.

### Useful extras

```bash
# rebuild the demo scenarios (also regenerates the intake screen's case list)
.venv/Scripts/python.exe scripts/build_scenarios.py

# full offline end-to-end check: every case, address in → PDF out, hash verified
.venv/Scripts/python.exe scripts/e2e.py

# tests
.venv/Scripts/python.exe -m pytest backend/tests -q      # 139 pass
npm --prefix frontend test                                # 20 pass

# production build + preview (also proxies /api to :8000)
npm --prefix frontend run build
npm --prefix frontend run preview                         # http://localhost:4173

# interactive API docs (FastAPI, free credibility point in a demo)
# http://127.0.0.1:8000/docs
```

---

## What is done

### Backend — complete and committed (`c483058`)

| Area | State |
|---|---|
| **Schema** (`backend/models/schema.sql`) | All 10 documented tables. `cases.data_provenance` is `NOT NULL` + CHECK; `attribution_tags.source` / `source_url` are `NOT NULL`. DB-enforced, not convention. |
| **Cache** (`backend/cache/store.py`) | Every upstream response cached before use. Lookup is *provider-agnostic*, so the offline path and the cached-live path are the same code path. |
| **Rate limiter** (`backend/cache/limiter.py`) | Token bucket per provider, matched to each documented free-tier limit. Raises rather than hanging; callers degrade to a partial graph. |
| **Adapters** (`backend/adapters/`) | `ChainAdapter` + fixture / Blockscout / Etherscan V2 / Blockchair. |
| **Graph** (`backend/graph/`) | Multi-hop builder, `amount × time-decay × depth-decay` weighting, dust filter, per-hop and total caps that set `expansion_limited` rather than truncating silently. |
| **Clustering** (`backend/clustering/`) | Common-input-ownership + change-address (BTC), behavioural (ETH). CoinJoin-like structures **reduce cluster confidence** instead of asserting a false merge. |
| **Detection** (`backend/patterns/`) | All five patterns + sanctioned/mixer match + cluster-context. Written to the master-report definitions **before** any fixture existed. |
| **Scoring** (`backend/scoring/engine.py`) | Documented weights exactly as in CLAUDE.md. Contributions sum to `raw_total`; the 100 cap is a visible line, not a silent clamp. |
| **Attribution** (`backend/attribution/`) | Real OFAC SDN + GraphSense TagPack parsers. Unattributed clusters stay unattributed. Bridge/mixer hops are confidence boundaries. |
| **Evidence** (`backend/evidence/hashing.py`) | SHA-256 over canonical JSON. `/verify` recomputes and compares. |
| **Reports** (`backend/reports/generator.py`) | reportlab PDF; data provenance is in the **body**, not a footnote. |
| **API** (`backend/api/`) | Every frozen endpoint. `/investigate` streams NDJSON progress. Review recomputes the score server-side. |

**139 backend tests pass.** `scripts/e2e.py` runs all four cases offline and
verifies every evidence hash.

### Frontend — built and working, **not yet committed**

| Screen | State |
|---|---|
| Intake | Done. Prepared-case list, live health readout, validation. |
| Workspace shell | Done. Case header, live stat strip, streaming progress rail. |
| Follow-the-money graph | Done. Hop-by-hop reveal, actor/address toggle, primary-trail highlight with everything else dimmed, role colour-coding, hover readout. |
| Why panel | Done. Contributions that add up, cap shown explicitly, per-finding evidence with real tx hashes. |
| Findings review | Done. Confirm / Reject / Note; rejecting visibly moves the score. |
| Exit points | Done. Ranked, with a clickable source link per match, boundary warnings. |
| Report | Done. Paper-surface preview, PDF generate/open, hash verify. |

**20 frontend tests pass** (NDJSON chunk-splitting, formatting honesty).

### Verified in a real browser

The full judge path was driven end to end in Chrome against the running stack:
prepared case → trace → graph animates → 6 findings with evidence → score 97/100
HIGH → exit point Binance with an OFAC/TagPack source link. **Zero console
errors.**

Measured on this machine, case A: **15 wallets, 10 transactions, 7 clusters,
6 findings, 0.1 s.**

---

## The four demo cases

| Case | Complaint | Shows |
|---|---|---|
| **A** | CYB-2026-00124 | Fan-out → peel chain → burst → consolidation → exchange deposit. Score **97/100 HIGH**, 6 findings. The main demo. |
| **C** | CYB-2026-00131 | Trail reaches a real Wormhole bridge contract → **"trail confidence degrades here"**, no exit named. The deliberate failure demo. |
| **D** | CYB-2026-00140 | Same converging shape as a laundering consolidation, into a real Binance address. **0 findings** — and the suppressed fan-in is shown *with its reason*. This is the proof the disambiguation works. |
| **E** | CYB-2026-00152 | Bitcoin: common-input-ownership + change-address clustering. |

**Provenance, stated plainly:** all four are `synthetic_scenario` — constructed
transactions, labelled as such in the UI header, the PDF body, and every
evaluation line. The **attribution data they match against is real** (OFAC SDN
Tornado Cash addresses, publicly labelled Binance / Kraken / Coinbase hot
wallets, real bridge contracts), each with a source URL a judge can open.

---

## Known state / what is left

### Not yet done
- [ ] **Commit the frontend.** It is written, builds, and passes tests, but is
      still untracked in git.
- [ ] **Evaluation harness** (`P7`) — the labelled set in `/data/evaluation` and
      the script that measures detection against it. Nothing may go on a metrics
      slide until this exists and has been run.
- [ ] **Copy-honesty lint test** — a test that greps user-facing strings for
      accusatory phrasing. Manually reviewed so far; not yet automated.
- [ ] **Fresh-clone check** — delete `.venv` + `fundtrail.db`, re-run the three
      commands, confirm green.
- [ ] **Demo walkthrough script** (3–5 min, mapped to master report §18).

### Known rough edges
- **Graph auto-fit under-zooms slightly.** The layout is legible and correct, but
  the automatic `zoomToFit` leaves more empty panel than it should. The **Fit**
  button in the graph toolbar fixes it in one click. Cosmetic, not blocking.
- **Bitcoin live path is unverified.** A keyless Blockchair probe from this
  machine returned **HTTP 430 — IP temporarily blacklisted**. The adapter is
  written to the documented API shape but has never seen a live response. BTC
  runs from constructed scenarios. *(This is a good Q&A answer, not a weakness:
  it is exactly why the architecture is cache-first.)*
- **Blockscout works keyless** and is the live ETH path, but no case currently
  ships with `live_cached` provenance. Prefetching a real address would upgrade
  case B from constructed to real — worth doing if there is time.
- No `GET /api/cases` list endpoint (not in the frozen contract), so the intake
  screen prefills from a generated list rather than listing saved cases.

### Deliberately cut
Everything in P9 (replay animation, case-list view, classifier layer); multi-chain
breadth beyond BTC + ETH; auth/multi-user.

---

## Non-negotiables — how each is actually enforced

1. **No fabricated numbers.** Nothing is pre-computed at seed time; findings,
   scores and counts only exist after the pipeline runs. `format.ts` renders a
   missing value as an em dash, never a zero. Tested.
2. **Confidence, never certainty.** Every finding string is probabilistic; a test
   asserts no accusatory word appears in detector output.
3. **Explainability.** Rule engine only. Every finding carries its backing tx
   hashes — enforced in the `Detection` constructor, which refuses to build a
   finding without them.
4. **Cache before fetch.** `store.put` is called before any payload is used; a
   test asserts the response is in `api_cache` before the caller sees it.
5. **Rate limits.** Token bucket, tests pin each configured rate to the
   provider's documented limit.
6. **Fail honestly.** Unhandled exceptions return a readable message, never a
   traceback. Rate limits and caps set `expansion_limited` with a note.
7. **Human in the loop.** No path finalises a conclusion without review;
   rejecting a finding recomputes the score server-side.
8. **Attribution provenance.** `source` + `source_url` are `NOT NULL`; the
   ingester raises rather than storing a provenance-free tag.

---

## Repo map

```
backend/     adapters cache graph clustering patterns scoring attribution
             evidence reports api models tests
frontend/    src/{screens,components,lib,contracts,test}
data/        tagpacks ofac curated fixtures evaluation reports
scripts/     seed.py  build_scenarios.py  e2e.py  emit_demo_cases.py
```

Frozen API contract lives in **two files that must change together**:
`backend/api/contracts.py` and `frontend/src/contracts/api.ts`.
