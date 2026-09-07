"""Runs the pattern library over a built, clustered graph."""
from __future__ import annotations

import sqlite3
from typing import Dict, List

from backend.models.db import dumps, new_id, utc_now_iso
from backend.patterns import (
    cluster_context,
    fan_in,
    fan_out,
    peel_chain,
    round_split,
    sanctioned_match,
    timing_burst,
)
from backend.patterns.base import Detection, DetectionContext

# Order is presentation order in the Why panel: strongest signals first, and
# within that, shape detectors before contextual ones.
DETECTORS = [
    ("fan_out", fan_out.detect),
    ("peel_chain", peel_chain.detect),
    ("sanctioned_match", sanctioned_match.detect),
    ("fan_in", fan_in.detect),
    ("round_split", round_split.detect),
    ("cluster_context", cluster_context.detect),
    ("timing_burst", timing_burst.detect),
]


def run_detectors(context: DetectionContext) -> List[Detection]:
    detections: List[Detection] = []
    for _name, detect in DETECTORS:
        detections.extend(detect(context))
    return detections


def persist_findings(
    conn: sqlite3.Connection, case_id: str, detections: List[Detection]
) -> List[str]:
    """Write findings for this case. Replaces any previous run.

    Review state is deliberately not preserved across re-runs: a re-investigation
    produces new evidence, so an analyst's earlier confirmation should not be
    silently carried onto a finding they have not seen.
    """
    from backend.config import PATTERN_WEIGHTS

    conn.execute("DELETE FROM findings WHERE case_id = ?", (case_id,))
    ids: List[str] = []
    now = utc_now_iso()

    for detection in detections:
        finding_id = new_id()
        ids.append(finding_id)
        conn.execute(
            "INSERT INTO findings (id, case_id, pattern_type, title, description,"
            " score_contribution, evidence_tx_hashes, evidence_detail,"
            " subject_addresses, detected_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'flagged')",
            (
                finding_id,
                case_id,
                detection.pattern_type,
                detection.title,
                detection.description,
                PATTERN_WEIGHTS[detection.pattern_type],
                dumps(detection.evidence_tx_hashes),
                dumps(detection.evidence_detail),
                dumps(detection.subject_addresses),
                now,
            ),
        )
    return ids


def suppression_report(context: DetectionContext) -> List[Dict[str, object]]:
    """What the engine checked and deliberately did not flag.

    Currently the fan-in disambiguation. Shown to the investigator so a
    suppressed signal is distinguishable from a signal nobody looked for.
    """
    out: List[Dict[str, object]] = []
    for item in fan_in.explain_suppressions(context):
        out.append(
            {
                "pattern_type": "fan_in",
                "subject": item["destination"],
                "reason": item["reason"],
                "detail": {
                    "sender_count": item["sender_count"],
                    "features": item["features"],
                },
            }
        )
    return out
