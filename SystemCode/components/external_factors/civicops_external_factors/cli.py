"""Command-line interface (offline).

  civicops-ef evaluate --input request.json [--output result.json] [--urgency-only]
  civicops-ef batch    --input requests.jsonl|.json|.csv --output results.jsonl [--summary]
  civicops-ef validate-cache [--no-checksums]
  civicops-ef info

Common options: --config config/proximity_rules.yaml  --osm-data-dir data/osm
                --threshold-m 150 (override all category thresholds)  --mode PURE_GEOGRAPHIC|CONTEXT_AWARE
Exit codes: 0 ok, 2 invalid input/config, 3 OSM cache problem.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .evaluator import DEFAULT_CONFIG, CriticalFacilityEvaluator
from .osm_loader import OSMCacheError, validate_cache_files
from .proximity_rules import ProximityRuleset, RulesetError


def _overrides(args) -> dict | None:
    over: dict = {}
    if getattr(args, "threshold_m", None) is not None:
        rs = ProximityRuleset.from_yaml(args.config)
        over["facility_categories"] = {c: {"threshold_m": args.threshold_m, "rule_id": r.rule_id}
                                       for c, r in rs.category_rules.items()}
    if getattr(args, "mode", None):
        over["decision_mode"] = args.mode
    return over or None


def _evaluator(args) -> CriticalFacilityEvaluator:
    return CriticalFacilityEvaluator.from_config(args.config, osm_data_dir=args.osm_data_dir,
                                                 overrides=_overrides(args))


def _read_requests(path: Path) -> list[dict]:
    text_suffix = path.suffix.lower()
    if text_suffix == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            for k in ("latitude", "longitude", "location_uncertainty_m"):
                if r.get(k) in ("", None):
                    r[k] = None
            for k in list(r):
                if r[k] == "":
                    r[k] = None
        return rows
    raw = path.read_text("utf-8-sig").strip()
    if text_suffix == ".jsonl" or (raw and raw[0] == "{" and "\n{" in raw):
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("requests", [data])
    if not isinstance(data, list):
        raise ValueError("input must be a JSON object, a list of objects, JSONL or CSV")
    return data


def cmd_evaluate(args) -> int:
    ev = _evaluator(args)
    req = json.loads(Path(args.input).read_text("utf-8-sig")) if args.input != "-" else json.load(sys.stdin)
    res = ev.evaluate(req)
    payload = res.to_urgency_payload() if args.urgency_only else res.to_dict()
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output:
        Path(args.output).write_text(text, "utf-8")
    else:
        print(text)
    return 0


def cmd_batch(args) -> int:
    ev = _evaluator(args)
    reqs = _read_requests(Path(args.input))
    results = ev.evaluate_batch(reqs)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in results:
            d = r.to_urgency_payload() if args.urgency_only else r.to_dict()
            fh.write(json.dumps(d, ensure_ascii=False, allow_nan=False) + "\n")
    summary = {"requests": len(results),
               "external_factor": dict(Counter(str(r.external_factor) for r in results)),
               "incomplete_reasons": dict(Counter(r.incomplete_reason for r in results if r.incomplete_reason)),
               "output": str(out)}
    print(json.dumps(summary, indent=2))
    return 0


def cmd_validate(args) -> int:
    rs = ProximityRuleset.from_yaml(args.config)
    data_dir = Path(args.osm_data_dir) if args.osm_data_dir else (Path(args.config).resolve().parent / (rs.osm_data_dir or "../data/osm")).resolve()
    rep = validate_cache_files(data_dir, verify_checksums=not args.no_checksums)
    try:
        ev = CriticalFacilityEvaluator.from_config(args.config, osm_data_dir=data_dir)
        rep["evaluator_loads"] = True
        rep["load_issues"] = ev.cache.issues
        rep["facilities_indexed"] = int(len(ev.engine.facilities))
        rep["osm_data_version"] = ev.cache.data_version
    except (OSMCacheError, RulesetError) as exc:
        rep["evaluator_loads"] = False
        rep["error"] = str(exc)
        rep["ok"] = False
    print(json.dumps(rep, indent=2))
    return 0 if rep["ok"] else 3


def cmd_info(args) -> int:
    ev = _evaluator(args)
    rs = ev.ruleset
    print(json.dumps({"component_version": __version__, "ruleset_version": rs.ruleset_version,
                      "decision_mode": rs.decision_mode, "thresholds_m": rs.thresholds_m,
                      "metric_crs": rs.metric_crs, "osm_data_version": ev.cache.data_version,
                      "facilities_indexed_by_category": ev.engine.facilities["category"].value_counts().to_dict(),
                      "rules": rs.rule_catalogue()}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="civicops-ef", description="CivicOps AI External Factors - "
                                 "critical facility proximity (offline evaluation)")
    ap.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(DEFAULT_CONFIG))
    common.add_argument("--osm-data-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in [("evaluate", cmd_evaluate, "evaluate one request (JSON object)"),
                               ("batch", cmd_batch, "evaluate many requests (JSON list, JSONL or CSV)"),
                               ("info", cmd_info, "print ruleset and cache information")]:
        p = sub.add_parser(name, parents=[common], help=helptext)
        p.set_defaults(func=fn)
        p.add_argument("--threshold-m", type=float, default=None, help="override every category threshold")
        p.add_argument("--mode", choices=["PURE_GEOGRAPHIC", "CONTEXT_AWARE"], default=None)
        if name in ("evaluate", "batch"):
            p.add_argument("--input", required=True, help="input file ('-' = stdin for evaluate)")
            p.add_argument("--output", required=(name == "batch"))
            p.add_argument("--urgency-only", action="store_true", help="emit the minimal Urgency hand-off payload")
    p = sub.add_parser("validate-cache", parents=[common], help="validate the local OSM cache")
    p.add_argument("--no-checksums", action="store_true")
    p.set_defaults(func=cmd_validate)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OSMCacheError as exc:
        print(f"OSM cache error: {exc}", file=sys.stderr)
        return 3
    except (RulesetError, ValueError, TypeError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"Input/config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
