"""Phase 7 - reproducible benchmark of the candidate routing approaches.

Approaches compared on identical inputs
---------------------------------------
A       Direct catalog lookup (``benchmarks/baselines.py``).
B       Catalog + business rules R01-R13 (router with referral knowledge off).
B+R14   B plus the informational historical referral advisory (default router).
B+R14r  B plus R14 with ``review_on_elevated_referral_risk=True`` (advisory holds).

Experiments
-----------
1. Functional scenarios: the simulator's labelled suite (every catalog code +
   every rule's positive/negative cases). Measures status correctness and,
   most importantly, *unsafe dispatches* (dispatch-eligible when the
   expectation is not).
2. Mixed synthetic stream (seeded): review/unresolved/invalid rates.
3. Historical replay of the validation and test splits as perfect-intake CO1 messages
   (RT code + borough only): coverage, agreement with the historical first
   agency (a consistency check, not verified routing accuracy - see
   DECISIONS.md D-03), review rate, and how many later-REFERRED requests each
   approach flagged or held. Skipped if the Parquet file is absent.
4. Performance: catalog load time, per-request latency percentiles, batch
   throughput and JSON serialisation cost.

Results go to ``outputs/benchmarks/benchmark_results.json`` (+ CSV tables).
Timings depend on the machine; the environment is recorded alongside.

Run:  python benchmarks/benchmark_agency.py
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from baselines import DirectLookup  # noqa: E402

from civicops_agency import AgencySkillRouter, RouterConfig, RULES, __version__  # noqa: E402
from civicops_agency.simulator import from_history_row, random_stream, scenario_suite  # noqa: E402

CATALOG = ROOT / "data" / "reference" / "request_catalog.csv"
PARQUET = ROOT / "data" / "research" / "requests_real.parquet"
OUT = ROOT / "outputs" / "benchmarks"


def flat(result) -> Dict[str, Any]:
    """Slim output plus its audit detail in one flat dict (the benchmark needs both)."""
    d = result.to_dict(detail=True)
    return {**d, **d.pop("detail")}


def build_approaches() -> Dict[str, Callable[[Any], Dict[str, Any]]]:
    a = DirectLookup(CATALOG)
    b = AgencySkillRouter.from_catalog(CATALOG, knowledge_path=None)
    b14 = AgencySkillRouter.from_catalog(CATALOG)
    b14r = AgencySkillRouter.from_catalog(CATALOG, config=RouterConfig(review_on_elevated_referral_risk=True))
    return {
        "A_direct_lookup": a.assign,
        "B_catalog_rules": lambda m: flat(b.assign(m)),
        "B+R14_advisory": lambda m: flat(b14.assign(m)),
        "B+R14_review_policy": lambda m: flat(b14r.assign(m)),
    }


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 3) if d else 0.0


# ---------------------------------------------------------------------------
# 1 + 2: functional behaviour
# ---------------------------------------------------------------------------
def functional(approaches, catalog) -> Dict[str, Any]:
    suite = scenario_suite(catalog)
    out: Dict[str, Any] = {"scenarios": len(suite), "per_approach": {}, "per_scenario": []}
    for name, fn in approaches.items():
        correct = unsafe = explained = 0
        for s in suite:
            r = fn(s["input"])
            correct += r["status"] == s["expected_status"] and r["dispatch_eligible"] == s["expected_eligible"]
            unsafe += r["dispatch_eligible"] and not s["expected_eligible"]
            explained += bool(r["explanation"]) and bool(r["rules_applied"])
        out["per_approach"][name] = {"status_correct": correct, "status_correct_pct": pct(correct, len(suite)),
                                     "unsafe_dispatches": unsafe, "explained_pct": pct(explained, len(suite))}
    # Scenario-level table for A vs B (the report lists where they differ).
    a, b = approaches["A_direct_lookup"], approaches["B_catalog_rules"]
    for s in suite:
        ra, rb = a(s["input"]), b(s["input"])
        out["per_scenario"].append({"scenario": s["name"], "expected": s["expected_status"],
                                    "A_status": ra["status"], "A_eligible": ra["dispatch_eligible"],
                                    "B_status": rb["status"], "B_eligible": rb["dispatch_eligible"]})

    stream = random_stream(catalog, 10000, seed=2026)
    rates: Dict[str, Any] = {}
    for name, fn in approaches.items():
        res = [fn(m) for m in stream]
        n = len(res)
        rates[name] = {
            "requests": n,
            "dispatch_eligible_pct": pct(sum(r["dispatch_eligible"] for r in res), n),
            "review_pct": pct(sum(r["status"] != "ASSIGNED" for r in res), n),
            "unresolved_pct": pct(sum(r["status"] == "UNRESOLVED" for r in res), n),
            "invalid_pct": pct(sum(r["status"] == "INVALID" for r in res), n),
            "explained_pct": pct(sum(bool(r["explanation"]) for r in res), n),
        }
    out["synthetic_stream"] = rates
    return out


# ---------------------------------------------------------------------------
# 3: historical replay (val and test splits; nothing is fitted here)
# ---------------------------------------------------------------------------
def historical(approaches) -> Dict[str, Any]:
    if not PARQUET.exists():
        return {"skipped": "requests_real.parquet not present (research-only file)"}
    import pandas as pd

    df = pd.read_parquet(PARQUET, columns=["unique_key", "request_type_code", "borough", "agency", "split",
                                           "outcome_class"])
    out: Dict[str, Any] = {}
    for split in ("val", "test"):
        part = df[df["split"] == split]
        msgs = [from_history_row(r) for r in part.to_dict("records")]
        hist_agency = part["agency"].tolist()
        referred = (part["outcome_class"] == "REFERRED").tolist()  # evaluation label only
        n, n_ref = len(part), sum(referred)
        res_split: Dict[str, Any] = {"records": n, "referred": n_ref}
        for name, fn in approaches.items():
            res = [fn(m) for m in msgs]
            flagged = [bool((r.get("referral_advisory") or {}).get("risk_level") == "ELEVATED") for r in res]
            held = [r["status"] != "ASSIGNED" for r in res]
            res_split[name] = {
                "coverage_pct": pct(sum(r["agency"] is not None for r in res), n),
                "agree_first_agency_pct": pct(sum(r["agency"] == h for r, h in zip(res, hist_agency)), n),
                "review_pct": pct(sum(held), n),
                "advisory_flag_pct": pct(sum(flagged), n),
                "referred_flagged": sum(f and y for f, y in zip(flagged, referred)),
                "referred_flagged_pct": pct(sum(f and y for f, y in zip(flagged, referred)), n_ref),
                "referred_held": sum(h and y for h, y in zip(held, referred)),
                "referred_held_pct": pct(sum(h and y for h, y in zip(held, referred)), n_ref),
                "precision_of_flag_pct": pct(sum(f and y for f, y in zip(flagged, referred)), sum(flagged)),
            }
        out[split] = res_split
    return out


# ---------------------------------------------------------------------------
# 4: performance
# ---------------------------------------------------------------------------
def timeit(fn: Callable[[], Any], repeat: int) -> List[float]:
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return times


def performance(catalog) -> Dict[str, Any]:
    load = timeit(lambda: AgencySkillRouter.from_catalog(CATALOG), 30)
    router = AgencySkillRouter.from_catalog(CATALOG)
    base = DirectLookup(CATALOG)
    stream = random_stream(catalog, 20000, seed=99)
    for m in stream[:500]:  # warm-up
        router.assign(m)
    lat = []
    for m in stream:
        t0 = time.perf_counter()
        router.assign(m)
        lat.append((time.perf_counter() - t0) * 1e6)
    lat.sort()
    q = lambda p: round(lat[min(len(lat) - 1, int(p * len(lat)))], 2)  # noqa: E731
    batches = {}
    for size in (1000, 10000, 50000):
        msgs = random_stream(catalog, size, seed=size)
        t = min(timeit(lambda: router.assign_batch(msgs), 3))
        tb = min(timeit(lambda: [base.assign(m) for m in msgs], 3))
        batches[str(size)] = {"router_req_per_s": round(size / t), "baseline_req_per_s": round(size / tb)}
    results = router.assign_batch(stream[:5000])
    ser = min(timeit(lambda: [r.to_json(None) for r in results], 3))
    return {
        "catalog_load_ms_median": round(statistics.median(load) * 1000, 3),
        "assign_latency_us": {"p50": q(0.50), "p95": q(0.95), "p99": q(0.99), "max": round(lat[-1], 2),
                              "mean": round(statistics.mean(lat), 2), "requests": len(lat)},
        "batch_throughput": batches,
        "to_json_us_per_result": round(ser / len(results) * 1e6, 2),
    }


def complexity() -> Dict[str, Any]:
    def sloc(path: Path) -> int:
        n = 0
        in_doc = False
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(('"""', "'''")):
                if not (len(s) > 3 and s.endswith(('"""', "'''"))):
                    in_doc = not in_doc
                continue
            if in_doc or not s or s.startswith("#"):
                continue
            n += 1
        return n

    pkg = ROOT / "civicops_agency"
    files = {p.name: sloc(p) for p in sorted(pkg.glob("*.py"))}
    return {"business_rules": len(RULES), "runtime_source_lines": sum(files.values()), "per_file": files,
            "baseline_source_lines": sloc(ROOT / "benchmarks" / "baselines.py"),
            "runtime_third_party_dependencies": 0}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    router = AgencySkillRouter.from_catalog(CATALOG)
    approaches = build_approaches()
    t0 = time.time()
    results = {
        "component_version": __version__,
        "catalog_version": router.catalog.version,
        "referral_knowledge": router.knowledge_status,
        "environment": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                        "platform": platform.platform(), "machine": platform.machine(),
                        "cpu_count": os.cpu_count()},
        "functional": functional(approaches, router.catalog),
        "historical": historical(approaches),
        "performance": performance(router.catalog),
        "complexity": complexity(),
    }
    results["benchmark_wall_seconds"] = round(time.time() - t0, 2)
    previous = OUT / "benchmark_results.json"
    if "skipped" in results["historical"] and previous.exists():
        # Without the research dataset (runtime package), keep the historical
        # replay from the last run that had it, and say so explicitly.
        old = json.loads(previous.read_text(encoding="utf-8")).get("historical", {})
        if "test" in old:
            old["reused_from_previous_run"] = True
            old["reuse_reason"] = results["historical"]["skipped"]
            results["historical"] = old
    previous.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items() if k != "functional"} | {
        "functional_summary": results["functional"]["per_approach"],
        "synthetic_stream": results["functional"]["synthetic_stream"]}, indent=2))


if __name__ == "__main__":
    main()
