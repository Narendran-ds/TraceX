"""Fixture adapter — the default demo path.

Reads scenario data that was seeded into api_cache. Never touches the network,
so the whole pipeline runs with WiFi off (ROADMAP P1 exit criterion).

The payload format is the normalized model serialized directly, which keeps the
scenario files readable and reviewable by a human:

    {
      "address": "...",
      "chain": "ethereum",
      "origin": "synthetic_scenario",
      "transactions": [
        {"tx_hash": "0x...", "timestamp": "2026-08-01T10:00:00+00:00",
         "inputs":  [{"address": "0xA", "amount": 4.0}],
         "outputs": [{"address": "0xB", "amount": 3.98}],
         "fee": 0.02}
      ]
    }
"""
from __future__ import annotations

from typing import Any

from backend.adapters.base import (
    AdapterUnavailable,
    AddressHistory,
    ChainAdapter,
    NormalizedTx,
    TxEndpoint,
    parse_timestamp,
)


class FixtureAdapter(ChainAdapter):
    name = "fixture"
    endpoint = "address_history"

    def __init__(self, chain: str):
        self.chain = chain

    def fetch_raw(self, address: str) -> Any:
        """There is no upstream. A cache miss here is a genuine 'no data' answer."""
        raise AdapterUnavailable(
            self.name,
            "the fixture adapter only serves seeded cache entries and has no upstream",
        )

    def normalize(self, payload: Any, address: str) -> AddressHistory:
        if not isinstance(payload, dict):
            raise ValueError("fixture payload must be an object")

        txs = []
        for raw in payload.get("transactions", []):
            txs.append(
                NormalizedTx(
                    tx_hash=raw["tx_hash"],
                    chain=payload.get("chain", self.chain),
                    timestamp=parse_timestamp(raw["timestamp"]),
                    inputs=[
                        TxEndpoint(address=i["address"], amount=float(i["amount"]))
                        for i in raw.get("inputs", [])
                    ],
                    outputs=[
                        TxEndpoint(address=o["address"], amount=float(o["amount"]))
                        for o in raw.get("outputs", [])
                    ],
                    fee=float(raw.get("fee", 0.0)),
                    is_internal=bool(raw.get("is_internal", False)),
                )
            )
        txs.sort(key=lambda t: t.timestamp)

        return AddressHistory(
            address=payload.get("address", address),
            chain=payload.get("chain", self.chain),
            transactions=txs,
            provider=self.name,
            origin=payload.get("origin", "synthetic_scenario"),
            truncated=bool(payload.get("truncated", False)),
            note=payload.get("note", ""),
        )

    def status_note(self) -> str:
        return "Serves seeded cache entries. No network, no API key."
