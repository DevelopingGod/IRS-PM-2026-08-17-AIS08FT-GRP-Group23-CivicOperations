"""Rank-to-weight transforms (a Dispatch modelling assumption, NOT a C11 output).

C11's ``overall_queue_position`` r = 1, 2, 3, ... is ordinal. Dispatch converts it to a positive,
strictly decreasing weight w(r) so the optimiser can trade off rank against travel. All transforms
give w(1) = 1. The choice and its parameter are selected on validation data and reported with a
sensitivity analysis (research/experiments.md).
"""
from __future__ import annotations

import math

import numpy as np

TRANSFORMS = ("reciprocal", "exponential", "linear", "log")


def rank_weights(ranks, transform: str = "reciprocal", param: float = 25.0, n_ranked: int | None = None) -> np.ndarray:
    """Map ranks (1 = highest priority) to weights in (0, 1].

    reciprocal : w = p / (p + r - 1)            (p = rank at which weight halves, minus 1)
    exponential: w = exp(-(r - 1) / p)
    linear     : w = 1 - (r - 1) / (N + 1)      (N = number of ranked active requests; floor > 0)
    log        : w = 1 / log2(r + 1)            (DCG-style discount; param unused)
    """
    r = np.asarray(ranks, dtype=float)
    if np.any(~np.isfinite(r)) or np.any(r < 1):
        raise ValueError("ranks must be finite and >= 1")
    if transform == "reciprocal":
        return param / (param + r - 1)
    if transform == "exponential":
        return np.exp(-(r - 1) / param)
    if transform == "linear":
        n = float(n_ranked if n_ranked is not None else r.max())
        return 1 - (r - 1) / (n + 1)
    if transform == "log":
        return 1 / np.log2(r + 1)
    raise ValueError(f"unknown transform {transform!r}; choose from {TRANSFORMS}")


def describe(transform: str, param: float) -> str:
    return {"reciprocal": f"w(r) = {param:g}/({param:g}+r-1)",
            "exponential": f"w(r) = exp(-(r-1)/{param:g})",
            "linear": "w(r) = 1-(r-1)/(N+1)",
            "log": "w(r) = 1/log2(r+1)"}[transform]


def half_weight_rank(transform: str, param: float, n_ranked: int = 1000) -> float:
    """Rank at which the weight falls to 0.5 (for documentation)."""
    if transform == "reciprocal":
        return param + 1
    if transform == "exponential":
        return 1 + param * math.log(2)
    if transform == "linear":
        return 1 + 0.5 * (n_ranked + 1)
    return 3.0
