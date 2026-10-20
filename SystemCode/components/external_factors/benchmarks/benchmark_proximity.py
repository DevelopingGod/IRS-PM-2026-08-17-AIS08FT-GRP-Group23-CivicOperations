"""Performance and correctness benchmarks for the proximity engine (real OSM cache).

Measures cold-load time and memory, single-request latency, batch throughput, STRtree versus
brute-force search (speed and identical results), candidate pruning, and the error of
projected EPSG:32118 distances relative to WGS84 geodesic distances.
Results: outputs/benchmarks/benchmark_results.json (+ chart).

Usage: python benchmarks/benchmark_proximity.py [--single 2000] [--brute 500]
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

OUT = ROOT / "outputs" / "benchmarks"


def rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / 2**20


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", type=int, default=2000)
    ap.add_argument("--brute", type=int, default=500)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    res: dict = {"machine": {"python": sys.version.split()[0], "platform": platform.platform(),
                             "processor": platform.processor(), "logical_cpus": os.cpu_count(),
                             "ram_gb": round(psutil.virtual_memory().total / 2**30, 1)},
                 "note": "Single-process, single-thread wall-clock timings on the development machine."}

    gc.collect()
    m0 = rss_mb()
    import civicops_external_factors  # noqa: F401  (import cost counted separately)
    m_import = rss_mb()
    from civicops_external_factors import CriticalFacilityEvaluator
    t0 = time.perf_counter()
    ev = CriticalFacilityEvaluator.from_config(ROOT / "config" / "proximity_rules.yaml")
    load_s = time.perf_counter() - t0
    m1 = rss_mb()
    res["load"] = {"evaluator_load_seconds": round(load_s, 3), "rss_before_import_mb": round(m0, 1),
                   "rss_after_import_mb": round(m_import, 1), "rss_after_load_mb": round(m1, 1),
                   "evaluator_incremental_mb": round(m1 - m_import, 1),
                   "facilities_indexed": int(len(ev.engine.facilities)),
                   "facility_cache_bytes_on_disk": (ROOT / "data/osm/facilities/critical_facilities.parquet").stat().st_size}

    from common import load_requests
    from holdout_evaluation import to_contexts
    df = load_requests()
    ctx = to_contexts(df)
    valid_ctx = [c for c in ctx if c["geolocation_status"] == "RESOLVED"]
    rng = np.random.default_rng(7)
    sample = [valid_ctx[i] for i in rng.choice(len(valid_ctx), size=min(args.single, len(valid_ctx)), replace=False)]

    for c in sample[:50]:  # warm-up
        ev.evaluate(c)
    lat = []
    for c in sample:
        t = time.perf_counter()
        ev.evaluate(c)
        lat.append((time.perf_counter() - t) * 1000)
    lat = np.array(lat)
    res["single_request_latency_ms"] = {"n": int(len(lat)), "mean": round(float(lat.mean()), 3),
                                        "p50": round(float(np.percentile(lat, 50)), 3),
                                        "p95": round(float(np.percentile(lat, 95)), 3),
                                        "p99": round(float(np.percentile(lat, 99)), 3),
                                        "max": round(float(lat.max()), 3)}
    batch = {}
    for n in (100, 1000, 10000, len(ctx)):
        sub = ctx[:n]
        t = time.perf_counter()
        ev.evaluate_batch(sub)
        s = time.perf_counter() - t
        batch[str(n)] = {"seconds": round(s, 3), "requests_per_second": round(n / s, 1)}
    res["batch_throughput"] = batch
    m2 = rss_mb()
    res["load"]["rss_after_full_batch_mb"] = round(m2, 1)

    # STRtree vs brute force
    eng = ev.engine
    bsample = sample[: args.brute]
    pts = eng.project([c["latitude"] for c in bsample], [c["longitude"] for c in bsample])
    cats = list(ev.ruleset.category_rules)
    t = time.perf_counter()
    tree_d = {c: eng.nearest(pts, c)[1] for c in cats}
    tree_s = time.perf_counter() - t
    t = time.perf_counter()
    brute_d = {c: np.array([eng.brute_force_distances(p, c).min() for p in pts]) for c in cats}
    brute_s = time.perf_counter() - t
    max_diff = max(float(np.max(np.abs(tree_d[c] - brute_d[c]))) for c in cats)
    # candidate pruning for the threshold query
    thr = ev.ruleset.max_threshold_m
    cand = 0
    for c in cats:
        p_idx, _, _ = eng.within(pts, c, thr)
        cand += len(p_idx)
    total = sum(len(eng.indexes[c].geoms) for c in cats)
    res["spatial_index"] = {"points": len(pts), "categories": len(cats),
                            "strtree_nearest_seconds": round(tree_s, 4), "brute_force_seconds": round(brute_s, 4),
                            "speedup_x": round(brute_s / max(tree_s, 1e-9), 1),
                            "max_abs_distance_difference_m": max_diff,
                            "results_identical": max_diff < 1e-6,
                            "facilities_per_point_brute_force": int(total),
                            "mean_candidates_within_threshold_per_point": round(cand / len(pts), 2)}

    # projected vs geodesic distance
    errs, dists = [], []
    for k, p in enumerate(pts):
        for c in cats:
            rows, d = eng.nearest(np.array([p]), c)
            if rows[0] >= 0 and d[0] > 1:
                g = eng.geodesic_distance_m(p, int(rows[0]))
                errs.append(d[0] - g)
                dists.append(d[0])
    errs, dists = np.array(errs), np.array(dists)
    rel = np.abs(errs) / dists
    res["distance_correctness"] = {
        "pairs": int(len(errs)), "method": "planar EPSG:32118 vs pyproj.Geod WGS84 between the same nearest points",
        "max_abs_error_m": round(float(np.abs(errs).max()), 4), "mean_abs_error_m": round(float(np.abs(errs).mean()), 4),
        "max_relative_error_pct": round(float(100 * rel.max()), 5), "mean_relative_error_pct": round(float(100 * rel.mean()), 5),
        "max_abs_error_within_250m_m": round(float(np.abs(errs[dists <= 250]).max()), 4) if (dists <= 250).any() else None,
        "naive_degree_subtraction_error_example": None}
    # naive comparison: treating degrees as Euclidean with 111,320 m/deg ignores longitude convergence
    a, b = (40.75, -73.99), (40.75, -73.98)
    naive = 111320 * abs(a[1] - b[1])
    from pyproj import Geod
    true = Geod(ellps="WGS84").inv(a[1], a[0], b[1], b[0])[2]
    res["distance_correctness"]["naive_degree_subtraction_error_example"] = {
        "segment": "0.01 deg of longitude at 40.75N", "naive_m": round(naive, 1), "geodesic_m": round(true, 1),
        "naive_error_pct": round(100 * (naive - true) / true, 1)}

    (OUT / "benchmark_results.json").write_text(json.dumps(res, indent=2), "utf-8")
    pd.DataFrame(lat, columns=["latency_ms"]).to_csv(OUT / "single_latency_samples.csv", index=False)

    from visualization import style
    style.apply()
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.6))
    axes[0].hist(lat, bins=60, color=style.SERIES[0], edgecolor=style.SURFACE, linewidth=0.6)
    for q, lab in ((50, "p50"), (95, "p95")):
        v = np.percentile(lat, q)
        axes[0].axvline(v, color=style.INK_2, lw=0.9, ls=(0, (3, 2)))
        axes[0].text(v, axes[0].get_ylim()[1] * 0.92, f" {lab} {v:.2f} ms", fontsize=8, color=style.INK_2)
    axes[0].set_xlabel("Single-request latency (ms)")
    axes[0].set_ylabel("Requests")
    axes[0].set_title("Single-request latency")
    axes[0].grid(axis="x", visible=False)
    ns = [int(k) for k in batch]
    axes[1].plot(ns, [batch[str(n)]["requests_per_second"] for n in ns], color=style.SERIES[0], marker="o", ms=5)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("Batch size (requests)")
    axes[1].set_ylabel("Requests per second")
    axes[1].set_title("Batch throughput")
    fig.tight_layout()
    style.save(fig, ROOT / "outputs" / "charts" / "benchmark_latency_throughput.png")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
