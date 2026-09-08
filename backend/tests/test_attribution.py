"""S4 gate: attribution, provenance, and honest dead ends."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.attribution.ingest import (
    MissingProvenance,
    coverage,
    ingest_all,
    parse_ofac_sdn_csv,
    parse_tagpack,
    upsert_tags,
)
from backend.attribution.matcher import (
    apply_attribution_to_clusters,
    degraded_note,
    load_attribution,
    rank_exit_candidates,
    trail_degraded,
    unattributed_terminal_count,
)
from backend.clustering.engine import cluster_graph
from backend.config import CURATED_DIR, OFAC_DIR, TAGPACK_DIR
from backend.graph.builder import build_graph
from backend.models.db import connect
from backend.tests.helpers import at, distribute, io, seed_scenario, tx

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)

TORNADO = "0x8589427373D6D84E98730D7795D8f6f8731FDA16"
BINANCE = "0x28C6c06298d514Db089934071355E5743bf21d60"
WORMHOLE = "0x3ee18B2214AFF97000D974cf647E7C347E8fa585"


# --- ingestion -------------------------------------------------------------

def test_shipped_tagpacks_parse(temp_db):
    paths = sorted(TAGPACK_DIR.glob("*.yaml"))
    assert paths, "no tagpacks shipped in data/tagpacks"
    for path in paths:
        tags = parse_tagpack(path)
        assert tags, f"{path.name} produced no tags"
        for tag in tags:
            assert tag.source, f"{tag.address} has no source"
            assert tag.source_url.startswith("http"), f"{tag.address} has no source URL"


def test_shipped_ofac_file_parses_real_sdn_addresses(temp_db):
    paths = sorted(OFAC_DIR.glob("*.csv"))
    assert paths, "no OFAC data shipped in data/ofac"
    tags = []
    for path in paths:
        tags.extend(parse_ofac_sdn_csv(path))
    assert tags
    addresses = {t.address.lower() for t in tags}
    assert TORNADO.lower() in addresses
    for tag in tags:
        assert tag.entity_type == "sanctioned"
        assert tag.source == "ofac_sdn"
        assert tag.source_url.startswith("https://")


def test_ofac_parser_extracts_the_documented_remarks_format(tmp_path):
    path = tmp_path / "sdn.csv"
    path.write_text(
        '1,"EXAMPLE ENTITY","Digital Currency Address - ETH '
        '0xAAAA1111bbbb2222cccc3333dddd4444eeee5555; Digital Currency Address - '
        'XBT 1ExampleBitcoinAddressAAAA."\n',
        encoding="utf-8",
    )
    tags = parse_ofac_sdn_csv(path)
    assert {t.chain for t in tags} == {"ethereum", "bitcoin"}
    assert all(t.confidence == 1.0 for t in tags)


def test_tagpack_without_source_is_refused(tmp_path):
    """CLAUDE.md rule 8: no provenance, no storage."""
    path = tmp_path / "bad.yaml"
    path.write_text(
        "title: Bad pack\ncurrency: ETH\ntags:\n  - address: '0xabc'\n    label: Somebody\n",
        encoding="utf-8",
    )
    with pytest.raises(MissingProvenance):
        parse_tagpack(path)


def test_curated_file_documents_what_it_could_not_verify():
    """The curation gap is visible rather than filled with a guess."""
    import yaml

    path = CURATED_DIR / "india_relevant_exchanges.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    pending = document.get("pending_verification", [])
    assert pending, "curated file should name entities it could not verify"
    for entry in pending:
        assert "reason" in entry
    # Nothing in pending_verification leaks into the loaded tag list.
    loaded = {t.address.lower() for t in parse_tagpack(path)}
    assert all("entity" not in str(a) for a in loaded)


def test_ingest_all_loads_every_source(temp_db):
    conn = connect(temp_db)
    try:
        counts = ingest_all(conn)
        conn.commit()
        assert counts["tagpacks"] > 0
        assert counts["ofac"] > 0
        assert counts["curated"] > 0

        total = conn.execute("SELECT COUNT(*) FROM attribution_tags").fetchone()[0]
        assert total > 0
        # Both columns are NOT NULL in the schema; assert no blanks slipped in.
        blanks = conn.execute(
            "SELECT COUNT(*) FROM attribution_tags WHERE source='' OR source_url=''"
        ).fetchone()[0]
        assert blanks == 0
    finally:
        conn.close()


def test_ingest_is_idempotent(temp_db):
    conn = connect(temp_db)
    try:
        ingest_all(conn)
        first = conn.execute("SELECT COUNT(*) FROM attribution_tags").fetchone()[0]
        ingest_all(conn)
        conn.commit()
        second = conn.execute("SELECT COUNT(*) FROM attribution_tags").fetchone()[0]
        assert first == second
    finally:
        conn.close()


def test_coverage_is_counted_not_asserted(temp_db):
    conn = connect(temp_db)
    try:
        ingest_all(conn)
        conn.commit()
        stats = coverage(conn)
        assert stats["tag_count"] > 0
        assert stats["entity_count"] > 0
        assert stats["source_count"] >= 2
        # Never implies exhaustive coverage.
        assert "partial" in stats["statement"]
        assert str(stats["tag_count"]) in stats["statement"]
    finally:
        conn.close()


# --- matching --------------------------------------------------------------

def _case(db_path, txs, seed="0xseed", max_hops=5):
    seed_scenario(db_path, "ethereum", distribute(txs))
    conn = connect(db_path)
    try:
        ingest_all(conn)
        conn.commit()
        graph = build_graph(seed, "ethereum", max_hops=max_hops, conn=conn,
                            mode="fixture", now=NOW)
        clustering = cluster_graph(graph)
        attribution = load_attribution(
            conn, "ethereum", [w.address for w in graph.wallets.values()]
        )
    finally:
        conn.close()
    apply_attribution_to_clusters(clustering, attribution)
    return graph, clustering, attribution


def test_exchange_exit_is_ranked_with_a_citable_source(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xmid", 5.0)]),
        tx("0xt2", at(30), [io("0xmid", 5.0)], [io(BINANCE, 5.0)]),
    ]
    graph, clustering, attribution = _case(temp_db, txs)
    candidates = rank_exit_candidates(graph, clustering, attribution)

    assert candidates
    top = candidates[0]
    assert top.attributed is True
    assert top.entity_name and "Binance" in top.entity_name
    assert top.sources
    assert top.sources[0].source_url.startswith("https://")
    assert 0.0 < top.confidence <= 1.0
    assert top.amount_received == pytest.approx(5.0)


def test_confidence_basis_explains_every_component(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xmid", 5.0)]),
        tx("0xt2", at(30), [io("0xmid", 5.0)], [io(BINANCE, 5.0)]),
    ]
    graph, clustering, attribution = _case(temp_db, txs)
    top = rank_exit_candidates(graph, clustering, attribution)[0]

    joined = " ".join(top.confidence_basis).lower()
    assert "attribution tag confidence" in joined
    assert "cluster confidence" in joined
    assert "hop-depth decay" in joined


def test_unattributed_terminal_cluster_is_named_as_such(temp_db):
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xmid", 5.0)]),
        tx("0xt2", at(30), [io("0xmid", 5.0)], [io("0xnobodyknows", 5.0)]),
    ]
    graph, clustering, attribution = _case(temp_db, txs)
    candidates = rank_exit_candidates(graph, clustering, attribution)

    assert candidates
    top = candidates[0]
    assert top.attributed is False
    assert top.entity_name is None, "an unattributed cluster was given a name"
    assert any("unattributed exit cluster" in b for b in top.confidence_basis)
    assert unattributed_terminal_count(clustering) >= 1


def test_bridge_hop_is_reported_as_a_confidence_boundary(temp_db):
    """CASE C — the deliberate failure demo."""
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xmid", 5.0)]),
        tx("0xt2", at(30), [io("0xmid", 5.0)], [io(WORMHOLE, 5.0)]),
    ]
    graph, clustering, attribution = _case(temp_db, txs)
    candidates = rank_exit_candidates(graph, clustering, attribution)

    boundary = [c for c in candidates if c.confidence_boundary]
    assert boundary, "bridge hop was not flagged as a confidence boundary"
    assert "cannot be followed deterministically" in boundary[0].boundary_note
    assert trail_degraded(candidates) is True

    note = degraded_note(candidates)
    assert "degrades" in note
    # It must not present an inferred destination as a traced one.
    assert "inferred destination as a traced one" in note


def test_sanctioned_match_outranks_an_exchange_label_on_the_same_cluster(temp_db):
    txs = [tx("0xt1", at(0), [io("0xseed", 5.0)], [io(TORNADO, 5.0)])]
    graph, clustering, attribution = _case(temp_db, txs)

    tornado_cluster = next(
        c for c in clustering.clusters if TORNADO.lower() in c.members
    )
    assert tornado_cluster.attribution_source == "ofac_sdn"
    assert tornado_cluster.role == "mixer"


def test_deeper_hops_carry_lower_confidence(temp_db):
    near = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io(BINANCE, 5.0)]),
    ]
    far = [
        tx("0xf1", at(0), [io("0xseed2", 5.0)], [io("0xa", 5.0)]),
        tx("0xf2", at(10), [io("0xa", 5.0)], [io("0xb", 5.0)]),
        tx("0xf3", at(20), [io("0xb", 5.0)], [io("0xc", 5.0)]),
        tx("0xf4", at(30), [io("0xc", 5.0)], [io(BINANCE, 5.0)]),
    ]
    g1, c1, a1 = _case(temp_db, near)
    near_conf = rank_exit_candidates(g1, c1, a1)[0].confidence

    g2, c2, a2 = _case(temp_db, far, seed="0xseed2")
    far_top = next(
        c for c in rank_exit_candidates(g2, c2, a2) if c.entity_name
    )
    assert far_top.confidence < near_conf


def test_no_attribution_data_yields_no_names_not_placeholders(temp_db):
    """With an empty tag table, every exit is honestly unattributed."""
    txs = [
        tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xmid", 5.0)]),
        tx("0xt2", at(30), [io("0xmid", 5.0)], [io("0xend", 5.0)]),
    ]
    seed_scenario(temp_db, "ethereum", distribute(txs))
    conn = connect(temp_db)
    try:
        graph = build_graph("0xseed", "ethereum", max_hops=4, conn=conn,
                            mode="fixture", now=NOW)
        clustering = cluster_graph(graph)
        attribution = load_attribution(
            conn, "ethereum", [w.address for w in graph.wallets.values()]
        )
    finally:
        conn.close()

    assert attribution == {}
    candidates = rank_exit_candidates(graph, clustering, attribution)
    assert all(c.entity_name is None for c in candidates)
    assert all(c.attributed is False for c in candidates)


def test_a_sanctioned_mixer_is_also_a_confidence_boundary(temp_db):
    """Regression: found by the evaluation harness.

    OFAC tags Tornado Cash as `sanctioned`, which is the honest type for a
    sanctions source — but nothing marked it as a mixing service, so the trail
    did not degrade there. Sanctioned and mixer are two independent public
    facts: a sanctioned personal wallet is still traceable onward, while a
    mixing service is a boundary whether or not anyone has sanctioned it.
    """
    txs = [tx("0xt1", at(0), [io("0xseed", 5.0)], [io(TORNADO, 5.0)])]
    graph, clustering, attribution = _case(temp_db, txs)
    candidates = rank_exit_candidates(graph, clustering, attribution)

    tornado = next(
        c for c in candidates
        if TORNADO.lower() in {m.lower() for m in c.cluster.members}
    )
    assert tornado.confidence_boundary is True, (
        "a sanctioned mixing service was not treated as a confidence boundary"
    )
    assert trail_degraded(candidates) is True

    # Both facts are present, each with its own source.
    sources = {s.source for s in tornado.sources}
    assert "ofac_sdn" in sources
    assert "graphsense_tagpack" in sources


def test_entity_name_variants_collapse_to_one_name():
    """Regression: 'Binance' and 'Binance (India-serving venue)' are one venue.

    Two sources tagging the same address with different name strings read as
    two separate entities, which makes the system look like it cannot count.
    """
    from backend.patterns.base import canonical_entity_names

    assert canonical_entity_names(
        ["Binance", "Binance (India-serving venue)"]
    ) == ["Binance"]
    assert canonical_entity_names(["Binance", "Kraken"]) == ["Binance", "Kraken"]
    assert canonical_entity_names([]) == []
