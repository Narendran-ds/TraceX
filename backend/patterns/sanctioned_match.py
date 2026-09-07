"""Signal — known mixer / sanctioned attribution match.

Weight +20 (CLAUDE.md scoring table: "Known mixer/sanctioned match").

Unlike the five shape detectors, this is a direct attribution hit: the trail
touched an address that a public source has designated. The provenance travels
with the finding, because an attribution claim in a case file that cannot be
traced to a public source is not usable (CLAUDE.md rule 8).
"""
from __future__ import annotations

from typing import Dict, List

from backend.patterns.base import Detection, DetectionContext, short

FLAGGED_ENTITY_TYPES = {"mixer", "sanctioned", "ransomware"}


def detect(context: DetectionContext) -> List[Detection]:
    detections: List[Detection] = []

    for address in sorted(context.graph.wallets):
        matches = [
            t for t in context.tags_for(address)
            if t.entity_type in FLAGGED_ENTITY_TYPES
        ]
        if not matches:
            continue

        # Only report a hit the trail actually reached with value.
        tx_hashes = sorted(
            {
                e.tx_hash
                for e in context.graph.edges
                if not e.dust
                and address in (e.to_address.lower(), e.from_address.lower())
            }
        )
        if not tx_hashes:
            continue

        wallet = context.graph.wallets[address]
        entity_names = sorted({m.entity_name for m in matches})
        entity_types = sorted({m.entity_type for m in matches})

        detections.append(
            Detection(
                pattern_type="sanctioned_match",
                title="Known mixer or sanctioned address match",
                description=(
                    f"The trail reached {short(wallet.address, 14)}, which is listed by "
                    f"{len(matches)} public source(s) as {', '.join(entity_names)} "
                    f"({', '.join(entity_types)}). Each match below carries its source "
                    "link for verification."
                ),
                evidence_tx_hashes=tx_hashes,
                evidence_detail={
                    "address": wallet.address,
                    "hop_depth": wallet.hop_depth,
                    "entity_names": entity_names,
                    "entity_types": entity_types,
                    "amount_received": round(wallet.total_in, 8),
                    "matches": [
                        {
                            "entity_name": m.entity_name,
                            "entity_type": m.entity_type,
                            "source": m.source,
                            "source_url": m.source_url,
                            "confidence": m.confidence,
                            "last_updated": m.last_updated,
                        }
                        for m in matches
                    ],
                },
                subject_addresses=[wallet.address],
            )
        )

    return detections
