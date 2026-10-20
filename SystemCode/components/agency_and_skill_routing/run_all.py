"""Reproduce every result in this package with one command.

    python run_all.py              # research (if dataset present) + tests + benchmark + report
    python run_all.py --skip-research

Steps
-----
1. Research scripts (need pandas/pyarrow and data/research/requests_real.parquet).
   Regenerates outputs/research/* and data/reference/referral_knowledge.json.
2. Example outputs (examples/outputs from examples/inputs), then pytest with JUnit XML
   -> outputs/tests/junit.xml and pytest_output.txt.
3. Benchmark -> outputs/benchmarks/benchmark_results.json.
4. HTML report -> reports/agency_skills_report.html (built only from 1-3).

Each step runs in a subprocess with the current interpreter so a failure is
reported with its own exit code and the remaining steps still run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARQUET = ROOT / "data" / "research" / "requests_real.parquet"
RESEARCH = ["research/catalog_consistency.py", "research/historical_analysis.py",
            "research/referral_analysis.py", "research/method_comparison.py"]


def step(title: str, args: list, log: Path = None) -> int:
    print(f"\n=== {title} ===", flush=True)
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=log is not None, text=True)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        print((proc.stdout or "").strip().splitlines()[-1] if proc.stdout else "", flush=True)
    print(f"-> exit code {proc.returncode}", flush=True)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-research", action="store_true", help="reuse existing outputs/research")
    args = parser.parse_args()
    codes = {}
    if not args.skip_research:
        if PARQUET.exists():
            for script in RESEARCH:
                codes[script] = step(script, [script], ROOT / "outputs" / "research" / "logs" / f"{Path(script).stem}.log")
        else:
            print(f"Historical dataset not found at {PARQUET}; reusing existing research outputs.")
    codes["examples"] = step("example outputs", ["examples/generate_outputs.py"])
    codes["pytest"] = step("tests", ["-m", "pytest", "-p", "no:cacheprovider",
                                      "--junitxml=outputs/tests/junit.xml", "-q"],
                           ROOT / "outputs" / "tests" / "pytest_output.txt")
    codes["benchmark"] = step("benchmark", ["benchmarks/benchmark_agency.py"],
                              ROOT / "outputs" / "benchmarks" / "benchmark_output.txt")
    codes["report"] = step("report", ["reports/build_report.py"])
    print("\nSummary:", codes)
    return 0 if all(c == 0 for c in codes.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
