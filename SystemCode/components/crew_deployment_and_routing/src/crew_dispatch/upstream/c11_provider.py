"""Adapters that obtain a C11 prioritised queue.

Dispatch's optimiser never calls C11: it consumes C11 *output* (``Snapshot.queue``). These providers
exist for (a) the research replay, which must re-run prioritisation as requests arrive, and (b) a
plug-and-play demo before the shared CivicOps root folder exists.

Resolution order (``get_provider``):
  1. ``C11FinalProvider``  - the confirmed contract ``c11_final.prioritize_from_c10`` loaded from the
     configured shared-root folder (default ``../C11_Prioritization``, env ``CIVICOPS_C11_PATH``).
  2. ``VendoredC11Provider`` - the unmodified, hash-verified ``c11_prioritization.py`` shipped in
     ``upstream/vendor``. It needs a C10 urgency per request; when no C10 output is present the
     catalog ``default_urgency`` is passed as a documented stand-in INPUT to C11 (Dispatch itself
     never reads urgency). ``overall_queue_position`` is then taken from C11's own returned order,
     exactly as ``c11_final`` numbers it (1..n over active requests; held requests None).
Every plan records which provider produced the queue and the SHA-256 of the C11 files used.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path

from ..config import COMPONENT_ROOT
from ..schemas import C11_QUEUE_STATUSES

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
VENDORED_C11_SHA256 = "921e36a2778f3339d1a4bdeda2a80382e3ef359f43651d83da8dd8086676f01e"


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _iso(t) -> str:
    return t.isoformat() if isinstance(t, datetime) else str(t)


class C11FinalProvider:
    name = "c11_final.prioritize_from_c10"

    def __init__(self, c11_dir: Path, entry: str = "c11_final:prioritize_from_c10"):
        self.c11_dir = Path(c11_dir).resolve()
        module_name, func = entry.split(":")
        sys.dont_write_bytecode = True         # never write __pycache__ into upstream folders
        if str(self.c11_dir) not in sys.path:
            sys.path.insert(0, str(self.c11_dir))
        spec = importlib.util.spec_from_file_location(module_name, self.c11_dir / f"{module_name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)           # raises if C10 / SLA files are missing
        self._fn = getattr(mod, func)
        self.files = {p.name: _sha(p) for p in sorted(self.c11_dir.glob("*.py"))}
        self.info = {"provider": self.name, "path": str(self.c11_dir), "sha256": self.files, "proxy_inputs": []}

    def prioritize(self, requests: list, now) -> list:
        return self._fn([dict(r) for r in requests], _iso(now), root=self.c11_dir)


class VendoredC11Provider:
    name = "vendored c11_prioritization.prioritize (fallback)"

    def __init__(self, catalog: dict | None = None):
        path = VENDOR_DIR / "c11_prioritization.py"
        digest = _sha(path)
        if digest != VENDORED_C11_SHA256:
            raise RuntimeError("vendored c11_prioritization.py does not match the recorded upstream hash")
        spec = importlib.util.spec_from_file_location("crew_dispatch_vendored_c11_prioritization", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._mod = mod
        self.catalog = catalog or {}
        self.info = {"provider": self.name, "path": str(path.relative_to(COMPONENT_ROOT)), "sha256": {path.name: digest},
                     "proxy_inputs": ["c11_urgency := catalog default_urgency when no C10 urgency is present",
                                      "no sla_target_hours (official SLA lookup lives in c11_final)",
                                      "overall_queue_position := 1-based index in C11's returned active order"]}

    def _prepare(self, r: dict) -> dict:
        row = dict(r)
        if not (row.get("c11_urgency") or row.get("recommended_urgency")):
            cat = self.catalog.get(row.get("request_type_code"), {})
            if cat.get("default_urgency"):
                row["c11_urgency"] = str(cat["default_urgency"]).upper()
                row["c11_urgency_source"] = "catalog_default_urgency_proxy_for_C10"
        if isinstance(row.get("created_date"), datetime):
            row["created_date"] = row["created_date"].isoformat()
        return row

    def prioritize(self, requests: list, now) -> list:
        out = self._mod.prioritize([self._prepare(r) for r in requests], _iso(now))
        pos = 0
        for item in out:
            if item["c11_queue_status"] == "hold":
                item["overall_queue_position"] = None
            else:
                pos += 1
                item["overall_queue_position"] = pos
            item["c11_provider"] = self.name
        return out


def get_provider(cfg: dict, catalog: dict | None = None):
    """Return (provider, resolution_log)."""
    log = []
    c11_dir = os.environ.get("CIVICOPS_C11_PATH") or cfg["upstream"]["c11_path"]
    c11_dir = Path(c11_dir) if Path(c11_dir).is_absolute() else (COMPONENT_ROOT / c11_dir)
    if (c11_dir / "c11_final.py").exists():
        try:
            return C11FinalProvider(c11_dir, cfg["upstream"]["c11_entry"]), log
        except Exception as e:   # missing C10 folder, SLA tables, ... -> documented fallback
            log.append(f"c11_final at {c11_dir} could not be loaded: {type(e).__name__}: {e}")
    else:
        log.append(f"no c11_final.py at {c11_dir}")
    if not cfg["upstream"].get("allow_vendored_fallback", True):
        raise RuntimeError("C11 unavailable and vendored fallback disabled: " + "; ".join(log))
    log.append("using vendored c11_prioritization fallback")
    return VendoredC11Provider(catalog), log


def validate_c11_output(records: list) -> list:
    """Check the confirmed C11 contract; returns a list of human-readable violations (empty = ok)."""
    issues = []
    ids = [r.get("request_id") for r in records]
    if len(ids) != len(set(ids)):
        issues.append("duplicate request_id")
    seen_hold = False
    expected = 1
    for r in records:
        st = r.get("c11_queue_status")
        if st not in C11_QUEUE_STATUSES:
            issues.append(f"{r.get('request_id')}: invalid c11_queue_status {st!r}")
        if r.get("dispatch_approved") not in (False, None):
            issues.append(f"{r.get('request_id')}: dispatch_approved must be False from C11")
        pos = r.get("overall_queue_position")
        if st == "hold":
            seen_hold = True
            if pos is not None:
                issues.append(f"{r.get('request_id')}: held request has a position")
        else:
            if seen_hold:
                issues.append(f"{r.get('request_id')}: active request after held requests")
            if pos != expected:
                issues.append(f"{r.get('request_id')}: overall_queue_position {pos!r} != expected {expected}")
            expected += 1
    return issues
