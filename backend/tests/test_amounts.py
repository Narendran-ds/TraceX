"""Regression tests for the round-amount definition.

Both of these pin bugs the evaluation harness found after the detectors were
already "done" and passing their own unit tests — which is the argument for
having the harness at all.

The bug: `is_round_amount` treated any two-decimal figure as round, because its
step list went down to 0.01. That made 1.31 and 0.87 "round numbers", so
round-number splitting fired on ordinary irregular payment activity. It would
have produced false positives in front of judges on any real trace.
"""
from __future__ import annotations

import pytest

from backend.amounts import is_round_amount


@pytest.mark.parametrize(
    "amount",
    [1.0, 2.0, 10.0, 0.5, 1.5, 0.25, 2.5, 25.0, 100.0, 0.1, 0.3],
)
def test_tidy_figures_are_round(amount):
    """The amounts a script or a human deliberately picks."""
    assert is_round_amount(amount) is True


@pytest.mark.parametrize(
    "amount",
    [1.31, 0.87, 1.44, 0.62, 1.09, 0.67, 0.96, 2.98731, 1.99, 0.0473],
)
def test_irregular_amounts_are_not_round(amount):
    """The amounts organic activity produces.

    Every value here was a false positive before the fix.
    """
    assert is_round_amount(amount) is False


def test_zero_and_negative_are_not_round():
    assert is_round_amount(0) is False
    assert is_round_amount(-1.0) is False
    assert is_round_amount(None) is False


def test_a_two_decimal_amount_is_not_automatically_round():
    """The specific regression: 0.01 must not be a step.

    If it were, essentially every transaction on a chain quoted to two decimals
    would read as a round number and the pattern would mean nothing.
    """
    for cents in range(1, 100):
        amount = 1 + cents / 100
        if amount in (1.1, 1.25, 1.5, 1.75, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8, 1.9):
            continue  # genuinely tidy at one decimal or a clean quarter
        assert is_round_amount(amount) is False, f"{amount} was treated as round"


def test_round_split_and_change_detection_share_one_definition():
    """Two copies of this rule would drift, and one of them would be wrong."""
    from backend.clustering import heuristics
    from backend.patterns import round_split

    assert round_split.is_round_amount is is_round_amount
    assert heuristics._is_round is is_round_amount
