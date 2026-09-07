"""Test helpers for constructing scenario data.

These build transaction payloads in the fixture format. Every scenario used in a
test is written to satisfy a detector definition that already exists — the
detectors are never tuned to make a scenario light up.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.cache import store
from backend.models.db import connect

BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def at(minutes: float = 0, hours: float = 0, days: float = 0) -> str:
    return (
        BASE_TIME + timedelta(minutes=minutes, hours=hours, days=days)
    ).isoformat()


def tx(
    tx_hash: str,
    timestamp: str,
    inputs: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
    fee: float = 0.0,
) -> Dict[str, Any]:
    return {
        "tx_hash": tx_hash,
        "timestamp": timestamp,
        "inputs": inputs,
        "outputs": outputs,
        "fee": fee,
    }


def io(address: str, amount: float) -> Dict[str, Any]:
    return {"address": address, "amount": amount}


def seed_address(
    conn,
    address: str,
    chain: str,
    transactions: List[Dict[str, Any]],
    origin: str = "synthetic_scenario",
) -> None:
    """Put one address's history into the cache, as the seeder would."""
    store.put(
        provider="fixture",
        chain=chain,
        endpoint="address_history",
        address=address,
        payload={
            "address": address,
            "chain": chain,
            "origin": origin,
            "transactions": transactions,
        },
        origin=origin,
        conn=conn,
    )


def seed_scenario(
    db_path,
    chain: str,
    per_address: Dict[str, List[Dict[str, Any]]],
    origin: str = "synthetic_scenario",
):
    """Seed a whole scenario: a map of address -> its transaction list."""
    conn = connect(db_path)
    try:
        for address, transactions in per_address.items():
            seed_address(conn, address, chain, transactions, origin=origin)
        conn.commit()
    finally:
        conn.close()


def distribute(
    transactions: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Route each transaction to every address that appears in it.

    Mirrors reality: querying any participating address returns that
    transaction, so the graph walker sees the same edge from either end.
    """
    per_address: Dict[str, List[Dict[str, Any]]] = {}
    for transaction in transactions:
        participants = {i["address"] for i in transaction["inputs"]}
        participants |= {o["address"] for o in transaction["outputs"]}
        for address in participants:
            per_address.setdefault(address, []).append(transaction)
    return per_address


def make_case(
    conn,
    case_id: str,
    address: str,
    chain: str,
    provenance: str = "synthetic_scenario",
    complaint_id: str = "CYB-TEST-0001",
    max_hops: int = 4,
) -> None:
    from backend.models.db import utc_now_iso

    conn.execute(
        "INSERT INTO cases (id, complaint_id, wallet_address, chain, status,"
        " data_provenance, provenance_note, max_hops, created_at)"
        " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
        (
            case_id,
            complaint_id,
            address,
            chain,
            provenance,
            "Constructed scenario used for automated tests.",
            max_hops,
            utc_now_iso(),
        ),
    )
