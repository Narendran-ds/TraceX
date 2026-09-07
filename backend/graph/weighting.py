"""Edge weighting and dust filtering.

Master report §2.2 step 2: edges are weighted by amount, time-decay (recent
movement is more actionable than movement from two years ago) and hop depth
(confidence decays with distance from the seed address).

    edge_weight = amount x 0.5^(age_days / halflife) x depth_decay^hop

Every term is a documented constant in backend/config.py, so any weight shown in
the UI can be recomputed by hand from the transaction it describes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.config import DUST_THRESHOLD, HOP_DEPTH_DECAY, TIME_DECAY_HALFLIFE_DAYS


def time_decay(timestamp: datetime, now: Optional[datetime] = None) -> float:
    """Exponential decay on transaction age. 1.0 at t=0, 0.5 at one half-life."""
    now = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - timestamp).total_seconds() / 86400.0)
    return 0.5 ** (age_days / TIME_DECAY_HALFLIFE_DAYS)


def depth_decay(hop_depth: int) -> float:
    """Confidence decays with each hop away from the victim-reported address."""
    return HOP_DEPTH_DECAY ** max(0, hop_depth)


def edge_weight(
    amount: float,
    timestamp: datetime,
    hop_depth: int,
    now: Optional[datetime] = None,
) -> float:
    return amount * time_decay(timestamp, now) * depth_decay(hop_depth)


def is_dust(amount: float, chain: str) -> bool:
    """Below-threshold transfers are recorded but not expanded or scored.

    Without this, a graph walk drowns in dust-attack outputs and the depth cap
    gets spent on transfers worth a few rupees.
    """
    return amount <= DUST_THRESHOLD.get(chain, 0.0)
