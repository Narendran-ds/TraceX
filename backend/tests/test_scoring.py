"""S3 gate: the scoring arithmetic.

The Why panel's numbers have to survive a judge adding them up by hand. These
tests pin that: contributions sum to raw_total, the cap is explicit, and a
rejected finding visibly moves the score.
"""
from __future__ import annotations

import pytest

from backend.config import PATTERN_WEIGHTS, SCORE_CAP
from backend.scoring.engine import (
    apply_score_to_case,
    risk_level_for,
    score_case,
    score_findings,
)
from backend.models.db import connect, dumps, new_id, utc_now_iso
from backend.tests.helpers import make_case


def finding(pattern_type, status="flagged", evidence=("0xa",)):
    return {
        "id": new_id(),
        "pattern_type": pattern_type,
        "title": pattern_type.replace("_", " ").title(),
        "score_contribution": PATTERN_WEIGHTS[pattern_type],
        "status": status,
        "evidence_tx_hashes": list(evidence),
    }


def test_weights_match_the_documented_table():
    """CLAUDE.md scoring table. These are hand-tuned constants, not learned."""
    assert PATTERN_WEIGHTS["fan_out"] == 20.0
    assert PATTERN_WEIGHTS["peel_chain"] == 20.0
    assert PATTERN_WEIGHTS["sanctioned_match"] == 20.0
    assert PATTERN_WEIGHTS["fan_in"] == 15.0
    assert PATTERN_WEIGHTS["round_split"] == 15.0
    assert PATTERN_WEIGHTS["cluster_context"] == 15.0
    assert PATTERN_WEIGHTS["timing_burst"] == 12.0


def test_documented_weights_total_117_which_is_why_a_cap_exists():
    assert sum(PATTERN_WEIGHTS.values()) == pytest.approx(117.0)
    assert SCORE_CAP == 100.0


def test_contributions_sum_exactly_to_raw_total():
    score = score_findings([
        finding("fan_out"), finding("peel_chain"), finding("timing_burst"),
    ])
    assert score.raw_total == pytest.approx(52.0)
    assert sum(c.weight for c in score.contributions) == pytest.approx(score.raw_total)
    assert score.capped_total == pytest.approx(52.0)
    assert score.was_capped is False


def test_cap_is_applied_and_reported_explicitly():
    """Every signal firing exceeds 100, so the cap must be visible, not silent."""
    score = score_findings([finding(p) for p in PATTERN_WEIGHTS])
    assert score.raw_total == pytest.approx(117.0)
    assert score.capped_total == pytest.approx(100.0)
    assert score.was_capped is True
    # The raw total is still reported, so the arithmetic on screen adds up.
    assert sum(c.weight for c in score.contributions) == pytest.approx(score.raw_total)


def test_rejected_findings_are_excluded_and_reported_separately():
    score = score_findings([
        finding("fan_out"),
        finding("peel_chain", status="rejected"),
    ])
    assert score.raw_total == pytest.approx(20.0)
    assert score.excluded_rejected_total == pytest.approx(20.0)
    # The rejected finding is still listed — it is not deleted from the record.
    assert len(score.contributions) == 2


def test_confirmed_findings_still_count():
    score = score_findings([finding("fan_out", status="confirmed")])
    assert score.raw_total == pytest.approx(20.0)


def test_risk_bands_are_documented_thresholds():
    assert risk_level_for(100.0) == "HIGH"
    assert risk_level_for(70.0) == "HIGH"
    assert risk_level_for(69.9) == "MEDIUM"
    assert risk_level_for(40.0) == "MEDIUM"
    assert risk_level_for(39.9) == "LOW"
    assert risk_level_for(0.0) == "NONE"


def test_no_findings_produces_no_score_not_a_placeholder():
    score = score_findings([])
    assert score.raw_total == 0.0
    assert score.capped_total == 0.0
    assert score.risk_level == "NONE"
    assert score.contributions == []


def test_obfuscation_detected_reflects_surviving_shape_findings():
    assert score_findings([finding("fan_out")]).obfuscation_detected is True
    # A sanctioned-list hit is an attribution fact, not an obfuscation shape.
    assert score_findings([finding("sanctioned_match")]).obfuscation_detected is False
    # Rejected by the analyst means it no longer counts as observed obfuscation.
    assert score_findings(
        [finding("fan_out", status="rejected")]
    ).obfuscation_detected is False


def test_score_case_reads_from_the_database(temp_db):
    conn = connect(temp_db)
    try:
        make_case(conn, "case-1", "0xseed", "ethereum")
        for pattern in ("fan_out", "peel_chain"):
            conn.execute(
                "INSERT INTO findings (id, case_id, pattern_type, title, description,"
                " score_contribution, evidence_tx_hashes, detected_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), "case-1", pattern, pattern, "desc",
                 PATTERN_WEIGHTS[pattern], dumps(["0xa"]), utc_now_iso()),
            )
        conn.commit()

        score = score_case(conn, "case-1")
        assert score.raw_total == pytest.approx(40.0)
        assert score.risk_level == "MEDIUM"
    finally:
        conn.close()


def test_apply_score_persists_to_the_case(temp_db):
    conn = connect(temp_db)
    try:
        make_case(conn, "case-1", "0xseed", "ethereum")
        conn.execute(
            "INSERT INTO findings (id, case_id, pattern_type, title, description,"
            " score_contribution, evidence_tx_hashes, detected_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), "case-1", "fan_out", "Fan out", "desc", 20.0,
             dumps(["0xa"]), utc_now_iso()),
        )
        apply_score_to_case(conn, "case-1")
        conn.commit()

        row = conn.execute(
            "SELECT suspicion_score, risk_level FROM cases WHERE id='case-1'"
        ).fetchone()
        assert row["suspicion_score"] == pytest.approx(20.0)
        assert row["risk_level"] == "LOW"
    finally:
        conn.close()


def test_new_case_has_no_score_until_the_pipeline_runs(temp_db):
    """No placeholder number can survive to a demo if there is never one."""
    conn = connect(temp_db)
    try:
        make_case(conn, "case-1", "0xseed", "ethereum")
        conn.commit()
        row = conn.execute(
            "SELECT suspicion_score, risk_level FROM cases WHERE id='case-1'"
        ).fetchone()
        assert row["suspicion_score"] is None
        assert row["risk_level"] is None
    finally:
        conn.close()


def test_weights_note_does_not_claim_the_weights_were_learned():
    """The note may say they were NOT learned; it may not claim they were.

    Checked as a claim rather than a keyword — "not learned from data" is the
    honest phrasing we want, and a naive keyword ban would reject it.
    """
    score = score_findings([finding("fan_out")])
    note = score.weights_note.lower()
    assert "hand-tuned" in note
    for claim in (
        "learned from",
        "trained on",
        "machine learning",
        "model predicts",
    ):
        index = note.find(claim)
        if index == -1:
            continue
        preceding = note[max(0, index - 12) : index]
        assert "not " in preceding, (
            f"weights note appears to claim they were {claim!r}: {score.weights_note}"
        )
