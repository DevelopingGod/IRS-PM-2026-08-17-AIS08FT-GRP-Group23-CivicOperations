"""Exhaustive optimum for tiny instances (optimality-gap check of the GA and baselines).

Enumerates every assignment of each request to {unserved} U {compatible crews} and every ordering of
each crew's stops. Complexity ~ (K+1)^n * prod(|route|!), so it is restricted to n <= 8.
"""
from __future__ import annotations

import itertools

from .instance import Instance, Solution

MAX_EXACT_REQUESTS = 8


def solve_exact(inst: Instance) -> Solution:
    if inst.n > MAX_EXACT_REQUESTS:
        raise ValueError(f"exact solver limited to n <= {MAX_EXACT_REQUESTS}")
    options = [[-1] + [int(k) for k in inst.compatible_crews(i)] for i in range(inst.n)]
    best, best_f = Solution(inst), Solution(inst).fitness()
    route_cache: dict = {}

    def best_order(k, members):
        key = (k, members)
        if key not in route_cache:
            top = None
            for perm in itertools.permutations(members):
                tr, wk, wd, ok = inst.route_stats(k, list(perm))
                if ok:
                    c = tr + inst.lambda_delay * wd
                    if top is None or c < top[0]:
                        top = (c, list(perm))
            route_cache[key] = top
        return route_cache[key]

    for assign in itertools.product(*options):
        routes = []
        ok = True
        for k in range(inst.K):
            members = tuple(i for i, a in enumerate(assign) if a == k)
            if not members:
                routes.append([])
                continue
            top = best_order(k, members)
            if top is None:
                ok = False
                break
            routes.append(top[1])
        if not ok:
            continue
        s = Solution(inst, routes)
        if s.fitness() < best_f - 1e-9:
            best, best_f = s, s.fitness()
    return best
