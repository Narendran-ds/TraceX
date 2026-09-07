"""Bitcoin adapter — Blockchair.

Status, stated plainly: a keyless probe of the Blockchair free tier from this
machine returned HTTP 430, "Your IP address is temporary blacklisted due to
exceeding usage of API resources". That is exactly the failure the cache-first
architecture exists for, and it means this adapter's live path is written to the
documented API shape but has NOT been verified against a live response. Bitcoin
cases in the prototype run from seeded scenario data, labelled as such.

The UTXO model is preserved end to end: `transaction_details=true` gives the
full input and output sets, which is what makes common-input-ownership
clustering possible.
"""
from __future__ import annotations

from typing import Any, Dict, List

import httpx

from backend import config
from backend.adapters.base import (
    AdapterUnavailable,
    AddressHistory,
    ChainAdapter,
    NormalizedTx,
    TxEndpoint,
    parse_timestamp,
)

SATOSHI = 1e8


def _sat_to_btc(value: Any) -> float:
    try:
        return float(int(value)) / SATOSHI
    except (TypeError, ValueError):
        return 0.0


class BlockchairAdapter(ChainAdapter):
    name = "blockchair"
    chain = "bitcoin"
    endpoint = "address_history"
    requires_key = False  # keyless tier exists, but is heavily throttled

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or config.BLOCKCHAIR_API_KEY
        self.base_url = config.PROVIDER_BASE_URLS["blockchair"]

    def is_configured(self) -> bool:
        # Usable without a key in principle; in practice the keyless tier
        # blacklisted this IP, so we report configured only with a key.
        return bool(self.api_key)

    def fetch_raw(self, address: str) -> Any:
        url = f"{self.base_url}/bitcoin/dashboards/address/{address}"
        params: Dict[str, Any] = {"transaction_details": "true", "limit": 100}
        if self.api_key:
            params["key"] = self.api_key
        try:
            response = httpx.get(
                url, params=params, timeout=config.UPSTREAM_TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(self.name, f"network error: {exc}") from exc

        if response.status_code == 430:
            raise AdapterUnavailable(
                self.name,
                "this IP is rate-limited by the keyless free tier (HTTP 430). "
                "Set BLOCKCHAIR_API_KEY to use the live path.",
            )
        if response.status_code == 429:
            raise AdapterUnavailable(self.name, "provider rate limit reached (HTTP 429)")
        if response.status_code >= 400:
            raise AdapterUnavailable(
                self.name, f"provider returned HTTP {response.status_code}"
            )
        payload = response.json()
        if payload.get("data") is None:
            context = payload.get("context", {})
            raise AdapterUnavailable(
                self.name, f"provider returned no data: {context.get('error', 'unknown')}"
            )
        return payload

    def normalize(self, payload: Any, address: str) -> AddressHistory:
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        # Blockchair keys the dashboard payload by the requested address.
        record = data.get(address) or (next(iter(data.values())) if data else {})
        raw_txs: List[Dict[str, Any]] = record.get("transactions", []) or []

        txs: List[NormalizedTx] = []
        for raw in raw_txs:
            if not isinstance(raw, dict):
                # Without transaction_details the API returns bare hashes; there
                # is nothing to build a graph edge from.
                continue
            inputs = [
                TxEndpoint(
                    address=i.get("recipient") or i.get("address") or "",
                    amount=_sat_to_btc(i.get("value", 0)),
                )
                for i in raw.get("inputs", [])
            ]
            outputs = [
                TxEndpoint(
                    address=o.get("recipient") or o.get("address") or "",
                    amount=_sat_to_btc(o.get("value", 0)),
                )
                for o in raw.get("outputs", [])
            ]
            inputs = [i for i in inputs if i.address]
            outputs = [o for o in outputs if o.address]
            if not inputs or not outputs:
                continue
            txs.append(
                NormalizedTx(
                    tx_hash=raw.get("hash", ""),
                    chain=self.chain,
                    timestamp=parse_timestamp(raw.get("time") or raw.get("block_time")),
                    inputs=inputs,
                    outputs=outputs,
                    fee=_sat_to_btc(raw.get("fee", 0)),
                )
            )
        txs.sort(key=lambda t: t.timestamp)

        declared = (record.get("address") or {}).get("transaction_count")
        truncated = bool(declared and declared > len(txs))

        return AddressHistory(
            address=address,
            chain=self.chain,
            transactions=txs,
            provider=self.name,
            origin="live_cached",
            truncated=truncated,
            note=(
                f"Address reports {declared} transactions; {len(txs)} were retrieved "
                "in this request." if truncated else ""
            ),
        )

    def status_note(self) -> str:
        if self.is_configured():
            return "Configured via BLOCKCHAIR_API_KEY."
        return (
            "Keyless free tier returned HTTP 430 (IP rate-limited) when last probed. "
            "Bitcoin runs from seeded cache."
        )
