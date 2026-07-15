from __future__ import annotations

import math
from collections.abc import Sequence


def binary_kelly(win_probability: float, net_odds: float) -> float:
    """Closed-form Kelly fraction for a binary bet risking 1 to win net_odds."""
    if net_odds <= 0:
        raise ValueError("net_odds must be positive")
    p = min(max(win_probability, 0.0), 1.0)
    q = 1.0 - p
    return max(0.0, (net_odds * p - q) / net_odds)


def continuous_kelly(mean_return: float, variance: float) -> float:
    """Mean/variance Kelly approximation for small approximately normal returns."""
    if variance <= 0:
        return 0.0
    return max(0.0, mean_return / variance)


def _growth(f: float, outcomes: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for probability, multiple in outcomes:
        value = 1.0 + f * multiple
        if value <= 0:
            return -math.inf
        total += probability * math.log(value)
    return total


def generalized_kelly(outcomes: Sequence[tuple[float, float]], tolerance: float = 1e-9) -> float:
    """Maximize E[log(1 + fX)] over a discrete outcome distribution.

    Outcomes are (probability, return_multiple_on_fraction). For example, an even-money
    binary bet is [(p, 1), (1-p, -1)].
    """
    if not outcomes:
        raise ValueError("outcomes are required")
    probability_sum = sum(p for p, _ in outcomes)
    if probability_sum <= 0:
        raise ValueError("probabilities must sum to a positive value")
    normalized = [(p / probability_sum, x) for p, x in outcomes]
    losses = [x for _, x in normalized if x < 0]
    upper = min(1.0, min((-1.0 / x) for x in losses) - 1e-12) if losses else 1.0
    if upper <= 0 or _growth(0.0, normalized) >= _growth(tolerance, normalized):
        return 0.0
    lo, hi = 0.0, upper
    gr = (math.sqrt(5) - 1) / 2
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)
    while hi - lo > tolerance:
        if _growth(c, normalized) < _growth(d, normalized):
            lo = c
            c = d
            d = lo + gr * (hi - lo)
        else:
            hi = d
            d = c
            c = hi - gr * (hi - lo)
    return max(0.0, min(upper, (lo + hi) / 2))
