"""Ethereum adapters: Blockscout (keyless) and Etherscan V2 (key-gated).

Blockscout is the documented fallback in CLAUDE.md and, unlike the others, its
free tier answered without a key when probed — so it is what the prefetch script
uses to put *real, verifiable* Ethereum transactions into the cache.

Etherscan V2 is implemented against the current free-tier shape but is only
usable with ETHERSCAN_API_KEY set. Note its July 2026 tightening: max records
per request dropped to 1,000, which we surface as `truncated` rather than
quietly returning a partial history as if it were complete.
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

WEI = 1e18
ETHERSCAN_MAX_RECORDS = 1000  # free tier cap since the July 2026 change


def _wei_to_eth(value: Any) -> float:
    try:
        return float(int(str(value))) / WEI
    except (TypeError, ValueError):
        return 0.0


def _normalize_evm_transfers(
    transfers: List[Dict[str, Any]], address: str, chain: str
) -> List[NormalizedTx]:
    """Account-model transfers -> one input, one output each.

    Zero-value transfers (contract calls with no ETH moved) are dropped: they
    are noise in a fund trail and would inflate the transaction count shown to
    the investigator.
    """
    txs: List[NormalizedTx] = []
    for t in transfers:
        amount = t["amount"]
        if amount <= 0:
            continue
        if not t.get("from") or not t.get("to"):
            continue  # contract creation has no recipient
        txs.append(
            NormalizedTx(
                tx_hash=t["hash"],
                chain=chain,
                timestamp=parse_timestamp(t["timestamp"]),
                inputs=[TxEndpoint(address=t["from"], amount=amount)],
                outputs=[TxEndpoint(address=t["to"], amount=amount)],
                fee=t.get("fee", 0.0),
                is_internal=bool(t.get("is_internal", False)),
            )
        )
    txs.sort(key=lambda t: t.timestamp)
    return txs


class BlockscoutAdapter(ChainAdapter):
    name = "blockscout"
    chain = "ethereum"
    endpoint = "address_history"
    requires_key = False

    def __init__(self, base_url: str = ""):
        self.base_url = base_url or config.PROVIDER_BASE_URLS["blockscout"]

    def fetch_raw(self, address: str) -> Any:
        url = f"{self.base_url}/addresses/{address}/transactions"
        try:
            response = httpx.get(url, timeout=config.UPSTREAM_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(self.name, f"network error: {exc}") from exc
        if response.status_code == 429:
            raise AdapterUnavailable(self.name, "provider rate limit reached (HTTP 429)")
        if response.status_code >= 400:
            raise AdapterUnavailable(
                self.name, f"provider returned HTTP {response.status_code}"
            )
        return response.json()

    def normalize(self, payload: Any, address: str) -> AddressHistory:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        transfers = []
        for item in items:
            to_obj = item.get("to") or {}
            from_obj = item.get("from") or {}
            fee_obj = item.get("fee") or {}
            transfers.append(
                {
                    "hash": item.get("hash"),
                    "from": from_obj.get("hash"),
                    "to": to_obj.get("hash"),
                    "amount": _wei_to_eth(item.get("value", 0)),
                    "timestamp": item.get("timestamp"),
                    "fee": _wei_to_eth(fee_obj.get("value", 0)),
                }
            )
        return AddressHistory(
            address=address,
            chain=self.chain,
            transactions=_normalize_evm_transfers(transfers, address, self.chain),
            provider=self.name,
            origin="live_cached",
            truncated=bool(payload.get("next_page_params")) if isinstance(payload, dict) else False,
            note=(
                "Blockscout returns one page per request; further pages were not "
                "fetched." if isinstance(payload, dict) and payload.get("next_page_params")
                else ""
            ),
        )

    def status_note(self) -> str:
        return "Free tier, no API key required."


class EtherscanAdapter(ChainAdapter):
    name = "etherscan"
    chain = "ethereum"
    endpoint = "address_history"
    requires_key = True

    def __init__(self, api_key: str = "", chain_id: int = 1):
        self.api_key = api_key or config.ETHERSCAN_API_KEY
        self.chain_id = chain_id
        self.base_url = config.PROVIDER_BASE_URLS["etherscan"]

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def fetch_raw(self, address: str) -> Any:
        if not self.is_configured():
            raise AdapterUnavailable(self.name, "ETHERSCAN_API_KEY is not set")
        params = {
            "chainid": self.chain_id,
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": ETHERSCAN_MAX_RECORDS,
            "sort": "asc",
            "apikey": self.api_key,
        }
        try:
            response = httpx.get(
                self.base_url, params=params, timeout=config.UPSTREAM_TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(self.name, f"network error: {exc}") from exc
        if response.status_code == 429:
            raise AdapterUnavailable(self.name, "provider rate limit reached (HTTP 429)")
        if response.status_code >= 400:
            raise AdapterUnavailable(
                self.name, f"provider returned HTTP {response.status_code}"
            )
        payload = response.json()
        # Etherscan signals throttling in the body with HTTP 200.
        if str(payload.get("message", "")).lower().startswith("notok"):
            raise AdapterUnavailable(
                self.name, f"provider declined the request: {payload.get('result')}"
            )
        return payload

    def normalize(self, payload: Any, address: str) -> AddressHistory:
        result = payload.get("result", []) if isinstance(payload, dict) else []
        if not isinstance(result, list):
            result = []
        transfers = [
            {
                "hash": r.get("hash"),
                "from": r.get("from"),
                "to": r.get("to"),
                "amount": _wei_to_eth(r.get("value", 0)),
                "timestamp": r.get("timeStamp"),
                "fee": 0.0,
            }
            for r in result
        ]
        truncated = len(result) >= ETHERSCAN_MAX_RECORDS
        return AddressHistory(
            address=address,
            chain=self.chain,
            transactions=_normalize_evm_transfers(transfers, address, self.chain),
            provider=self.name,
            origin="live_cached",
            truncated=truncated,
            note=(
                f"Etherscan free tier caps a request at {ETHERSCAN_MAX_RECORDS} records; "
                "this address has at least that many and the history is partial."
                if truncated else ""
            ),
        )

    def status_note(self) -> str:
        return (
            "Configured." if self.is_configured()
            else "Available but not configured (no ETHERSCAN_API_KEY)."
        )
