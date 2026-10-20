"""Public entry point: :class:`AgencySkillRouter`.

Typical use::

    from civicops_agency import AgencySkillRouter

    router = AgencySkillRouter.from_catalog("data/reference/request_catalog.csv")
    result = router.assign(co1_output)          # dict or JSON string
    print(result.to_dict())                     # slim hand-off for duplicate detection
    print(result.to_dict(detail=True))          # + rules, findings, advisory, versions

Pipeline for one request (each step is a documented business rule)::

    R01 validate -> R02 emergency -> R03 ready -> R04 human
        -> R05 special label?  yes: stop lookup
        -> R06 catalog lookup (R07 unknown / R08 integrity) -> R10 skills
        -> R09 confidence -> R14 referral advisory
        -> R12 safe unresolved -> R13 eligibility -> R11 explanation

Every rule is evaluated even after an earlier one has already decided that
the request cannot be dispatched, so the output lists *all* reasons, not just
the first one. A person reviewing a held request then sees, for example, both
"needs a human" and "unknown code".

The router is immutable after construction and holds no per-request state, so
one instance can be shared across threads.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

from . import __version__
from .catalog import Catalog, CatalogError, load_agency_names, load_catalog
from .config import RouterConfig
from .explanations import build_explanation
from .knowledge import KnowledgeError, ReferralKnowledge
from .rules import (RuleContext, next_step_for, r02_emergency, r03_readiness, r04_human_review, r05_special_labels,
                    r06_catalog_lookup, r09_confidence, r10_skills, r12_safe_unresolved, r13_eligibility,
                    r14_referral_advisory)
from .schemas import (AgencyResolution, AssignmentResult, CO1Request, Finding, Location, NextStep, Severity,
                      Status)
from .validators import validate_co1

log = logging.getLogger(__name__)

PathLike = Union[str, Path]
MATRIX_FILENAME = "NYC311_agency_matrix.md"
KNOWLEDGE_FILENAME = "referral_knowledge.json"
CATALOG_FILENAME = "request_catalog.csv"


class _Auto:
    """Sentinel: look for the optional file next to the catalog."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "AUTO"


AUTO = _Auto()


def default_catalog_path() -> Path:
    """Locate the catalog without the caller passing a path.

    Order: ``$CIVICOPS_CATALOG``; ``data/reference/request_catalog.csv`` in the
    package's project root (source checkout or extracted ZIP); the same
    relative path under the current working directory.
    """
    env = os.environ.get("CIVICOPS_CATALOG")
    candidates = [Path(env)] if env else []
    candidates += [
        Path(__file__).resolve().parents[1] / "data" / "reference" / CATALOG_FILENAME,
        Path.cwd() / "data" / "reference" / CATALOG_FILENAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise CatalogError("Could not find request_catalog.csv. Pass a path explicitly or set CIVICOPS_CATALOG.")


class AgencySkillRouter:
    """Assigns the responsible agency and required crew skills to CO1 output."""

    def __init__(self, catalog: Catalog, agency_names: Optional[Dict[str, str]] = None,
                 knowledge: Optional[ReferralKnowledge] = None, config: Optional[RouterConfig] = None,
                 knowledge_status: Optional[str] = None) -> None:
        self._catalog = catalog
        self._names = dict(agency_names or {})
        self._config = config or RouterConfig()
        self._knowledge: Optional[ReferralKnowledge] = None
        self._knowledge_status = knowledge_status or "NOT_CONFIGURED"
        if knowledge is not None:
            if not self._config.use_referral_knowledge:
                self._knowledge_status = "DISABLED_BY_CONFIG"
            elif knowledge.catalog_sha256 and knowledge.catalog_sha256 != catalog.sha256 \
                    and not self._config.allow_stale_knowledge:
                # Referral rates describe a specific catalog version; with a
                # different catalog they could describe re-defined codes.
                self._knowledge_status = "DISABLED_STALE_CATALOG"
                log.warning("Referral knowledge was built for another catalog version; advisories disabled.")
            else:
                self._knowledge = knowledge
                self._knowledge_status = "ACTIVE"

    # ------------------------------------------------------------------ build
    @classmethod
    def from_catalog(cls, catalog_path: PathLike, matrix_path: Union[PathLike, None, _Auto] = AUTO,
                     knowledge_path: Union[PathLike, None, _Auto] = AUTO,
                     config: Optional[RouterConfig] = None) -> "AgencySkillRouter":
        """Build a router from files on disk.

        Args:
            catalog_path: the shared ``request_catalog.csv`` (required).
            matrix_path: agency matrix markdown used for full agency names.
                ``AUTO`` looks next to the catalog; ``None`` disables names.
            knowledge_path: ``referral_knowledge.json`` for R14 advisories.
                ``AUTO`` looks next to the catalog; ``None`` disables them.
            config: optional :class:`RouterConfig`.
        """
        config = config or RouterConfig()
        catalog = load_catalog(catalog_path, strict=config.strict_catalog)
        folder = Path(catalog_path).resolve().parent

        names: Dict[str, str] = {}
        mpath = folder / MATRIX_FILENAME if isinstance(matrix_path, _Auto) else matrix_path
        if mpath is not None and (not isinstance(matrix_path, _Auto) or Path(mpath).is_file()):
            names = load_agency_names(mpath)

        knowledge, status = None, "NOT_CONFIGURED"
        kpath = folder / KNOWLEDGE_FILENAME if isinstance(knowledge_path, _Auto) else knowledge_path
        if kpath is not None and (not isinstance(knowledge_path, _Auto) or Path(kpath).is_file()):
            try:
                knowledge = ReferralKnowledge.load(kpath)
            except KnowledgeError as exc:
                # Advisories are optional: a broken file must not stop routing.
                log.error("Referral knowledge unavailable: %s", exc)
                status = "ERROR"
        return cls(catalog, names, knowledge, config, knowledge_status=status)

    @classmethod
    def default(cls, config: Optional[RouterConfig] = None) -> "AgencySkillRouter":
        """Build a router from the catalog found by :func:`default_catalog_path`."""
        return cls.from_catalog(default_catalog_path(), config=config)

    # ------------------------------------------------------------- properties
    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def config(self) -> RouterConfig:
        return self._config

    @property
    def knowledge_status(self) -> str:
        return self._knowledge_status

    def describe(self) -> Dict[str, Any]:
        """Configuration snapshot (useful for logs and audits)."""
        return {
            "component_version": __version__,
            "catalog_path": str(self._catalog.path) if self._catalog.path else None,
            "catalog_version": self._catalog.version,
            "catalog_codes": len(self._catalog.entries),
            "catalog_issues": len(self._catalog.issues),
            "agency_names_loaded": len(self._names),
            "referral_knowledge": self._knowledge_status,
            "config": dict(self._config.__dict__),
        }

    # ------------------------------------------------------------------- API
    def assign(self, co1_output: Union[Mapping[str, Any], str, bytes]) -> AssignmentResult:
        """Assign agency and skills to one CO1 message (dict or JSON text).

        Never raises for bad input: malformed input yields ``status=INVALID``.
        An unexpected internal error yields a safe HELD result (shared rule 6:
        a failing component falls back to its safe default).
        """
        try:
            if isinstance(co1_output, (str, bytes, bytearray)):
                try:
                    co1_output = json.loads(co1_output)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    return self._invalid_json(str(exc))
            return self._assign(co1_output)
        except Exception as exc:  # pragma: no cover - defensive safety net
            log.exception("Unexpected error while routing a request")
            return self._safe_default(co1_output, exc)

    def assign_batch(self, items: Iterable[Union[Mapping[str, Any], str]]) -> List[AssignmentResult]:
        """Assign every item independently; one bad item never affects another."""
        return [self.assign(item) for item in items]

    def assign_json(self, text: str, indent: Optional[int] = 2, detail: bool = False) -> str:
        """JSON in, JSON out. Accepts one object or an array of objects.

        ``detail=True`` adds the audit ``detail`` block to every result.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return self._invalid_json(str(exc)).to_json(indent, detail=detail)
        if isinstance(data, list):
            return json.dumps([r.to_dict(detail=detail) for r in self.assign_batch(data)], indent=indent,
                              ensure_ascii=False)
        return self.assign(data).to_json(indent, detail=detail)

    # -------------------------------------------------------------- internals
    def _assign(self, raw: Any) -> AssignmentResult:
        request, r01 = validate_co1(raw, normalize_request_type=self._config.normalize_request_type)
        ctx = RuleContext(request=request, config=self._config)
        ctx.mark("R01")
        ctx.findings.extend(r01)

        r02_emergency(ctx)
        r03_readiness(ctx)
        r04_human_review(ctx)
        if request.request_type is not None and not r05_special_labels(ctx):
            r06_catalog_lookup(ctx, self._catalog)
            r10_skills(ctx)
        r09_confidence(ctx)
        r14_referral_advisory(ctx, self._knowledge)
        r12_safe_unresolved(ctx)
        status = r13_eligibility(ctx)
        ctx.mark("R11")
        return self._result(ctx, status)

    def _result(self, ctx: RuleContext, status: Status) -> AssignmentResult:
        label = ctx.entry.citizen_label if ctx.entry is not None else None
        result = AssignmentResult(
            request=ctx.request,
            status=status,
            agency=ctx.agency,
            required_skills=list(ctx.skills),
            dispatch_eligible=status == Status.ASSIGNED,
            next_step=next_step_for(status, ctx),
            explanation=build_explanation(ctx, status, self._names, label),
            agency_name=self._names.get(ctx.agency) if ctx.agency else None,
            citizen_label=label,
            agency_resolution=ctx.resolution,
            rules_applied=sorted(ctx.evaluated, key=lambda r: int(r[1:])),
            findings=list(ctx.findings),
            referral_advisory=ctx.advisory,
            catalog_version=self._catalog.version,
            component_version=__version__,
        )
        log.debug("Routed %s -> %s (%s)", result.request_type, result.status.value, result.agency)
        return result

    def _invalid_json(self, error: str) -> AssignmentResult:
        ctx = RuleContext(request=CO1Request(None, None, None, None, None, None, Location()), config=self._config)
        ctx.add("R01", "INVALID_JSON", Severity.INVALID, f"Input is not valid JSON: {error}", None)
        r12_safe_unresolved(ctx)
        status = r13_eligibility(ctx)
        ctx.mark("R11")
        return self._result(ctx, status)

    def _safe_default(self, raw: Any, exc: Exception) -> AssignmentResult:
        finding = Finding("R12", "INTERNAL_ERROR", Severity.HOLD,
                          f"Internal error ({type(exc).__name__}); held for a person as the safe default.")
        return AssignmentResult(
            request=CO1Request(None, None, None, None, None, None, Location()), status=Status.HELD, agency=None,
            required_skills=[], dispatch_eligible=False, next_step=NextStep.HUMAN_REVIEW,
            explanation="An internal error occurred, so the request is held for a person [R12].",
            agency_name=None, citizen_label=None, agency_resolution=AgencyResolution.NOT_ATTEMPTED,
            rules_applied=["R11", "R12"], findings=[finding], referral_advisory=None,
            catalog_version=self._catalog.version, component_version=__version__)
