"""Central configuration.

Every tunable that affects a displayed number lives here, so the value shown in
the UI can always be traced back to a documented constant rather than a magic
number buried in a detector.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = REPO_ROOT / "data"
FIXTURE_DIR = DATA_DIR / "fixtures"
TAGPACK_DIR = DATA_DIR / "tagpacks"
OFAC_DIR = DATA_DIR / "ofac"
CURATED_DIR = DATA_DIR / "curated"
EVALUATION_DIR = DATA_DIR / "evaluation"
REPORT_DIR = DATA_DIR / "reports"

DB_PATH = Path(os.environ.get("FUNDTRAIL_DB", REPO_ROOT / "fundtrail.db"))
SCHEMA_PATH = Path(__file__).resolve().parent / "models" / "schema.sql"

# --- Graph expansion -------------------------------------------------------
DEFAULT_MAX_HOPS = 4
MAX_HOPS_CEILING = 6
# Addresses expanded per hop, highest edge-weight first. Caps combinatorial
# blowup; when it bites, the case is flagged expansion_limited rather than
# silently truncated.
MAX_EXPANSIONS_PER_HOP = 40
MAX_TOTAL_ADDRESSES = 600

# Dust thresholds in native chain units. Transfers at or below these are kept in
# the DB (marked is_dust) but excluded from expansion and pattern detection.
DUST_THRESHOLD = {
    "bitcoin": 0.0005,
    "ethereum": 0.005,
}

# Edge weight = amount_share * time_decay * depth_decay.
# Half-life for the time-decay term, in days: movement from two years ago is
# less actionable than movement from last week (master report §2.2 step 2).
TIME_DECAY_HALFLIFE_DAYS = 180.0
# Confidence decay applied per hop away from the seed address.
HOP_DEPTH_DECAY = 0.85

# --- Upstream providers ----------------------------------------------------
# Token-bucket settings matched to each provider's documented free-tier limit.
# CLAUDE.md rule 5: never fire uncapped requests.
RATE_LIMITS = {
    # 30 req/min free plan; soft 5 req/sec under load.
    "blockchair": {"rate_per_sec": 0.5, "burst": 5},
    # 5 calls/sec free tier, shared across all V2 chains.
    "etherscan": {"rate_per_sec": 5.0, "burst": 5},
    # Free tier, no key required. Kept deliberately conservative.
    "blockscout": {"rate_per_sec": 2.0, "burst": 4},
}

PROVIDER_BASE_URLS = {
    "blockchair": "https://api.blockchair.com",
    "etherscan": "https://api.etherscan.io/v2/api",
    "blockscout": "https://eth.blockscout.com/api/v2",
}

ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
BLOCKCHAIR_API_KEY = os.environ.get("BLOCKCHAIR_API_KEY", "")

CACHE_TTL_SECONDS = 7 * 24 * 3600
UPSTREAM_TIMEOUT_SECONDS = 20.0

# Default adapter mode. "fixture" reads the seeded cache and never touches the
# network — the demo path. "live" attempts an upstream fetch on a cache miss.
ADAPTER_MODE = os.environ.get("FUNDTRAIL_ADAPTER_MODE", "fixture")

# --- Scoring ---------------------------------------------------------------
# Hand-tuned heuristic weights, documented in CLAUDE.md and master report §13.2.
# These are NOT learned or trained, and must never be described as such.
PATTERN_WEIGHTS = {
    "fan_out": 20.0,
    "peel_chain": 20.0,
    "sanctioned_match": 20.0,
    "fan_in": 15.0,
    "round_split": 15.0,
    "cluster_context": 15.0,
    "timing_burst": 12.0,
}

# The weights above total 117, so a case that triggers every signal would exceed
# a 0-100 presentation scale. We cap at 100 and show the cap as an explicit line
# in the Why panel, so the displayed contributions always add up.
SCORE_CAP = 100.0

# Risk bands over the capped score. Documented thresholds, not tuned to a case.
RISK_BANDS = [
    (70.0, "HIGH"),
    (40.0, "MEDIUM"),
    (1.0, "LOW"),
    (0.0, "NONE"),
]

# --- Pattern detection thresholds -----------------------------------------
# Each threshold implements the definition in master report §13.1. They are set
# from those definitions and are never adjusted to make a fixture light up.
FAN_OUT_MIN_RECIPIENTS = 5          # "N >= 5 previously-unseen addresses"
FAN_OUT_WINDOW_SECONDS = 3600

FAN_IN_MIN_SENDERS = 5              # "N >= 5 addresses converge on one"
FAN_IN_WINDOW_SECONDS = 3600

PEEL_CHAIN_MIN_HOPS = 3             # a chain of at least 3 peel steps
PEEL_SMALL_MAX_RATIO = 0.25         # the peeled side output is <=25% of input
PEEL_REMAINDER_MIN_RATIO = 0.60     # the remainder carried forward is >=60%

ROUND_SPLIT_MIN_PARTS = 3
ROUND_SPLIT_EQUALITY_TOLERANCE = 0.05   # parts within 5% of each other
ROUND_SPLIT_WINDOW_SECONDS = 3600

TIMING_BURST_MIN_TXS = 5
TIMING_BURST_WINDOW_SECONDS = 600   # tight window: 5+ txs inside 10 minutes

# --- Contextual disambiguation --------------------------------------------
# CLAUDE.md: fan-in looks identical for laundering consolidation and a
# legitimate exchange deposit. These features do the real work of telling them
# apart, and fan-in is not reported without them.
CLUSTER_NEW_MAX_AGE_DAYS = 30       # cluster first seen within this window is "new"
CLUSTER_DORMANT_MIN_DAYS = 30       # no activity for this long after the burst
HIGH_VOLUME_TX_COUNT = 100          # an address this busy looks like a service
