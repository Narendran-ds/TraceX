"""End-to-end check: every seeded case, address in -> PDF out, from cache only.

This is the gate for "the full pipeline runs offline". It forces the fixture
adapter, so a network call would fail rather than silently rescue a cache miss,
and it asserts the evidence hash verifies at the end.

    py -3.11 scripts/e2e.py

Exit code 0 means every case ran clean. Anything printed as FAIL is a real
failure, not a warning.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force the offline path before anything imports config.
os.environ["FUNDTRAIL_ADAPTER_MODE"] = "fixture"

from backend import config  # noqa: E402
from backend.api.pipeline import run_investigation  # noqa: E402
from backend.api.views import (  # noqa: E402
    attribution_response,
    case_summary,
    findings_response,
)
from backend.evidence.hashing import verify_evidence  # noqa: E402
from backend.models.db import connect  # noqa: E402
from backend.reports.generator import generate_report  # noqa: E402

config.ADAPTER_MODE = "fixture"

failures: List[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)
        print(f"      FAIL  {message}")


def run_case(conn, case_row) -> None:
    case_id = case_row["id"]
    print(f"\n  {case_row['complaint_id']}  {case_row['wallet_address'][:22]}…  "
          f"({case_row['chain']}, {case_row['data_provenance']})")

    started = time.perf_counter()
    events = list(run_investigation(conn, case_id, mode="fixture"))
    elapsed = time.perf_counter() - started

    check(events[-1].event == "complete",
          f"pipeline did not complete: {events[-1].event} — {events[-1].message}")
    check(any(e.event == "hop_complete" for e in events),
          "no hop_complete progress events were streamed")

    summary = case_summary(conn, case_id)
    findings = findings_response(conn, case_id)
    attribution = attribution_response(conn, case_id)

    print(f"      {summary.counts.wallets_discovered} wallets, "
          f"{summary.counts.transactions_analysed} transactions, "
          f"{summary.counts.clusters_identified} clusters, "
          f"{len(findings.findings)} findings, "
          f"score {summary.suspicion_score} ({summary.risk_level}), "
          f"{elapsed * 1000:.0f} ms")

    check(summary.status == "complete", "case status is not complete")
    check(summary.counts.wallets_discovered > 0, "no wallets were discovered")
    check(summary.counts.clusters_identified > 0, "no clusters were identified")

    # Score arithmetic must add up on screen.
    contribution_sum = sum(
        c.score_contribution for c in findings.score.contributions
        if c.status != "rejected"
    )
    check(abs(contribution_sum - findings.score.raw_total) < 1e-6,
          f"contributions {contribution_sum} != raw_total {findings.score.raw_total}")
    check(findings.score.capped_total <= findings.score.cap,
          "capped total exceeds the cap")

    # Every finding must link to real transactions in this case.
    tx_hashes = {
        r["tx_hash"] for r in conn.execute(
            "SELECT DISTINCT tx_hash FROM transactions WHERE case_id = ?", (case_id,)
        )
    }
    for finding in findings.findings:
        check(bool(finding.evidence_tx_hashes),
              f"finding {finding.pattern_type} has no evidence hashes")
        for tx_hash in finding.evidence_tx_hashes:
            check(tx_hash in tx_hashes,
                  f"finding {finding.pattern_type} cites unknown tx {tx_hash[:16]}")

    # Attribution must carry provenance on every named match.
    for candidate in attribution.candidates:
        if candidate.attributed:
            check(bool(candidate.sources),
                  "an attributed exit has no sources")
            for source in candidate.sources:
                check(source.source_url.startswith("http"),
                      f"source {source.source} has no dereferenceable URL")
        else:
            check(candidate.entity_name is None,
                  "an unattributed exit was given an entity name")

    for signal in findings.suppressed_signals:
        print(f"      suppressed: {signal.pattern_type} on "
              f"{signal.subject[:18]}… — {signal.reason[:70]}…")

    if attribution.trail_degraded:
        print(f"      trail degrades: {attribution.trail_degraded_note[:80]}…")

    # --- report + evidence hash ---
    result = generate_report(conn, case_id, "e2e-check")
    conn.commit()

    pdf = Path(result["pdf_path"])
    check(pdf.exists(), "PDF was not written")
    check(pdf.stat().st_size > 2000, f"PDF looks truncated ({pdf.stat().st_size} bytes)")

    verification = verify_evidence(conn, case_id, result["evidence_id"])
    check(verification is not None, "evidence record could not be read back")
    check(verification and verification["match"] is True,
          "evidence hash did not verify")

    print(f"      PDF {pdf.name} ({pdf.stat().st_size:,} bytes), "
          f"hash {result['evidence_hash'][:16]}… verified")


def main() -> int:
    print("End-to-end check — offline, cache only (FUNDTRAIL_ADAPTER_MODE=fixture)")
    print(f"Database: {config.DB_PATH}")

    conn = connect()
    try:
        cache_entries = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        tags = conn.execute("SELECT COUNT(*) FROM attribution_tags").fetchone()[0]
        cases = conn.execute("SELECT * FROM cases ORDER BY complaint_id").fetchall()

        print(f"Cache: {cache_entries} address histories · "
              f"Attribution: {tags} tags · Cases: {len(cases)}")

        if not cases:
            print("\nNo cases. Run: py -3.11 scripts/seed.py --reset")
            return 1

        for case in cases:
            run_case(conn, case)
    finally:
        conn.close()

    print()
    if failures:
        print(f"FAILED — {len(failures)} problem(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"PASSED — {len(cases)} case(s) ran end to end offline, "
          "every evidence hash verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
