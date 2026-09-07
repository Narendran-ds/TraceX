"""S5 gate: the frozen API contract, end to end through the HTTP layer."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.attribution.ingest import ingest_all
from backend.models.db import connect
from backend.tests.helpers import at, distribute, io, seed_scenario, tx

BINANCE = "0x28C6c06298d514Db089934071355E5743bf21d60"


@pytest.fixture()
def client(temp_db, monkeypatch):
    """A TestClient bound to an isolated database with a seeded scenario."""
    from backend import config
    from backend.api import routes
    from backend.models import db as db_module

    monkeypatch.setattr(config, "ADAPTER_MODE", "fixture")
    monkeypatch.setattr(routes, "DB_PATH", temp_db)

    txs = [
        tx("0xfan", at(0), [io("0xseed", 12.0)],
           [io(f"0xl{i}", 2.0) for i in range(6)]),
        *[
            tx(f"0xc{i}", at(minutes=4 + i), [io(f"0xl{i}", 2.0)],
               [io("0xcollector", 1.99)])
            for i in range(1, 6)
        ],
        tx("0xcash", at(hours=2), [io("0xcollector", 9.95)], [io(BINANCE, 9.94)]),
    ]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        ingest_all(conn)
        conn.commit()
    finally:
        conn.close()

    from backend.api.main import app

    with TestClient(app) as test_client:
        yield test_client


def open_case(client, address="0xseed", chain="ethereum", complaint="CYB-TEST-1"):
    response = client.post("/api/cases", json={
        "complaint_id": complaint, "wallet_address": address, "chain": chain,
    })
    assert response.status_code == 200, response.text
    return response.json()


def investigate(client, case_id):
    """Consume the NDJSON stream and return the parsed events."""
    with client.stream("POST", f"/api/cases/{case_id}/investigate") as response:
        assert response.status_code == 200
        assert "ndjson" in response.headers["content-type"]
        events = [
            json.loads(line) for line in response.iter_lines() if line.strip()
        ]
    return events


# ===========================================================================
# health
# ===========================================================================

def test_health_reports_real_counts(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["cache_entries"] > 0
    assert body["attribution_tags"] > 0
    assert body["offline_capable"] is True
    names = {a["name"] for a in body["adapters"]}
    assert {"fixture", "blockscout", "etherscan", "blockchair"} <= names


# ===========================================================================
# POST /api/cases
# ===========================================================================

def test_create_case_declares_provenance(client):
    case = open_case(client)
    assert case["provenance"]["kind"] == "synthetic_scenario"
    assert case["provenance"]["note"]
    assert case["status"] == "pending"


def test_new_case_has_no_score_or_risk_level(client):
    """Nothing is pre-computed, so no number can be a seeded placeholder."""
    case = open_case(client)
    assert case["suspicion_score"] is None
    assert case["risk_level"] is None
    assert case["obfuscation_detected"] is None
    assert case["counts"]["wallets_discovered"] == 0


def test_create_case_rejects_an_unknown_chain(client):
    response = client.post("/api/cases", json={
        "complaint_id": "X", "wallet_address": "0xseed", "chain": "dogecoin",
    })
    assert response.status_code == 422
    assert "chain" in response.json()["message"].lower()


def test_create_case_on_an_uncached_address_degrades_readably(client):
    """Offline mode with no cached data is an honest answer, not a crash."""
    response = client.post("/api/cases", json={
        "complaint_id": "X", "wallet_address": "0xneverseen", "chain": "ethereum",
    })
    assert response.status_code == 422
    body = response.json()
    assert "offline mode" in body["message"]
    assert "traceback" not in body["message"].lower()


# ===========================================================================
# POST /api/cases/{id}/investigate — streaming
# ===========================================================================

def test_investigate_streams_progress_then_completes(client):
    case = open_case(client)
    events = investigate(client, case["id"])

    assert events[0]["event"] == "started"
    assert events[-1]["event"] == "complete"
    assert events[-1]["percent"] == 100
    assert [e["event"] for e in events].count("hop_complete") >= 1
    # Percentages advance monotonically so a progress bar never goes backwards.
    percents = [e["percent"] for e in events]
    assert percents == sorted(percents)


def test_investigate_on_a_missing_case_is_404(client):
    response = client.post("/api/cases/does-not-exist/investigate")
    assert response.status_code == 404


def test_completed_case_has_computed_values(client):
    case = open_case(client)
    investigate(client, case["id"])

    summary = client.get(f"/api/cases/{case['id']}").json()
    assert summary["status"] == "complete"
    assert summary["suspicion_score"] > 0
    assert summary["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    assert summary["counts"]["wallets_discovered"] > 0
    assert summary["counts"]["transactions_analysed"] > 0
    assert summary["counts"]["clusters_identified"] > 0
    assert summary["total_amount_traced"] > 0
    assert summary["amount_unit"] == "ETH"


# ===========================================================================
# GET /api/cases/{id}/graph
# ===========================================================================

def test_graph_returns_both_collapsed_and_expanded_views(client):
    case = open_case(client)
    investigate(client, case["id"])

    collapsed = client.get(f"/api/cases/{case['id']}/graph?collapsed=true").json()
    expanded = client.get(f"/api/cases/{case['id']}/graph?collapsed=false").json()

    assert collapsed["collapsed"] is True
    assert expanded["collapsed"] is False
    assert all(n["kind"] == "cluster" for n in collapsed["nodes"])
    assert all(n["kind"] == "address" for n in expanded["nodes"])
    assert len(collapsed["nodes"]) <= len(expanded["nodes"])
    assert collapsed["collapse_ratio"] is not None
    assert collapsed["address_count"] == expanded["address_count"]


def test_graph_edges_reference_existing_nodes(client):
    case = open_case(client)
    investigate(client, case["id"])
    for collapsed in (True, False):
        body = client.get(
            f"/api/cases/{case['id']}/graph?collapsed={str(collapsed).lower()}"
        ).json()
        ids = {n["id"] for n in body["nodes"]}
        for edge in body["edges"]:
            assert edge["source"] in ids, f"dangling edge source {edge['source']}"
            assert edge["target"] in ids, f"dangling edge target {edge['target']}"


def test_graph_marks_a_primary_trail(client):
    case = open_case(client)
    investigate(client, case["id"])
    body = client.get(f"/api/cases/{case['id']}/graph?collapsed=true").json()
    assert any(n["on_primary_trail"] for n in body["nodes"])


def test_graph_of_an_uninvestigated_case_is_empty_not_an_error(client):
    case = open_case(client)
    body = client.get(f"/api/cases/{case['id']}/graph").json()
    assert body["nodes"] == []
    assert body["edges"] == []


# ===========================================================================
# GET /api/cases/{id}/clusters
# ===========================================================================

def test_clusters_carry_confidence_and_method(client):
    case = open_case(client)
    investigate(client, case["id"])
    body = client.get(f"/api/cases/{case['id']}/clusters").json()

    assert body["clusters"]
    for cluster in body["clusters"]:
        assert 0 < cluster["cluster_confidence"] <= 1
        assert cluster["clustering_method"] in (
            "common_input", "change_address", "behavioral", "singleton"
        )
        assert cluster["member_count"] == len(cluster["members"])


# ===========================================================================
# GET /api/cases/{id}/findings
# ===========================================================================

def test_findings_have_evidence_and_a_breakdown_that_adds_up(client):
    case = open_case(client)
    investigate(client, case["id"])
    body = client.get(f"/api/cases/{case['id']}/findings").json()

    assert body["findings"]
    for finding in body["findings"]:
        assert finding["evidence_tx_hashes"], "finding without backing transactions"
        assert finding["evidence_detail"], "finding without computed detail"
        assert finding["score_contribution"] > 0

    score = body["score"]
    contributions = sum(
        c["score_contribution"] for c in score["contributions"]
        if c["status"] != "rejected"
    )
    assert contributions == pytest.approx(score["raw_total"])
    assert score["capped_total"] == pytest.approx(min(score["raw_total"], score["cap"]))
    assert "hand-tuned" in score["weights_note"]


def test_findings_expose_suppressed_signals(client):
    """The fan-in into a real exchange is checked and deliberately not flagged."""
    case = open_case(client)
    investigate(client, case["id"])
    body = client.get(f"/api/cases/{case['id']}/findings").json()

    # The scenario consolidates at 0xcollector (flagged) and then deposits at a
    # real exchange. Whatever is suppressed must carry a reason.
    for signal in body["suppressed_signals"]:
        assert signal["reason"]
        assert signal["detail"]


# ===========================================================================
# POST review — human in the loop
# ===========================================================================

def test_confirming_a_finding_records_the_reviewer(client):
    case = open_case(client)
    investigate(client, case["id"])
    findings = client.get(f"/api/cases/{case['id']}/findings").json()["findings"]
    finding = findings[0]

    response = client.post(
        f"/api/cases/{case['id']}/findings/{finding['id']}/review",
        json={"action": "confirm", "note": "Matches the complaint timeline.",
              "reviewed_by": "Inspector Rao"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["finding"]["status"] == "confirmed"
    assert body["finding"]["analyst_note"] == "Matches the complaint timeline."
    assert body["finding"]["reviewed_by"] == "Inspector Rao"
    assert body["finding"]["reviewed_at"]


def test_rejecting_a_finding_lowers_the_score_server_side(client):
    """The human-in-the-loop step has a real effect, not a decorative one."""
    case = open_case(client)
    investigate(client, case["id"])

    before = client.get(f"/api/cases/{case['id']}").json()["suspicion_score"]
    finding = client.get(
        f"/api/cases/{case['id']}/findings"
    ).json()["findings"][0]

    response = client.post(
        f"/api/cases/{case['id']}/findings/{finding['id']}/review",
        json={"action": "reject", "note": "Consistent with the victim's own transfer."},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["score"]["excluded_rejected_total"] == pytest.approx(
        finding["score_contribution"]
    )
    # Re-fetching the case (no new endpoint invented) shows the moved score.
    after = client.get(f"/api/cases/{case['id']}").json()["suspicion_score"]
    assert after < before


def test_a_rejected_finding_is_kept_in_the_record(client):
    case = open_case(client)
    investigate(client, case["id"])
    finding = client.get(f"/api/cases/{case['id']}/findings").json()["findings"][0]
    client.post(
        f"/api/cases/{case['id']}/findings/{finding['id']}/review",
        json={"action": "reject"},
    )
    body = client.get(f"/api/cases/{case['id']}/findings").json()
    statuses = {f["id"]: f["status"] for f in body["findings"]}
    assert statuses[finding["id"]] == "rejected", "rejected finding was deleted"


def test_review_rejects_an_unknown_action(client):
    case = open_case(client)
    investigate(client, case["id"])
    finding = client.get(f"/api/cases/{case['id']}/findings").json()["findings"][0]
    response = client.post(
        f"/api/cases/{case['id']}/findings/{finding['id']}/review",
        json={"action": "maybe"},
    )
    assert response.status_code == 422


def test_review_of_an_unknown_finding_is_404(client):
    case = open_case(client)
    investigate(client, case["id"])
    response = client.post(
        f"/api/cases/{case['id']}/findings/nope/review", json={"action": "confirm"}
    )
    assert response.status_code == 404


# ===========================================================================
# GET /api/cases/{id}/attribution
# ===========================================================================

def test_attribution_returns_ranked_exits_with_sources(client):
    case = open_case(client)
    investigate(client, case["id"])
    body = client.get(f"/api/cases/{case['id']}/attribution").json()

    assert body["candidates"]
    confidences = [c["confidence"] for c in body["candidates"]]
    assert confidences == sorted(confidences, reverse=True), "candidates not ranked"

    attributed = [c for c in body["candidates"] if c["attributed"]]
    assert attributed, "the real exchange address was not attributed"
    for candidate in attributed:
        assert candidate["sources"]
        for source in candidate["sources"]:
            assert source["source_url"].startswith("http")

    assert body["coverage"]["tag_count"] > 0
    assert "partial" in body["coverage"]["statement"]


# ===========================================================================
# report + evidence
# ===========================================================================

def test_report_generates_a_pdf_and_a_verifiable_hash(client, tmp_path, monkeypatch):
    from backend import config
    from backend.reports import generator

    monkeypatch.setattr(generator, "REPORT_DIR", tmp_path)

    case = open_case(client)
    investigate(client, case["id"])

    response = client.post(f"/api/cases/{case['id']}/report", json={
        "generated_by": "Inspector Rao"
    })
    assert response.status_code == 200
    report = response.json()

    assert report["evidence_hash"]
    assert len(report["evidence_hash"]) == 64
    assert report["hash_algorithm"] == "sha256"

    from pathlib import Path
    assert Path(report["pdf_path"]).exists()

    verify = client.get(
        f"/api/cases/{case['id']}/evidence/{report['evidence_id']}/verify"
    ).json()
    assert verify["match"] is True
    assert verify["stored_hash"] == verify["recomputed_hash"] == report["evidence_hash"]


def test_report_pdf_downloads(client, tmp_path, monkeypatch):
    from backend.reports import generator

    monkeypatch.setattr(generator, "REPORT_DIR", tmp_path)
    case = open_case(client)
    investigate(client, case["id"])
    report = client.post(f"/api/cases/{case['id']}/report", json={}).json()

    response = client.get(report["download_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"


def test_tampering_with_a_stored_payload_is_detected(client, tmp_path, monkeypatch):
    """This is the whole point of the hash: a modified record stops verifying."""
    from backend.reports import generator

    monkeypatch.setattr(generator, "REPORT_DIR", tmp_path)
    case = open_case(client)
    investigate(client, case["id"])
    report = client.post(f"/api/cases/{case['id']}/report", json={}).json()

    from backend.models.db import connect as raw_connect

    conn = raw_connect()
    try:
        conn.execute(
            "UPDATE evidence_records SET payload_json = ? WHERE id = ?",
            ('{"tampered":true}', report["evidence_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    verify = client.get(
        f"/api/cases/{case['id']}/evidence/{report['evidence_id']}/verify"
    ).json()
    assert verify["match"] is False
    assert verify["recomputed_hash"] != verify["stored_hash"]


def test_verify_of_an_unknown_evidence_record_is_404(client):
    case = open_case(client)
    response = client.get(f"/api/cases/{case['id']}/evidence/nope/verify")
    assert response.status_code == 404


# ===========================================================================
# error handling
# ===========================================================================

def test_unknown_case_returns_a_readable_message_not_a_traceback(client):
    response = client.get("/api/cases/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "case_not_found"
    assert "does-not-exist" in body["message"]
    assert "Traceback" not in json.dumps(body)


def test_audit_trail_records_every_case_action(client, tmp_path, monkeypatch):
    from backend.reports import generator

    monkeypatch.setattr(generator, "REPORT_DIR", tmp_path)
    case = open_case(client)
    investigate(client, case["id"])
    finding = client.get(f"/api/cases/{case['id']}/findings").json()["findings"][0]
    client.post(
        f"/api/cases/{case['id']}/findings/{finding['id']}/review",
        json={"action": "confirm"},
    )
    client.post(f"/api/cases/{case['id']}/report", json={})

    from backend.models.db import connect as raw_connect

    conn = raw_connect()
    try:
        actions = [
            r["action"] for r in conn.execute(
                "SELECT action FROM audit_log WHERE case_id = ? ORDER BY created_at",
                (case["id"],),
            )
        ]
    finally:
        conn.close()

    assert "case_created" in actions
    assert "investigation_started" in actions
    assert "investigation_completed" in actions
    assert "finding_confirmed" in actions
    assert "report_generated" in actions
