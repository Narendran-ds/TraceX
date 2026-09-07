"""Investigator report — PDF plus the evidence payload it is hashed against.

The report is written to go into a case file, so the language rules apply here
more strictly than anywhere else in the system: findings are "patterns
consistent with…", the score is a heuristic total with its weights shown, and
the data provenance is stated in the body — not a footnote — so nobody can read
this document and mistake a constructed scenario for a traced one.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.api.views import (
    attribution_response,
    case_summary,
    findings_response,
    clusters_response,
)
from backend.config import REPORT_DIR
from backend.evidence.hashing import store_evidence
from backend.models.db import utc_now_iso
from backend.scoring.engine import documented_weights

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#4b5563")
RULE = colors.HexColor("#d1d5db")
ACCENT = colors.HexColor("#1d4ed8")
WARN = colors.HexColor("#b45309")


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=16, leading=20,
            textColor=INK, alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=9, leading=12,
            textColor=MUTED, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=11, leading=14,
            textColor=INK, spaceBefore=12, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=8.5, leading=12, textColor=INK,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontSize=7.5, leading=10, textColor=MUTED,
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["Normal"], fontSize=7, leading=9.5,
            fontName="Courier", textColor=INK,
        ),
        "warn": ParagraphStyle(
            "warn", parent=base["Normal"], fontSize=8.5, leading=12, textColor=WARN,
        ),
    }


def _kv_table(rows: List[Tuple[str, str]], styles) -> Table:
    data = [
        [Paragraph(f"<b>{k}</b>", styles["body"]), Paragraph(v, styles["body"])]
        for k, v in rows
    ]
    table = Table(data, colWidths=[45 * mm, 120 * mm])
    table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
        ])
    )
    return table


def build_evidence_payload(conn: sqlite3.Connection, case_id: str) -> Dict[str, Any]:
    """The exact object that gets hashed.

    Deliberately excludes anything that changes between runs (generation
    timestamps, file paths), so re-verifying the same case's evidence later
    produces the same digest.
    """
    summary = case_summary(conn, case_id)
    findings = findings_response(conn, case_id)
    attribution = attribution_response(conn, case_id)
    clusters = clusters_response(conn, case_id)

    return {
        "case": {
            "id": summary.id,
            "complaint_id": summary.complaint_id,
            "wallet_address": summary.wallet_address,
            "chain": summary.chain,
            "status": summary.status,
            "created_at": summary.created_at,
            "completed_at": summary.completed_at,
            "data_provenance": summary.provenance.kind,
            "provenance_note": summary.provenance.note,
            "max_hops": summary.max_hops,
            "expansion_limited": summary.expansion_limited,
            "expansion_note": summary.expansion_note,
        },
        "totals": {
            "wallets_discovered": summary.counts.wallets_discovered,
            "transactions_analysed": summary.counts.transactions_analysed,
            "clusters_identified": summary.counts.clusters_identified,
            "hops_traversed": summary.counts.hops_traversed,
            "amount_traced": summary.total_amount_traced,
            "amount_unit": summary.amount_unit,
        },
        "score": {
            "raw_total": findings.score.raw_total,
            "cap": findings.score.cap,
            "capped_total": findings.score.capped_total,
            "was_capped": findings.score.was_capped,
            "excluded_rejected_total": findings.score.excluded_rejected_total,
            "risk_level": findings.score.risk_level,
            "weights": documented_weights(),
        },
        "findings": [
            {
                "id": f.id,
                "pattern_type": f.pattern_type,
                "title": f.title,
                "description": f.description,
                "score_contribution": f.score_contribution,
                "status": f.status,
                "analyst_note": f.analyst_note,
                "reviewed_by": f.reviewed_by,
                "reviewed_at": f.reviewed_at,
                "evidence_tx_hashes": f.evidence_tx_hashes,
            }
            for f in findings.findings
        ],
        "clusters": [
            {
                "id": c.id,
                "label": c.label,
                "member_count": c.member_count,
                "method": c.clustering_method,
                "confidence": c.cluster_confidence,
                "attributed_entity": c.attributed_entity,
                "attribution_source": c.attribution_source,
                "is_terminal": c.is_terminal,
            }
            for c in clusters.clusters
        ],
        "attribution": {
            "candidates": [
                {
                    "cluster_id": c.cluster_id,
                    "entity_name": c.entity_name,
                    "entity_type": c.entity_type,
                    "attributed": c.attributed,
                    "confidence": c.confidence,
                    "amount_received": c.amount_received,
                    "confidence_boundary": c.confidence_boundary,
                    "sources": [
                        {"source": s.source, "source_url": s.source_url,
                         "matched_address": s.matched_address}
                        for s in c.sources
                    ],
                }
                for c in attribution.candidates
            ],
            "coverage": attribution.coverage.statement,
            "trail_degraded": attribution.trail_degraded,
            "trail_degraded_note": attribution.trail_degraded_note,
        },
    }


def generate_report(
    conn: sqlite3.Connection, case_id: str, generated_by: str
) -> Dict[str, Any]:
    """Build the PDF and store the hashed evidence record."""
    summary = case_summary(conn, case_id)
    findings = findings_response(conn, case_id)
    attribution = attribution_response(conn, case_id)

    payload = build_evidence_payload(conn, case_id)
    evidence_id, evidence_hash = store_evidence(conn, case_id, payload)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{summary.complaint_id.replace('/', '-')}-{case_id[:8]}.pdf"
    path = REPORT_DIR / filename

    styles = _styles()
    story: List[Any] = []
    unit = summary.amount_unit

    # --- header ---
    story.append(Paragraph("Fund-Trail Investigation Report", styles["title"]))
    story.append(
        Paragraph(
            "Decision support for a victim-initiated cybercrime complaint. "
            "Every finding in this report requires investigator confirmation and "
            "describes patterns, not conclusions about any person.",
            styles["subtitle"],
        )
    )

    # --- 1. case metadata ---
    story.append(Paragraph("1. Case", styles["h2"]))
    story.append(_kv_table([
        ("Complaint reference", summary.complaint_id),
        ("Reported address", summary.wallet_address),
        ("Chain", summary.chain),
        ("Case opened", summary.created_at),
        ("Analysis completed", summary.completed_at or "not completed"),
        ("Report generated by", generated_by),
    ], styles))

    # --- 2. data provenance, in the body ---
    story.append(Paragraph("2. Data provenance", styles["h2"]))
    provenance_style = (
        styles["warn"] if summary.provenance.kind == "synthetic_scenario"
        else styles["body"]
    )
    story.append(
        Paragraph(
            f"<b>{summary.provenance.label}.</b> {summary.provenance.note}",
            provenance_style,
        )
    )
    if summary.expansion_limited:
        story.append(Spacer(1, 3))
        story.append(Paragraph(summary.expansion_note, styles["warn"]))

    # --- 3. what was traced ---
    story.append(Paragraph("3. Financial summary", styles["h2"]))
    story.append(_kv_table([
        ("Amount traced from reported address", f"{summary.total_amount_traced} {unit}"),
        ("Wallets discovered", str(summary.counts.wallets_discovered)),
        ("Transactions analysed", str(summary.counts.transactions_analysed)),
        ("Entity clusters identified", str(summary.counts.clusters_identified)),
        ("Hops traversed", str(summary.counts.hops_traversed)),
    ], styles))

    # --- 4. assessment ---
    story.append(Paragraph("4. Assessment", styles["h2"]))
    if findings.findings:
        cap_line = (
            f"{findings.score.raw_total} raw, capped at {findings.score.cap:.0f} "
            f"→ {findings.score.capped_total}"
            if findings.score.was_capped
            else f"{findings.score.capped_total} of {findings.score.cap:.0f}"
        )
        story.append(_kv_table([
            ("Heuristic suspicion score", cap_line),
            ("Risk band", findings.score.risk_level),
            (
                "Obfuscation patterns observed",
                "Yes" if summary.obfuscation_detected else "No",
            ),
            (
                "Findings reviewed",
                f"{summary.counts.findings_confirmed} confirmed, "
                f"{summary.counts.findings_rejected} rejected, "
                f"{summary.counts.findings_flagged} awaiting review",
            ),
        ], styles))
        if findings.score.excluded_rejected_total:
            story.append(Spacer(1, 3))
            story.append(
                Paragraph(
                    f"{findings.score.excluded_rejected_total} points were excluded "
                    "because the reviewing investigator rejected those findings.",
                    styles["small"],
                )
            )
        story.append(Spacer(1, 4))
        story.append(Paragraph(findings.score.weights_note, styles["small"]))
    else:
        story.append(
            Paragraph(
                "No patterns from the detection library were observed on this trail. "
                "That is not a finding of innocence — it means this particular set of "
                "signals did not appear in the traced activity.",
                styles["body"],
            )
        )

    # --- 5. findings with evidence ---
    story.append(Paragraph("5. Findings and supporting evidence", styles["h2"]))
    if not findings.findings:
        story.append(Paragraph("None.", styles["body"]))
    for index, finding in enumerate(findings.findings, start=1):
        block = [
            Paragraph(
                f"<b>{index}. {finding.title}</b> "
                f"(+{finding.score_contribution:g}, {finding.status})",
                styles["body"],
            ),
            Paragraph(finding.description, styles["small"]),
        ]
        if finding.analyst_note:
            block.append(
                Paragraph(
                    f"<b>Investigator note:</b> {finding.analyst_note}", styles["small"]
                )
            )
        block.append(
            Paragraph(
                "Evidence transactions: "
                + ", ".join(finding.evidence_tx_hashes[:8])
                + (
                    f" (+{len(finding.evidence_tx_hashes) - 8} more)"
                    if len(finding.evidence_tx_hashes) > 8
                    else ""
                ),
                styles["mono"],
            )
        )
        block.append(Spacer(1, 6))
        story.append(KeepTogether(block))

    # --- 6. attribution ---
    story.append(Paragraph("6. Probable exit points", styles["h2"]))
    if attribution.trail_degraded:
        story.append(Paragraph(attribution.trail_degraded_note, styles["warn"]))
        story.append(Spacer(1, 4))

    if attribution.candidates:
        rows = [[
            Paragraph("<b>Entity</b>", styles["small"]),
            Paragraph("<b>Confidence</b>", styles["small"]),
            Paragraph(f"<b>Received ({unit})</b>", styles["small"]),
            Paragraph("<b>Source</b>", styles["small"]),
        ]]
        for candidate in attribution.candidates[:8]:
            name = candidate.entity_name or "Unattributed exit cluster"
            source_text = (
                "<br/>".join(
                    f"{s.source}: {s.source_url}" for s in candidate.sources[:2]
                )
                or "No matching public source"
            )
            rows.append([
                Paragraph(name, styles["small"]),
                Paragraph(f"{candidate.confidence:.2f}", styles["small"]),
                Paragraph(f"{candidate.amount_received:g}", styles["small"]),
                Paragraph(source_text, styles["mono"]),
            ])
        table = Table(rows, colWidths=[45 * mm, 20 * mm, 25 * mm, 75 * mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
    else:
        story.append(
            Paragraph(
                "No exit point could be identified from the traced activity.",
                styles["body"],
            )
        )

    story.append(Spacer(1, 5))
    story.append(Paragraph(attribution.coverage.statement, styles["small"]))
    if attribution.unattributed_terminal_clusters:
        story.append(
            Paragraph(
                f"{attribution.unattributed_terminal_clusters} terminal cluster(s) "
                "had no match in the loaded attribution sources and are reported as "
                "unattributed rather than assigned a probable name.",
                styles["small"],
            )
        )

    # --- 7. limitations ---
    story.append(Paragraph("7. Limitations", styles["h2"]))
    story.append(Paragraph(
        "This report is decision support, not a determination. Specifically: "
        "(a) attribution data is incomplete by nature, so an unmatched cluster means "
        "'not present in the sources loaded here', not 'not an exchange'; "
        "(b) funds that pass through a mixing service or a cross-chain bridge cannot "
        "be followed deterministically on-chain, and the report says so rather than "
        "inferring a destination; "
        "(c) clustering heuristics are inferences about common control, not proof of "
        "ownership; "
        "(d) the suspicion score is a hand-tuned rule total, not a probability of "
        "criminality; "
        "(e) no finding here asserts that any person committed an offence.",
        styles["small"],
    ))

    # --- 8. evidence integrity ---
    story.append(Paragraph("8. Evidence integrity", styles["h2"]))
    story.append(_kv_table([
        ("Evidence record", evidence_id),
        ("Hash algorithm", "SHA-256"),
        ("Evidence hash", evidence_hash),
    ], styles))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "The hash above covers the case metadata, findings, cluster assignments and "
        "attribution results as they stood when this report was generated. It is "
        "computed over a canonical JSON serialization, so the same evidence always "
        "produces the same digest. Re-verify it at any time through "
        f"GET /api/cases/{case_id}/evidence/{evidence_id}/verify — if the stored "
        "record has been altered, the recomputed hash will not match.",
        styles["small"],
    ))

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Fund-Trail Investigation Report — {summary.complaint_id}",
        author="SIH26183 Fund-Trail Investigation Assistant",
    )
    doc.build(story)

    return {
        "pdf_path": str(path),
        "filename": filename,
        "evidence_id": evidence_id,
        "evidence_hash": evidence_hash,
        "generated_at": utc_now_iso(),
        "generated_by": generated_by,
    }
