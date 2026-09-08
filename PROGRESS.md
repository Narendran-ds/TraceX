# PROGRESS — SIH26183 Fund-Trail Investigation Assistant

Working prototype for the college selection round. **The whole thing runs offline
from cache.** No API key required, no network call on the demo path.

Last updated: 2026-09-08 · Phases P0–P7 complete.

---

## Run it

```bash
# one-time setup (from the repo root)
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
npm --prefix frontend install

# 1. seed          2. API (:8000)                                3. UI (:5173)
.venv/Scripts/python.exe scripts/seed.py --reset
.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8000
npm --prefix frontend run dev
```

Open **http://localhost:5173**, pick a prepared case, press Trace. Nothing else
is needed during the demo.

> **`python` on this machine is 3.9; the project needs 3.11.** Use `py -3.11` or
> `.venv/Scripts/python.exe`. Git Bash: `.venv/Scripts/python.exe`.
> PowerShell: `.venv\Scripts\python.exe`.

### Everything else

```bash
.venv/Scripts/python.exe -m pytest backend/tests -q     # 172 pass
npm --prefix frontend test                               # 20 pass
.venv/Scripts/python.exe scripts/e2e.py                  # 4 cases, address → PDF, offline
.venv/Scripts/python.exe scripts/evaluate.py             # the metrics harness
.venv/Scripts/python.exe scripts/build_scenarios.py      # regenerate demo scenarios
.venv/Scripts/python.exe scripts/build_evaluation_set.py # regenerate the labelled set
npm --prefix frontend run build && npm --prefix frontend run preview   # :4173
# API docs: http://127.0.0.1:8000/docs
```

---

## Demo walkthrough (4 minutes)

Maps to master report §18. One person drives, one narrates. Run it three times
out loud before the day.

**0:00 — the human story.** *Intake screen.*
> "A citizen reports a crypto scam to the 1930 helpline. They have lost their
> savings. All the investigator has is one wallet address. Tracing that by hand
> takes hours — and the money is gone in hours."

Point at the header: **Runs offline**, 45 cached address histories, 28
attribution tags. Nothing here needs the internet.

**0:30 — one address, one click.** Select **CYB-2026-00124**, press Trace.
> "One address. One click."

**0:50 — the scale reveal.** *Counters fill, graph builds hop by hop.*
> "Fifteen wallets, ten transactions, seven actors — work that would take an
> officer most of a day by hand. It took a tenth of a second."

**1:15 — the collapse.** Point at `15 → 7` and the numbered cluster nodes.
> "Using the same common-input-ownership heuristic professional forensic
> analysts use, those addresses collapse into the actual actors behind them."

Toggle **Actors / Addresses** once to show both views.

**1:40 — the detection.** *Findings panel.*
> "Rapid fan-out. A peel chain. Round-number splitting. A timing burst. These
> are the specific shapes deliberate laundering makes."

**2:00 — the explanation.** Point at the Why panel and add it up out loud.
> "20 plus 20 plus 15 plus 15 plus 15 plus 12 is 97. We don't ask you to trust
> a score — every point is attributed to a signal, and every signal links to the
> transaction hashes behind it. This is a rule engine and we can show you every
> rule."

Expand one finding to show the backing transactions.

**2:25 — the human in the loop.** Reject one finding.
> "The investigator decides, not the system. Reject a finding and the score
> moves — 97 to 77 — and the rejection is recorded in the case file with who did
> it and when."

**2:45 — the answer.** *Exit points tab.*
> "Most likely cash-out point: Binance, 55% confidence, matched against
> GraphSense TagPacks and our curated list — and there's the source link you can
> open right now. That's the actionable output, because an exchange account,
> unlike a wallet, can be frozen."

Point at candidate 2: **Unattributed exit cluster**.
> "And where we can't name it, we don't."

**3:05 — the deliverable.** *Report tab → Generate PDF → Verify hash.*
> "One click produces a case-file-ready report with a SHA-256 hash, so this
> evidence can be verified as unmodified later." *(click Verify: Hash matches)*

**3:20 — the honesty close.** New case → **CYB-2026-00131** → Trace → Exit points.
> "Two things we won't overclaim. When the trail reaches a bridge, we say the
> trail degrades — we don't guess what came out the other side."

New case → **CYB-2026-00140** → Trace.
> "And this is the same converging shape as laundering, into a real exchange.
> Score zero. The engine checked it and deliberately did not flag it — and it
> tells you why. Distinguishing those two is the hard part of this problem, and
> it's the part we actually built."

**Fallback if anything stalls:** press **Fit** on the graph toolbar; re-run from
the intake screen. Every case runs from cache, so a network failure cannot break
the demo.

---

## What is done

### Backend — P0–P4, P6 backend

| Area | State |
|---|---|
| Schema | 10 documented tables. `cases.data_provenance` NOT NULL + CHECK; `attribution_tags.source`/`source_url` NOT NULL. Enforced by the database, not by convention. |
| Cache | Every upstream response cached before use. Provider-agnostic lookup, so the offline path and the cached-live path are one code path. |
| Rate limiter | Token bucket per provider at each documented free-tier limit. Degrades to a partial graph rather than hanging. |
| Adapters | `ChainAdapter` + fixture / Blockscout / Etherscan V2 / Blockchair. |
| Graph | Multi-hop, `amount × time-decay × depth-decay` weighting, dust filter, caps that flag `expansion_limited` instead of truncating silently. |
| Clustering | Common-input + change-address (BTC), behavioural (ETH). CoinJoin-like structures reduce cluster confidence rather than asserting a false merge. |
| Detection | Five patterns + sanctioned/mixer match + cluster-context, written to the documented definitions before any fixture existed. |
| Scoring | Documented weights exactly. Contributions sum to `raw_total`; the 100 cap is a visible line. |
| Attribution | Real OFAC SDN + TagPack parsers. Unattributed stays unattributed. Bridges and mixers are confidence boundaries. |
| Evidence | SHA-256 over canonical JSON; `/verify` recomputes and compares. |
| Reports | reportlab PDF with data provenance in the body. |
| API | Every frozen endpoint, NDJSON progress streaming, server-side score recompute on review. |

### Frontend — P5, P6 frontend

Intake · workspace shell with live counters · follow-the-money graph (hop reveal,
actor/address toggle, primary-trail highlight, role colours, hover readout) · Why
panel · findings review · exit points · report with hash verification.

### P7 — evaluation

`scripts/evaluate.py` over 16 labelled cases in `data/evaluation/cases/`. It
found two real bugs the unit tests had missed (below), which is the argument for
having built it.

---

## Measured results

From `scripts/evaluate.py`, run offline. Regenerate any time; results land in
`data/evaluation/results/latest.md`.

| Metric | Measured |
|---|---|
| Cases matching every label | **16 of 16** |
| Patterns correctly flagged | 13 |
| Patterns missed | 0 |
| False positives | 0 |
| Correctly not flagged | 64 |
| Precision / recall | 100% / 100% |
| Correct exit in top 3 | 5 of 5 cases expecting a named exit |
| Trail-degradation calls correct | 16 of 16 |
| Median time per case | 0.007 s |
| Manual tracing baseline | **not measured** |

**Read the caveat before putting any of this on a slide.** All 16 cases are
constructed scenarios. These numbers show the engine behaves as its documentation
specifies — it fires on the shapes it defines, stays silent below its thresholds,
and declines to flag legitimate activity. **They are not real-world detection
accuracy.** `data/evaluation/README.md` explains how to add documented real cases;
the harness prints this caveat in its own output.

**The manual baseline is genuinely missing.** The time comparison is the safest
strong metric available, and half of it does not exist yet. Somebody needs to
trace one case by hand with a stopwatch. The harness prints `not measured` rather
than inventing it.

---

## Bugs the evaluation harness found

Both had passing unit tests and would have shown up in front of judges.

**1. Round-number splitting fired on ordinary amounts.** `is_round_amount`
stepped down to 0.01, so any two-decimal figure counted as round — 1.31 and 0.87
were "round numbers", which is most real transactions. The definition now stops
at 0.1 and lives in `backend/amounts.py`, shared with change-address detection so
the two cannot drift apart.

**2. A sanctioned mixer was not a confidence boundary.** OFAC tags Tornado Cash
`sanctioned`, which is the honest type for a sanctions source — but nothing marked
it as a *mixing service*, so a trail through it was reported as if it could
continue past an anonymity set. Sanctioned and mixer are independent facts: a
sanctioned personal wallet is still traceable onward. The tagpack now carries the
mixer fact with its own source URL.

---

## The four demo cases

| Case | Complaint | Shows |
|---|---|---|
| **A** | CYB-2026-00124 | Fan-out → peel chain → burst → consolidation → exchange. **97/100 HIGH**, 6 findings. The main demo. |
| **C** | CYB-2026-00131 | Trail reaches a real Wormhole bridge → trail degrades, no exit named. |
| **D** | CYB-2026-00140 | Same shape as laundering, into a real Binance address. **0 findings**, with the suppression and its reason shown. |
| **E** | CYB-2026-00152 | Bitcoin: common-input-ownership + change-address clustering. |

All four are `synthetic_scenario`, labelled in the UI header, the PDF body and
every evaluation line. The **attribution data they match against is real** — OFAC
SDN Tornado Cash addresses, publicly labelled Binance/Kraken/Coinbase hot wallets,
real bridge contracts — each with a source URL a judge can open.

---

## Verified

- **Fresh clone**: cloned to a clean directory, `venv` + `pip install` +
  `npm install`, seed, tests, e2e, evaluation, frontend build — all green. This
  also proves everything needed is committed.
- **Browser**: full judge path driven in Chrome against the running stack, **zero
  console errors**.
- **Offline**: `scripts/e2e.py` forces the fixture adapter, so a network call
  would fail rather than silently rescue a cache miss.

---

## What is left

- [ ] **Stopwatch a manual trace.** The only missing metric, and the most
      persuasive one. A human task; nothing in the code can produce it.
- [ ] **Add documented real cases** to `data/evaluation/cases/` so the harness can
      report real-world numbers instead of behavioural ones.
- [ ] **Prefetch a real ETH address** via Blockscout (which works keyless) so one
      demo case carries `live_cached` provenance with judge-verifiable hashes.
      Roughly an hour, and the highest-value remaining work.
- [ ] Rehearse the walkthrough above three times on the demo machine.
- [ ] Record a backup video of a clean run.

### Known limitation, worth rehearsing as an answer

**The Bitcoin live path is unverified.** A keyless Blockchair probe from this
machine returned **HTTP 430 — "IP address is temporarily blacklisted due to
exceeding usage"**. The adapter is written to the documented API shape but has
never seen a live response, so BTC runs from constructed scenarios. This is not a
weakness to hide — it is precisely the failure the cache-first architecture exists
to survive, and it happened to us for real.

### Deliberately cut

All of P9 (replay animation, case-list view, classifier layer); multi-chain
breadth beyond BTC + ETH; auth and multi-user.

---

## How each non-negotiable is enforced

1. **No fabricated numbers** — nothing is pre-computed at seed time; `format.ts`
   renders a missing value as an em dash, never a zero. Tested.
2. **Confidence, never certainty** — `backend/tests/test_language.py` greps every
   user-facing string for accusatory and overclaiming language, with two tests
   proving the lint can actually fail.
3. **Explainability** — the `Detection` constructor refuses to build a finding
   without backing transaction hashes.
4. **Cache before fetch** — a test asserts the response is in `api_cache` before
   the caller sees it.
5. **Rate limits** — tests pin each configured rate to the provider's documented
   limit.
6. **Fail honestly** — unhandled exceptions return a readable message, never a
   traceback; caps set `expansion_limited` with a note.
7. **Human in the loop** — no path finalises without review; rejecting recomputes
   the score server-side.
8. **Attribution provenance** — `source` + `source_url` are NOT NULL and the
   ingester raises rather than storing a provenance-free tag.

---

## Repo map

```
backend/   adapters cache graph clustering patterns scoring attribution
           evidence reports api models tests · amounts.py config.py
frontend/  src/{screens,components,lib,contracts,test}
data/      tagpacks ofac curated fixtures evaluation reports
scripts/   seed · build_scenarios · build_evaluation_set · evaluate · e2e
```

The frozen API contract lives in **two files that change together**:
`backend/api/contracts.py` and `frontend/src/contracts/api.ts`.
