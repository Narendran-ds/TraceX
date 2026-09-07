"""Clustering orchestration.

Turns the raw address graph into actor clusters — the "hundreds of addresses
collapse into a handful of actual actors" step. The collapse ratio the UI shows
is counted from this output, never asserted.

Confidence per method reflects how strong the underlying heuristic actually is:

    common_input   0.90  the strongest heuristic in blockchain forensics
    change_address 0.70  a sound but assumption-laden inference
    behavioral     0.55  correlation, not ownership evidence
    singleton      1.00  "this address is itself" — trivially true

CoinJoin-like involvement subtracts from whichever confidence applies, and the
reason is carried in confidence_notes so it reaches the investigator.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from backend.adapters.base import NormalizedTx, iso
from backend.clustering.heuristics import (
    UnionFind,
    cluster_behavioral,
    cluster_common_input,
    detect_change_outputs,
)
from backend.graph.builder import BuiltGraph
from backend.models.db import dumps, new_id

METHOD_CONFIDENCE = {
    "common_input": 0.90,
    "change_address": 0.70,
    "behavioral": 0.55,
    "singleton": 1.00,
}

COINJOIN_CONFIDENCE_PENALTY = 0.35


@dataclass
class Cluster:
    id: str
    label: str
    members: List[str]
    method: str
    confidence: float
    confidence_notes: List[str] = field(default_factory=list)
    role: str = "intermediary"
    is_terminal: bool = False
    total_in: float = 0.0
    total_out: float = 0.0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    hop_depth: int = 0
    attributed_entity: Optional[str] = None
    attribution_source: Optional[str] = None

    @property
    def member_count(self) -> int:
        return len(self.members)


@dataclass
class ClusteringResult:
    clusters: List[Cluster]
    address_to_cluster: Dict[str, str]

    @property
    def address_count(self) -> int:
        return len(self.address_to_cluster)

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)

    @property
    def collapse_ratio(self) -> Optional[float]:
        """Addresses per cluster. Computed, never stated as a target."""
        if not self.clusters:
            return None
        return round(self.address_count / self.cluster_count, 2)


def cluster_graph(graph: BuiltGraph) -> ClusteringResult:
    """Cluster the addresses in a built graph using chain-appropriate heuristics."""
    transactions: Sequence[NormalizedTx] = list(graph.transactions.values())
    addresses = list(graph.wallets.keys())

    uf = UnionFind()
    for address in addresses:
        uf.add(address)

    methods: Dict[str, str] = {a: "singleton" for a in addresses}
    notes: Dict[str, List[str]] = {a: [] for a in addresses}
    coinjoin_touched: set = set()

    if graph.chain == "bitcoin":
        ci_uf, coinjoin_touched, ci_notes = cluster_common_input(transactions)
        for address in addresses:
            root = ci_uf.find(address) if address in ci_uf.parent else None
            if root is None:
                continue
            for member in ci_uf.groups().get(root, []):
                if member in uf.parent or member in methods:
                    uf.union(address, member)
        # Record which addresses were actually merged by common input.
        for group in ci_uf.groups().values():
            if len(group) > 1:
                for member in group:
                    if member in methods:
                        methods[member] = "common_input"
        for address, entries in ci_notes.items():
            notes.setdefault(address, []).extend(entries)

        change_map = detect_change_outputs(transactions)
        for change_address, owner in change_map.items():
            if change_address in methods:
                uf.union(change_address, owner)
                if methods[change_address] == "singleton":
                    methods[change_address] = "change_address"
                notes.setdefault(change_address, []).append(
                    "Identified as a likely change output and grouped with the "
                    "spending address. Change detection is an inference, not proof "
                    "of ownership."
                )
    else:
        beh_uf, beh_notes = cluster_behavioral(transactions)
        for group in beh_uf.groups().values():
            present = [m for m in group if m in methods]
            if len(present) < 2:
                continue
            for member in present[1:]:
                uf.union(present[0], member)
            for member in present:
                if methods[member] == "singleton":
                    methods[member] = "behavioral"
        for address, entries in beh_notes.items():
            if address in notes:
                notes[address].extend(entries)

    # --- materialize clusters ---
    groups: Dict[str, List[str]] = {}
    for address in addresses:
        groups.setdefault(uf.find(address), []).append(address)

    clusters: List[Cluster] = []
    address_to_cluster: Dict[str, str] = {}

    for index, (_root, members) in enumerate(
        sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])), start=1
    ):
        member_methods = [methods.get(m, "singleton") for m in members]
        # The strongest heuristic that contributed decides how the cluster is
        # labelled; a cluster is only as strong as its weakest link, so the
        # confidence uses the weakest contributing method.
        if len(members) == 1:
            method = "singleton"
        else:
            for candidate in ("behavioral", "change_address", "common_input"):
                if candidate in member_methods:
                    method = candidate
                    break
            else:
                method = "common_input"

        confidence = METHOD_CONFIDENCE[method]
        cluster_notes: List[str] = []
        for member in members:
            cluster_notes.extend(notes.get(member, []))

        if any(m in coinjoin_touched for m in members):
            confidence = max(0.05, confidence - COINJOIN_CONFIDENCE_PENALTY)

        cluster = Cluster(
            id=new_id(),
            label=f"Cluster {index}",
            members=sorted(members),
            method=method,
            confidence=round(confidence, 2),
            confidence_notes=_dedupe(cluster_notes),
        )

        hop_depths = [graph.wallets[m].hop_depth for m in members if m in graph.wallets]
        cluster.hop_depth = min(hop_depths) if hop_depths else 0
        for member in members:
            wallet = graph.wallets.get(member)
            if not wallet:
                continue
            cluster.total_in += wallet.total_in
            cluster.total_out += wallet.total_out
            if wallet.first_seen and (
                cluster.first_seen is None or wallet.first_seen < cluster.first_seen
            ):
                cluster.first_seen = wallet.first_seen
            if wallet.last_seen and (
                cluster.last_seen is None or wallet.last_seen > cluster.last_seen
            ):
                cluster.last_seen = wallet.last_seen
            address_to_cluster[member] = cluster.id

        clusters.append(cluster)

    _assign_roles(graph, clusters, address_to_cluster)
    return ClusteringResult(clusters=clusters, address_to_cluster=address_to_cluster)


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _assign_roles(
    graph: BuiltGraph, clusters: List[Cluster], address_to_cluster: Dict[str, str]
) -> None:
    """Mark the seed cluster and any cluster with no onward spend.

    Roles beyond this (exchange / mixer / bridge) are set by the attribution
    layer, which is the only thing entitled to name an entity.
    """
    seed_key = graph.seed.lower()
    outgoing_addresses = {e.from_address.lower() for e in graph.edges if not e.dust}

    for cluster in clusters:
        if seed_key in cluster.members:
            cluster.role = "suspect"
        # Terminal: nothing left this cluster, so the traced value stops here.
        if not any(m in outgoing_addresses for m in cluster.members):
            cluster.is_terminal = True


def persist_clusters(
    conn: sqlite3.Connection, case_id: str, result: ClusteringResult
) -> None:
    conn.execute("UPDATE wallets SET cluster_id = NULL WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM clusters WHERE case_id = ?", (case_id,))

    for cluster in result.clusters:
        conn.execute(
            "INSERT INTO clusters (id, case_id, cluster_label, member_count,"
            " clustering_method, cluster_confidence, confidence_notes,"
            " attributed_entity, attribution_source, role, is_terminal,"
            " first_seen, last_seen, total_in, total_out)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cluster.id,
                case_id,
                cluster.label,
                cluster.member_count,
                cluster.method,
                cluster.confidence,
                dumps(cluster.confidence_notes),
                cluster.attributed_entity,
                cluster.attribution_source,
                cluster.role,
                1 if cluster.is_terminal else 0,
                iso(cluster.first_seen) if cluster.first_seen else None,
                iso(cluster.last_seen) if cluster.last_seen else None,
                cluster.total_in,
                cluster.total_out,
            ),
        )
        for member in cluster.members:
            conn.execute(
                "UPDATE wallets SET cluster_id = ? WHERE case_id = ? AND LOWER(address) = ?",
                (cluster.id, case_id, member),
            )
