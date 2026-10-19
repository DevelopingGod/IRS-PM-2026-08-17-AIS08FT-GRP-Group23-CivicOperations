"""Baseline dispatch heuristics. Same instance, constraints and information as the GA.

rank_first_greedy
    Requests in C11 global order (overall_queue_position ascending). Each request is appended to the
    END of a compatible crew's route (so every crew serves its jobs in rank order) - the crew that can
    START it earliest; ties: smaller added travel, then crew_id. Skipped if no crew can fit it.
travel_aware_greedy
    Repeatedly performs the single feasible insertion (any crew, any position) with the smallest
    added travel per unit of rank weight, (delta_travel + 1e-6) / w_i, until nothing fits.
"""
from __future__ import annotations

import numpy as np

from .instance import Instance, Solution, best_insertions, insert


def rank_first_greedy(inst: Instance) -> Solution:
    sol = Solution(inst)
    order = np.argsort(inst.rank, kind="stable")
    finish = np.zeros(inst.K)        # minutes after avail when the crew finishes its last stop
    last = [inst.origin(k) for k in range(inst.K)]
    for i in order:
        i = int(i)
        best = None
        for k in inst.compatible_crews(i):
            k = int(k)
            if sol.routes[k]:
                dtravel = inst.M[last[k], i] + inst.M[i, inst.depot(k)] - inst.M[last[k], inst.depot(k)]
            else:
                dtravel = inst.M[last[k], i] + inst.M[i, inst.depot(k)]
            if sol.stats[k][1] + dtravel + inst.service[i] > inst.cap[k] + 1e-9:
                continue
            start = inst.avail[k] + finish[k] + inst.M[last[k], i]
            key = (start, dtravel, inst.crew_ids[k])
            if best is None or key < best[0]:
                best = (key, k)
        if best is not None:
            k = best[1]
            finish[k] += inst.M[last[k], i] + inst.service[i]
            last[k] = i
            sol.set_route(k, sol.routes[k] + [i])
    return sol


def travel_aware_greedy(inst: Instance) -> Solution:
    sol = Solution(inst)
    if inst.n == 0 or inst.K == 0:
        return sol
    cand = np.arange(inst.n)
    w = np.maximum(inst.weight, 1e-12)
    ratio = np.full((inst.K, inst.n), np.inf)
    pos = np.zeros((inst.K, inst.n), np.int64)

    def refresh(k, idx):
        p, dtr, _, ok = best_insertions(inst, sol, k, cand[idx])
        pos[k, idx] = p
        ratio[k, idx] = np.where(ok, (dtr + 1e-6) / w[idx], np.inf)

    for k in range(inst.K):
        refresh(k, np.arange(inst.n))
    open_ = np.ones(inst.n, bool)
    while True:
        r = np.where(open_[None, :], ratio, np.inf)
        flat = int(np.argmin(r))
        k, i = divmod(flat, inst.n)
        if not np.isfinite(r[k, i]):
            break
        insert(sol, k, int(pos[k, i]), i)
        open_[i] = False
        ratio[:, i] = np.inf
        idx = np.nonzero(open_)[0]
        if len(idx):
            refresh(k, idx)
    return sol
