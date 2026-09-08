"""Generate the labelled evaluation set into data/evaluation/cases/.

WHAT THIS SET IS, AND IS NOT
============================
Every case here is a **constructed scenario with known ground truth**. It
measures one thing honestly: *does the engine do what its own documentation says
it does* — fire on the shapes it defines, stay silent just below its thresholds,
and refuse to flag legitimate activity that looks identical.

It does **not** measure real-world laundering detection. That needs traces from
publicly documented cases, which is a research task (master report §15.1) this
set is deliberately not pretending to be. Every case carries
`ground_truth_source`, and the harness prints the split, so nobody can read a
number off this set and call it real-world accuracy.

Add a documented real case by writing a file here with
`ground_truth_source` set to the public write-up URL. The harness will pick it
up and the split in its report will change on its own.

Run:  py -3.11 scripts/build_evaluation_set.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.config import (  # noqa: E402
    EVALUATION_DIR,
    FAN_IN_MIN_SENDERS,
    FAN_OUT_MIN_RECIPIENTS,
    PEEL_CHAIN_MIN_HOPS,
    ROUND_SPLIT_MIN_PARTS,
    TIMING_BURST_MIN_TXS,
)

CASES_DIR = EVALUATION_DIR / "cases"

ANCHOR = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)

# Real, publicly documented addresses — used so attribution resolves to a
# genuine public source. See data/tagpacks/ and data/ofac/.
BINANCE_HOT = "0x28C6c06298d514Db089934071355E5743bf21d60"
KRAKEN_HOT = "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2"
WORMHOLE_BRIDGE = "0x3ee18B2214AFF97000D974cf647E7C347E8fa585"
TORNADO_SDN = "0x8589427373D6D84E98730D7795D8f6f8731FDA16"

CONSTRUCTED = "constructed for this evaluation set"


def addr(label: str) -> str:
    return "0x" + hashlib.sha256(f"sih26183:eval:{label}".encode()).hexdigest()[:40]


def tx_hash(label: str) -> str:
    return "0x" + hashlib.sha256(f"sih26183:evaltx:{label}".encode()).hexdigest()


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
    per_address: Dict[str, List[Dict[str, Any]]] = {}
    for transaction in transactions:
        participants = {i["address"] for i in transaction["inputs"]}
        participants |= {o["address"] for o in transaction["outputs"]}
        for address in participants:
            per_address.setdefault(address, []).append(transaction)
    return per_address


def case(
    slug: str,
    title: str,
    rationale: str,
    seed: str,
    transactions: List[Dict[str, Any]],
    expect_patterns: List[str],
    expect_absent: List[str],
    chain: str = "ethereum",
    expect_exit_entity: Optional[str] = None,
    expect_trail_degraded: bool = False,
    ground_truth_source: str = CONSTRUCTED,
    max_hops: int = 5,
) -> Dict[str, Any]:
    return {
        "slug": slug,
        "title": title,
        "rationale": rationale,
        "chain": chain,
        "seed_address": seed,
        "max_hops": max_hops,
        # 'constructed' means the transactions were built to a definition;
        # a URL means the trace comes from a publicly documented incident.
        "ground_truth_source": ground_truth_source,
        "provenance": (
            "synthetic_scenario" if ground_truth_source == CONSTRUCTED else "live_cached"
        ),
        "expected": {
            "patterns_present": sorted(expect_patterns),
            "patterns_absent": sorted(expect_absent),
            "exit_entity_contains": expect_exit_entity,
            "trail_degraded": expect_trail_degraded,
        },
        "addresses": distribute(transactions),
    }


ALL_PATTERNS = [
    "fan_out", "fan_in", "peel_chain", "round_split",
    "timing_burst", "sanctioned_match", "cluster_context",
]


def absent_except(*present: str) -> List[str]:
    return [p for p in ALL_PATTERNS if p not in present]


# ===========================================================================
# Positive cases — each pattern fires at its documented definition
# ===========================================================================

def eval_fan_out() -> Dict[str, Any]:
    seed = addr("fanout/seed")
    outs = [addr(f"fanout/r{i}") for i in range(FAN_OUT_MIN_RECIPIENTS + 1)]
    txs = [
        tx("fanout/fund", when(days=-3), [(addr("fanout/src"), 6.1)], [(seed, 6.0)]),
        # Irregular amounts so this isolates fan-out from round-splitting.
        tx("fanout/split", when(0), [(seed, 6.0)],
           [(a, v) for a, v in zip(outs, [1.31, 0.87, 1.44, 0.62, 1.09, 0.67])]),
    ]
    return case(
        "fan-out-positive",
        "Rapid fan-out into six previously-unseen addresses",
        "Six fresh recipients inside the one-hour window: the documented "
        "definition of rapid fan-out. Amounts are irregular so round-number "
        "splitting stays silent and the cases do not test each other.",
        seed, txs,
        expect_patterns=["fan_out"],
        expect_absent=absent_except("fan_out"),
    )


def eval_fan_in() -> Dict[str, Any]:
    seed = addr("fanin/seed")
    collector = addr("fanin/collector")
    senders = [addr(f"fanin/s{i}") for i in range(FAN_IN_MIN_SENDERS)]
    txs = [
        tx("fanin/spread", when(days=-2), [(seed, 5.0)],
           [(a, v) for a, v in zip(senders, [1.13, 0.94, 1.02, 0.88, 1.03])]),
    ]
    for index, (sender, value) in enumerate(zip(senders, [1.13, 0.94, 1.02, 0.88, 1.03])):
        txs.append(
            tx(f"fanin/in{index}", when(minutes=index * 3),
               [(sender, value)], [(collector, value - 0.01)])
        )
    return case(
        "fan-in-positive",
        "Five addresses converge on an unattributed collector",
        "The converging shape with contextual features that do NOT indicate a "
        "service: the destination is unattributed, low-volume and new. Fan-in "
        "should be reported here.",
        seed, txs,
        expect_patterns=["fan_in"],
        expect_absent=["sanctioned_match", "peel_chain", "round_split"],
    )


def eval_peel_chain() -> Dict[str, Any]:
    seed = addr("peel/seed")
    txs = [tx("peel/fund", when(days=-2), [(addr("peel/src"), 50.5)], [(seed, 50.0)])]
    current, amount = seed, 50.0
    for step in range(PEEL_CHAIN_MIN_HOPS + 1):
        remainder = round(amount * 0.87, 8)
        peel = round(amount - remainder, 8)
        nxt = addr(f"peel/hop{step}")
        txs.append(
            tx(f"peel/step{step}", when(hours=step * 3),
               [(current, amount)],
               [(nxt, remainder), (addr(f"peel/side{step}"), peel)])
        )
        current, amount = nxt, remainder
    return case(
        "peel-chain-positive",
        "Four-hop peel chain",
        "Each hop forwards 87% and peels 13% to a side address — inside the "
        "documented remainder and peel ratios, over more than the minimum hops.",
        seed, txs, max_hops=6,
        expect_patterns=["peel_chain"],
        expect_absent=["fan_in", "sanctioned_match", "round_split"],
    )


def eval_round_split() -> Dict[str, Any]:
    seed = addr("round/seed")
    outs = [addr(f"round/p{i}") for i in range(ROUND_SPLIT_MIN_PARTS + 1)]
    txs = [
        tx("round/fund", when(days=-2), [(addr("round/src"), 8.1)], [(seed, 8.0)]),
        tx("round/split", when(0), [(seed, 8.0)], [(a, 2.0) for a in outs]),
    ]
    return case(
        "round-split-positive",
        "Split into four equal 2.0 parts",
        "Near-equal, round-number parts inside the window. Only four "
        "recipients, one below the fan-out threshold, so this isolates "
        "round-number splitting.",
        seed, txs,
        expect_patterns=["round_split"],
        expect_absent=["fan_out", "fan_in", "peel_chain", "sanctioned_match"],
    )


def eval_timing_burst() -> Dict[str, Any]:
    seed = addr("burst/seed")
    hub = addr("burst/hub")
    txs = [
        tx("burst/fund", when(days=-2), [(seed, 6.0)], [(hub, 6.0)]),
    ]
    # Distinct transactions inside the tight window — a burst is counted over
    # transactions, not edges, so one fan-out tx would not qualify.
    # Irregular amounts: identical parts would be round-number splitting as
    # well, and this case exists to isolate timing.
    burst_values = [0.97, 0.83, 1.12, 0.64, 1.06, 0.91]
    for index, value in enumerate(burst_values[: TIMING_BURST_MIN_TXS + 1]):
        txs.append(
            tx(f"burst/out{index}", when(minutes=index * 1.5),
               [(hub, value)], [(addr(f"burst/d{index}"), value - 0.01)])
        )
    return case(
        "timing-burst-positive",
        "Six transactions from one cluster inside nine minutes",
        "Separate transactions in a tight window, from addresses the clustering "
        "groups together. Amounts are irregular so round-splitting stays silent.",
        seed, txs,
        expect_patterns=["timing_burst"],
        expect_absent=["peel_chain", "sanctioned_match", "round_split"],
    )


def eval_sanctioned() -> Dict[str, Any]:
    seed = addr("sdn/seed")
    txs = [
        tx("sdn/fund", when(days=-2), [(addr("sdn/src"), 3.1)], [(seed, 3.0)]),
        tx("sdn/hop", when(0), [(seed, 3.0)], [(addr("sdn/mid"), 2.99)]),
        tx("sdn/mixer", when(hours=1), [(addr("sdn/mid"), 2.99)], [(TORNADO_SDN, 2.98)]),
    ]
    return case(
        "sanctioned-match-positive",
        "Trail reaches an OFAC-designated address",
        "The destination is a real Tornado Cash address on the OFAC SDN list. "
        "A mixer is also a confidence boundary, so this case asserts both the "
        "attribution hit and the trail degradation that must come with it.",
        seed, txs,
        expect_patterns=["sanctioned_match"],
        expect_absent=["fan_out", "fan_in", "round_split"],
        expect_exit_entity="TORNADO",
        expect_trail_degraded=True,
    )


# ===========================================================================
# Negative cases — just under threshold, or legitimate activity
# ===========================================================================

def eval_fan_out_below() -> Dict[str, Any]:
    seed = addr("fanoutneg/seed")
    outs = [addr(f"fanoutneg/r{i}") for i in range(FAN_OUT_MIN_RECIPIENTS - 1)]
    txs = [
        tx("fanoutneg/fund", when(days=-3), [(addr("fanoutneg/src"), 4.1)], [(seed, 4.0)]),
        tx("fanoutneg/split", when(0), [(seed, 4.0)],
           [(a, v) for a, v in zip(outs, [1.13, 0.87, 1.31, 0.64])]),
    ]
    return case(
        "fan-out-below-threshold",
        "Four recipients — one below the fan-out threshold",
        "The threshold is a definition, not a dial. One recipient short must "
        "produce silence, or the threshold means nothing.",
        seed, txs,
        expect_patterns=[],
        expect_absent=ALL_PATTERNS,
    )


def eval_fan_out_spread() -> Dict[str, Any]:
    seed = addr("fanoutspread/seed")
    txs = [
        tx("fanoutspread/fund", when(days=-20), [(addr("fanoutspread/src"), 8.1)],
           [(seed, 8.0)]),
    ]
    for index in range(FAN_OUT_MIN_RECIPIENTS + 2):
        txs.append(
            tx(f"fanoutspread/out{index}", when(days=-18 + index * 2),
               [(seed, 1.13)], [(addr(f"fanoutspread/r{index}"), 1.12)])
        )
    return case(
        "fan-out-outside-window",
        "Seven recipients spread over two weeks",
        "Enough recipients, but nothing rapid about it. Ordinary outgoing "
        "payment activity must not read as layering.",
        seed, txs,
        expect_patterns=[],
        expect_absent=ALL_PATTERNS,
    )


def eval_legitimate_consolidation() -> Dict[str, Any]:
    """The control case CLAUDE.md explicitly requires."""
    seed = addr("legit/seed")
    payees = [addr(f"legit/p{i}") for i in range(FAN_IN_MIN_SENDERS + 1)]
    values = [1.37, 0.94, 2.11, 1.68, 0.72, 2.03]
    txs = [
        tx("legit/fund", when(days=-30), [(addr("legit/src"), 9.1)], [(seed, 9.0)]),
    ]
    for index, (payee, value) in enumerate(zip(payees, values)):
        txs.append(
            tx(f"legit/pay{index}", when(days=-24 + index * 2.5),
               [(seed, value + 0.01)], [(payee, value)])
        )
    for index, (payee, value) in enumerate(zip(payees, values)):
        txs.append(
            tx(f"legit/deposit{index}", when(minutes=index * 4),
               [(payee, value)], [(BINANCE_HOT, value - 0.01)])
        )
    return case(
        "legitimate-exchange-consolidation",
        "Six unrelated addresses deposit at a real exchange",
        "The single most important negative in the set. Identical converging "
        "shape to laundering consolidation, into a publicly labelled Binance "
        "address. Fan-in must be suppressed by the contextual features, and the "
        "suppression must be reported with its reason.",
        seed, txs,
        expect_patterns=[],
        expect_absent=ALL_PATTERNS,
        expect_exit_entity="Binance",
    )


def eval_even_split_not_peel() -> Dict[str, Any]:
    seed = addr("evensplit/seed")
    txs = [tx("evensplit/fund", when(days=-2), [(addr("evensplit/src"), 20.1)],
              [(seed, 20.0)])]
    current, amount = seed, 20.0
    for step in range(PEEL_CHAIN_MIN_HOPS + 1):
        half = round(amount / 2, 8)
        nxt = addr(f"evensplit/hop{step}")
        txs.append(
            tx(f"evensplit/step{step}", when(hours=step * 3),
               [(current, amount)], [(nxt, half), (addr(f"evensplit/other{step}"), half)])
        )
        current, amount = nxt, half
    return case(
        "even-splits-not-a-peel-chain",
        "Repeated 50/50 splits",
        "A chain of even splits has no dominant remainder, so it is not "
        "peeling. Tests that the peel ratios do real work rather than matching "
        "any chain of two-output transactions.",
        seed, txs, max_hops=6,
        expect_patterns=[],
        expect_absent=["peel_chain"],
    )


def eval_clean_pass_through() -> Dict[str, Any]:
    seed = addr("clean/seed")
    txs = [
        tx("clean/fund", when(days=-6), [(addr("clean/src"), 2.51)], [(seed, 2.5)]),
        tx("clean/hop1", when(days=-4), [(seed, 2.5)], [(addr("clean/a"), 2.49)]),
        tx("clean/hop2", when(days=-2), [(addr("clean/a"), 2.49)],
           [(KRAKEN_HOT, 2.48)]),
    ]
    return case(
        "clean-trace-to-exchange",
        "Two ordinary hops to a labelled exchange",
        "A trace with no obfuscation shape at all, ending at a real Kraken "
        "address. Should score zero and still attribute the exit — a clean "
        "trace is a useful answer, not a failed one.",
        seed, txs,
        expect_patterns=[],
        expect_absent=ALL_PATTERNS,
        expect_exit_entity="Kraken",
    )


def eval_timing_spread() -> Dict[str, Any]:
    seed = addr("burstneg/seed")
    hub = addr("burstneg/hub")
    txs = [tx("burstneg/fund", when(days=-14), [(seed, 6.0)], [(hub, 6.0)])]
    burst_values = [0.97, 0.83, 1.12, 0.64, 1.06, 0.91]
    for index, value in enumerate(burst_values[: TIMING_BURST_MIN_TXS + 1]):
        txs.append(
            tx(f"burstneg/out{index}", when(days=-12 + index * 1.5),
               [(hub, value)], [(addr(f"burstneg/d{index}"), value - 0.01)])
        )
    return case(
        "timing-spread-not-a-burst",
        "Six transactions spread over nine days",
        "Same transaction count as the burst case, paced like a human. Must "
        "stay silent.",
        seed, txs,
        expect_patterns=[],
        expect_absent=["timing_burst"],
    )


def eval_single_tx_not_a_burst() -> Dict[str, Any]:
    seed = addr("onetx/seed")
    outs = [addr(f"onetx/r{i}") for i in range(8)]
    txs = [
        tx("onetx/fund", when(days=-3), [(addr("onetx/src"), 8.1)], [(seed, 8.0)]),
        tx("onetx/fan", when(0), [(seed, 8.0)],
           [(a, v) for a, v in zip(outs, [1.13, 0.87, 1.44, 0.62, 1.09, 0.67, 1.21, 0.93])]),
    ]
    return case(
        "single-transaction-not-a-burst",
        "One transaction with eight outputs",
        "Eight edges but one transaction. A burst counts transactions, not "
        "edges — counting edges would score the same event twice, once as "
        "fan-out and again as a burst.",
        seed, txs,
        expect_patterns=["fan_out"],
        expect_absent=["timing_burst", "peel_chain", "sanctioned_match"],
    )


# ===========================================================================
# Attribution and degradation cases
# ===========================================================================

def eval_bridge_deadend() -> Dict[str, Any]:
    seed = addr("bridge/seed")
    hops = [addr(f"bridge/h{i}") for i in range(3)]
    txs = [
        tx("bridge/fund", when(days=-2), [(addr("bridge/src"), 6.1)], [(seed, 6.0)]),
        tx("bridge/split", when(0), [(seed, 6.0)],
           [(a, v) for a, v in zip(hops, [2.13, 1.94, 1.88])]),
    ]
    for index, (hop, value) in enumerate(zip(hops, [2.13, 1.94, 1.88])):
        txs.append(
            tx(f"bridge/out{index}", when(hours=1 + index),
               [(hop, value)], [(WORMHOLE_BRIDGE, value - 0.01)])
        )
    return case(
        "bridge-dead-end",
        "Trail reaches a cross-chain bridge",
        "The deliberate failure case. Tracing can follow the money to the "
        "bridge and no further; the system must report the boundary rather "
        "than inferring what came out the other side.",
        seed, txs,
        expect_patterns=[],
        expect_absent=["sanctioned_match"],
        expect_exit_entity="Wormhole",
        expect_trail_degraded=True,
    )


def eval_unattributed_exit() -> Dict[str, Any]:
    seed = addr("unattrib/seed")
    txs = [
        tx("unattrib/fund", when(days=-4), [(addr("unattrib/src"), 4.1)], [(seed, 4.0)]),
        tx("unattrib/hop", when(days=-2), [(seed, 4.0)], [(addr("unattrib/a"), 3.99)]),
        tx("unattrib/end", when(0), [(addr("unattrib/a"), 3.99)],
           [(addr("unattrib/nobody"), 3.98)]),
    ]
    return case(
        "unattributed-exit-cluster",
        "Trail ends at an address in no attribution source",
        "The exit must be reported as unattributed, never upgraded to a "
        "probable name. Tests the refusal to guess.",
        seed, txs,
        expect_patterns=[],
        expect_absent=ALL_PATTERNS,
        expect_exit_entity=None,
    )


# ===========================================================================
# Multi-pattern case
# ===========================================================================

def eval_full_laundering() -> Dict[str, Any]:
    seed = addr("full/seed")
    layers = [addr(f"full/l{i}") for i in range(6)]
    collector = addr("full/collector")

    txs = [
        tx("full/victim", when(days=-2), [(addr("full/victim"), 12.1)], [(seed, 12.0)]),
        tx("full/fanout", when(0), [(seed, 12.0)], [(a, 2.0) for a in layers]),
    ]
    current, amount = layers[0], 2.0
    for step in range(4):
        remainder = round(amount * 0.88, 8)
        peel = round(amount - remainder, 8)
        nxt = addr(f"full/peel{step + 1}")
        txs.append(
            tx(f"full/peel{step}", when(minutes=3 + step * 2), [(current, amount)],
               [(nxt, remainder), (addr(f"full/side{step}"), peel)])
        )
        current, amount = nxt, remainder
    for index, layer in enumerate(layers[1:], start=1):
        txs.append(
            tx(f"full/consolidate{index}", when(minutes=4 + index),
               [(layer, 2.0)], [(collector, 1.99)])
        )
    txs.append(
        tx("full/cashout", when(hours=2), [(collector, 9.95)], [(BINANCE_HOT, 9.94)])
    )
    return case(
        "full-laundering-chain",
        "Fan-out, peel chain, burst, consolidation, exchange deposit",
        "The composite case: several patterns on one trail, ending at a real "
        "labelled exchange. Tests that patterns compose rather than "
        "suppressing each other.",
        seed, txs,
        expect_patterns=["fan_out", "peel_chain", "round_split", "fan_in",
                         "timing_burst", "cluster_context"],
        expect_absent=["sanctioned_match"],
        expect_exit_entity="Binance",
    )


BUILDERS = [
    eval_fan_out,
    eval_fan_in,
    eval_peel_chain,
    eval_round_split,
    eval_timing_burst,
    eval_sanctioned,
    eval_fan_out_below,
    eval_fan_out_spread,
    eval_legitimate_consolidation,
    eval_even_split_not_peel,
    eval_clean_pass_through,
    eval_timing_spread,
    eval_single_tx_not_a_burst,
    eval_bridge_deadend,
    eval_unattributed_exit,
    eval_full_laundering,
]


def main() -> int:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    for existing in CASES_DIR.glob("*.json"):
        existing.unlink()

    built = []
    for builder in BUILDERS:
        document = builder()
        path = CASES_DIR / f"{document['slug']}.json"
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        built.append(document)
        expected = document["expected"]["patterns_present"]
        print(
            f"  {document['slug']:<38} "
            f"{len(document['addresses']):>3} addr  "
            f"expect: {', '.join(expected) if expected else '(nothing)'}"
        )

    constructed = sum(1 for d in built if d["ground_truth_source"] == CONSTRUCTED)
    print(
        f"\n{len(built)} labelled case(s) written to {CASES_DIR}\n"
        f"  {constructed} constructed scenario(s), "
        f"{len(built) - constructed} documented real-world case(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
