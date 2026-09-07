"""Evidence hashing.

Turns the output from "a screenshot" into "a verifiable artifact". The hash is
SHA-256 over a *canonical* JSON serialization — sorted keys, no insignificant
whitespace, fixed separators — so the same evidence always hashes to the same
value regardless of dict ordering or the Python version that produced it. A hash
that depends on serialization luck is not evidence of anything.

`evidence_records.payload_hash` is never regenerated silently on read. The verify
endpoint recomputes from the stored payload and compares, which is what makes a
tampered record detectable rather than quietly re-blessed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict, Optional, Tuple

from backend.models.db import new_id, utc_now_iso

HASH_ALGORITHM = "sha256"


def canonical_json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 preserved."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def compute_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def store_evidence(
    conn: sqlite3.Connection, case_id: str, payload: Dict[str, Any]
) -> Tuple[str, str]:
    """Persist an evidence payload with its hash. Returns (evidence_id, hash)."""
    evidence_id = new_id()
    serialized = canonical_json(payload)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    conn.execute(
        "INSERT INTO evidence_records (id, case_id, payload_json, payload_hash,"
        " hash_algorithm, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (evidence_id, case_id, serialized, digest, HASH_ALGORITHM, utc_now_iso()),
    )
    return evidence_id, digest


def verify_evidence(
    conn: sqlite3.Connection, case_id: str, evidence_id: str
) -> Optional[Dict[str, Any]]:
    """Recompute the hash from the stored payload and compare it to the stored one."""
    row = conn.execute(
        "SELECT * FROM evidence_records WHERE id = ? AND case_id = ?",
        (evidence_id, case_id),
    ).fetchone()
    if row is None:
        return None

    stored_payload = row["payload_json"]
    recomputed = hashlib.sha256(stored_payload.encode("utf-8")).hexdigest()

    return {
        "evidence_id": row["id"],
        "case_id": row["case_id"],
        "stored_hash": row["payload_hash"],
        "recomputed_hash": recomputed,
        "match": recomputed == row["payload_hash"],
        "hash_algorithm": row["hash_algorithm"],
        "created_at": row["created_at"],
        "verified_at": utc_now_iso(),
    }
