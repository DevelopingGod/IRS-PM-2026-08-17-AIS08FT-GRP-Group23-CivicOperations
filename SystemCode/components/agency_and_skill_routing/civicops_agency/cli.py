"""Command-line interface.

Commands::

    civicops-agency assign INPUT [-o OUTPUT] [--detail]   # JSON object, array or .jsonl; '-' = stdin
    civicops-agency catalog-check [--strict]   # integrity report for the catalog
    civicops-agency rules                      # print the business-rule register
    civicops-agency demo                       # run examples/inputs and show input vs output

Common options: ``--catalog PATH`` (default: auto-detected
``data/reference/request_catalog.csv``), ``--matrix PATH``, ``--knowledge
PATH``, ``--no-knowledge``, ``--min-confidence X``, ``--review-elevated-risk``,
``--strict-catalog``.

Exit codes: 0 success; 1 catalog cannot be used; 2 bad command-line input
(unreadable file or invalid JSON document); 3 ``--fail-on-invalid`` was given
and at least one request was INVALID.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from . import __version__
from .catalog import CatalogError
from .config import RouterConfig
from .router import AUTO, AgencySkillRouter, default_catalog_path
from .rules import RULES
from .schemas import Status


def _add_router_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--catalog", type=Path, default=None, help="path to request_catalog.csv")
    p.add_argument("--matrix", type=Path, default=None, help="path to NYC311_agency_matrix.md (agency names)")
    p.add_argument("--knowledge", type=Path, default=None, help="path to referral_knowledge.json")
    p.add_argument("--no-knowledge", action="store_true", help="disable R14 referral advisories")
    p.add_argument("--min-confidence", type=float, default=None, help="hold requests below this CO1 confidence")
    p.add_argument("--review-elevated-risk", action="store_true",
                   help="hold requests whose historical referral risk is ELEVATED")
    p.add_argument("--strict-catalog", action="store_true", help="refuse a catalog with any integrity problem")


def _build_router(args: argparse.Namespace) -> AgencySkillRouter:
    config = RouterConfig(min_confidence=args.min_confidence,
                          review_on_elevated_referral_risk=args.review_elevated_risk,
                          strict_catalog=args.strict_catalog,
                          use_referral_knowledge=not args.no_knowledge)
    catalog = args.catalog or default_catalog_path()
    knowledge = None if args.no_knowledge else (args.knowledge or AUTO)
    return AgencySkillRouter.from_catalog(catalog, matrix_path=args.matrix or AUTO, knowledge_path=knowledge,
                                          config=config)


def _read_items(source: str) -> tuple:
    """Return (items, was_list). Supports a JSON object, a JSON array or JSON Lines."""
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8-sig")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not source.endswith(".jsonl"):
        try:
            data = json.loads(text)
            return (data, True) if isinstance(data, list) else ([data], False)
        except json.JSONDecodeError:
            # Not a single JSON document: accept it as JSON Lines only if every
            # line looks like an object, otherwise report the original error.
            if len(lines) < 2 or not all(line.lstrip().startswith("{") for line in lines):
                raise
    # JSON Lines: each line goes to the router as text, so a malformed line
    # becomes one INVALID result instead of aborting the whole batch.
    return lines, True


def cmd_assign(args: argparse.Namespace) -> int:
    router = _build_router(args)
    try:
        items, was_list = _read_items(args.input)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read input {args.input}: {exc}", file=sys.stderr)
        return 2
    results = router.assign_batch(items)
    payload: Any = ([r.to_dict(detail=args.detail) for r in results] if was_list
                    else results[0].to_dict(detail=args.detail))
    text = json.dumps(payload, indent=None if args.compact else 2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        counts: dict = {}
        for r in results:
            counts[r.status.value] = counts.get(r.status.value, 0) + 1
        print(f"Wrote {len(results)} result(s) to {args.output}: {counts}", file=sys.stderr)
    else:
        print(text)
    if args.fail_on_invalid and any(r.status == Status.INVALID for r in results):
        return 3
    return 0


def cmd_catalog_check(args: argparse.Namespace) -> int:
    report = _build_router(args).catalog.integrity_report()
    print(json.dumps(report, indent=2))
    return 0 if report["usable"] else 1


def cmd_rules(_: argparse.Namespace) -> int:
    for rule in RULES.values():
        print(f"{rule.id}  {rule.name}\n     {rule.statement}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    router = _build_router(args)
    inputs = Path(__file__).resolve().parents[1] / "examples" / "inputs"
    files = sorted(inputs.glob("*.json"))
    if not files:
        print(f"No example files found in {inputs}", file=sys.stderr)
        return 2
    print(f"civicops-agency {__version__} | catalog {router.catalog.version} | "
          f"referral knowledge {router.knowledge_status}\n")
    print("INPUT  = fields received from CO1 (no agency, no skills, no status)")
    print("OUTPUT = what this component adds\n")
    print(f"{'example file':<26}{'INPUT request_type':<20}| {'OUTPUT status':<14}{'agency':<8}{'required_skills':<22}"
          f"{'eligible':<9}next_step")
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for item in (data if isinstance(data, list) else [data]):
            r = router.assign(item)
            shown = item.get("request_type") if isinstance(item, dict) else None
            print(f"{f.stem:<26}{str(shown):<20}| {r.status.value:<14}{str(r.agency or '-'):<8}"
                  f"{','.join(r.required_skills) or '-':<22}{str(r.dispatch_eligible):<9}{r.next_step.value}")
    example = inputs / "valid_co1.json"
    raw = json.loads(example.read_text(encoding="utf-8"))
    print(f"\nINPUT from CO1 ({example.name}):\n{json.dumps(raw, indent=2)}")
    print(f"\nOUTPUT to duplicate detection:\n{router.assign(raw).to_json()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civicops-agency",
                                     description="CivicOps agency assignment and required skills.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log catalog loading details")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("assign", help="route CO1 output (file or '-' for stdin)")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="write results to this file instead of stdout")
    p.add_argument("--compact", action="store_true", help="single-line JSON")
    p.add_argument("--fail-on-invalid", action="store_true", help="exit 3 if any request is INVALID")
    p.add_argument("--detail", action="store_true",
                   help="add the audit 'detail' block (rules applied, findings, referral advisory, versions)")
    _add_router_options(p)
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("catalog-check", help="print the catalog integrity report")
    _add_router_options(p)
    p.set_defaults(func=cmd_catalog_check)

    p = sub.add_parser("rules", help="list the business rules")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("demo", help="run the bundled examples")
    _add_router_options(p)
    p.set_defaults(func=cmd_demo)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return int(args.func(args))
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
