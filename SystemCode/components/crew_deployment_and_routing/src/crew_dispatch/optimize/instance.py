"""Optimisation instance (one agency sub-problem), solution encoding and objective.

Unified node index space for the travel-time matrix ``M`` (minutes):
    0 .. n-1          eligible, routable requests of this agency
    n .. n+K-1        crew planning origins (current position after locked work)
    n+K .. n+2K-1     crew depots (route end); columns are zero when return_to_depot is off

Route of crew k:  origin_k -> s_1 -> ... -> s_L -> depot_k   (an empty route costs nothing)
Work_k   = travel_k + sum(service)       must satisfy  Work_k <= cap_k
cap_k    = min(remaining work-time budget, minutes until end of duty)  (computed by the engine)
start_i  = avail_k + time to reach i along the route  (minutes after the planning instant)

Objective (minimise, minutes-equivalent):
    F = lambda_B * sum_{i unserved} w_i + sum_k travel_k + lambda_D * sum_{i served} w_i * start_i / 60
        + lambda_C * #(previously planned requests now moved to another crew or dropped)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Instance:
    agency: str
    req_ids: list
    crew_ids: list
    weight: np.ndarray        # (n,)
    rank: np.ndarray          # (n,) overall_queue_position
    service: np.ndarray       # (n,) minutes
    compat: np.ndarray        # (K, n) bool
    M: np.ndarray             # (n+2K, n+2K) minutes
    avail: np.ndarray         # (K,) minutes after planning instant when crew is free
    cap: np.ndarray           # (K,) usable work minutes
    prev_crew: np.ndarray     # (n,) crew index in previous plan, -1 if none
    lambda_backlog: float = 120.0
    lambda_delay: float = 10.0
    lambda_change: float = 5.0
    meta: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.req_ids)

    @property
    def K(self) -> int:
        return len(self.crew_ids)

    def origin(self, k: int) -> int:
        return self.n + k

    def depot(self, k: int) -> int:
        return self.n + self.K + k

    def compatible_crews(self, i: int) -> np.ndarray:
        return np.nonzero(self.compat[:, i])[0]

    # ------------------------------------------------------------------ route evaluation
    def route_stats(self, k: int, seq) -> tuple:
        """(travel, work, weighted_start_hours, feasible) for crew k serving seq in order."""
        if not seq:
            return 0.0, 0.0, 0.0, True
        M = self.M
        prev = self.origin(k)
        t = 0.0
        travel = 0.0
        wdelay = 0.0
        for i in seq:
            leg = M[prev, i]
            travel += leg
            t += leg
            wdelay += self.weight[i] * (self.avail[k] + t)
            t += self.service[i]
            prev = i
        back = M[prev, self.depot(k)]
        travel += back
        work = t + back
        return travel, work, wdelay / 60.0, work <= self.cap[k] + 1e-9

    def schedule(self, k: int, seq) -> list:
        """Per stop: (request index, travel_in, start, finish) in minutes after the planning instant."""
        out, prev, t = [], self.origin(k), float(self.avail[k])
        for i in seq:
            leg = float(self.M[prev, i])
            t += leg
            out.append((i, leg, t, t + float(self.service[i])))
            t += float(self.service[i])
            prev = i
        return out


class Solution:
    """Explicit route set + implicit unassigned pool, with cached route statistics."""

    __slots__ = ("inst", "routes", "stats", "_fitness")

    def __init__(self, inst: Instance, routes=None):
        self.inst = inst
        self.routes = [list(r) for r in routes] if routes is not None else [[] for _ in range(inst.K)]
        self.stats = [inst.route_stats(k, r) for k, r in enumerate(self.routes)]
        self._fitness = None

    def copy(self) -> "Solution":
        s = Solution.__new__(Solution)
        s.inst, s.routes, s.stats, s._fitness = self.inst, [list(r) for r in self.routes], list(self.stats), self._fitness
        return s

    def set_route(self, k: int, seq) -> None:
        self.routes[k] = list(seq)
        self.stats[k] = self.inst.route_stats(k, self.routes[k])
        self._fitness = None

    def assigned(self) -> np.ndarray:
        a = np.full(self.inst.n, -1, np.int64)
        for k, r in enumerate(self.routes):
            a[r] = k
        return a

    def unassigned(self) -> list:
        a = self.assigned()
        return [int(i) for i in np.nonzero(a < 0)[0]]

    def feasible(self) -> bool:
        a = np.concatenate([np.asarray(r, np.int64) for r in self.routes]) if self.routes else np.zeros(0, np.int64)
        if len(a) != len(set(a.tolist())):
            return False
        for k, r in enumerate(self.routes):
            if r and not self.inst.compat[k, r].all():
                return False
        return all(s[3] for s in self.stats)

    def components(self) -> dict:
        inst = self.inst
        a = self.assigned()
        served = a >= 0
        changed = int(np.sum((inst.prev_crew >= 0) & (a != inst.prev_crew)))
        return {"backlog_weight": float(inst.weight[~served].sum()),
                "travel_min": float(sum(s[0] for s in self.stats)),
                "work_min": float(sum(s[1] for s in self.stats)),
                "weighted_start_h": float(sum(s[2] for s in self.stats)),
                "changes": changed, "served": int(served.sum()), "unserved": int((~served).sum())}

    def fitness(self) -> float:
        if self._fitness is None:
            c = self.components()
            inst = self.inst
            self._fitness = (inst.lambda_backlog * c["backlog_weight"] + c["travel_min"]
                             + inst.lambda_delay * c["weighted_start_h"] + inst.lambda_change * c["changes"])
        return self._fitness

    def key(self) -> tuple:
        return tuple(tuple(r) for r in self.routes)


# ---------------------------------------------------------------------- insertion primitives
def route_arrays(inst: Instance, k: int, seq):
    """Arrays describing the insertion gaps of a route (positions 0..L)."""
    nodes = [inst.origin(k)] + list(seq) + [inst.depot(k)]
    prev = np.asarray(nodes[:-1])
    nxt = np.asarray(nodes[1:])
    base = inst.M[prev, nxt] if seq else np.zeros(1)
    # finish time (relative to avail) of the stop before each gap
    fin = np.zeros(len(seq) + 1)
    t = 0.0
    p = inst.origin(k)
    for j, i in enumerate(seq):
        t += inst.M[p, i] + inst.service[i]
        fin[j + 1] = t
        p = i
    w = inst.weight[list(seq)] if seq else np.zeros(0)
    suffix_w = np.concatenate([np.cumsum(w[::-1])[::-1], [0.0]]) if len(seq) else np.zeros(1)
    return prev, nxt, base, fin, suffix_w


def best_insertions(inst: Instance, sol: Solution, k: int, cands: np.ndarray):
    """Best feasible insertion position of each candidate into route k.

    Returns (positions, delta_travel, delta_objective_without_backlog, feasible) arrays over cands.
    delta_objective = delta_travel + lambda_D * delta weighted start-hours.
    """
    seq = sol.routes[k]
    prev, nxt, base, fin, suffix_w = route_arrays(inst, k, seq)
    C = np.asarray(cands)
    d_in = inst.M[np.ix_(prev, C)]                      # (L+1, |C|)
    d_out = inst.M[np.ix_(C, nxt)].T                    # (L+1, |C|)
    dtravel = d_in + d_out - base[:, None]
    dwork = dtravel + inst.service[C][None, :]
    work = sol.stats[k][1]
    feas = (work + dwork <= inst.cap[k] + 1e-9) & inst.compat[k, C][None, :]
    start_new = inst.avail[k] + fin[:, None] + d_in
    ddelay = (inst.weight[C][None, :] * start_new + dwork * suffix_w[:, None]) / 60.0
    dobj = dtravel + inst.lambda_delay * ddelay
    # keeping a request on its previously planned crew avoids the plan-change penalty
    dobj = dobj - inst.lambda_change * (inst.prev_crew[C] == k)[None, :]
    dobj_masked = np.where(feas, dobj, np.inf)
    pos = np.argmin(dobj_masked, axis=0)
    cols = np.arange(len(C))
    return pos, dtravel[pos, cols], dobj_masked[pos, cols], np.isfinite(dobj_masked[pos, cols])


def insert(sol: Solution, k: int, pos: int, i: int) -> None:
    seq = sol.routes[k]
    sol.set_route(k, seq[:pos] + [int(i)] + seq[pos:])


def fill_greedy(sol: Solution, order, objective_aware: bool = True) -> Solution:
    """Insert unassigned requests, in the given priority order, at their cheapest feasible position.

    Vectorised: one insertion table per crew over all candidates; only the crew that received a
    request is recomputed. With objective_aware=True an insertion is made only if it lowers F
    (backlog saving lambda_B*w_i exceeds the added travel and delay).
    """
    inst = sol.inst
    assigned = sol.assigned()
    cand = np.asarray([int(i) for i in order if assigned[int(i)] < 0], np.int64)
    if len(cand) == 0 or inst.K == 0:
        return sol
    K = inst.K
    pos = np.zeros((K, len(cand)), np.int64)
    dobj = np.full((K, len(cand)), np.inf)
    for k in range(K):
        if inst.compat[k, cand].any():
            p, _, d, ok = best_insertions(inst, sol, k, cand)
            pos[k], dobj[k] = p, np.where(ok, d, np.inf)
    done = np.zeros(len(cand), bool)
    for idx in range(len(cand)):
        k = int(np.argmin(dobj[:, idx]))
        best = dobj[k, idx]
        i = int(cand[idx])
        if not np.isfinite(best) or (objective_aware and best >= inst.lambda_backlog * inst.weight[i]):
            continue
        insert(sol, k, int(pos[k, idx]), i)
        done[idx] = True
        rest = np.nonzero(~done)[0]
        rest = rest[rest > idx]
        if len(rest):
            p, _, d, ok = best_insertions(inst, sol, k, cand[rest])
            pos[k, rest], dobj[k, rest] = p, np.where(ok, d, np.inf)
    return sol
