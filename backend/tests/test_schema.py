"""S0 gate: the schema loads and enforces the honesty constraints we rely on."""
from __future__ import annotations

import sqlite3

import pytest

from backend.models.db import connect, init_db, new_id, utc_now_iso


def _tables(conn) -> set:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_all_documented_tables_exist(temp_db):
    conn = connect(temp_db)
    try:
        expected = {
            "cases", "wallets", "transactions", "clusters", "findings",
            "attribution_tags", "evidence_records", "reports", "api_cache",
            "audit_log",
        }
        assert expected.issubset(_tables(conn))
    finally:
        conn.close()


def test_case_requires_data_provenance(temp_db):
    """A case cannot exist without declaring what data it is made of."""
    conn = connect(temp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cases (id, complaint_id, wallet_address, chain, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (new_id(), "CYB-1", "addr", "bitcoin", utc_now_iso()),
            )
    finally:
        conn.close()


def test_case_rejects_unknown_provenance(temp_db):
    conn = connect(temp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cases (id, complaint_id, wallet_address, chain,"
                " data_provenance, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), "CYB-1", "addr", "bitcoin", "made_up", utc_now_iso()),
            )
    finally:
        conn.close()


def test_attribution_tag_requires_source_and_url(temp_db):
    """CLAUDE.md rule 8: an attribution claim without provenance is not stored."""
    conn = connect(temp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO attribution_tags (address, chain, entity_name, entity_type,"
                " confidence, last_updated) VALUES (?, ?, ?, ?, ?, ?)",
                ("addr", "bitcoin", "SomeExchange", "exchange", 0.9, utc_now_iso()),
            )
    finally:
        conn.close()


def test_init_db_is_idempotent(temp_db):
    init_db(temp_db)
    init_db(temp_db)
    conn = connect(temp_db)
    try:
        assert "cases" in _tables(conn)
    finally:
        conn.close()
