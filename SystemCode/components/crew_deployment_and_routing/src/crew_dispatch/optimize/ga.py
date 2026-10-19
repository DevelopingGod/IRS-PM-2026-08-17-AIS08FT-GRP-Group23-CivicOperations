"""Genetic algorithm for rank-aware crew assignment and sequencing (formulation B).

Chromosome  : one ordered stop list per crew; requests in no list form the unserved pool.
Feasibility : preserved by construction. Every operator either removes stops (which never increases
              route work, by the triangle inequality of shortest-path travel times) or inserts a stop
              only where it fits the crew's skills and remaining work-time capacity.
Crossover   : route-based - each crew inherits its route from parent A or B, duplicates are removed,
              then the pool is re-inserted greedily in rank order (``fill_greedy``).
Mutation    : relocate | exchange | 2-opt | ruin-and-recreate (random or spatial) | swap-in of a
              high-weight unserved request in place of low-weight stops.
Selection   : tournament (k), generational replacement with elitism.
Initial pop : rank-first greedy, travel-aware greedy, objective-aware rank-order fill, the repaired
              previous plan (warm start, dynamic mode) and noisy rank-order fills.
Termination : max generations, stall generations, wall-clock safety limit (reported if hit).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import numpy as np

from .greedy import rank_first_greedy, travel_aware_greedy
from .instance import Instance, Solution, best_insertions, fill_greedy, insert


@dataclass
class GAConfig:
    population: int = 40
    generations: int = 150
    stall_generations: int = 30
    time_limit_s: float = 60.0
    tournament_k: int = 3
    elite: int = 2
    crossover_rate: float = 0.9
    mutation_rate: float = 0.7
    polish_every: int = 10
    init_noise_sigma: float = 0.5
    ruin_fraction: float = 0.15

    @classmethod
    def from_dict(cls, d: dict | None) -> "GAConfig":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class GAResult:
    best: Solution
    generations: int
    evaluations: int
    terminated_by: str
    history: list = field(default_factory=list)
    runtime_s: float = 0.0
    seeded_from: list = field(default_factory=list)


def two_opt(sol: Solution, k: int) -> bool:
    """First-improvement 2-opt on one route under the full objective. Returns True if improved."""
    inst = sol.inst
    seq = sol.routes[k]
    if len(seq) < 3:
        return False
    best_cost = sol.stats[k][0] + inst.lambda_delay * sol.stats[k][2]
    improved_any = False
    improved = True
    while improved:
        improved = False
        for i in range(len(seq) - 1):
            for j in range(i + 1, len(seq)):
                cand = seq[:i] + seq[i:j + 1][::-1] + seq[j + 1:]
                tr, wk, wd, ok = inst.route_stats(k, cand)
                c = tr + inst.lambda_delay * wd
                if ok and c < best_cost - 1e-9:
                    seq, best_cost, improved, improved_any = cand, c, True, True
                    break
            if improved:
                break
    if improved_any:
        sol.set_route(k, seq)
    return improved_any


def polish(sol: Solution) -> Solution:
    for k in range(sol.inst.K):
        two_opt(sol, k)
    return sol


class GeneticAlgorithm:
    def __init__(self, inst: Instance, cfg: GAConfig, seed: int):
        self.inst = inst
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.evaluations = 0
        self.rank_order = np.argsort(inst.rank, kind="stable")

    # ------------------------------------------------------------------ helpers
    def noisy_order(self, sigma: float) -> np.ndarray:
        noise = self.np_rng.lognormal(0.0, sigma, self.inst.n) if sigma > 0 else np.ones(self.inst.n)
        return np.argsort(self.inst.rank * noise, kind="stable")

    def fill(self, sol: Solution) -> Solution:
        return fill_greedy(sol, self.rank_order)

    def remove(self, sol: Solution, victims) -> None:
        victims = set(int(v) for v in victims)
        for k, r in enumerate(sol.routes):
            if victims.intersection(r):
                sol.set_route(k, [i for i in r if i not in victims])

    def served(self, sol: Solution) -> list:
        return [i for r in sol.routes for i in r]

    # ------------------------------------------------------------------ operators
    def crossover(self, a: Solution, b: Solution) -> Solution:
        child = Solution(self.inst, [list(a.routes[k]) if self.rng.random() < 0.5 else list(b.routes[k])
                                     for k in range(self.inst.K)])
        seen = set()
        order = list(range(self.inst.K))
        self.rng.shuffle(order)
        for k in order:
            r = child.routes[k]
            kept = [i for i in r if i not in seen]
            seen.update(kept)
            if len(kept) != len(r):
                child.set_route(k, kept)
        return self.fill(child)

    def mut_relocate(self, sol: Solution) -> None:
        served = self.served(sol)
        if not served:
            return
        i = self.rng.choice(served)
        self.remove(sol, [i])
        best = (np.inf, -1, -1)
        for k in self.inst.compatible_crews(i):
            pos, _, dobj, ok = best_insertions(self.inst, sol, int(k), np.asarray([i]))
            if ok[0] and dobj[0] < best[0]:
                best = (dobj[0], int(k), int(pos[0]))
        if best[1] >= 0:
            insert(sol, best[1], best[2], i)

    def mut_exchange(self, sol: Solution) -> None:
        busy = [k for k in range(self.inst.K) if sol.routes[k]]
        if len(busy) < 2:
            return
        a, b = self.rng.sample(busy, 2)
        ia = self.rng.randrange(len(sol.routes[a]))
        ib = self.rng.randrange(len(sol.routes[b]))
        i, j = sol.routes[a][ia], sol.routes[b][ib]
        if not (self.inst.compat[b, i] and self.inst.compat[a, j]):
            return
        ra, rb = list(sol.routes[a]), list(sol.routes[b])
        ra[ia], rb[ib] = j, i
        if self.inst.route_stats(a, ra)[3] and self.inst.route_stats(b, rb)[3]:
            sol.set_route(a, ra)
            sol.set_route(b, rb)

    def mut_two_opt_random(self, sol: Solution) -> None:
        busy = [k for k in range(self.inst.K) if len(sol.routes[k]) >= 3]
        if not busy:
            return
        k = self.rng.choice(busy)
        r = sol.routes[k]
        i, j = sorted(self.rng.sample(range(len(r)), 2))
        cand = r[:i] + r[i:j + 1][::-1] + r[j + 1:]
        if self.inst.route_stats(k, cand)[3]:
            sol.set_route(k, cand)

    def mut_ruin_recreate(self, sol: Solution) -> None:
        served = self.served(sol)
        if not served:
            return
        q = max(1, int(round(self.cfg.ruin_fraction * len(served) * self.rng.random())) + 1)
        if self.rng.random() < 0.5:
            victims = self.rng.sample(served, min(q, len(served)))
        else:   # spatially related removal around a random seed request
            seed = self.rng.choice(served)
            d = self.inst.M[seed, served]
            victims = [served[t] for t in np.argsort(d)[:q]]
        self.remove(sol, victims)
        fill_greedy(sol, self.noisy_order(self.cfg.init_noise_sigma))

    def mut_swap_in(self, sol: Solution) -> None:
        pool = sol.unassigned()
        pool = [u for u in pool if self.inst.compat[:, u].any()]
        if not pool:
            return
        w = self.inst.weight[pool]
        u = pool[int(self.np_rng.choice(len(pool), p=w / w.sum()))]
        crews = [int(k) for k in self.inst.compatible_crews(u) if sol.routes[int(k)]]
        if not crews:
            return
        k = self.rng.choice(crews)
        route = list(sol.routes[k])
        # remove lowest-weight stops (that weigh less than u) until u fits, at most 3 removals
        for _ in range(3):
            lighter = [i for i in route if self.inst.weight[i] < self.inst.weight[u]]
            if not lighter:
                return
            victim = min(lighter, key=lambda i: self.inst.weight[i])
            route.remove(victim)
            sol.set_route(k, route)
            pos, _, _, ok = best_insertions(self.inst, sol, k, np.asarray([u]))
            if ok[0]:
                insert(sol, k, int(pos[0]), u)
                break
        fill_greedy(sol, self.rank_order)

    def mutate(self, sol: Solution) -> Solution:
        ops = (self.mut_relocate, self.mut_exchange, self.mut_two_opt_random, self.mut_ruin_recreate, self.mut_swap_in)
        weights = (0.2, 0.15, 0.15, 0.3, 0.2)
        n_ops = 1 + (self.rng.random() < 0.3)
        for _ in range(n_ops):
            self.rng.choices(ops, weights)[0](sol)
        return self.fill(sol)

    # ------------------------------------------------------------------ main loop
    def initial_population(self, warm_starts=()) -> tuple:
        pop, labels, keys = [], [], set()

        def add(sol, label):
            if sol.key() not in keys and sol.feasible():
                keys.add(sol.key())
                pop.append(sol)
                labels.append(label)

        add(rank_first_greedy(self.inst), "rank_first_greedy")
        add(travel_aware_greedy(self.inst), "travel_aware_greedy")
        add(fill_greedy(Solution(self.inst), self.rank_order), "rank_order_fill")
        for w in warm_starts:
            add(self.fill(w.copy()), "previous_plan")
        tries = 0
        while len(pop) < self.cfg.population and tries < 5 * self.cfg.population:
            tries += 1
            s = fill_greedy(Solution(self.inst), self.noisy_order(self.cfg.init_noise_sigma * (1 + tries % 3)))
            if self.rng.random() < 0.5:
                self.mut_ruin_recreate(s)
            add(s, "noisy_fill")
        while len(pop) < self.cfg.population:      # tiny instances: duplicates are fine
            pop.append(pop[len(pop) % max(1, len(labels))].copy())
        return pop, sorted(set(labels))

    def tournament(self, pop) -> Solution:
        cands = self.rng.sample(pop, min(self.cfg.tournament_k, len(pop)))
        return min(cands, key=lambda s: s.fitness())

    def run(self, warm_starts=()) -> GAResult:
        t0 = time.perf_counter()
        if self.inst.n == 0 or self.inst.K == 0:
            return GAResult(Solution(self.inst), 0, 0, "empty", [], 0.0, [])
        pop, seeded = self.initial_population(warm_starts)
        best = min(pop, key=lambda s: s.fitness()).copy()
        history = [best.fitness()]
        stall, gen, why = 0, 0, "generations"
        for gen in range(1, self.cfg.generations + 1):
            pop.sort(key=lambda s: s.fitness())
            nxt = [p.copy() for p in pop[: self.cfg.elite]]
            while len(nxt) < self.cfg.population:
                a = self.tournament(pop)
                if self.rng.random() < self.cfg.crossover_rate:
                    child = self.crossover(a, self.tournament(pop))
                else:
                    child = a.copy()
                if self.rng.random() < self.cfg.mutation_rate:
                    child = self.mutate(child)
                self.evaluations += 1
                nxt.append(child)
            pop = nxt
            gbest = min(pop, key=lambda s: s.fitness())
            if self.cfg.polish_every and gen % self.cfg.polish_every == 0:
                gbest = polish(gbest.copy())
                pop.append(gbest)
            if gbest.fitness() < best.fitness() - 1e-9:
                best, stall = gbest.copy(), 0
            else:
                stall += 1
            history.append(best.fitness())
            if stall >= self.cfg.stall_generations:
                why = "stall"
                break
            if time.perf_counter() - t0 > self.cfg.time_limit_s:
                why = "time_limit"
                break
        best = polish(best.copy())
        history.append(best.fitness())
        assert best.feasible(), "GA produced an infeasible solution"
        return GAResult(best, gen, self.evaluations, why, history, time.perf_counter() - t0, seeded)


def solve_ga(inst: Instance, cfg: GAConfig, seed: int, warm_starts=()) -> GAResult:
    return GeneticAlgorithm(inst, cfg, seed).run(warm_starts)
