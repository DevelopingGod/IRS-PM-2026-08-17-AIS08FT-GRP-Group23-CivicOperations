"""Solution metrics shared by every method (so comparisons use one definition)."""
from __future__ import annotations

import numpy as np

from .instance import Instance, Solution


def avoidable_inversions(sol: Solution) -> dict:
    """Priority inversions that a single swap could have avoided.

    A pair (i, j) is an avoidable inversion when i is unserved, j is served by crew k, i outranks j
    (rank_i < rank_j), crew k is compatible with i, and replacing j by i at j's position keeps crew
    k within capacity. ``bypassed`` counts unserved requests with at least one such j.
    """
    inst = sol.inst
    a = sol.assigned()
    unserved = np.nonzero(a < 0)[0]
    pairs = 0
    bypassed = 0
    for i in unserved:
        hit = False
        for k in inst.compatible_crews(int(i)):
            k = int(k)
            r = sol.routes[k]
            for p, j in enumerate(r):
                if inst.rank[i] >= inst.rank[j]:
                    continue
                cand = r[:p] + [int(i)] + r[p + 1:]
                if inst.route_stats(k, cand)[3]:
                    pairs += 1
                    hit = True
        bypassed += hit
    return {"avoidable_inversion_pairs": int(pairs), "bypassed_requests": int(bypassed)}


def solution_metrics(sol: Solution, with_inversions: bool = True) -> dict:
    inst = sol.inst
    c = sol.components()
    a = sol.assigned()
    served = a >= 0
    total_w = float(inst.weight.sum())
    cap_total = float(np.sum(inst.cap[[k for k in range(inst.K) if inst.cap[k] > 0]])) if inst.K else 0.0
    starts = []
    for k, r in enumerate(sol.routes):
        for i, _, st, _ in inst.schedule(k, r):
            starts.append((i, st))
    out = {
        "eligible": inst.n, "served": c["served"], "unserved": c["unserved"],
        "served_pct": 100.0 * c["served"] / inst.n if inst.n else 100.0,
        "weighted_served_share": float(inst.weight[served].sum()) / total_w if total_w else 1.0,
        "backlog_weight": c["backlog_weight"],
        "travel_min": c["travel_min"], "service_min": float(inst.service[served].sum()),
        "work_min": c["work_min"], "travel_per_served_min": c["travel_min"] / c["served"] if c["served"] else 0.0,
        "utilisation": c["work_min"] / cap_total if cap_total else 0.0,
        "weighted_start_h": c["weighted_start_h"],
        "mean_start_min_weighted": (sum(inst.weight[i] * st for i, st in starts) / sum(inst.weight[i] for i, _ in starts)) if starts else 0.0,
        "changes": c["changes"], "fitness": sol.fitness(),
        "median_rank_served": float(np.median(inst.rank[served])) if served.any() else None,
        "median_rank_unserved": float(np.median(inst.rank[~served])) if (~served).any() else None,
        "crews_used": int(sum(1 for r in sol.routes if r)),
    }
    if with_inversions:
        out.update(avoidable_inversions(sol))
    return out
