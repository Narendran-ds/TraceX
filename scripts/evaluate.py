"""Run the pipeline against the labelled evaluation set and report what happened.

This is the only place project metrics may come from. Every number it prints was
computed from a run that just happened; nothing here is typed in by hand.

THE HONESTY RULE THIS SCRIPT ENFORCES
=====================================
The report always states the composition of the set it measured — how many cases
are constructed scenarios versus documented real-world traces. A number from a
constructed set says "the engine does what its documentation says". It does NOT
say "the engine detects real laundering", and this script will not let a reader
confuse the two.

It also refuses to print a manual-versus-automated time comparison, because no
manual baseline has been stopwatched yet. That is a human task (master report
§15.2). The field is shown as not measured until somebody measures it.

Run:  py -3.11 scripts/evaluate.py
      py -3.11 scripts/evaluate.py --json     # machine-readable only
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force the offline path before config is imported: an evaluation that silently
# reached the network would not be reproducible.
os.environ["FUNDTRAIL_ADAPTER_MODE"] = "fixture"

from backend import config  # noqa: E402
from backend.api.pipeline import run_investigation  # noqa: E402
from backend.api.views import (  # noqa: E402
    attribution_response,
    case_summary,
    findings_response,
)
from backend.attribution.ingest import ingest_all  # noqa: E402
from backend.cache import store  # noqa: E402
from backend.models.db import connect, new_id, reset_db, utc_now_iso  # noqa: E402

config.ADAPTER_MODE = "fixture"

CASES_DIR = config.EVALUATION_DIR / "cases"
RESULTS_DIR = config.EVALUATION_DIR / "results"
EVAL_DB = config.EVALUATION_DIR / "evaluation.db"

CONSTRUCTED = "constructed for this evaluation set"


def load_cases() -> List[Dict[str, Any]]:
    if not CASES_DIR.exists():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CASES_DIR.glob("*.json"))
    ]


def run_case(conn, document: Dict[str, Any]) -> Dict[str, Any]:
    """Seed one labelled case, run the pipeline, and compare against its labels."""
    chain = document["chain"]
    provenance = document["provenance"]

    for address, transactions in document["addresses"].items():
        store.put(
            provider="fixture",
            chain=chain,
            endpoint="address_history",
            address=address,
            payload={
                "address": address,
                "chain": chain,
                "origin": provenance,
                "transactions": transactions,
            },
            origin=provenance,
            conn=conn,
        )

    case_id = new_id()
    conn.execute(
        "INSERT INTO cases (id, complaint_id, wallet_address, chain, status,"
        " data_provenance, provenance_note, max_hops, created_at)"
        " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
        (
            case_id,
            f"EVAL-{document['slug']}",
            document["seed_address"],
            chain,
            provenance,
            f"Evaluation case. Ground truth: {document['ground_truth_source']}.",
            document.get("max_hops", 5),
            utc_now_iso(),
        ),
    )
    conn.commit()

    started = time.perf_counter()
    list(run_investigation(conn, case_id, mode="fixture"))
    elapsed = time.perf_counter() - started

    summary = case_summary(conn, case_id)
    findings = findings_response(conn, case_id)
    attribution = attribution_response(conn, case_id)

    detected = {f.pattern_type for f in findings.findings}
    expected_present = set(document["expected"]["patterns_present"])
    expected_absent = set(document["expected"]["patterns_absent"])

    # Only patterns the case actually makes a claim about are scored. A pattern
    # in neither list is "not asserted either way" and is ignored rather than
    # silently counted as a miss.
    true_positives = sorted(expected_present & detected)
    false_negatives = sorted(expected_present - detected)
    false_positives = sorted(expected_absent & detected)
    true_negatives = sorted(expected_absent - detected)

    # --- attribution ---
    expected_entity = document["expected"]["exit_entity_contains"]
    top_three = attribution.candidates[:3]
    if expected_entity is None:
        # The claim is that nothing gets named, not that something specific does.
        attribution_ok = all(c.entity_name is None for c in attribution.candidates)
        attribution_note = (
            "no exit named (as expected)" if attribution_ok
            else "an exit was named where none should be"
        )
    else:
        attribution_ok = any(
            c.entity_name and expected_entity.lower() in c.entity_name.lower()
            for c in top_three
        )
        found = next(
            (c.entity_name for c in top_three
             if c.entity_name and expected_entity.lower() in c.entity_name.lower()),
            None,
        )
        attribution_note = (
            f"{found} in top-3" if attribution_ok
            else f"{expected_entity} not in top-3"
        )

    degraded_ok = attribution.trail_degraded == document["expected"]["trail_degraded"]

    return {
        "slug": document["slug"],
        "title": document["title"],
        "ground_truth_source": document["ground_truth_source"],
        "provenance": provenance,
        "elapsed_seconds": round(elapsed, 4),
        "graph": {
            "wallets": summary.counts.wallets_discovered,
            "transactions": summary.counts.transactions_analysed,
            "clusters": summary.counts.clusters_identified,
            "hops": summary.counts.hops_traversed,
        },
        "score": summary.suspicion_score,
        "risk_level": summary.risk_level,
        "patterns": {
            "expected_present": sorted(expected_present),
            "expected_absent": sorted(expected_absent),
            "detected": sorted(detected),
            "true_positives": true_positives,
            "false_negatives": false_negatives,
            "false_positives": false_positives,
            "true_negatives": true_negatives,
        },
        "attribution": {
            "expected_entity": expected_entity,
            "ok": attribution_ok,
            "note": attribution_note,
            "top_three": [
                {"entity": c.entity_name, "confidence": c.confidence}
                for c in top_three
            ],
        },
        "trail_degraded": {
            "expected": document["expected"]["trail_degraded"],
            "actual": attribution.trail_degraded,
            "ok": degraded_ok,
        },
        "suppressed_signals": [
            {"pattern": s.pattern_type, "reason": s.reason}
            for s in findings.suppressed_signals
        ],
        "passed": (
            not false_negatives and not false_positives
            and attribution_ok and degraded_ok
        ),
    }


def aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp = sum(len(r["patterns"]["true_positives"]) for r in results)
    fn = sum(len(r["patterns"]["false_negatives"]) for r in results)
    fp = sum(len(r["patterns"]["false_positives"]) for r in results)
    tn = sum(len(r["patterns"]["true_negatives"]) for r in results)

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None

    attribution_cases = [r for r in results
                         if r["attribution"]["expected_entity"] is not None]
    attribution_hits = sum(1 for r in attribution_cases if r["attribution"]["ok"])

    times = [r["elapsed_seconds"] for r in results]
    constructed = sum(1 for r in results if r["ground_truth_source"] == CONSTRUCTED)

    return {
        "generated_at": utc_now_iso(),
        "case_count": len(results),
        "set_composition": {
            "constructed_scenarios": constructed,
            "documented_real_cases": len(results) - constructed,
        },
        "cases_fully_correct": sum(1 for r in results if r["passed"]),
        "pattern_detection": {
            "true_positives": tp,
            "false_negatives": fn,
            "false_positives": fp,
            "true_negatives": tn,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
        },
        "attribution": {
            "cases_expecting_a_named_exit": len(attribution_cases),
            "correct_exit_in_top_3": attribution_hits,
        },
        "trail_degradation": {
            "cases": len(results),
            "correct": sum(1 for r in results if r["trail_degraded"]["ok"]),
        },
        "timing": {
            "total_seconds": round(sum(times), 3),
            "median_seconds": round(statistics.median(times), 4) if times else None,
            "max_seconds": round(max(times), 4) if times else None,
            # Not measured. A manual stopwatch baseline is a human task and this
            # script will not invent one. See master report §15.2.
            "manual_baseline_seconds": None,
            "manual_baseline_note": (
                "Not measured. Stopwatch a manual trace of the same case by hand "
                "and record it here before any time comparison is presented."
            ),
        },
        "graph_scale": {
            "total_wallets": sum(r["graph"]["wallets"] for r in results),
            "total_transactions": sum(r["graph"]["transactions"] for r in results),
            "max_wallets_in_one_case": max(
                (r["graph"]["wallets"] for r in results), default=0
            ),
        },
    }


def render_markdown(summary: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
    composition = summary["set_composition"]
    detection = summary["pattern_detection"]

    def pct(value):
        return f"{value * 100:.1f}%" if value is not None else "n/a"

    lines = [
        "# Evaluation results",
        "",
        f"Generated {summary['generated_at']} by `scripts/evaluate.py`.",
        "Every figure below was computed from the run that produced this file.",
        "",
        "## What this set measures",
        "",
        f"- **{composition['constructed_scenarios']} constructed scenario(s)**",
        f"- **{composition['documented_real_cases']} documented real-world case(s)**",
        "",
    ]

    if composition["documented_real_cases"] == 0:
        lines += [
            "> Every case in this set is constructed. These numbers show that the",
            "> engine behaves as its own documentation specifies — it fires on the",
            "> shapes it defines, stays silent below its thresholds, and declines to",
            "> flag legitimate activity that looks identical.",
            ">",
            "> **They are not real-world detection accuracy.** Presenting them as",
            "> such would be the exact overclaim this project refuses to make. To",
            "> measure that, add traces from publicly documented laundering cases to",
            "> `data/evaluation/cases/` with `ground_truth_source` set to the public",
            "> write-up, and re-run this script.",
            "",
        ]

    lines += [
        "## Measured results",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Cases run | {summary['case_count']} |",
        f"| Cases correct on every assertion | {summary['cases_fully_correct']} of {summary['case_count']} |",
        f"| Patterns correctly flagged | {detection['true_positives']} |",
        f"| Patterns missed | {detection['false_negatives']} |",
        f"| False positives | {detection['false_positives']} |",
        f"| Correctly not flagged | {detection['true_negatives']} |",
        f"| Precision | {pct(detection['precision'])} |",
        f"| Recall | {pct(detection['recall'])} |",
        f"| Correct exit in top 3 | {summary['attribution']['correct_exit_in_top_3']} of {summary['attribution']['cases_expecting_a_named_exit']} |",
        f"| Trail-degradation calls correct | {summary['trail_degradation']['correct']} of {summary['trail_degradation']['cases']} |",
        f"| Median time per case | {summary['timing']['median_seconds']} s |",
        f"| Slowest case | {summary['timing']['max_seconds']} s |",
        f"| Addresses processed across the set | {summary['graph_scale']['total_wallets']} |",
        f"| Transactions processed across the set | {summary['graph_scale']['total_transactions']} |",
        f"| Manual tracing baseline | not measured |",
        "",
        "## Per-case",
        "",
        "| Case | Result | Detected | Missed | False positives | Time |",
        "|---|---|---|---|---|---|",
    ]

    for result in results:
        patterns = result["patterns"]
        lines.append(
            f"| `{result['slug']}` "
            f"| {'pass' if result['passed'] else 'FAIL'} "
            f"| {', '.join(patterns['detected']) or '—'} "
            f"| {', '.join(patterns['false_negatives']) or '—'} "
            f"| {', '.join(patterns['false_positives']) or '—'} "
            f"| {result['elapsed_seconds']:.3f} s |"
        )

    failed = [r for r in results if not r["passed"]]
    if failed:
        lines += ["", "## Cases that did not match their labels", ""]
        for result in failed:
            patterns = result["patterns"]
            lines.append(f"### `{result['slug']}` — {result['title']}")
            if patterns["false_negatives"]:
                lines.append(f"- Missed: {', '.join(patterns['false_negatives'])}")
            if patterns["false_positives"]:
                lines.append(f"- False positive: {', '.join(patterns['false_positives'])}")
            if not result["attribution"]["ok"]:
                lines.append(f"- Attribution: {result['attribution']['note']}")
            if not result["trail_degraded"]["ok"]:
                lines.append(
                    f"- Trail degradation: expected "
                    f"{result['trail_degraded']['expected']}, got "
                    f"{result['trail_degraded']['actual']}"
                )
            lines.append("")

    lines += [
        "",
        "## Manual baseline",
        "",
        summary["timing"]["manual_baseline_note"],
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="print JSON only, no human-readable output")
    args = parser.parse_args()

    documents = load_cases()
    if not documents:
        print(
            f"No labelled cases in {CASES_DIR}.\n"
            "Run: py -3.11 scripts/build_evaluation_set.py"
        )
        return 1

    # A throwaway database, so an evaluation run never touches the demo data
    # and always starts from the same state.
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    reset_db(EVAL_DB)

    conn = connect(EVAL_DB)
    try:
        ingest_all(conn)
        conn.commit()

        if not args.json:
            print("Evaluation — offline, cache only")
            print(f"Cases: {len(documents)}  ·  Database: {EVAL_DB}\n")

        results = []
        for document in documents:
            result = run_case(conn, document)
            results.append(result)
            if not args.json:
                mark = "pass" if result["passed"] else "FAIL"
                print(
                    f"  {mark:<5} {result['slug']:<38} "
                    f"{result['elapsed_seconds']:.3f}s  "
                    f"score {result['score']}  "
                    f"detected: {', '.join(result['patterns']['detected']) or '—'}"
                )
    finally:
        conn.close()

    summary = aggregate(results)
    payload = {"summary": summary, "cases": results}

    (RESULTS_DIR / "latest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    markdown = render_markdown(summary, results)
    (RESULTS_DIR / "latest.md").write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if summary["cases_fully_correct"] == summary["case_count"] else 1

    detection = summary["pattern_detection"]
    print(
        f"\n{summary['cases_fully_correct']} of {summary['case_count']} cases "
        "matched every label."
    )
    print(
        f"  patterns: {detection['true_positives']} flagged, "
        f"{detection['false_negatives']} missed, "
        f"{detection['false_positives']} false positive, "
        f"{detection['true_negatives']} correctly not flagged"
    )
    if detection["precision"] is not None:
        print(
            f"  precision {detection['precision'] * 100:.1f}%  ·  "
            f"recall {detection['recall'] * 100:.1f}%"
        )
    print(
        f"  attribution: correct exit in top-3 for "
        f"{summary['attribution']['correct_exit_in_top_3']} of "
        f"{summary['attribution']['cases_expecting_a_named_exit']} cases expecting one"
    )
    print(
        f"  timing: median {summary['timing']['median_seconds']}s, "
        f"slowest {summary['timing']['max_seconds']}s "
        f"(manual baseline: not measured)"
    )

    composition = summary["set_composition"]
    print(
        f"\nSet composition: {composition['constructed_scenarios']} constructed "
        f"scenario(s), {composition['documented_real_cases']} documented "
        "real-world case(s)."
    )
    if composition["documented_real_cases"] == 0:
        print(
            "  These numbers show the engine behaves as documented. They are NOT\n"
            "  real-world detection accuracy and must not be presented as such."
        )

    print(f"\nWritten to {RESULTS_DIR / 'latest.md'} and latest.json")
    return 0 if summary["cases_fully_correct"] == summary["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
