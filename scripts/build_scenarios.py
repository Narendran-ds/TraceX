"""Generate the offline demo scenarios into data/fixtures/.

ORDER MATTERS AND IS DELIBERATE. The detectors in backend/patterns/ were written
first, from the definitions in the master report. These scenarios are then
constructed to satisfy those definitions. No threshold was ever moved to make a
scenario light up — the detector tests in backend/tests/test_patterns.py pin
each threshold from both sides.

Synthetic addresses are derived deterministically as
    "0x" + sha256("sih26183:" + label).hexdigest()[:40]
so anyone can regenerate them and confirm they were constructed rather than
observed. Real, publicly documented addresses (exchange hot wallets, bridge
contracts, OFAC-designated addresses) are used verbatim where the scenario needs
an attributable endpoint, and are marked as such below.

Run:  py -3.11 scripts/build_scenarios.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.config import FIXTURE_DIR  # noqa: E402

# Anchor for every scenario. Far enough in the past that the "created, burst,
# then dormant" life-cycle signal has real dormancy to measure, and fixed so the
# fixtures are reproducible.
ANCHOR = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)

# --- real, publicly documented addresses ----------------------------------
# Used verbatim so attribution in the demo resolves to a genuine public source
# that a judge can open and check. See data/tagpacks/ and data/ofac/.
BINANCE_HOT = "0x28C6c06298d514Db089934071355E5743bf21d60"
WORMHOLE_BRIDGE = "0x3ee18B2214AFF97000D974cf647E7C347E8fa585"


def addr(label: str) -> str:
    """Deterministic synthetic address. Reproducible, and clearly generated."""
    digest = hashlib.sha256(f"sih26183:{label}".encode("utf-8")).hexdigest()
    return "0x" + digest[:40]


def tx_hash(label: str) -> str:
    digest = hashlib.sha256(f"sih26183:tx:{label}".encode("utf-8")).hexdigest()
    return "0x" + digest


def when(minutes: float = 0, hours: float = 0, days: float = 0) -> str:
    return (ANCHOR + timedelta(minutes=minutes, hours=hours, days=days)).isoformat()


def tx(label: str, timestamp: str, inputs: List, outputs: List, fee: float = 0.0):
    return {
        "tx_hash": tx_hash(label),
        "timestamp": timestamp,
        "inputs": [{"address": a, "amount": round(v, 8)} for a, v in inputs],
        "outputs": [{"address": a, "amount": round(v, 8)} for a, v in outputs],
        "fee": fee,
    }


def distribute(transactions: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Route each transaction to every address that participates in it.

    Mirrors reality: querying any participating address returns that
    transaction, so the walker sees the same edge from either end.
    """
    per_address: Dict[str, List[Dict[str, Any]]] = {}
    for transaction in transactions:
        participants = {i["address"] for i in transaction["inputs"]}
        participants |= {o["address"] for o in transaction["outputs"]}
        for address in participants:
            per_address.setdefault(address, []).append(transaction)
    return per_address


def write_scenario(
    slug: str,
    complaint_id: str,
    title: str,
    narrative: str,
    demonstrates: List[str],
    seed: str,
    chain: str,
    transactions: List[Dict[str, Any]],
    provenance: str = "synthetic_scenario",
) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"{slug}.json"
    document = {
        "slug": slug,
        "complaint_id": complaint_id,
        "title": title,
        "narrative": narrative,
        "demonstrates": demonstrates,
        "seed_address": seed,
        "chain": chain,
        "provenance": provenance,
        "generated_by": "scripts/build_scenarios.py",
        "address_derivation": (
            "Synthetic addresses are '0x' + sha256('sih26183:' + label)[:40]. "
            "Addresses that resolve to a real public attribution source "
            "(exchange hot wallets, bridge contracts, OFAC-designated addresses) "
            "are real and used verbatim."
        ),
        "addresses": distribute(transactions),
    }
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


# ===========================================================================
# CASE A — laundering trace
# ===========================================================================

def scenario_laundering() -> Path:
    """Fan-out -> peel chain -> burst -> consolidation -> exchange deposit.

    Built to satisfy, in order:
      fan_out        6 fresh recipients inside the 1h window
      round_split    6 near-equal parts
      peel_chain     4 hops, each forwarding >=60% and peeling <=25%
      timing_burst   6 distinct transactions inside the 10m window
      fan_in         5 senders converging on an unattributed collector
      cluster_context the layer cluster is new, bursts, then goes dormant
    """
    seed = addr("caseA/seed")
    layers = [addr(f"caseA/layer{i}") for i in range(6)]
    collector = addr("caseA/collector")

    transactions: List[Dict[str, Any]] = []

    # Victim's funds arrive at the reported address.
    transactions.append(
        tx("A/victim", when(days=-2), [(addr("caseA/victim"), 12.06)], [(seed, 12.0)],
           fee=0.06)
    )

    # 1. Rapid fan-out into six fresh addresses, in near-equal parts.
    transactions.append(
        tx("A/fanout", when(0), [(seed, 12.0)], [(a, 2.0) for a in layers])
    )

    # 2. Peel chain off layer 0: a dominant remainder carries on while small
    #    slices are shaved to side addresses.
    current, amount = layers[0], 2.0
    for step in range(4):
        remainder = round(amount * 0.88, 8)
        peel = round(amount - remainder, 8)
        nxt = addr(f"caseA/peel{step + 1}")
        transactions.append(
            tx(f"A/peel{step}", when(minutes=3 + step * 2),
               [(current, amount)],
               [(nxt, remainder), (addr(f"caseA/side{step}"), peel)])
        )
        current, amount = nxt, remainder

    # 3. The other five layer addresses converge on one collector, fast.
    for index, layer in enumerate(layers[1:], start=1):
        transactions.append(
            tx(f"A/consolidate{index}", when(minutes=4 + index),
               [(layer, 2.0)], [(collector, 1.99)], fee=0.01)
        )

    # 4. The collector deposits to a real, publicly labelled exchange address.
    transactions.append(
        tx("A/cashout", when(hours=2), [(collector, 9.95)], [(BINANCE_HOT, 9.94)],
           fee=0.01)
    )

    return write_scenario(
        slug="case-a-laundering",
        complaint_id="CYB-2026-00124",
        title="Layered trail ending at an exchange deposit",
        narrative=(
            "A victim reports losing funds to a fake investment platform. The "
            "reported address immediately splits the funds across six fresh "
            "addresses, runs one branch through a peel chain, re-aggregates the "
            "rest at a single collection address, and deposits to an exchange "
            "roughly two hours later."
        ),
        demonstrates=[
            "rapid fan-out into previously-unseen addresses",
            "round-number splitting",
            "peel chain",
            "timing burst",
            "fan-in consolidation at an unattributed collector",
            "attributed cash-out point with a citable public source",
        ],
        seed=seed,
        chain="ethereum",
        transactions=transactions,
    )


# ===========================================================================
# CASE C — bridge dead-end (the deliberate failure demo)
# ===========================================================================

def scenario_bridge_deadend() -> Path:
    """The trail is real, the patterns are real, and the answer is 'we can't say'.

    This is the case that proves the system does not fabricate a destination.
    """
    seed = addr("caseC/seed")
    hops = [addr(f"caseC/hop{i}") for i in range(5)]

    transactions: List[Dict[str, Any]] = []

    transactions.append(
        tx("C/victim", when(days=-1), [(addr("caseC/victim"), 8.04)], [(seed, 8.0)],
           fee=0.04)
    )

    # Fan-out to five fresh addresses inside the window.
    transactions.append(
        tx("C/fanout", when(0), [(seed, 8.0)], [(a, 1.6) for a in hops])
    )

    # All five push into a cross-chain bridge within a tight window. The trail
    # is followable right up to the bridge, and stops there.
    for index, hop in enumerate(hops):
        transactions.append(
            tx(f"C/bridge{index}", when(minutes=5 + index),
               [(hop, 1.6)], [(WORMHOLE_BRIDGE, 1.59)], fee=0.01)
        )

    return write_scenario(
        slug="case-c-bridge-deadend",
        complaint_id="CYB-2026-00131",
        title="Trail reaching a cross-chain bridge",
        narrative=(
            "The reported address splits funds and moves every branch into a "
            "cross-chain bridge contract within minutes. On-chain tracing can "
            "follow the money to the bridge and no further — the system reports "
            "that boundary rather than inferring a destination on the other side."
        ),
        demonstrates=[
            "rapid fan-out",
            "the trail reaching a documented bridge contract",
            "confidence boundary reported instead of a guessed destination",
        ],
        seed=seed,
        chain="ethereum",
        transactions=transactions,
    )


# ===========================================================================
# CASE D — legitimate consolidation (the negative case)
# ===========================================================================

def scenario_legitimate_consolidation() -> Path:
    """The same converging shape as case A, and it must NOT be flagged.

    CLAUDE.md is explicit that fan-in cannot ship without contextual
    disambiguation, because a legitimate exchange deposit consolidation looks
    identical to a laundering consolidation. This case exists to show that the
    disambiguation does real work — and to be shown to judges deliberately.

    The differences from case A that make it legitimate:
      * funds leave the reported address over days, not inside one window, so
        there is no rapid fan-out and no scripted-looking split;
      * the recipients are not funded together, so they do not cluster into one
        actor;
      * the destination is a publicly attributed exchange, so the converging
        shape is recognised as ordinary deposit activity.
    """
    seed = addr("caseD/seed")
    payees = [addr(f"caseD/payee{i}") for i in range(6)]

    transactions: List[Dict[str, Any]] = []

    transactions.append(
        tx("D/funding", when(days=-20), [(addr("caseD/source"), 9.05)], [(seed, 9.0)],
           fee=0.05)
    )

    # Payments spread across days, in irregular amounts: ordinary activity.
    irregular = [1.37, 0.94, 2.11, 1.68, 0.72, 2.03]
    for index, (payee, value) in enumerate(zip(payees, irregular)):
        transactions.append(
            tx(f"D/pay{index}", when(days=-14 + index * 2.3),
               [(seed, value + 0.01)], [(payee, value)], fee=0.01)
        )

    # Later, all six independently deposit to the same real exchange address.
    # Identical convergence shape to case A — and correctly not flagged.
    for index, (payee, value) in enumerate(zip(payees, irregular)):
        transactions.append(
            tx(f"D/deposit{index}", when(minutes=index * 4),
               [(payee, value)], [(BINANCE_HOT, value - 0.01)], fee=0.01)
        )

    return write_scenario(
        slug="case-d-legitimate-consolidation",
        complaint_id="CYB-2026-00140",
        title="Ordinary exchange deposit consolidation (control case)",
        narrative=(
            "A control case with the same converging shape as a laundering "
            "consolidation. Funds leave over days in irregular amounts, the "
            "recipients are unrelated to each other, and they all later deposit "
            "at the same publicly attributed exchange. The engine checks the "
            "convergence, applies its contextual features, and deliberately does "
            "not flag it."
        ),
        demonstrates=[
            "a converging shape that is correctly NOT reported",
            "fan-in disambiguation via attribution and cluster age",
            "the suppressed signal shown with its reason, not hidden",
        ],
        seed=seed,
        chain="ethereum",
        transactions=transactions,
    )


# ===========================================================================
# CASE E — Bitcoin, UTXO clustering
# ===========================================================================

def scenario_bitcoin_cluster() -> Path:
    """Bitcoin case exercising common-input-ownership and change detection.

    Bitcoin runs from constructed data because the keyless Blockchair free tier
    returned HTTP 430 (IP rate-limited) when probed from this machine. The
    adapter is written against the documented API shape; only the live path is
    unverified. See PROGRESS.md.
    """
    def btc(label: str) -> str:
        digest = hashlib.sha256(f"sih26183:btc:{label}".encode("utf-8")).hexdigest()
        return "bc1q" + digest[:38]

    seed = btc("caseE/seed")
    wallet = [btc(f"caseE/w{i}") for i in range(6)]
    exit_address = btc("caseE/exit")

    transactions: List[Dict[str, Any]] = []

    transactions.append(
        tx("E/victim", when(days=-3), [(btc("caseE/victim"), 1.5)], [(seed, 1.49)],
           fee=0.01)
    )

    # Fan-out into six addresses.
    transactions.append(
        tx("E/fanout", when(0), [(seed, 1.49)],
           [(a, 0.248) for a in wallet], fee=0.002)
    )

    # A spend with a round payment and a non-round remainder returning to a
    # fresh address: the textbook change-address signature.
    transactions.append(
        tx("E/change", when(minutes=20), [(wallet[0], 0.248)],
           [(btc("caseE/merchant"), 0.2), (btc("caseE/change"), 0.04731)], fee=0.0007)
    )

    # Five addresses spent together as inputs to one transaction — the
    # common-input-ownership heuristic collapses them into one entity.
    transactions.append(
        tx("E/cospend", when(minutes=40),
           [(a, 0.248) for a in wallet[1:]],
           [(exit_address, 1.238)], fee=0.002)
    )

    return write_scenario(
        slug="case-e-bitcoin-clustering",
        complaint_id="CYB-2026-00152",
        title="Bitcoin trail with common-input clustering",
        narrative=(
            "A Bitcoin case where the suspect splits funds across six addresses "
            "and later spends five of them together as inputs to a single "
            "transaction — revealing common control, and collapsing the visible "
            "address set into a much smaller set of actors."
        ),
        demonstrates=[
            "common-input-ownership clustering",
            "change-address detection",
            "the address-to-cluster collapse on a UTXO chain",
        ],
        seed=seed,
        chain="bitcoin",
        transactions=transactions,
    )


def main() -> int:
    built = [
        scenario_laundering(),
        scenario_bridge_deadend(),
        scenario_legitimate_consolidation(),
        scenario_bitcoin_cluster(),
    ]
    for path in built:
        document = json.loads(path.read_text(encoding="utf-8"))
        tx_count = len({
            t["tx_hash"]
            for txs in document["addresses"].values()
            for t in txs
        })
        print(
            f"  {path.name:<40} {len(document['addresses']):>3} addresses, "
            f"{tx_count:>3} transactions"
        )
    print(f"\n{len(built)} scenario(s) written to {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
