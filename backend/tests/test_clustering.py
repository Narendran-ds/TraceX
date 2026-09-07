"""S2 gate: clustering heuristics and the collapse.

ROADMAP P2 exit criterion is that the address -> cluster collapse demonstrably
works. The collapse ratio here is counted from the result, never asserted as a
target figure.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.adapters.base import NormalizedTx, TxEndpoint
from backend.clustering.engine import cluster_graph, persist_clusters
from backend.clustering.heuristics import (
    cluster_behavioral,
    cluster_common_input,
    detect_change_outputs,
    is_coinjoin_like,
)
from backend.graph.builder import build_graph, persist_graph
from backend.models.db import connect, loads
from backend.tests.helpers import at, distribute, io, make_case, seed_scenario, tx

NOW = datetime(2026, 8, 2, 10, 0, 0, tzinfo=timezone.utc)


def ntx(tx_hash, ts, inputs, outputs):
    return NormalizedTx(
        tx_hash=tx_hash,
        chain="bitcoin",
        timestamp=datetime.fromisoformat(ts),
        inputs=[TxEndpoint(a, v) for a, v in inputs],
        outputs=[TxEndpoint(a, v) for a, v in outputs],
    )


# --- common-input-ownership -----------------------------------------------

def test_common_input_unions_co_spent_addresses():
    txs = [ntx("t1", at(0), [("A", 1.0), ("B", 2.0), ("C", 3.0)], [("Z", 6.0)])]
    uf, coinjoin, _ = cluster_common_input(txs)
    assert uf.find("A") == uf.find("B") == uf.find("C")
    assert not coinjoin


def test_common_input_does_not_union_unrelated_transactions():
    txs = [
        ntx("t1", at(0), [("A", 1.0), ("B", 1.0)], [("Z", 2.0)]),
        ntx("t2", at(10), [("C", 1.0), ("D", 1.0)], [("Y", 2.0)]),
    ]
    uf, _, _ = cluster_common_input(txs)
    assert uf.find("A") == uf.find("B")
    assert uf.find("A") != uf.find("C")


def test_transitive_merge_across_transactions():
    """A+B together, then B+C together, makes one three-address entity."""
    txs = [
        ntx("t1", at(0), [("A", 1.0), ("B", 1.0)], [("Z", 2.0)]),
        ntx("t2", at(10), [("B", 1.0), ("C", 1.0)], [("Y", 2.0)]),
    ]
    uf, _, _ = cluster_common_input(txs)
    assert uf.find("A") == uf.find("C")


# --- CoinJoin -------------------------------------------------------------

def test_coinjoin_shape_is_recognised():
    equal = [(f"out{i}", 1.0) for i in range(5)]
    inputs = [(f"in{i}", 1.0) for i in range(5)]
    assert is_coinjoin_like(ntx("cj", at(0), inputs, equal)) is True


def test_ordinary_multi_input_spend_is_not_a_coinjoin():
    txs = ntx("t1", at(0), [("A", 1.0), ("B", 2.0)], [("Z", 2.5), ("A", 0.5)])
    assert is_coinjoin_like(txs) is False


def test_coinjoin_reduces_confidence_instead_of_merging():
    """CLAUDE.md: reduce cluster confidence rather than assert a false merge."""
    equal = [(f"out{i}", 1.0) for i in range(5)]
    inputs = [(f"in{i}", 1.0) for i in range(5)]
    uf, coinjoin, notes = cluster_common_input([ntx("cj", at(0), inputs, equal)])

    assert uf.find("in0") != uf.find("in1"), "CoinJoin participants were falsely merged"
    assert "in0" in coinjoin
    assert any("CoinJoin-like" in n for n in notes["in0"])


# --- change-address detection ---------------------------------------------

def test_change_output_is_detected():
    """Round payment out, non-round remainder back to a fresh address."""
    txs = [ntx("t1", at(0), [("A", 5.0)], [("PAY", 2.0), ("CHG", 2.98731)])]
    result = detect_change_outputs(txs)
    assert result.get("chg") == "A"


def test_change_detection_declines_when_both_outputs_are_round():
    txs = [ntx("t1", at(0), [("A", 5.0)], [("X", 2.0), ("Y", 3.0)])]
    assert detect_change_outputs(txs) == {}


def test_change_detection_declines_on_previously_seen_address():
    txs = [
        ntx("t0", at(-10), [("Q", 1.0)], [("KNOWN", 1.0)]),
        ntx("t1", at(0), [("A", 5.0)], [("PAY", 2.0), ("KNOWN", 2.98731)]),
    ]
    assert "known" not in detect_change_outputs(txs)


# --- behavioral (account model) -------------------------------------------

def test_behavioral_groups_addresses_funded_together():
    txs = [
        ntx("t1", at(0), [("FUNDER", 1.0)], [("X", 1.0)]),
        ntx("t2", at(5), [("FUNDER", 1.0)], [("Y", 1.0)]),
        ntx("t3", at(9), [("FUNDER", 1.0)], [("Z", 1.0)]),
    ]
    uf, notes = cluster_behavioral(txs)
    assert uf.find("X") == uf.find("Y") == uf.find("Z")
    assert any("weaker evidence" in n for n in notes["x"])


def test_behavioral_does_not_group_across_a_long_gap():
    txs = [
        ntx("t1", at(0), [("FUNDER", 1.0)], [("X", 1.0)]),
        ntx("t2", at(hours=10), [("FUNDER", 1.0)], [("Y", 1.0)]),
    ]
    uf, _ = cluster_behavioral(txs)
    assert uf.find("x") != uf.find("y")


# --- end-to-end clustering over a built graph ------------------------------

def test_bitcoin_graph_collapses_to_fewer_clusters(temp_db):
    """Many addresses, co-spent in groups, collapse into a handful of actors."""
    txs = [
        tx("t1", at(0), [io("seed", 12.0)],
           [io("a1", 3.0), io("a2", 3.0), io("a3", 3.0), io("a4", 3.0)]),
        # a1..a4 are later co-spent together, revealing one owner.
        tx("t2", at(60), [io("a1", 3.0), io("a2", 3.0), io("a3", 3.0), io("a4", 3.0)],
           [io("exit", 12.0)]),
    ]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("seed", "bitcoin", max_hops=4, conn=conn,
                            mode="fixture", now=NOW)
        result = cluster_graph(graph)
    finally:
        conn.close()

    assert result.address_count == 6           # seed, a1-a4, exit
    assert result.cluster_count < result.address_count, "no collapse occurred"
    assert result.collapse_ratio is not None
    assert result.collapse_ratio > 1.0

    merged = [c for c in result.clusters if c.member_count == 4]
    assert len(merged) == 1
    assert merged[0].method == "common_input"
    assert merged[0].confidence == pytest.approx(0.90)


def test_seed_cluster_is_marked_suspect_and_exit_is_terminal(temp_db):
    txs = [
        tx("t1", at(0), [io("seed", 5.0)], [io("mid", 5.0)]),
        tx("t2", at(30), [io("mid", 5.0)], [io("exit", 5.0)]),
    ]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("seed", "bitcoin", max_hops=4, conn=conn,
                            mode="fixture", now=NOW)
        result = cluster_graph(graph)
    finally:
        conn.close()

    by_member = {m: c for c in result.clusters for m in c.members}
    assert by_member["seed"].role == "suspect"
    assert by_member["exit"].is_terminal is True
    assert by_member["mid"].is_terminal is False


def test_singleton_clusters_are_full_confidence(temp_db):
    txs = [tx("t1", at(0), [io("seed", 5.0)], [io("b", 5.0)])]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("seed", "bitcoin", max_hops=2, conn=conn,
                            mode="fixture", now=NOW)
        result = cluster_graph(graph)
    finally:
        conn.close()

    assert all(c.method == "singleton" for c in result.clusters)
    assert all(c.confidence == pytest.approx(1.0) for c in result.clusters)


def test_coinjoin_in_graph_lowers_cluster_confidence(temp_db):
    inputs = [io(f"in{i}", 1.0) for i in range(5)]
    outputs = [io(f"out{i}", 0.99) for i in range(5)]
    txs = [
        tx("t0", at(-60), [io("seed", 5.0)],
           [io("in0", 1.0), io("in1", 1.0), io("in2", 1.0), io("in3", 1.0),
            io("in4", 1.0)]),
        tx("cj", at(0), inputs, outputs),
    ]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("seed", "bitcoin", max_hops=4, conn=conn,
                            mode="fixture", now=NOW)
        result = cluster_graph(graph)
    finally:
        conn.close()

    touched = [c for c in result.clusters
               if any(m.startswith("in") for m in c.members)]
    assert touched, "expected clusters covering the CoinJoin participants"
    assert any(c.confidence < 1.0 for c in touched)
    assert any(
        any("CoinJoin-like" in n for n in c.confidence_notes) for c in touched
    )


def test_persist_clusters_links_wallets(temp_db):
    txs = [
        tx("t1", at(0), [io("seed", 6.0)], [io("a1", 3.0), io("a2", 3.0)]),
        tx("t2", at(60), [io("a1", 3.0), io("a2", 3.0)], [io("exit", 6.0)]),
    ]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        make_case(conn, "case-1", "seed", "bitcoin")
        graph = build_graph("seed", "bitcoin", max_hops=4, conn=conn,
                            mode="fixture", now=NOW)
        persist_graph(conn, "case-1", graph)
        result = cluster_graph(graph)
        persist_clusters(conn, "case-1", result)
        conn.commit()

        unlinked = conn.execute(
            "SELECT COUNT(*) FROM wallets WHERE case_id='case-1' AND cluster_id IS NULL"
        ).fetchone()[0]
        assert unlinked == 0

        rows = conn.execute(
            "SELECT confidence_notes FROM clusters WHERE case_id='case-1'"
        ).fetchall()
        for row in rows:
            assert isinstance(loads(row["confidence_notes"], None), list)
    finally:
        conn.close()


def test_persist_clusters_is_idempotent(temp_db):
    txs = [tx("t1", at(0), [io("seed", 5.0)], [io("b", 5.0)])]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        make_case(conn, "case-1", "seed", "bitcoin")
        graph = build_graph("seed", "bitcoin", max_hops=2, conn=conn,
                            mode="fixture", now=NOW)
        persist_graph(conn, "case-1", graph)
        result = cluster_graph(graph)
        persist_clusters(conn, "case-1", result)
        persist_clusters(conn, "case-1", result)
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM clusters WHERE case_id='case-1'"
        ).fetchone()[0] == result.cluster_count
    finally:
        conn.close()
