"""Seed the database: schema, attribution sources, and the demo scenarios.

One command, and the system is ready to demo offline:

    py -3.11 scripts/seed.py

What it does, in order:
  1. create (or reset) the SQLite schema,
  2. ingest every attribution source in /data — TagPacks, OFAC SDN, curated,
  3. load every scenario in data/fixtures/ into api_cache,
  4. open a case per scenario, so a judge can pick one and press Investigate
     without touching a terminal.

It does NOT run the pipeline. Scores, risk levels, cluster counts and findings
are all produced by the investigation itself — nothing is pre-baked into the
database, so no number on screen can be a seeded placeholder.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.attribution.ingest import ingest_all  # noqa: E402
from backend.cache import store  # noqa: E402
from backend.config import DB_PATH, FIXTURE_DIR  # noqa: E402
from backend.models.db import (  # noqa: E402
    connect,
    init_db,
    new_id,
    record_audit,
    reset_db,
    utc_now_iso,
)

SCENARIO_PROVENANCE_NOTES = {
    "synthetic_scenario": (
        "This case runs on a constructed scenario, not real on-chain activity. "
        "It exists so the detection engine can be demonstrated offline. The "
        "clustering, pattern detection, attribution matching and scoring applied "
        "to it are the same code that runs on live chain data, and the "
        "attribution sources it matches against are real public sources."
    ),
    "live_cached": (
        "This case runs on real transactions fetched from a public blockchain API "
        "and cached locally. Every transaction hash can be checked on a public "
        "block explorer."
    ),
}


def load_scenarios(conn, verbose: bool = True) -> List[Dict]:
    """Load every scenario file into api_cache. Returns the scenario metadata."""
    loaded: List[Dict] = []
    files = sorted(FIXTURE_DIR.glob("*.json"))
    if not files:
        print(
            f"  no scenario files in {FIXTURE_DIR}\n"
            "  run: py -3.11 scripts/build_scenarios.py"
        )
        return loaded

    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        chain = document["chain"]
        provenance = document.get("provenance", "synthetic_scenario")

        for address, transactions in document["addresses"].items():
            store.put(
                provider="fixture",
                chain=chain,
                endpoint="address_history",
                address=address,
                payload={
                    "address": address,
                    "chain": chain,
                    "origin": provenance,
                    "transactions": transactions,
                },
                origin=provenance,
                conn=conn,
            )

        loaded.append(document)
        if verbose:
            print(
                f"  {path.name:<40} {len(document['addresses']):>3} addresses "
                f"({provenance})"
            )
    return loaded


def open_cases(conn, scenarios: List[Dict], verbose: bool = True) -> int:
    """Open one pending case per scenario so the demo starts on the intake screen."""
    opened = 0
    for scenario in scenarios:
        existing = conn.execute(
            "SELECT id FROM cases WHERE complaint_id = ?",
            (scenario["complaint_id"],),
        ).fetchone()
        if existing:
            continue

        provenance = scenario.get("provenance", "synthetic_scenario")
        case_id = new_id()
        conn.execute(
            "INSERT INTO cases (id, complaint_id, wallet_address, chain, status,"
            " data_provenance, provenance_note, max_hops, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (
                case_id,
                scenario["complaint_id"],
                scenario["seed_address"],
                scenario["chain"],
                provenance,
                SCENARIO_PROVENANCE_NOTES[provenance],
                4,
                utc_now_iso(),
            ),
        )
        record_audit(conn, case_id, "case_created", "seed_script", {
            "scenario": scenario.get("slug"),
            "title": scenario.get("title"),
            "data_provenance": provenance,
        })
        opened += 1
        if verbose:
            print(f"  {scenario['complaint_id']:<20} {scenario['title']}")
    return opened


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true",
        help="delete the database first (default: keep and top up)",
    )
    args = parser.parse_args()

    print(f"Database: {DB_PATH}")
    if args.reset and DB_PATH.exists():
        print("Resetting…")
        reset_db(DB_PATH)
    else:
        init_db(DB_PATH)

    conn = connect()
    try:
        print("\nAttribution sources")
        counts = ingest_all(conn)
        for source, count in counts.items():
            print(f"  {source:<12} {count:>5} tags")
        total_tags = conn.execute(
            "SELECT COUNT(*) FROM attribution_tags"
        ).fetchone()[0]
        entities = conn.execute(
            "SELECT COUNT(DISTINCT entity_name) FROM attribution_tags"
        ).fetchone()[0]
        print(f"  {'stored':<12} {total_tags:>5} tags across {entities} entities")

        print("\nScenarios -> api_cache")
        scenarios = load_scenarios(conn)

        print("\nCases opened (pending — nothing is pre-computed)")
        opened = open_cases(conn, scenarios)
        if opened == 0:
            print("  (all scenario cases already exist)")

        conn.commit()

        cache_total = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        case_total = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        findings = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]

        print(
            f"\nReady. {cache_total} cached address histories, {case_total} cases, "
            f"{findings} findings (findings are produced by the pipeline, not seeded)."
        )
        print("\nNext:")
        print("  py -3.11 -m uvicorn backend.api.main:app --port 8000")
        print("  npm --prefix frontend run dev")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
