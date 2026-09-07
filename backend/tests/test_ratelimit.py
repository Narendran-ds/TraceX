"""S1 gate: the token bucket actually caps request rate.

CLAUDE.md rule 5. These use an injected clock so the tests are deterministic and
do not spend real seconds sleeping.
"""
from __future__ import annotations

import pytest

from backend.cache.limiter import LimiterRegistry, RateLimitExceeded, TokenBucket
from backend.config import RATE_LIMITS


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_burst_is_capped():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=1.0, burst=3, clock=clock)
    assert [bucket.try_acquire() for _ in range(3)] == [True, True, True]
    assert bucket.try_acquire() is False, "bucket allowed more than its burst"


def test_tokens_refill_at_the_configured_rate():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=2.0, burst=2, clock=clock)
    bucket.try_acquire()
    bucket.try_acquire()
    assert bucket.try_acquire() is False

    clock.advance(0.5)          # 0.5s at 2/sec == 1 token
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


def test_refill_never_exceeds_burst():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=10.0, burst=2, clock=clock)
    clock.advance(1000.0)
    assert bucket.tokens == pytest.approx(2.0)


def test_time_until_available_is_reported():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=2.0, burst=1, clock=clock)
    bucket.try_acquire()
    assert bucket.time_until_available() == pytest.approx(0.5)


def test_acquire_waits_then_succeeds():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=4.0, burst=1, clock=clock)
    bucket.try_acquire()

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    bucket.acquire(max_wait=5.0, sleep=fake_sleep)
    assert slept, "acquire returned without waiting for a token"
    assert sum(slept) == pytest.approx(0.25)


def test_acquire_raises_rather_than_waiting_forever():
    """A stalled provider degrades the result; it does not hang the investigation."""
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=0.01, burst=1, clock=clock)
    bucket.name = "slowprovider"
    bucket.try_acquire()

    with pytest.raises(RateLimitExceeded) as exc:
        bucket.acquire(max_wait=1.0, sleep=lambda s: clock.advance(s))
    assert exc.value.provider == "slowprovider"


def test_registry_returns_one_bucket_per_provider():
    reg = LimiterRegistry({"a": {"rate_per_sec": 1.0, "burst": 1}})
    assert reg.bucket("a") is reg.bucket("a")
    assert reg.bucket("a") is not reg.bucket("b")


def test_configured_limits_match_documented_provider_limits():
    """The limiter is matched to each provider's published free-tier limit."""
    # Blockchair: 30 requests/min == 0.5/sec.
    assert RATE_LIMITS["blockchair"]["rate_per_sec"] == pytest.approx(0.5)
    # Etherscan free tier: 5 calls/sec.
    assert RATE_LIMITS["etherscan"]["rate_per_sec"] == pytest.approx(5.0)
    # Blockscout has no published hard number; we stay deliberately conservative.
    assert RATE_LIMITS["blockscout"]["rate_per_sec"] <= 5.0


def test_bucket_rejects_nonsense_configuration():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_sec=0, burst=1)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_sec=1, burst=0)
