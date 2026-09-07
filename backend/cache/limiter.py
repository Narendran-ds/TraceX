"""Token-bucket rate limiter.

CLAUDE.md rule 5: all upstream calls go through a limiter matched to the
provider's documented limit. Never fire uncapped requests.

Graph expansion re-queries the same providers constantly, so this is the
difference between a working demo and an IP that gets blocked — which is not
hypothetical here: a keyless Blockchair probe from this machine returned
HTTP 430 "IP address is temporarily blacklisted due to exceeding usage".
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional

from backend.config import RATE_LIMITS


class RateLimitExceeded(Exception):
    """Raised when a token could not be acquired inside the caller's budget.

    Callers translate this into a partial result with an "expansion limited"
    flag — never into a raw 429 on screen.
    """

    def __init__(self, provider: str, waited: float):
        super().__init__(
            f"Rate limit budget for {provider} exhausted after waiting {waited:.1f}s"
        )
        self.provider = provider
        self.waited = waited


class TokenBucket:
    """Classic token bucket: `burst` capacity, refilled at `rate_per_sec`."""

    def __init__(self, rate_per_sec: float, burst: int, clock=time.monotonic):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self.rate_per_sec = float(rate_per_sec)
        self.burst = int(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate_per_sec)
            self._updated = now

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill_locked()
            return self._tokens

    def try_acquire(self, amount: float = 1.0) -> bool:
        """Take a token if one is available right now. Never blocks."""
        with self._lock:
            self._refill_locked()
            if self._tokens >= amount:
                self._tokens -= amount
                return True
            return False

    def time_until_available(self, amount: float = 1.0) -> float:
        with self._lock:
            self._refill_locked()
            deficit = amount - self._tokens
            if deficit <= 0:
                return 0.0
            return deficit / self.rate_per_sec

    def acquire(self, amount: float = 1.0, max_wait: float = 10.0, sleep=time.sleep) -> None:
        """Block until a token is available, up to `max_wait` seconds.

        Raises RateLimitExceeded rather than waiting indefinitely, so a slow
        provider degrades the result instead of hanging the investigation.
        """
        waited = 0.0
        while True:
            if self.try_acquire(amount):
                return
            delay = self.time_until_available(amount)
            if waited + delay > max_wait:
                raise RateLimitExceeded(getattr(self, "name", "provider"), waited)
            # A small floor keeps the loop from spinning on tiny deltas.
            step = max(delay, 0.01)
            sleep(step)
            waited += step


class LimiterRegistry:
    """One bucket per provider, shared process-wide."""

    def __init__(self, limits: Optional[Dict[str, Dict[str, float]]] = None):
        self._limits = limits if limits is not None else RATE_LIMITS
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def bucket(self, provider: str) -> TokenBucket:
        with self._lock:
            if provider not in self._buckets:
                cfg = self._limits.get(provider, {"rate_per_sec": 1.0, "burst": 1})
                bucket = TokenBucket(
                    rate_per_sec=float(cfg["rate_per_sec"]), burst=int(cfg["burst"])
                )
                bucket.name = provider  # type: ignore[attr-defined]
                self._buckets[provider] = bucket
            return self._buckets[provider]

    def acquire(self, provider: str, max_wait: float = 10.0) -> None:
        self.bucket(provider).acquire(max_wait=max_wait)

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            return {name: b.tokens for name, b in self._buckets.items()}


limiters = LimiterRegistry()
