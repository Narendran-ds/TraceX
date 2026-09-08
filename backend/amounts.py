"""Amount shape helpers.

A leaf module on purpose: both the round-split detector and the change-address
heuristic need the same notion of "a round number", and two copies of that
definition would drift. Importing from either package into the other would
create a cycle, so the definition lives here and both import it.
"""
from __future__ import annotations

# Steps a human or a script actually picks when choosing an amount. Deliberately
# stops at 0.1: including 0.01 would make *any* two-decimal figure "round", which
# is most ordinary transactions — 1.31 and 0.87 are not round numbers, and a
# detector that says they are will fire on normal payment activity.
ROUND_STEPS = (100.0, 50.0, 25.0, 10.0, 5.0, 1.0, 0.5, 0.25, 0.1)

_TOLERANCE = 1e-9


def is_round_amount(amount: float) -> bool:
    """True when the amount is a tidy figure at its own magnitude.

    Round: 2.0, 0.5, 0.25, 10, 1.5.
    Not round: 1.31, 0.87, 2.98731 — the awkward figures organic activity
    produces and a script does not.
    """
    if amount is None or amount <= 0:
        return False

    for step in ROUND_STEPS:
        if step > amount:
            continue
        quotient = amount / step
        nearest = round(quotient)
        if nearest >= 1 and abs(quotient - nearest) < _TOLERANCE:
            return True
    return False
