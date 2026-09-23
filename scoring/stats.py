"""Decay, shrinkage, standardization, percentile — the pure numeric core."""
from __future__ import annotations

import math
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def years_between(later: date, earlier: date) -> float:
    return max(0.0, (later - earlier).days / 365.25)


def decay_weight(years_ago: float, lam: float) -> float:
    return math.exp(-lam * years_ago)


def decayed_mean(values: Sequence[Tuple[float, float, float]]) -> Tuple[Optional[float], float]:
    """values: (x, n, years_ago-weight). Returns (weighted mean, effective n = Σ w·n)."""
    num = sum(x * n * w for x, n, w in values)
    n_eff = sum(n * w for _, n, w in values)
    return (num / n_eff if n_eff > 0 else None), n_eff


def shrink(xbar: Optional[float], n_eff: float, mu: float, m: float) -> float:
    """Bayesian shrinkage toward the prior mean mu with prior strength m (pseudo-observations).
    With no data (n_eff = 0 or xbar None) the answer is exactly the prior."""
    if xbar is None or n_eff <= 0:
        return mu
    return (n_eff * xbar + m * mu) / (n_eff + m)


def confidence_from_n(n_eff: float, m: float) -> float:
    """Share of the shrunk estimate that comes from the data rather than the prior: n/(n+m)."""
    if n_eff <= 0:
        return 0.0
    return n_eff / (n_eff + m)


def weighted_mean(pairs: Iterable[Tuple[float, float]]) -> Optional[float]:
    num = den = 0.0
    for x, w in pairs:
        num += x * w; den += w
    return num / den if den > 0 else None


def mean_sd(xs: Sequence[float]) -> Tuple[float, float]:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return mu, math.sqrt(var)


def zscore(x: float, mu: float, sd: float) -> float:
    return (x - mu) / sd if sd > 1e-12 else 0.0


def percentile_rank(x: float, population: Sequence[float]) -> float:
    """Percentile of x within population (inclusive of x), 0..100; midrank for ties."""
    if not population:
        return 50.0
    below = sum(1 for p in population if p < x)
    equal = sum(1 for p in population if p == x)
    return 100.0 * (below + 0.5 * equal) / len(population)


def ols(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float, float]:
    """Simple OLS y = a + b x. Returns (a, b, r2, residual_sd)."""
    n = len(xs)
    if n < 2:
        raise ValueError("need at least two points to fit")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx > 1e-12 else 0.0
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    sd = math.sqrt(ss_res / (n - 2)) if n > 2 else 0.0
    return a, b, r2, sd
