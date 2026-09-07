"""ChainAdapter interface and the normalized cross-chain transaction model.

Bitcoin is UTXO-based (many inputs, many outputs) and Ethereum is account-based
(one sender, one recipient). The normalized model keeps the input/output sets,
because throwing them away would destroy the common-input-ownership heuristic —
the single most powerful clustering tool in blockchain forensics. Ethereum
transfers simply normalize to one input and one output.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

AMOUNT_UNITS = {"bitcoin": "BTC", "ethereum": "ETH"}


def parse_timestamp(value: Any) -> datetime:
    """Parse the assorted timestamp shapes providers return, always to UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            dt = datetime.fromtimestamp(int(text), tz=timezone.utc)
        else:
            # Blockscout returns '...Z'; Blockchair returns 'YYYY-MM-DD HH:MM:SS'.
            text = text.replace("Z", "+00:00")
            if " " in text and "T" not in text:
                text = text.replace(" ", "T", 1)
            dt = datetime.fromisoformat(text)
    else:
        raise ValueError(f"Unparseable timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class TxEndpoint:
    """One side of a transfer: an address and the amount attributed to it."""

    address: str
    amount: float


@dataclass
class NormalizedTx:
    tx_hash: str
    chain: str
    timestamp: datetime
    inputs: List[TxEndpoint] = field(default_factory=list)
    outputs: List[TxEndpoint] = field(default_factory=list)
    fee: float = 0.0
    # Set when the provider reports the transfer as a contract/internal call, so
    # behavioral clustering can treat it differently from a plain transfer.
    is_internal: bool = False

    @property
    def input_addresses(self) -> List[str]:
        return [i.address for i in self.inputs]

    @property
    def output_addresses(self) -> List[str]:
        return [o.address for o in self.outputs]

    @property
    def total_input(self) -> float:
        return sum(i.amount for i in self.inputs)

    @property
    def total_output(self) -> float:
        return sum(o.amount for o in self.outputs)

    def amount_to(self, address: str) -> float:
        target = address.lower()
        return sum(o.amount for o in self.outputs if o.address.lower() == target)

    def amount_from(self, address: str) -> float:
        source = address.lower()
        return sum(i.amount for i in self.inputs if i.address.lower() == source)


@dataclass
class AddressHistory:
    """Everything the pipeline needs about one address."""

    address: str
    chain: str
    transactions: List[NormalizedTx] = field(default_factory=list)
    provider: str = "unknown"
    origin: str = "synthetic_scenario"   # live_cached | synthetic_scenario
    fetched_at: Optional[str] = None
    # True when the provider capped the record count (Etherscan's free tier drops
    # to 1,000 records per request). Surfaced, never silently swallowed.
    truncated: bool = False
    note: str = ""

    @property
    def tx_count(self) -> int:
        return len(self.transactions)

    @property
    def first_seen(self) -> Optional[datetime]:
        return min((t.timestamp for t in self.transactions), default=None)

    @property
    def last_seen(self) -> Optional[datetime]:
        return max((t.timestamp for t in self.transactions), default=None)

    @property
    def total_in(self) -> float:
        return sum(t.amount_to(self.address) for t in self.transactions)

    @property
    def total_out(self) -> float:
        return sum(t.amount_from(self.address) for t in self.transactions)

    def outgoing(self) -> List[NormalizedTx]:
        target = self.address.lower()
        return [t for t in self.transactions if target in
                {a.lower() for a in t.input_addresses}]

    def incoming(self) -> List[NormalizedTx]:
        target = self.address.lower()
        return [t for t in self.transactions if target in
                {a.lower() for a in t.output_addresses}]


class ChainAdapter(abc.ABC):
    """Chain-agnostic upstream interface.

    Implementations must not be called directly by the pipeline — everything
    goes through backend.adapters.registry, which enforces cache-before-use and
    the token bucket.
    """

    name: str = "abstract"
    chain: str = "unknown"
    endpoint: str = "address_history"
    requires_key: bool = False

    @abc.abstractmethod
    def fetch_raw(self, address: str) -> Any:
        """Fetch the provider's raw response. Never called on a cache hit."""

    @abc.abstractmethod
    def normalize(self, payload: Any, address: str) -> AddressHistory:
        """Turn a raw (or cached) provider payload into the common model."""

    def is_configured(self) -> bool:
        return True

    def status_note(self) -> str:
        return ""


class AdapterUnavailable(Exception):
    """The adapter cannot serve this request (no key, no network, blocked)."""

    def __init__(self, adapter: str, reason: str):
        super().__init__(f"{adapter} unavailable: {reason}")
        self.adapter = adapter
        self.reason = reason


def empty_history(address: str, chain: str, note: str) -> AddressHistory:
    return AddressHistory(
        address=address, chain=chain, transactions=[], provider="none", note=note
    )


def summarize(history: AddressHistory) -> Dict[str, Any]:
    return {
        "address": history.address,
        "chain": history.chain,
        "tx_count": history.tx_count,
        "total_in": history.total_in,
        "total_out": history.total_out,
        "provider": history.provider,
        "origin": history.origin,
        "truncated": history.truncated,
    }
