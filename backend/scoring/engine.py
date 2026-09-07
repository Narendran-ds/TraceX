"""Weighted scoring engine.

A transparent rule system, deliberately — the output goes into a legal case
file, and an investigator (or a defence lawyer, or a judge) must be able to
interrogate exactly why any score was assigned. This is not "explainable AI";
it is a rule engine, and describing it accurately is the stronger claim.

The arithmetic on screen has to survive a judge doing it by hand:

    contributions      the documented weight of each confirmed/flagged finding
    raw_total          their exact sum
    cap                100.0, because the documented weights total 117
    capped_total       min(raw_total, cap)

Rejected findings are excluded from raw_total and reported separately, so
rejecting a finding visibly moves the score. That is the human-in-the-loop step
having a real effect rather than a decorative one.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.config import PATTERN_WEIGHTS, RISK_BANDS, SCORE_CAP

WEIGHTS_NOTE = (
    "Weights are a documented hand-tuned heuristic, not learned from data. "
    "Each contribution below is the fixed weight for that signal; the total is "
    f"their sum, capped at {SCORE_CAP:.0f} for presentation."
)


@dataclass
class Contribution:
    finding_id: str
    pattern_type: str
    title: str
    weight: float
    status: str
    evidence_count: int


@dataclass
class Score:
    contributions: List[Contribution] = field(default_factory=list)
    raw_total: float = 0.0
    cap: float = SCORE_CAP
    capped_total: float = 0.0
    was_capped: bool = False
    excluded_rejected_total: float = 0.0
    risk_level: str = "NONE"
    weights_note: str = WEIGHTS_NOTE

    @property
    def obfuscation_detected(self) -> bool:
        """Any surviving shape-pattern finding means obfuscation was observed."""
        shape_patterns = {"fan_out", "fan_in", "peel_chain", "round_split", "timing_burst"}
        return any(
            c.pattern_type in shape_patterns and c.status != "rejected"
            for c in self.contributions
        )


def risk_level_for(score: float) -> str:
    """Documented bands. Not tuned per case."""
    for threshold, label in RISK_BANDS:
        if score >= threshold:
            return label
    return "NONE"


def score_findings(rows: List[Dict]) -> Score:
    """Compute the score from finding rows as stored.

    `rows` need only carry id, pattern_type, title, score_contribution, status
    and evidence_tx_hashes.
    """
    contributions: List[Contribution] = []
    raw_total = 0.0
    rejected_total = 0.0

    for row in rows:
        weight = float(row["score_contribution"])
        status = row["status"]
        evidence = row.get("evidence_tx_hashes") or []
        if isinstance(evidence, str):
            import json

            try:
                evidence = json.loads(evidence)
            except ValueError:
                evidence = []

        contributions.append(
            Contribution(
                finding_id=row["id"],
                pattern_type=row["pattern_type"],
                title=row["title"],
                weight=weight,
                status=status,
                evidence_count=len(evidence),
            )
        )

        if status == "rejected":
            # An analyst rejected this. It stops counting, and we say so.
            rejected_total += weight
        else:
            raw_total += weight

    capped = min(raw_total, SCORE_CAP)
    return Score(
        contributions=contributions,
        raw_total=round(raw_total, 2),
        cap=SCORE_CAP,
        capped_total=round(capped, 2),
        was_capped=raw_total > SCORE_CAP,
        excluded_rejected_total=round(rejected_total, 2),
        risk_level=risk_level_for(capped),
    )


def score_case(conn: sqlite3.Connection, case_id: str) -> Score:
    rows = conn.execute(
        "SELECT id, pattern_type, title, score_contribution, status, evidence_tx_hashes"
        " FROM findings WHERE case_id = ?",
        (case_id,),
    ).fetchall()
    return score_findings([dict(r) for r in rows])


def apply_score_to_case(
    conn: sqlite3.Connection, case_id: str, score: Optional[Score] = None
) -> Score:
    """Recompute and persist the case-level score. Called after every review."""
    score = score or score_case(conn, case_id)
    conn.execute(
        "UPDATE cases SET suspicion_score = ?, risk_level = ? WHERE id = ?",
        (score.capped_total, score.risk_level, case_id),
    )
    return score


def documented_weights() -> Dict[str, float]:
    """The weight table, for display and for the report appendix."""
    return dict(PATTERN_WEIGHTS)
