"""Authoritative request catalog (agency, required skill, est_service_minutes per request type)."""
from __future__ import annotations

import csv
from pathlib import Path


def load_catalog(path: str | Path) -> dict:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        out[r["request_type_code"]] = {
            "agency": r["agency"].strip(), "required_skill": r["required_skill"].strip(),
            "est_service_minutes": float(r["est_service_minutes"]), "default_urgency": r["default_urgency"].strip(),
            "dispatchable": str(r.get("dispatchable", "TRUE")).strip().upper() == "TRUE",
            "citizen_label": r.get("citizen_label"),
        }
    return out


def vocabulary(catalog: dict) -> dict:
    """agency -> set of valid skills."""
    voc: dict = {}
    for v in catalog.values():
        voc.setdefault(v["agency"], set()).add(v["required_skill"])
    return voc
