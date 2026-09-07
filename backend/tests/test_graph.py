"""S2 gate: graph expansion, weighting, dust filtering and caps."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.graph.builder import build_graph, persist_graph, primary_trail
from backend.graph.weighting import depth_decay, edge_weight, is_dust, time_decay
from backend.models.db import connect
from backend.tests.helpers import at, distribute, io, make_case, seed_scenario, tx

NOW = datetime(2026, 8, 2, 10, 0, 0, tzinfo=timezone.utc)


def test_time_decay_halves_at_one_halflife():
    from backend.config import TIME_DECAY_HALFLIFE_DAYS

    recent = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert time_decay(recent, now) == pytest.approx(1.0)

    from datetime import timedelta
    older = now - timedelta(days=TIME_DECAY_HALFLIFE_DAYS)
    assert time_decay(older, now) == pytest.approx(0.5, abs=1e-6)


def test_depth_decay_reduces_with_distance_from_seed():
    assert depth_decay(0) == pytest.approx(1.0)
    assert depth_decay(1) > depth_decay(2) > depth_decay(3)


def test_edge_weight_combines_all_three_terms():
    ts = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert edge_weight(2.0, ts, 0, now) == pytest.approx(2.0)
    assert edge_weight(2.0, ts, 1, now) == pytest.approx(2.0 * depth_decay(1))


def test_dust_threshold_is_chain_specific():
    assert is_dust(0.0001, "bitcoin") is True
    assert is_dust(0.01, "bitcoin") is False
    assert is_dust(0.001, "ethereum") is True


def test_single_hop_graph(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xb", 5.0)]),
    ]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("0xseed", "ethereum", max_hops=2, conn=conn,
                            mode="fixture", now=NOW)
    finally:
        conn.close()

    assert graph.address_count == 2
    assert graph.edge_count == 1
    assert graph.edges[0].from_address == "0xseed"
    assert graph.edges[0].to_address == "0xb"
    assert graph.edges[0].amount == pytest.approx(5.0)


def test_multi_hop_expansion_respects_hop_cap(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xb", 5.0)]),
        tx("0xt2", at(10), [io("0xb", 5.0)], [io("0xc", 5.0)]),
        tx("0xt3", at(20), [io("0xc", 5.0)], [io("0xd", 5.0)]),
        tx("0xt4", at(30), [io("0xd", 5.0)], [io("0xe", 5.0)]),
    ]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        shallow = build_graph("0xseed", "ethereum", max_hops=2, conn=conn,
                              mode="fixture", now=NOW)
        deep = build_graph("0xseed", "ethereum", max_hops=4, conn=conn,
                           mode="fixture", now=NOW)
    finally:
        conn.close()

    assert shallow.hops_traversed == 2
    assert shallow.address_count == 3          # seed, b, c
    assert deep.address_count == 5             # seed .. e


def test_dust_transfers_are_recorded_but_not_expanded(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)],
           [io("0xreal", 4.999), io("0xdust", 0.001)]),
        tx("0xt2", at(10), [io("0xdust", 0.001)], [io("0xdeeper", 0.001)]),
    ]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("0xseed", "ethereum", max_hops=3, conn=conn,
                            mode="fixture", now=NOW)
    finally:
        conn.close()

    addresses = set(graph.wallets)
    assert "0xreal" in addresses
    # The dust edge is kept as evidence, but the walk does not follow it.
    assert any(e.dust for e in graph.edges)
    assert "0xdeeper" not in addresses


def test_change_output_is_not_treated_as_an_onward_hop(temp_db):
    """A UTXO output returning to a sending address is change, not a new hop."""
    txs = [
        tx("t1", at(0), [io("addrA", 5.0)], [io("addrB", 3.0), io("addrA", 2.0)]),
    ]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("addrA", "bitcoin", max_hops=3, conn=conn,
                            mode="fixture", now=NOW)
    finally:
        conn.close()

    targets = {e.to_address for e in graph.edges}
    assert targets == {"addrB"}


def test_shared_input_amount_is_apportioned_not_double_counted(temp_db):
    """Two people co-spending should not each be credited the full output."""
    txs = [
        tx("t1", at(0), [io("addrA", 3.0), io("addrB", 1.0)], [io("addrZ", 4.0)]),
    ]
    seed_scenario(temp_db, "bitcoin", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("addrA", "bitcoin", max_hops=2, conn=conn,
                            mode="fixture", now=NOW)
    finally:
        conn.close()

    edge = next(e for e in graph.edges if e.from_address == "addrA")
    # addrA supplied 3 of 4 units of input, so it carries 3 of the 4 output.
    assert edge.amount == pytest.approx(3.0)


def test_per_hop_expansion_cap_sets_expansion_limited(temp_db, monkeypatch):
    import backend.graph.builder as builder

    monkeypatch.setattr(builder, "MAX_EXPANSIONS_PER_HOP", 3)

    outputs = [io(f"0xr{i}", 1.0) for i in range(8)]
    txs = [tx("0xt1", at(0), [io("0xseed", 8.0)], outputs)]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = builder.build_graph("0xseed", "ethereum", max_hops=2, conn=conn,
                                    mode="fixture", now=NOW)
    finally:
        conn.close()

    assert graph.expansion_limited is True
    assert "per-hop cap" in graph.expansion_note
    # Degrades to a partial graph, never a crash.
    assert graph.address_count == 4  # seed + 3 kept


def test_missing_address_yields_empty_leaf_not_an_error(temp_db):
    txs = [tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xunknown", 5.0)])]
    conn = connect(temp_db)
    try:
        from backend.tests.helpers import seed_address
        seed_address(conn, "0xseed", "ethereum", txs)
        conn.commit()
        graph = build_graph("0xseed", "ethereum", max_hops=3, conn=conn,
                            mode="fixture", now=NOW)
    finally:
        conn.close()

    assert "0xunknown" in graph.wallets
    assert graph.address_count == 2


def test_primary_trail_follows_the_largest_flow(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 10.0)], [io("0xbig", 9.0), io("0xsmall", 1.0)]),
        tx("0xt2", at(10), [io("0xbig", 9.0)], [io("0xexit", 9.0)]),
        tx("0xt3", at(10), [io("0xsmall", 1.0)], [io("0xother", 1.0)]),
    ]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        graph = build_graph("0xseed", "ethereum", max_hops=4, conn=conn,
                            mode="fixture", now=NOW)
    finally:
        conn.close()

    trail = primary_trail(graph, ["0xexit", "0xother"])
    assert trail == ["0xseed", "0xbig", "0xexit"]


def test_persist_graph_writes_wallets_and_transactions(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xb", 5.0)]),
        tx("0xt2", at(10), [io("0xb", 5.0)], [io("0xc", 5.0)]),
    ]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        make_case(conn, "case-1", "0xseed", "ethereum")
        graph = build_graph("0xseed", "ethereum", max_hops=3, conn=conn,
                            mode="fixture", now=NOW)
        persist_graph(conn, "case-1", graph)
        conn.commit()

        wallets = conn.execute(
            "SELECT COUNT(*) FROM wallets WHERE case_id='case-1'"
        ).fetchone()[0]
        edges = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE case_id='case-1'"
        ).fetchone()[0]
        assert wallets == 3
        assert edges == 2
    finally:
        conn.close()


def test_persist_graph_is_idempotent(temp_db):
    txs = [tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xb", 5.0)])]
    seed_scenario(temp_db, "ethereum", distribute(txs))

    conn = connect(temp_db)
    try:
        make_case(conn, "case-1", "0xseed", "ethereum")
        graph = build_graph("0xseed", "ethereum", max_hops=2, conn=conn,
                            mode="fixture", now=NOW)
        persist_graph(conn, "case-1", graph)
        persist_graph(conn, "case-1", graph)
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM wallets WHERE case_id='case-1'"
        ).fetchone()[0] == 2
    finally:
        conn.close()
