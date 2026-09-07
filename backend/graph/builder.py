"""Multi-hop transaction graph builder.

Walks outward from the victim-reported address, following the money. Bounded by
a hop cap, a per-hop expansion cap and a total-address cap — and when a cap
bites, the case is flagged `expansion_limited` with a readable note rather than
silently returning a truncated graph as if it were complete (CLAUDE.md rule 6).

UTXO detail is preserved. For a Bitcoin transaction with several inputs, the
amount attributed to an edge is the output value scaled by the traced address's
share of the transaction's inputs, so a shared-input transaction does not
over-credit any one participant.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from backend.adapters.base import AddressHistory, NormalizedTx, iso
from backend.adapters.registry import resolve_history
from backend.config import (
    MAX_EXPANSIONS_PER_HOP,
    MAX_TOTAL_ADDRESSES,
)
from backend.graph.weighting import edge_weight, is_dust
from backend.models.db import new_id

ProgressFn = Callable[[str, str, int, dict], None]


@dataclass
class EdgeRecord:
    tx_hash: str
    from_address: str
    to_address: str
    amount: float
    timestamp: datetime
    hop_depth: int
    chain: str
    weight: float
    dust: bool


@dataclass
class WalletRecord:
    address: str
    hop_depth: int
    total_in: float = 0.0
    total_out: float = 0.0
    tx_count: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    def observe(self, timestamp: datetime) -> None:
        if self.first_seen is None or timestamp < self.first_seen:
            self.first_seen = timestamp
        if self.last_seen is None or timestamp > self.last_seen:
            self.last_seen = timestamp


@dataclass
class BuiltGraph:
    seed: str
    chain: str
    wallets: Dict[str, WalletRecord] = field(default_factory=dict)
    edges: List[EdgeRecord] = field(default_factory=list)
    # Full transaction objects, keyed by hash — the clustering and pattern
    # engines need the input/output sets, not just the flattened edges.
    transactions: Dict[str, NormalizedTx] = field(default_factory=dict)
    hops_traversed: int = 0
    expansion_limited: bool = False
    expansion_note: str = ""
    origins: Set[str] = field(default_factory=set)
    providers: Set[str] = field(default_factory=set)

    @property
    def address_count(self) -> int:
        return len(self.wallets)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    @property
    def total_amount(self) -> float:
        """Amount leaving the seed address — what the case is actually about."""
        seed = self.seed.lower()
        return sum(e.amount for e in self.edges
                   if e.from_address.lower() == seed and not e.dust)

    def outgoing_from(self, address: str) -> List[EdgeRecord]:
        target = address.lower()
        return [e for e in self.edges if e.from_address.lower() == target]


def _limit_note(reason: str) -> str:
    return (
        f"Expansion limited: {reason}. The graph below is partial — treat absent "
        "branches as un-traced, not as absent activity."
    )


def build_graph(
    seed_address: str,
    chain: str,
    max_hops: int,
    conn: Optional[sqlite3.Connection] = None,
    mode: Optional[str] = None,
    now: Optional[datetime] = None,
    on_progress: Optional[ProgressFn] = None,
) -> BuiltGraph:
    """Expand outward from the seed address, following outgoing value."""
    now = now or datetime.now(timezone.utc)
    graph = BuiltGraph(seed=seed_address, chain=chain)

    frontier: List[str] = [seed_address]
    seen: Set[str] = {seed_address.lower()}
    graph.wallets[seed_address.lower()] = WalletRecord(
        address=seed_address, hop_depth=0
    )

    for hop in range(max_hops):
        if not frontier:
            break

        # (address, weight) candidates discovered at this hop.
        candidates: Dict[str, float] = {}

        for address in frontier:
            resolution = resolve_history(chain, address, conn=conn, mode=mode)
            history = resolution.history
            graph.providers.add(resolution.provider)
            graph.origins.add(history.origin)

            if resolution.limited and not graph.expansion_limited:
                graph.expansion_limited = True
                graph.expansion_note = _limit_note(resolution.limit_note or "upstream unavailable")
            if history.truncated and not graph.expansion_limited:
                graph.expansion_limited = True
                graph.expansion_note = _limit_note(
                    history.note or "the provider capped the number of records returned"
                )

            _absorb_history(graph, history, address, hop, now, seen, candidates)

        graph.hops_traversed = hop + 1

        # Rank the next frontier by edge weight so the caps spend the budget on
        # the branches that actually carry the money.
        ranked: List[Tuple[str, float]] = sorted(
            candidates.items(), key=lambda kv: kv[1], reverse=True
        )
        if len(ranked) > MAX_EXPANSIONS_PER_HOP:
            graph.expansion_limited = True
            graph.expansion_note = _limit_note(
                f"hop {hop + 1} produced {len(ranked)} onward addresses, above the "
                f"per-hop cap of {MAX_EXPANSIONS_PER_HOP}"
            )
            ranked = ranked[:MAX_EXPANSIONS_PER_HOP]

        next_frontier: List[str] = []
        for address, _weight in ranked:
            if len(graph.wallets) >= MAX_TOTAL_ADDRESSES:
                graph.expansion_limited = True
                graph.expansion_note = _limit_note(
                    f"the total address cap of {MAX_TOTAL_ADDRESSES} was reached"
                )
                break
            key = address.lower()
            if key in seen:
                continue
            seen.add(key)
            graph.wallets.setdefault(
                key, WalletRecord(address=address, hop_depth=hop + 1)
            )
            next_frontier.append(address)

        if on_progress:
            on_progress(
                "hop_complete",
                f"Hop {hop + 1} complete",
                hop + 1,
                {
                    "addresses": graph.address_count,
                    "transactions": len(graph.transactions),
                    "edges": graph.edge_count,
                },
            )

        frontier = next_frontier

    return graph


def _absorb_history(
    graph: BuiltGraph,
    history: AddressHistory,
    address: str,
    hop: int,
    now: datetime,
    seen: Set[str],
    candidates: Dict[str, float],
) -> None:
    """Record one address's transactions into the graph."""
    key = address.lower()
    wallet = graph.wallets.setdefault(key, WalletRecord(address=address, hop_depth=hop))

    for tx in history.transactions:
        graph.transactions.setdefault(tx.tx_hash, tx)
        wallet.observe(tx.timestamp)

        received = tx.amount_to(address)
        spent = tx.amount_from(address)
        if received or spent:
            wallet.tx_count += 1
        wallet.total_in += received
        wallet.total_out += spent

        if not spent:
            continue  # incoming only: context for this wallet, not an onward hop

        # The traced address's share of this transaction's inputs. For an
        # account-model transfer this is 1.0; for a shared-input UTXO spend it
        # apportions the outputs rather than crediting the whole amount.
        total_input = tx.total_input
        share = (spent / total_input) if total_input > 0 else 1.0

        input_set = {a.lower() for a in tx.input_addresses}
        for out in tx.outputs:
            to_key = out.address.lower()
            if to_key in input_set:
                continue  # change returning to the sender is not an onward hop
            amount = out.amount * share
            dust = is_dust(amount, history.chain or graph.chain)
            weight = edge_weight(amount, tx.timestamp, hop, now=now)

            graph.edges.append(
                EdgeRecord(
                    tx_hash=tx.tx_hash,
                    from_address=address,
                    to_address=out.address,
                    amount=amount,
                    timestamp=tx.timestamp,
                    hop_depth=hop,
                    chain=history.chain or graph.chain,
                    weight=weight,
                    dust=dust,
                )
            )

            if dust or to_key in seen:
                continue
            candidates[out.address] = max(candidates.get(out.address, 0.0), weight)


# --- persistence -----------------------------------------------------------

def persist_graph(conn: sqlite3.Connection, case_id: str, graph: BuiltGraph) -> None:
    """Write wallets and transactions for this case. Idempotent per case."""
    conn.execute("DELETE FROM transactions WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM wallets WHERE case_id = ?", (case_id,))

    for wallet in graph.wallets.values():
        conn.execute(
            "INSERT INTO wallets (id, case_id, address, hop_depth, first_seen,"
            " last_seen, total_in, total_out, tx_count)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                case_id,
                wallet.address,
                wallet.hop_depth,
                iso(wallet.first_seen) if wallet.first_seen else None,
                iso(wallet.last_seen) if wallet.last_seen else None,
                wallet.total_in,
                wallet.total_out,
                wallet.tx_count,
            ),
        )

    for edge in graph.edges:
        conn.execute(
            "INSERT OR IGNORE INTO transactions (id, case_id, tx_hash, from_address,"
            " to_address, amount, timestamp, chain, hop_depth, edge_weight, is_dust)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                case_id,
                edge.tx_hash,
                edge.from_address,
                edge.to_address,
                edge.amount,
                iso(edge.timestamp),
                edge.chain,
                edge.hop_depth,
                edge.weight,
                1 if edge.dust else 0,
            ),
        )


def primary_trail(graph: BuiltGraph, terminal_addresses: Iterable[str]) -> List[str]:
    """Highest-weight path from the seed to a terminal address.

    This is the "primary trail" the graph screen highlights while dimming
    everything else. Greedy on edge weight, which is what an analyst following
    the largest movement by hand would do.
    """
    targets = {a.lower() for a in terminal_addresses}
    if not targets:
        return []

    best_path: List[str] = []
    best_score = -1.0

    # Depth-limited search; the hop cap keeps the branching factor bounded.
    def walk(address: str, path: List[str], score: float, visited: Set[str]) -> None:
        nonlocal best_path, best_score
        if address.lower() in targets and len(path) > 1:
            if score > best_score:
                best_score = score
                best_path = list(path)
            return
        if len(path) > 12:
            return
        for edge in sorted(
            graph.outgoing_from(address), key=lambda e: e.weight, reverse=True
        ):
            if edge.dust:
                continue
            nxt = edge.to_address.lower()
            if nxt in visited:
                continue
            visited.add(nxt)
            path.append(edge.to_address)
            walk(edge.to_address, path, score + edge.weight, visited)
            path.pop()
            visited.discard(nxt)

    walk(graph.seed, [graph.seed], 0.0, {graph.seed.lower()})
    return best_path
