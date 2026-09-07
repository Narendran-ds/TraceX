"""S1 gate: cache-before-use, and the offline path.

ROADMAP P1 exit criterion: a second fetch is served from cache and the entire
flow works with the network off.
"""
from __future__ import annotations

import pytest

from backend.adapters import registry
from backend.adapters.base import AdapterUnavailable, AddressHistory, ChainAdapter
from backend.cache import store
from backend.models.db import connect

FIXTURE_PAYLOAD = {
    "address": "0xseed",
    "chain": "ethereum",
    "origin": "synthetic_scenario",
    "transactions": [
        {
            "tx_hash": "0xaaa",
            "timestamp": "2026-08-01T10:00:00+00:00",
            "inputs": [{"address": "0xseed", "amount": 4.0}],
            "outputs": [{"address": "0xb", "amount": 3.98}],
            "fee": 0.02,
        },
        {
            "tx_hash": "0xbbb",
            "timestamp": "2026-08-01T10:05:00+00:00",
            "inputs": [{"address": "0xc", "amount": 1.0}],
            "outputs": [{"address": "0xseed", "amount": 1.0}],
        },
    ],
}


def test_put_then_get_roundtrip(temp_db):
    conn = connect(temp_db)
    try:
        store.put("fixture", "ethereum", "address_history", "0xSEED",
                  FIXTURE_PAYLOAD, "synthetic_scenario", conn=conn)
        conn.commit()
        entry = store.get("fixture", "ethereum", "address_history", "0xseed", conn=conn)
        assert entry is not None
        assert entry.payload["transactions"][0]["tx_hash"] == "0xaaa"
        assert entry.origin == "synthetic_scenario"
    finally:
        conn.close()


def test_cache_key_is_address_case_insensitive(temp_db):
    conn = connect(temp_db)
    try:
        store.put("fixture", "ethereum", "address_history", "0xAbCd",
                  FIXTURE_PAYLOAD, "synthetic_scenario", conn=conn)
        conn.commit()
        assert store.get_any("ethereum", "0xABCD", conn=conn) is not None
        assert store.get_any("ethereum", "0xabcd", conn=conn) is not None
    finally:
        conn.close()


def test_put_rejects_undeclared_origin(temp_db):
    """A cached payload must declare whether it is real or constructed."""
    with pytest.raises(ValueError):
        store.put("fixture", "ethereum", "address_history", "0xa",
                  FIXTURE_PAYLOAD, "whatever")


def test_get_any_finds_entry_from_any_provider(temp_db):
    """Offline replay does not depend on knowing which provider filled the cache."""
    conn = connect(temp_db)
    try:
        store.put("blockscout", "ethereum", "address_history", "0xseed",
                  {"items": []}, "live_cached", conn=conn)
        conn.commit()
        entry = store.get_any("ethereum", "0xseed", conn=conn)
        assert entry is not None
        assert entry.provider == "blockscout"
    finally:
        conn.close()


def test_get_any_prefers_live_cached_over_synthetic(temp_db):
    conn = connect(temp_db)
    try:
        store.put("fixture", "ethereum", "address_history", "0xseed",
                  FIXTURE_PAYLOAD, "synthetic_scenario", conn=conn)
        store.put("blockscout", "ethereum", "address_history", "0xseed",
                  {"items": []}, "live_cached", conn=conn)
        conn.commit()
        entry = store.get_any("ethereum", "0xseed", conn=conn)
        assert entry.origin == "live_cached"
    finally:
        conn.close()


def test_resolve_history_serves_from_cache(temp_db):
    conn = connect(temp_db)
    try:
        store.put("fixture", "ethereum", "address_history", "0xseed",
                  FIXTURE_PAYLOAD, "synthetic_scenario", conn=conn)
        conn.commit()
        res = registry.resolve_history("ethereum", "0xseed", conn=conn, mode="fixture")
        assert res.served_from_cache is True
        assert res.history.tx_count == 2
        assert res.history.total_in == pytest.approx(1.0)
        assert res.history.total_out == pytest.approx(4.0)
        assert res.history.origin == "synthetic_scenario"
    finally:
        conn.close()


def test_fixture_mode_never_calls_upstream(temp_db, monkeypatch):
    """The demo path must not touch the network, even on a cache miss."""
    called = {"n": 0}

    def explode(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("fixture mode attempted an upstream fetch")

    monkeypatch.setattr("httpx.get", explode)

    conn = connect(temp_db)
    try:
        res = registry.resolve_history("ethereum", "0xmissing", conn=conn, mode="fixture")
    finally:
        conn.close()

    assert called["n"] == 0
    assert res.history.tx_count == 0
    # Degrades with a readable note rather than an exception (CLAUDE.md rule 6).
    assert "offline demo mode" in res.history.note


def test_live_mode_caches_before_returning(temp_db, monkeypatch):
    """CLAUDE.md rule 4: the response is in api_cache before the caller sees it."""
    raw = {"items": [
        {
            "hash": "0xfeed",
            "from": {"hash": "0xseed"},
            "to": {"hash": "0xdest"},
            "value": "1000000000000000000",
            "timestamp": "2026-08-01T10:00:00.000000Z",
            "fee": {"value": "0"},
        }
    ]}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return raw

    monkeypatch.setattr("httpx.get", lambda *a, **k: FakeResponse())

    conn = connect(temp_db)
    try:
        res = registry.resolve_history("ethereum", "0xseed", conn=conn, mode="live")
        conn.commit()
        assert res.served_from_cache is False
        assert res.provider == "blockscout"
        assert res.history.tx_count == 1
        assert res.history.transactions[0].outputs[0].amount == pytest.approx(1.0)
        assert res.history.origin == "live_cached"

        cached = store.get_any("ethereum", "0xseed", conn=conn)
        assert cached is not None, "response was used without being cached"
        assert cached.origin == "live_cached"
    finally:
        conn.close()


def test_second_resolve_is_served_from_cache(temp_db, monkeypatch):
    """P1 exit criterion: the second fetch does not hit the network."""
    calls = {"n": 0}
    raw = {"items": []}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return raw

    def counting_get(*_a, **_k):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr("httpx.get", counting_get)

    conn = connect(temp_db)
    try:
        registry.resolve_history("ethereum", "0xseed", conn=conn, mode="live")
        conn.commit()
        assert calls["n"] == 1
        second = registry.resolve_history("ethereum", "0xseed", conn=conn, mode="live")
        assert calls["n"] == 1, "second resolve hit the network instead of the cache"
        assert second.served_from_cache is True
    finally:
        conn.close()


def test_upstream_failure_degrades_without_raising(temp_db, monkeypatch):
    """A dead upstream yields an explained empty history, never a raw error."""
    import httpx as _httpx

    def boom(*_a, **_k):
        raise _httpx.ConnectError("no route to host")

    monkeypatch.setattr("httpx.get", boom)

    conn = connect(temp_db)
    try:
        res = registry.resolve_history("ethereum", "0xseed", conn=conn, mode="live")
    finally:
        conn.close()

    assert res.history.tx_count == 0
    assert "No cached data and no upstream" in res.history.note
    assert res.limited is True


def test_stats_reports_origin_split(temp_db):
    conn = connect(temp_db)
    try:
        store.put("fixture", "ethereum", "address_history", "0xa",
                  FIXTURE_PAYLOAD, "synthetic_scenario", conn=conn)
        store.put("blockscout", "ethereum", "address_history", "0xb",
                  {"items": []}, "live_cached", conn=conn)
        conn.commit()
        s = store.stats(conn=conn)
        assert s["total"] == 2
        assert s["by_origin"]["synthetic_scenario"] == 1
        assert s["by_origin"]["live_cached"] == 1
    finally:
        conn.close()
