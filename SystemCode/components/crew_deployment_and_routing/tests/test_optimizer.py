import numpy as np
import pytest

from crew_dispatch.optimize.exact import solve_exact
from crew_dispatch.optimize.ga import GAConfig, solve_ga
from crew_dispatch.optimize.greedy import rank_first_greedy, travel_aware_greedy
from crew_dispatch.optimize.instance import Instance, Solution, fill_greedy
from crew_dispatch.optimize.weights import TRANSFORMS, rank_weights


def random_instance(n=7, K=2, seed=0, cap=120.0, lam_b=120.0):
    rng = np.random.default_rng(seed)
    pts = rng.uniform(0, 10, size=(n + 2 * K, 2))
    pts[n + K:] = pts[n:n + K]                       # depot = origin
    M = np.hypot(*(pts[:, None, :] - pts[None, :, :]).transpose(2, 0, 1)) * 3.0
    ranks = np.arange(1, n + 1, dtype=float)
    return Instance(agency="X", req_ids=[f"r{i}" for i in range(n)], crew_ids=[f"c{k}" for k in range(K)],
                    weight=rank_weights(ranks, "reciprocal", 5), rank=ranks, service=rng.uniform(10, 40, n),
                    compat=rng.uniform(size=(K, n)) < 0.85, M=M, avail=np.zeros(K), cap=np.full(K, cap),
                    prev_crew=np.full(n, -1), lambda_backlog=lam_b)


@pytest.mark.parametrize("tr", TRANSFORMS)
def test_weights_monotone(tr):
    w = rank_weights(np.arange(1, 200), tr, 25, 199)
    assert w[0] == pytest.approx(1.0) and np.all(np.diff(w) < 0) and np.all(w > 0)


def test_weights_reject_invalid():
    with pytest.raises(ValueError):
        rank_weights([0, 1], "reciprocal", 25)


@pytest.mark.parametrize("seed", range(6))
def test_all_methods_feasible(seed):
    inst = random_instance(n=25, K=3, seed=seed, cap=150)
    for sol in (rank_first_greedy(inst), travel_aware_greedy(inst), fill_greedy(Solution(inst), np.argsort(inst.rank)),
                solve_ga(inst, GAConfig(population=16, generations=25), seed).best):
        assert sol.feasible()


@pytest.mark.parametrize("seed", range(8))
def test_ga_close_to_exact_on_tiny_instances(seed):
    inst = random_instance(n=6, K=2, seed=seed, cap=90)
    opt = solve_exact(inst).fitness()
    ga = solve_ga(inst, GAConfig(population=30, generations=60), seed).best.fitness()
    assert ga >= opt - 1e-6
    assert ga <= opt * 1.05 + 1e-6


def test_rank_first_serves_in_rank_order_per_crew():
    inst = random_instance(n=20, K=3, seed=11, cap=200)
    sol = rank_first_greedy(inst)
    for r in sol.routes:
        assert list(inst.rank[r]) == sorted(inst.rank[r])


def test_change_penalty_counts_moved_requests():
    inst = random_instance(n=6, K=2, seed=2, cap=500)
    inst.compat[:] = True
    inst.prev_crew = np.array([0, 0, 1, 1, -1, -1])
    s = Solution(inst, [[0, 1], [2, 3]])
    assert s.components()["changes"] == 0
    s2 = Solution(inst, [[2, 1], [0]])
    assert s2.components()["changes"] == 3          # 0 and 2 swapped crews, 3 dropped


def test_ga_reproducible():
    inst = random_instance(n=20, K=3, seed=4, cap=150)
    a = solve_ga(inst, GAConfig(population=16, generations=20), 9).best
    b = solve_ga(inst, GAConfig(population=16, generations=20), 9).best
    assert a.key() == b.key()
