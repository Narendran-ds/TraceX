"""api_cache access.

CLAUDE.md rule 4: every upstream response is cached before use, and the full
pipeline must run offline from cache.

The design point that makes offline mode free: cache lookup is
*provider-agnostic*. `get_any` finds whatever is cached for (chain, address)
regardless of which provider produced it, and the caller normalizes using that
provider's normalizer. So a case prefetched from Blockscout and a case seeded
from a scenario file are read back through exactly the same path — offline is
not a special mode, it is the normal mode with no upstream attached.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import CACHE_TTL_SECONDS
from backend.models.db import connect, utc_now_iso


def cache_key(provider: str, chain: str, endpoint: str, address: str) -> str:
    """chain:endpoint:address, namespaced by provider so normalizers stay honest."""
    return f"{provider}:{chain}:{endpoint}:{address.lower()}"


@dataclass
class CacheEntry:
    cache_key: str
    provider: str
    payload: Any
    origin: str          # live_cached | synthetic_scenario
    fetched_at: str
    ttl_seconds: int

    @property
    def is_expired(self) -> bool:
        """Expiry is advisory: a stale entry still beats no entry offline."""
        if self.ttl_seconds <= 0:
            return False
        try:
            fetched = datetime.fromisoformat(self.fetched_at)
        except ValueError:
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        return age > self.ttl_seconds


def _row_to_entry(row: sqlite3.Row) -> CacheEntry:
    return CacheEntry(
        cache_key=row["cache_key"],
        provider=row["provider"],
        payload=json.loads(row["response_json"]),
        origin=row["origin"],
        fetched_at=row["fetched_at"],
        ttl_seconds=row["ttl_seconds"],
    )


def put(
    provider: str,
    chain: str,
    endpoint: str,
    address: str,
    payload: Any,
    origin: str,
    conn: Optional[sqlite3.Connection] = None,
    ttl_seconds: int = CACHE_TTL_SECONDS,
) -> str:
    """Store an upstream (or seeded) response. Called before the payload is used."""
    if origin not in ("live_cached", "synthetic_scenario"):
        raise ValueError(f"origin must declare what the data is, got {origin!r}")

    key = cache_key(provider, chain, endpoint, address)
    owned = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            "INSERT INTO api_cache (cache_key, response_json, provider, fetched_at,"
            " ttl_seconds, origin) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(cache_key) DO UPDATE SET"
            " response_json=excluded.response_json, provider=excluded.provider,"
            " fetched_at=excluded.fetched_at, ttl_seconds=excluded.ttl_seconds,"
            " origin=excluded.origin",
            (
                key,
                json.dumps(payload, separators=(",", ":"), default=str),
                provider,
                utc_now_iso(),
                ttl_seconds,
                origin,
            ),
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
    return key


def get(
    provider: str,
    chain: str,
    endpoint: str,
    address: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[CacheEntry]:
    key = cache_key(provider, chain, endpoint, address)
    owned = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT * FROM api_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        return _row_to_entry(row) if row else None
    finally:
        if owned:
            conn.close()


def get_any(
    chain: str,
    address: str,
    endpoint: str = "address_history",
    conn: Optional[sqlite3.Connection] = None,
    provider_preference: Optional[List[str]] = None,
) -> Optional[CacheEntry]:
    """Find a cached response for this address from any provider.

    This is what lets the demo run offline from whatever was prefetched, without
    the caller needing to know which provider produced it.
    """
    suffix = f":{chain}:{endpoint}:{address.lower()}"
    owned = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            "SELECT * FROM api_cache WHERE cache_key LIKE ?", ("%" + suffix,)
        ).fetchall()
        if not rows:
            return None
        entries = [_row_to_entry(r) for r in rows]
        if len(entries) == 1:
            return entries[0]
        # Prefer an explicitly requested provider, then real cached data over a
        # constructed scenario, then whichever was fetched most recently.
        order = provider_preference or []

        def rank(entry: CacheEntry):
            pref = order.index(entry.provider) if entry.provider in order else len(order)
            return (pref, 0 if entry.origin == "live_cached" else 1, entry.fetched_at)

        entries.sort(key=rank)
        return entries[0]
    finally:
        if owned:
            conn.close()


def stats(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    owned = conn is None
    conn = conn or connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        by_origin = {
            r["origin"]: r["n"]
            for r in conn.execute(
                "SELECT origin, COUNT(*) AS n FROM api_cache GROUP BY origin"
            )
        }
        by_provider = {
            r["provider"]: r["n"]
            for r in conn.execute(
                "SELECT provider, COUNT(*) AS n FROM api_cache GROUP BY provider"
            )
        }
        return {"total": total, "by_origin": by_origin, "by_provider": by_provider}
    finally:
        if owned:
            conn.close()


def clear(conn: Optional[sqlite3.Connection] = None) -> None:
    owned = conn is None
    conn = conn or connect()
    try:
        conn.execute("DELETE FROM api_cache")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
