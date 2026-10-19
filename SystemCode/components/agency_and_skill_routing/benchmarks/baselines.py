"""Approach A - direct catalog lookup, kept as the mandatory baseline.

This is deliberately the simplest possible router: read the CSV, map the RT
code to agency and skill, and call the request dispatchable whenever the code
is found. It performs no input validation, emergency gating, readiness or
human-review checks and produces no explanation. It exists so the benchmark
can show, on identical inputs, what the knowledge-based rules of Approach B
add (and what they cost in speed).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict


class DirectLookup:
    def __init__(self, catalog_path: Path) -> None:
        with Path(catalog_path).open(encoding="utf-8-sig", newline="") as fh:
            self.table = {r["request_type_code"]: (r["agency"], r["required_skill"].split("|"))
                          for r in csv.DictReader(fh)}

    def assign(self, msg: Any) -> Dict[str, Any]:
        code = msg.get("request_type") if isinstance(msg, dict) else None
        hit = self.table.get(code) if isinstance(code, str) else None
        if hit is None:
            return {"status": "UNRESOLVED", "agency": None, "required_skills": [], "dispatch_eligible": False,
                    "explanation": "", "rules_applied": []}
        return {"status": "ASSIGNED", "agency": hit[0], "required_skills": list(hit[1]), "dispatch_eligible": True,
                "explanation": "", "rules_applied": []}
