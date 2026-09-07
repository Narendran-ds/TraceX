"""S3 gate: the pattern library.

Each test asserts a detector against the definition it implements. Thresholds
were written from those definitions in master report §13.1 and are NOT adjusted
to make a scenario light up — several tests below pin that by checking a
just-under-threshold scenario stays silent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.clustering.engine import cluster_graph
from backend.config import (
    FAN_IN_MIN_SENDERS,
    FAN_OUT_MIN_RECIPIENTS,
    PEEL_CHAIN_MIN_HOPS,
    ROUND_SPLIT_MIN_PARTS,
    TIMING_BURST_MIN_TXS,
)
from backend.graph.builder import build_graph
from backend.models.db import connect
from backend.patterns import (
    cluster_context,
    fan_in,
    fan_out,
    peel_chain,
    round_split,
    sanctioned_match,
    timing_burst,
)
from backend.patterns.base import AttributionMatch, DetectionContext
from backend.tests.helpers import at, distribute, io, seed_scenario, tx

NOW = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)


def build_context(db_path, seed, chain, txs, attribution=None, now=NOW, max_hops=5):
    seed_scenario(db_path, chain, distribute(txs))
    conn = connect(db_path)
    try:
        graph = build_graph(seed, chain, max_hops=max_hops, conn=conn,
                            mode="fixture", now=now)
        clustering = cluster_graph(graph)
    finally:
        conn.close()
    return DetectionContext(
        graph=graph,
        clustering=clustering,
        chain=chain,
        now=now,
        attribution=attribution or {},
    )


def tag(address, name, entity_type, source="manual_curated"):
    return AttributionMatch(
        address=address,
        entity_name=name,
        entity_type=entity_type,
        source=source,
        source_url=f"https://example.org/{source}/{address}",
        confidence=0.9,
        last_updated="2026-01-01T00:00:00+00:00",
    )


# ===========================================================================
# Pattern 1 — Rapid fan-out
# ===========================================================================

def test_fan_out_fires_at_the_documented_threshold(temp_db):
    outs = [io(f"0xr{i}", 1.0) for i in range(FAN_OUT_MIN_RECIPIENTS)]
    txs = [tx("0xfan", at(0), [io("0xseed", 5.0)], outs)]
    context = build_context(temp_db, "0xseed", "ethereum", txs)

    found = fan_out.detect(context)
    assert len(found) == 1
    detail = found[0].evidence_detail
    assert detail["recipient_count"] == FAN_OUT_MIN_RECIPIENTS
    assert detail["threshold_recipients"] == FAN_OUT_MIN_RECIPIENTS
    assert found[0].evidence_tx_hashes == ["0xfan"]


def test_fan_out_stays_silent_one_below_threshold(temp_db):
    outs = [io(f"0xr{i}", 1.0) for i in range(FAN_OUT_MIN_RECIPIENTS - 1)]
    txs = [tx("0xfan", at(0), [io("0xseed", 4.0)], outs)]
    context = build_context(temp_db, "0xseed", "ethereum", txs)
    assert fan_out.detect(context) == []


def test_fan_out_ignores_transfers_outside_the_window(temp_db):
    """Six recipients, but spread across days — that is not a rapid fan-out."""
    txs = [
        tx(f"0xt{i}", at(days=i), [io("0xseed", 1.0)], [io(f"0xr{i}", 1.0)])
        for i in range(6)
    ]
    context = build_context(temp_db, "0xseed", "ethereum", txs)
    assert fan_out.detect(context) == []


def test_fan_out_requires_previously_unseen_recipients(temp_db):
    """Recipients the address already dealt with are not fresh layering hops."""
    prior = [
        tx(f"0xp{i}", at(days=-10), [io(f"0xr{i}", 0.5)], [io("0xother", 0.5)])
        for i in range(FAN_OUT_MIN_RECIPIENTS)
    ]
    fan = [
        tx("0xfan", at(0), [io("0xseed", 5.0)],
           [io(f"0xr{i}", 1.0) for i in range(FAN_OUT_MIN_RECIPIENTS)])
    ]
    context = build_context(temp_db, "0xseed", "ethereum", prior + fan)
    assert fan_out.detect(context) == []


def test_fan_out_evidence_links_real_tx_hashes(temp_db):
    outs = [io(f"0xr{i}", 1.0) for i in range(6)]
    txs = [tx("0xfanhash", at(0), [io("0xseed", 6.0)], outs)]
    context = build_context(temp_db, "0xseed", "ethereum", txs)
    detection = fan_out.detect(context)[0]
    assert all(h in context.graph.transactions for h in detection.evidence_tx_hashes)


# ===========================================================================
# Pattern 2 — Fan-in, and its disambiguation
# ===========================================================================

def _consolidation(destination="0xcollector", senders=FAN_IN_MIN_SENDERS):
    """Fan-out from a seed, then convergence onto one destination."""
    spread = [
        tx("0xspread", at(days=-1), [io("0xseed", float(senders))],
           [io(f"0xs{i}", 1.0) for i in range(senders)])
    ]
    converge = [
        tx(f"0xin{i}", at(minutes=i), [io(f"0xs{i}", 1.0)], [io(destination, 1.0)])
        for i in range(senders)
    ]
    return spread + converge


def test_fan_in_fires_on_an_unattributed_collector(temp_db):
    context = build_context(temp_db, "0xseed", "ethereum", _consolidation())
    found = fan_in.detect(context)
    assert len(found) == 1
    detail = found[0].evidence_detail
    assert detail["sender_count"] == FAN_IN_MIN_SENDERS
    assert detail["disambiguation"]["attributed_as_service"] is False


def test_fan_in_stays_silent_one_below_threshold(temp_db):
    context = build_context(
        temp_db, "0xseed", "ethereum",
        _consolidation(senders=FAN_IN_MIN_SENDERS - 1),
    )
    assert fan_in.detect(context) == []


def test_fan_in_is_suppressed_for_an_attributed_exchange(temp_db):
    """CASE D — the negative case.

    An identical converging shape, but the destination is a known exchange
    deposit address. CLAUDE.md is explicit that fan-in must not ship without
    this disambiguation, and this is the test that proves it works.
    """
    attribution = {
        "0xexchange": [tag("0xexchange", "ExampleExchange", "exchange", "graphsense_tagpack")]
    }
    context = build_context(
        temp_db, "0xseed", "ethereum",
        _consolidation(destination="0xexchange"),
        attribution=attribution,
    )

    assert fan_in.detect(context) == [], "legitimate exchange consolidation was flagged"

    # And the suppression is visible, not silent.
    suppressed = fan_in.explain_suppressions(context)
    assert len(suppressed) == 1
    assert "ExampleExchange" in suppressed[0]["reason"]
    assert suppressed[0]["features"]["attributed_as_service"] is True


def test_fan_in_is_suppressed_for_a_high_volume_long_lived_destination(temp_db):
    """A busy, long-established address behaves like a service even untagged."""
    from backend.config import CLUSTER_NEW_MAX_AGE_DAYS, HIGH_VOLUME_TX_COUNT

    destination = "0xbusy"
    # A long history of prior activity: high volume, spanning well beyond the
    # "new cluster" window.
    history = [
        tx(f"0xh{i}", at(days=-(CLUSTER_NEW_MAX_AGE_DAYS + 60) + i * 0.4),
           [io(f"0xpast{i}", 0.5)], [io(destination, 0.5)])
        for i in range(HIGH_VOLUME_TX_COUNT + 5)
    ]
    context = build_context(
        temp_db, "0xseed", "ethereum",
        _consolidation(destination=destination) + history,
        max_hops=3,
    )

    features = fan_in._features(context, destination)
    assert features.high_volume is True
    assert features.cluster_is_new is False
    assert features.looks_like_a_service is True
    assert fan_in.detect(context) == []


def test_fan_in_disambiguation_features_are_always_computed(temp_db):
    context = build_context(temp_db, "0xseed", "ethereum", _consolidation())
    detection = fan_in.detect(context)[0]
    features = detection.evidence_detail["disambiguation"]
    for key in (
        "attributed_as_service",
        "destination_tx_count",
        "high_volume",
        "cluster_age_days",
        "cluster_is_new",
        "dormant_before_days",
    ):
        assert key in features, f"disambiguation feature {key} missing"


# ===========================================================================
# Pattern 3 — Peel chain
# ===========================================================================

def _peel(hops=PEEL_CHAIN_MIN_HOPS, remainder_ratio=0.9):
    """Each hop forwards a large remainder and peels a small slice off."""
    txs = []
    amount = 100.0
    current = "0xseed"
    for i in range(hops):
        remainder = amount * remainder_ratio
        peel = amount - remainder
        nxt = f"0xhop{i + 1}"
        txs.append(
            tx(f"0xpeel{i}", at(hours=i), [io(current, amount)],
               [io(nxt, remainder), io(f"0xside{i}", peel)])
        )
        current, amount = nxt, remainder
    return txs


def test_peel_chain_fires_at_the_documented_length(temp_db):
    context = build_context(temp_db, "0xseed", "ethereum", _peel(), max_hops=8)
    found = peel_chain.detect(context)
    assert len(found) == 1
    assert found[0].evidence_detail["chain_length"] >= PEEL_CHAIN_MIN_HOPS
    assert len(found[0].evidence_tx_hashes) >= PEEL_CHAIN_MIN_HOPS


def test_peel_chain_stays_silent_one_hop_short(temp_db):
    context = build_context(
        temp_db, "0xseed", "ethereum", _peel(hops=PEEL_CHAIN_MIN_HOPS - 1), max_hops=8
    )
    assert peel_chain.detect(context) == []


def test_even_splits_are_not_a_peel_chain(temp_db):
    """A 50/50 split has no dominant remainder, so it is not peeling."""
    context = build_context(
        temp_db, "0xseed", "ethereum", _peel(remainder_ratio=0.5), max_hops=8
    )
    assert peel_chain.detect(context) == []


def test_peel_chain_records_the_path_and_the_peeled_amounts(temp_db):
    context = build_context(temp_db, "0xseed", "ethereum", _peel(hops=4), max_hops=8)
    detail = peel_chain.detect(context)[0].evidence_detail
    assert detail["path"][0] == "0xseed"
    assert len(detail["steps"]) == 4
    assert detail["total_peeled"] > 0
    assert detail["final_remainder"] > detail["total_peeled"]


# ===========================================================================
# Pattern 4 — Round-number splitting
# ===========================================================================

def test_round_split_fires_on_near_equal_parts(temp_db):
    outs = [io(f"0xp{i}", 2.0) for i in range(ROUND_SPLIT_MIN_PARTS)]
    txs = [tx("0xsplit", at(0), [io("0xseed", 6.0)], outs)]
    context = build_context(temp_db, "0xseed", "ethereum", txs)

    found = round_split.detect(context)
    assert len(found) == 1
    assert found[0].evidence_detail["near_equal"] is True
    assert found[0].evidence_detail["part_count"] == ROUND_SPLIT_MIN_PARTS


def test_round_split_stays_silent_on_irregular_amounts(temp_db):
    outs = [io("0xp1", 1.37281), io("0xp2", 3.91043), io("0xp3", 0.66192)]
    txs = [tx("0xsplit", at(0), [io("0xseed", 5.94516)], outs)]
    context = build_context(temp_db, "0xseed", "ethereum", txs)
    assert round_split.detect(context) == []


def test_round_split_stays_silent_below_minimum_parts(temp_db):
    outs = [io(f"0xp{i}", 2.0) for i in range(ROUND_SPLIT_MIN_PARTS - 1)]
    txs = [tx("0xsplit", at(0), [io("0xseed", 4.0)], outs)]
    context = build_context(temp_db, "0xseed", "ethereum", txs)
    assert round_split.detect(context) == []


def test_is_round_amount_recognises_tidy_figures():
    assert round_split.is_round_amount(2.0) is True
    assert round_split.is_round_amount(0.5) is True
    assert round_split.is_round_amount(0.25) is True
    assert round_split.is_round_amount(1.37281) is False


# ===========================================================================
# Pattern 5 — Timing burst
# ===========================================================================

def test_timing_burst_fires_on_tight_clustered_transfers(temp_db):
    txs = [tx("0xfund", at(days=-1), [io("0xseed", 10.0)], [io("0xburst", 10.0)])]
    txs += [
        tx(f"0xb{i}", at(minutes=i), [io("0xburst", 1.0)], [io(f"0xd{i}", 1.0)])
        for i in range(TIMING_BURST_MIN_TXS)
    ]
    context = build_context(temp_db, "0xseed", "ethereum", txs)

    found = timing_burst.detect(context)
    assert found, "expected a timing burst"
    detail = found[0].evidence_detail
    assert detail["transaction_count"] >= TIMING_BURST_MIN_TXS
    assert detail["window_seconds"] <= detail["threshold_window_seconds"]


def test_timing_burst_stays_silent_when_transfers_are_spread_out(temp_db):
    txs = [tx("0xfund", at(days=-1), [io("0xseed", 10.0)], [io("0xslow", 10.0)])]
    txs += [
        tx(f"0xb{i}", at(hours=i * 6), [io("0xslow", 1.0)], [io(f"0xd{i}", 1.0)])
        for i in range(TIMING_BURST_MIN_TXS)
    ]
    context = build_context(temp_db, "0xseed", "ethereum", txs)
    assert timing_burst.detect(context) == []


# ===========================================================================
# Signal — sanctioned / mixer match
# ===========================================================================

def test_sanctioned_match_fires_with_full_provenance(temp_db):
    txs = [tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xmixer", 5.0)])]
    attribution = {
        "0xmixer": [tag("0xmixer", "ExampleMixer", "mixer", "ofac_sdn")]
    }
    context = build_context(temp_db, "0xseed", "ethereum", txs, attribution=attribution)

    found = sanctioned_match.detect(context)
    assert len(found) == 1
    match = found[0].evidence_detail["matches"][0]
    assert match["source"] == "ofac_sdn"
    assert match["source_url"].startswith("https://")


def test_sanctioned_match_ignores_ordinary_exchange_tags(temp_db):
    txs = [tx("0xt1", at(0), [io("0xseed", 5.0)], [io("0xexch", 5.0)])]
    attribution = {"0xexch": [tag("0xexch", "ExampleExchange", "exchange")]}
    context = build_context(temp_db, "0xseed", "ethereum", txs, attribution=attribution)
    assert sanctioned_match.detect(context) == []


# ===========================================================================
# Signal — cluster new + burst + dormant
# ===========================================================================

def test_cluster_context_fires_on_a_short_lived_bursting_cluster(temp_db):
    """Created recently, moved fast, then went quiet."""
    txs = [tx("0xfund", at(days=-1), [io("0xseed", 10.0)], [io("0xtemp", 10.0)])]
    txs += [
        tx(f"0xb{i}", at(minutes=i), [io("0xtemp", 1.0)], [io(f"0xd{i}", 1.0)])
        for i in range(TIMING_BURST_MIN_TXS)
    ]
    # NOW is two months after the scenario, so the cluster is long dormant.
    context = build_context(temp_db, "0xseed", "ethereum", txs, now=NOW)

    found = cluster_context.detect(context)
    assert found, "expected the new+burst+dormant signal"
    detail = found[0].evidence_detail
    assert detail["active_days"] <= detail["new_threshold_days"]
    assert detail["dormant_days"] >= detail["dormant_threshold_days"]


def test_cluster_context_stays_silent_when_activity_is_recent(temp_db):
    """Still active means not dormant, so the life-cycle signature is absent."""
    txs = [tx("0xfund", at(days=-1), [io("0xseed", 10.0)], [io("0xtemp", 10.0)])]
    txs += [
        tx(f"0xb{i}", at(minutes=i), [io("0xtemp", 1.0)], [io(f"0xd{i}", 1.0)])
        for i in range(TIMING_BURST_MIN_TXS)
    ]
    recent_now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    context = build_context(temp_db, "0xseed", "ethereum", txs, now=recent_now)
    assert cluster_context.detect(context) == []


def test_cluster_context_ignores_attributed_services(temp_db):
    txs = [tx("0xfund", at(days=-1), [io("0xseed", 10.0)], [io("0xtemp", 10.0)])]
    txs += [
        tx(f"0xb{i}", at(minutes=i), [io("0xtemp", 1.0)], [io(f"0xd{i}", 1.0)])
        for i in range(TIMING_BURST_MIN_TXS)
    ]
    attribution = {"0xtemp": [tag("0xtemp", "ExampleExchange", "exchange")]}
    context = build_context(temp_db, "0xseed", "ethereum", txs,
                            attribution=attribution, now=NOW)
    assert cluster_context.detect(context) == []


# ===========================================================================
# Contract-level invariants across the whole library
# ===========================================================================

def test_every_detection_carries_backing_transaction_hashes(temp_db):
    from backend.patterns.engine import run_detectors

    txs = _peel(hops=4) + [
        tx("0xfan", at(hours=20), [io("0xhop4", 40.0)],
           [io(f"0xr{i}", 8.0) for i in range(5)])
    ]
    context = build_context(temp_db, "0xseed", "ethereum", txs, max_hops=8)

    detections = run_detectors(context)
    assert detections
    for detection in detections:
        assert detection.evidence_tx_hashes, f"{detection.pattern_type} has no evidence"
        for tx_hash in detection.evidence_tx_hashes:
            assert tx_hash in context.graph.transactions


def test_no_detection_uses_accusatory_language(temp_db):
    """CLAUDE.md rule 2: confidence, never certainty."""
    from backend.patterns.engine import run_detectors

    context = build_context(temp_db, "0xseed", "ethereum", _peel(hops=4), max_hops=8)
    banned = ["criminal", "scammer", "fraudster", "guilty", "proves", "definitely"]
    for detection in run_detectors(context):
        text = (detection.title + " " + detection.description).lower()
        for word in banned:
            assert word not in text, f"accusatory word '{word}' in {detection.pattern_type}"


def test_a_single_fan_out_transaction_is_not_a_timing_burst(temp_db):
    """One transaction with six outputs is six edges but ONE transaction.

    Counting edges here would report a burst on top of the fan-out that already
    scored, inflating the case for a single event.
    """
    outs = [io(f"0xr{i}", 1.0) for i in range(8)]
    txs = [tx("0xonefan", at(0), [io("0xseed", 8.0)], outs)]
    context = build_context(temp_db, "0xseed", "ethereum", txs)

    assert fan_out.detect(context), "fan-out should still fire"
    assert timing_burst.detect(context) == [], (
        "a single transaction was counted as a burst of transactions"
    )
