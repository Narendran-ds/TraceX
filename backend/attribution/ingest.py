"""Attribution data ingestion.

Three sources, each parsed in its real published format so the same code works
against a full download:

  * GraphSense TagPacks — YAML with per-tag provenance. The TagPack spec requires
    that attribution tags originate from public sources and carry a
    dereferenceable pointer to that source; we preserve `source` per row rather
    than collapsing it to the pack name.
  * OFAC SDN — the Treasury's sdn.csv, where digital currency addresses appear
    inside the remarks field as "Digital Currency Address - ETH 0x...".
  * A hand-curated list — same YAML shape as a TagPack, for entries verified by
    hand from public sources.

CLAUDE.md rule 8: every row carries `source` and `source_url`, both NOT NULL in
the schema. A tag we cannot point at a public source for does not get stored —
`ingest_tagpack` raises rather than inserting a provenance-free claim.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from backend.config import CURATED_DIR, OFAC_DIR, TAGPACK_DIR
from backend.models.db import utc_now_iso

# GraphSense category -> our entity_type vocabulary.
CATEGORY_MAP = {
    "exchange": "exchange",
    "mixing_service": "mixer",
    "mixer": "mixer",
    "tumbler": "mixer",
    "sanctioned": "sanctioned",
    "ransomware": "ransomware",
    "bridge": "bridge",
    "service": "service",
    "wallet_service": "service",
    "payment_processor": "service",
    "hosted_wallet": "service",
}

CURRENCY_TO_CHAIN = {
    "BTC": "bitcoin",
    "XBT": "bitcoin",
    "ETH": "ethereum",
    "ETHEREUM": "ethereum",
    "BITCOIN": "bitcoin",
}

# "Digital Currency Address - ETH 0xabc..." as it appears in OFAC remarks.
OFAC_ADDRESS_RE = re.compile(
    r"Digital Currency Address\s*-\s*([A-Z0-9]+)\s+([a-zA-Z0-9]+)"
)

OFAC_SOURCE_URL = (
    "https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists"
)


@dataclass
class Tag:
    address: str
    chain: str
    entity_name: str
    entity_type: str
    source: str
    source_url: str
    confidence: float
    last_updated: str


class MissingProvenance(ValueError):
    """Raised when a tag has no dereferenceable source. It is not stored."""


def _normalise_chain(value: Optional[str], default: str = "ethereum") -> str:
    if not value:
        return default
    return CURRENCY_TO_CHAIN.get(str(value).upper(), str(value).lower())


def parse_tagpack(path: Path) -> List[Tag]:
    """Parse one GraphSense-style TagPack YAML file.

    Pack-level `source`, `lastmod`, `currency` and `confidence` act as defaults;
    a tag may override any of them.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    pack_source_url = document.get("source", "")
    pack_source = document.get("source_name") or document.get("creator") or "graphsense_tagpack"
    pack_currency = document.get("currency")
    pack_updated = str(document.get("lastmod") or utc_now_iso())
    pack_confidence = float(document.get("confidence", 0.8))

    tags: List[Tag] = []
    for entry in document.get("tags", []) or []:
        address = (entry.get("address") or "").strip()
        if not address:
            continue

        source_url = entry.get("source") or pack_source_url
        if not source_url:
            raise MissingProvenance(
                f"Tag for {address} in {path.name} has no source URL. An attribution "
                "claim without a dereferenceable public source is not stored."
            )

        raw_category = (entry.get("category") or entry.get("entity_type") or "service").lower()
        entity_type = CATEGORY_MAP.get(raw_category, "service")

        tags.append(
            Tag(
                address=address,
                chain=_normalise_chain(entry.get("currency") or pack_currency),
                entity_name=entry.get("label") or entry.get("actor") or "Unnamed entity",
                entity_type=entity_type,
                source=entry.get("source_name") or pack_source,
                source_url=source_url,
                confidence=float(entry.get("confidence", pack_confidence)),
                last_updated=str(entry.get("lastmod") or pack_updated),
            )
        )
    return tags


def parse_ofac_sdn_csv(path: Path) -> List[Tag]:
    """Extract digital currency addresses from an OFAC sdn.csv.

    The real file has no header row and holds the entity name in column 2 and
    free-text remarks in the last column; addresses live inside the remarks.
    """
    tags: List[Tag] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            if row[0].strip().lower() in ("ent_num", "id"):
                continue  # a header, if this export has one
            entity_name = row[1].strip().strip('"')
            remarks = " ".join(cell for cell in row[2:])
            for currency, address in OFAC_ADDRESS_RE.findall(remarks):
                chain = CURRENCY_TO_CHAIN.get(currency.upper())
                if not chain:
                    continue  # a chain this prototype does not trace
                tags.append(
                    Tag(
                        address=address,
                        chain=chain,
                        entity_name=entity_name,
                        entity_type="sanctioned",
                        source="ofac_sdn",
                        source_url=OFAC_SOURCE_URL,
                        # A government designation is the highest-confidence
                        # attribution available to us.
                        confidence=1.0,
                        last_updated=utc_now_iso(),
                    )
                )
    return tags


def upsert_tags(conn: sqlite3.Connection, tags: Iterable[Tag]) -> int:
    count = 0
    for tag in tags:
        if not tag.source or not tag.source_url:
            raise MissingProvenance(
                f"Refusing to store a tag for {tag.address} without provenance."
            )
        conn.execute(
            "INSERT INTO attribution_tags (address, chain, entity_name, entity_type,"
            " source, source_url, confidence, last_updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(address, chain, source) DO UPDATE SET"
            " entity_name=excluded.entity_name, entity_type=excluded.entity_type,"
            " source_url=excluded.source_url, confidence=excluded.confidence,"
            " last_updated=excluded.last_updated",
            (
                tag.address.lower(),
                tag.chain,
                tag.entity_name,
                tag.entity_type,
                tag.source,
                tag.source_url,
                tag.confidence,
                tag.last_updated,
            ),
        )
        count += 1
    return count


def ingest_all(conn: sqlite3.Connection) -> Dict[str, int]:
    """Load every attribution source shipped in /data. Returns per-source counts."""
    counts: Dict[str, int] = {}

    for directory, label in ((TAGPACK_DIR, "tagpacks"), (CURATED_DIR, "curated")):
        total = 0
        if directory.exists():
            for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
                total += upsert_tags(conn, parse_tagpack(path))
        counts[label] = total

    ofac_total = 0
    if OFAC_DIR.exists():
        for path in sorted(OFAC_DIR.glob("*.csv")):
            ofac_total += upsert_tags(conn, parse_ofac_sdn_csv(path))
    counts["ofac"] = ofac_total

    return counts


def coverage(conn: sqlite3.Connection) -> Dict[str, object]:
    """Counted from the database. Never implies exhaustive coverage."""
    tag_count = conn.execute("SELECT COUNT(*) FROM attribution_tags").fetchone()[0]
    entity_count = conn.execute(
        "SELECT COUNT(DISTINCT entity_name) FROM attribution_tags"
    ).fetchone()[0]
    sources = [
        r["source"]
        for r in conn.execute(
            "SELECT DISTINCT source FROM attribution_tags ORDER BY source"
        )
    ]
    return {
        "tag_count": tag_count,
        "entity_count": entity_count,
        "source_count": len(sources),
        "sources": sources,
        "statement": (
            f"Matched against {len(sources)} attribution source(s) covering "
            f"{entity_count} known entities across {tag_count} tagged addresses. "
            "Coverage of public attribution data is partial by nature — an "
            "unmatched cluster means 'not in these sources', not 'not an exchange'."
        ),
    }
