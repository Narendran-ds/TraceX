"""Adapter resolution — the one place the pipeline asks for address data.

Enforces CLAUDE.md rules 4, 5 and 6 in a single path:

    resolve_history(chain, address)
        1. cache lookup (provider-agnostic)                  -> hit: normalize and return
        2. mode == 'fixture'                                 -> honest "no data" answer
        3. token bucket -> live fetch -> cache -> normalize   -> live path

Because step 1 does not care which provider produced the cached row, a case
prefetched from Blockscout and a case seeded from a scenario file read back
identically. Offline is not a special mode.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from backend import config
from backend.adapters.base import (
    AdapterUnavailable,
    AddressHistory,
    ChainAdapter,
    empty_history,
)
from backend.adapters.bitcoin import BlockchairAdapter
from backend.adapters.ethereum import BlockscoutAdapter, EtherscanAdapter
from backend.adapters.fixture import FixtureAdapter
from backend.cache import store
from backend.cache.limiter import RateLimitExceeded, limiters

# Live adapters in the order they are tried, per chain.
LIVE_ADAPTERS: Dict[str, List[str]] = {
    "ethereum": ["blockscout", "etherscan"],
    "bitcoin": ["blockchair"],
}


def build_adapter(name: str, chain: str) -> ChainAdapter:
    if name == "fixture":
        return FixtureAdapter(chain)
    if name == "blockscout":
        return BlockscoutAdapter()
    if name == "etherscan":
        return EtherscanAdapter()
    if name == "blockchair":
        return BlockchairAdapter()
    raise ValueError(f"Unknown adapter: {name}")


def normalizer_for(provider: str, chain: str) -> ChainAdapter:
    """The adapter that knows how to read a cached payload from `provider`."""
    try:
        return build_adapter(provider, chain)
    except ValueError:
        return FixtureAdapter(chain)


@dataclass
class Resolution:
    """What resolve_history did, so callers can report it honestly."""

    history: AddressHistory
    served_from_cache: bool
    provider: str
    limited: bool = False
    limit_note: str = ""


def resolve_history(
    chain: str,
    address: str,
    conn: Optional[sqlite3.Connection] = None,
    mode: Optional[str] = None,
    allow_live: bool = True,
) -> Resolution:
    """Return an address's normalized history, cache first.

    Never raises for a missing address: an address with no cached data and no
    reachable upstream yields an empty history with a note explaining why. The
    caller decides whether that is a dead end or just a leaf.
    """
    mode = mode or config.ADAPTER_MODE

    entry = store.get_any(chain, address, conn=conn)
    if entry is not None:
        adapter = normalizer_for(entry.provider, chain)
        history = adapter.normalize(entry.payload, address)
        history.provider = entry.provider
        history.origin = entry.origin
        history.fetched_at = entry.fetched_at
        return Resolution(history=history, served_from_cache=True, provider=entry.provider)

    if mode == "fixture" or not allow_live:
        return Resolution(
            history=empty_history(
                address,
                chain,
                "No cached data for this address. The system is running from cache "
                "(offline demo mode), so nothing was fetched upstream.",
            ),
            served_from_cache=False,
            provider="none",
        )

    # --- live path: token bucket, fetch, cache, then normalize ---
    reasons: List[str] = []
    for name in LIVE_ADAPTERS.get(chain, []):
        adapter = build_adapter(name, chain)
        if adapter.requires_key and not adapter.is_configured():
            reasons.append(f"{name}: not configured")
            continue
        try:
            limiters.acquire(name, max_wait=10.0)
        except RateLimitExceeded:
            return Resolution(
                history=empty_history(
                    address, chain, f"Rate limit budget for {name} was exhausted."
                ),
                served_from_cache=False,
                provider=name,
                limited=True,
                limit_note=(
                    f"Expansion stopped early: the {name} rate limit was reached. "
                    "The graph below is partial."
                ),
            )
        try:
            payload = adapter.fetch_raw(address)
        except AdapterUnavailable as exc:
            reasons.append(f"{name}: {exc.reason}")
            continue

        # Cache before use. No exceptions (CLAUDE.md rule 4).
        store.put(
            provider=name,
            chain=chain,
            endpoint=adapter.endpoint,
            address=address,
            payload=payload,
            origin="live_cached",
            conn=conn,
        )
        history = adapter.normalize(payload, address)
        history.provider = name
        history.origin = "live_cached"
        return Resolution(history=history, served_from_cache=False, provider=name)

    detail = "; ".join(reasons) if reasons else "no adapter available for this chain"
    return Resolution(
        history=empty_history(
            address,
            chain,
            f"No cached data and no upstream could serve this address ({detail}).",
        ),
        served_from_cache=False,
        provider="none",
        limited=bool(reasons),
        limit_note=(
            "Expansion limited: upstream providers were unavailable. "
            f"({detail})" if reasons else ""
        ),
    )
