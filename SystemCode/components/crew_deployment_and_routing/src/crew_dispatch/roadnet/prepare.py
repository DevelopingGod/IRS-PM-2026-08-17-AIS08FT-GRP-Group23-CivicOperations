"""Build the enriched local road network and validate routing (``crew-dispatch prepare-network``)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .network import RoadNetwork, build_from_graphml
from .router import Router


def prepare_network(component_root: Path, n_validation_pairs: int = 300, seed: int = 20261019) -> dict:
    root = Path(component_root)
    src = root / "data" / "road_network" / "source"
    out_dir = root / "data" / "road_network" / "derived"
    t0 = time.perf_counter()
    net, report = build_from_graphml(src / "nyc_drive.graphml", src / "boundaries" / "nyc_coverage.gpkg")
    report["build_seconds"] = round(time.perf_counter() - t0, 2)
    net.save(out_dir / "nyc_drive_enriched.npz")
    report["validation"] = validate_routing(net, n_validation_pairs, seed)
    (out_dir / "speed_enrichment_report.json").write_text(json.dumps(report, indent=2))
    return report


def validate_routing(net: RoadNetwork, n_pairs: int, seed: int, speed_factor: float = 1.0) -> dict:
    """Heuristic bound over all edges + A* vs Dijkstra on random node pairs of the main SCC."""
    router = Router(net, speed_factor=speed_factor)
    bound = router.check_heuristic_bound()
    rng = np.random.default_rng(seed)
    pairs = rng.choice(router.scc_nodes, size=(n_pairs, 2))
    t1 = time.perf_counter()
    router.build_landmarks(k=8, seed=seed)
    alt_prep_s = time.perf_counter() - t1
    max_rel, exp_a, exp_d, exp_l, t_a, t_d, t_l, mismatches = 0.0, 0, 0, 0, 0.0, 0.0, 0.0, 0
    for s, t in pairs:
        t1 = time.perf_counter(); a = router.astar(int(s), int(t)); t_a += time.perf_counter() - t1
        t1 = time.perf_counter(); d = router.dijkstra(int(s), int(t)); t_d += time.perf_counter() - t1
        t1 = time.perf_counter(); l = router.astar(int(s), int(t), heuristic="alt"); t_l += time.perf_counter() - t1
        rel = max(abs(a.cost_s - d.cost_s), abs(l.cost_s - d.cost_s)) / max(d.cost_s, 1e-9)
        max_rel = max(max_rel, rel)
        mismatches += rel > 1e-9
        exp_a += a.expanded
        exp_d += d.expanded
        exp_l += l.expanded
    # scipy one-to-many must agree with the point-to-point searches too
    rows = router.rows(pairs[:50, 0])
    sp_rel = max(abs(float(rows[int(s)][int(t)]) - router.dijkstra(int(s), int(t)).cost_s) / max(router.dijkstra(int(s), int(t)).cost_s, 1e-9)
                 for s, t in pairs[:50])
    return {"heuristic_bound": bound, "pairs": int(n_pairs), "seed": seed, "speed_factor": speed_factor,
            "astar_vs_dijkstra_max_rel_diff": max_rel, "astar_vs_dijkstra_mismatches": int(mismatches),
            "scipy_vs_python_dijkstra_max_rel_diff_float32": float(sp_rel),
            "mean_expanded_astar_haversine": exp_a / n_pairs, "mean_expanded_astar_alt": exp_l / n_pairs,
            "mean_expanded_dijkstra": exp_d / n_pairs,
            "expansion_ratio_haversine": exp_a / max(exp_d, 1), "expansion_ratio_alt": exp_l / max(exp_d, 1),
            "mean_ms_astar_haversine": 1000 * t_a / n_pairs, "mean_ms_astar_alt": 1000 * t_l / n_pairs,
            "mean_ms_dijkstra": 1000 * t_d / n_pairs, "alt_landmarks": 8, "alt_preprocess_s": alt_prep_s,
            "scc_nodes": int(len(router.scc_nodes)), "v_max_mps": router.v_max_mps}
