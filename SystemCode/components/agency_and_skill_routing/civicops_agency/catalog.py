"""Loading and integrity-checking the shared request catalog and agency names.

The catalog (``request_catalog.csv``) is the team's single source of truth for
"request type -> agency and crew skill" (shared rule 3). This module never
hardcodes any mapping: it reads the CSV at run time, so a catalog update by
the group takes effect without code changes.

Integrity checks (business rule R08) are done once, at load time:

* malformed codes (not ``RT`` + three digits) are skipped and reported;
* identical duplicate rows are collapsed with a warning;
* rows that give one code different agencies or skills mark that code as
  ``CONFLICT`` - the router then refuses to choose between them;
* rows with no agency or no skill mark the code as ``INCOMPLETE``.

Columns the router does not need (``est_service_minutes``,
``default_urgency``, ``descriptor_regex`` ...) are intentionally not loaded:
service time, urgency and intake classification belong to other components.

The agency matrix markdown is parsed only for its acronym glossary, to show a
full agency name next to the abbreviation. The matrix never changes a catalog
mapping (see DECISIONS.md, D-06).
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

CODE_PATTERN = re.compile(r"^RT\d{3}$")
REQUIRED_COLUMNS = ("request_type_code", "agency", "required_skill")
# Multi-valued cells use '|' in the catalog (see complaint_type); the same
# convention is accepted for required_skill so a code may need several skills.
MULTI_VALUE_SEPARATOR = "|"
TRUE_VALUES = {"TRUE", "T", "YES", "Y", "1"}
FALSE_VALUES = {"FALSE", "F", "NO", "N", "0"}


class CatalogError(Exception):
    """The catalog cannot be used at all (missing file/columns, or strict-mode violation)."""


@dataclass(frozen=True)
class CatalogIssue:
    """An integrity problem found while loading the catalog."""

    code: str            # e.g. DUPLICATE_IDENTICAL, CONFLICT, MISSING_AGENCY
    request_type: str
    message: str
    line: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {"code": self.code, "request_type": self.request_type, "message": self.message, "line": self.line}


@dataclass(frozen=True)
class CatalogEntry:
    """The resolved catalog knowledge for one RT code.

    ``state`` is ``OK``, ``CONFLICT`` or ``INCOMPLETE``. For a conflict,
    ``candidate_agencies``/``candidate_skills`` list every competing value so
    a reviewer can see the disagreement; ``agency`` is then ``None``.
    """

    code: str
    state: str
    agency: Optional[str]
    required_skills: Tuple[str, ...]
    citizen_label: Optional[str] = None
    complaint_family: Optional[str] = None
    dispatchable: Optional[bool] = True
    candidate_agencies: Tuple[str, ...] = ()
    candidate_skills: Tuple[Tuple[str, ...], ...] = ()
    source_lines: Tuple[int, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state == "OK"


@dataclass
class Catalog:
    """In-memory, read-only view of the shared request catalog."""

    path: Optional[Path]
    entries: Dict[str, CatalogEntry]
    issues: List[CatalogIssue] = field(default_factory=list)
    sha256: str = ""
    rows_read: int = 0

    # ------------------------------------------------------------------ API
    def lookup(self, code: str) -> Optional[CatalogEntry]:
        """Return the entry for an exact RT code, or ``None`` if unknown."""
        return self.entries.get(code)

    def codes(self) -> List[str]:
        return sorted(self.entries)

    @property
    def version(self) -> str:
        """Short content hash; identical content gives identical versions on any OS."""
        return self.sha256[:12] if self.sha256 else "unversioned"

    def integrity_report(self) -> Dict[str, object]:
        """Summary used by the ``catalog-check`` CLI command and the tests."""
        states: Dict[str, int] = {}
        for e in self.entries.values():
            states[e.state] = states.get(e.state, 0) + 1
        return {
            "path": str(self.path) if self.path else None,
            "sha256": self.sha256,
            "rows_read": self.rows_read,
            "codes": len(self.entries),
            "states": states,
            "agencies": sorted({e.agency for e in self.entries.values() if e.agency}),
            "skills": sorted({s for e in self.entries.values() for s in e.required_skills}),
            "issues": [i.to_dict() for i in self.issues],
            "usable": all(e.ok for e in self.entries.values()) and not any(
                i.code in ("MALFORMED_CODE",) for i in self.issues),
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def _parse_bool(raw: str) -> Optional[bool]:
    """Parse the catalog's ``dispatchable`` cell; ``None`` if it is unreadable."""
    token = raw.strip().upper()
    if token in TRUE_VALUES:
        return True
    if token in FALSE_VALUES:
        return False
    return None


def _split_skills(raw: str) -> Tuple[str, ...]:
    skills = [s.strip().upper() for s in raw.split(MULTI_VALUE_SEPARATOR)]
    # Preserve order but drop blanks and repeats.
    seen: List[str] = []
    for s in skills:
        if s and s not in seen:
            seen.append(s)
    return tuple(seen)


def content_hash(text: str) -> str:
    """SHA-256 of the catalog text with a leading BOM removed and newlines normalised.

    Normalising makes the version identical on Windows (CRLF) and Unix (LF)
    checkouts, so the referral knowledge is not disabled by a line-ending change.
    """
    normalised = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def catalog_content_hash(path: PathLike) -> str:
    """Content hash of a catalog file (see :func:`content_hash`)."""
    return content_hash(Path(path).read_bytes().decode("utf-8-sig"))


def load_catalog(path: PathLike, strict: bool = False) -> Catalog:
    """Read and validate the catalog CSV.

    Args:
        path: location of ``request_catalog.csv``.
        strict: raise :class:`CatalogError` on any conflict, incomplete entry or
            malformed code instead of loading the remaining valid entries.

    Returns:
        A :class:`Catalog`. In non-strict mode problems are recorded in
        ``catalog.issues`` and affected codes resolve to ``UNRESOLVED`` at
        routing time, so a single bad row never takes down the whole router.
    """
    p = Path(path)
    if not p.is_file():
        raise CatalogError(f"Catalog file not found: {p}")
    text = p.read_bytes().decode("utf-8-sig")
    return _build_catalog(text.splitlines(), path=p, sha256=content_hash(text), strict=strict)


def catalog_from_rows(rows: Iterable[Dict[str, str]], strict: bool = False) -> Catalog:
    """Build a catalog from dictionaries (used by tests and simulations)."""
    rows = list(rows)
    if not rows:
        raise CatalogError("Catalog has no rows")
    # Serialise through the csv module so quoting behaves exactly like a file.
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    text = out.getvalue()
    return _build_catalog(text.splitlines(), path=None, sha256=content_hash(text), strict=strict)


def _build_catalog(lines: List[str], path: Optional[Path], sha256: str, strict: bool) -> Catalog:
    reader = csv.DictReader(lines)
    columns = [c.strip() for c in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise CatalogError(f"Catalog is missing required column(s): {', '.join(missing)}")
    has_dispatchable = "dispatchable" in columns

    issues: List[CatalogIssue] = []
    grouped: Dict[str, List[Tuple[int, Dict[str, str]]]] = {}
    rows_read = 0
    for line_no, row in enumerate(reader, start=2):  # header is line 1
        row = {(k or "").strip(): v for k, v in row.items()}
        rows_read += 1
        code = _clean(row.get("request_type_code")).upper()
        if not CODE_PATTERN.match(code):
            issues.append(CatalogIssue("MALFORMED_CODE", code or "<blank>",
                                       f"Row skipped: request_type_code {code!r} is not RT followed by 3 digits.",
                                       line_no))
            continue
        grouped.setdefault(code, []).append((line_no, row))

    entries: Dict[str, CatalogEntry] = {}
    for code, items in grouped.items():
        parsed = []
        for line_no, row in items:
            agency = _clean(row.get("agency")).upper() or None
            skills = _split_skills(_clean(row.get("required_skill")))
            dispatchable: Optional[bool] = True
            if has_dispatchable:
                dispatchable = _parse_bool(_clean(row.get("dispatchable")))
                if dispatchable is None:
                    issues.append(CatalogIssue(
                        "UNREADABLE_DISPATCHABLE", code,
                        f"dispatchable value {row.get('dispatchable')!r} is not TRUE/FALSE; treated as not "
                        "dispatchable (fail-safe).", line_no))
            parsed.append((line_no, agency, skills, dispatchable,
                           _clean(row.get("citizen_label")) or None, _clean(row.get("complaint_family")) or None))

        lines_used = tuple(p[0] for p in parsed)
        distinct = {(p[1], p[2], p[3]) for p in parsed}
        first = parsed[0]
        if len(parsed) > 1 and len(distinct) == 1:
            issues.append(CatalogIssue("DUPLICATE_IDENTICAL", code,
                                       f"{len(parsed)} identical rows; collapsed to one.", lines_used[1]))
        if len(distinct) > 1:
            agencies = tuple(sorted({p[1] or "<blank>" for p in parsed}))
            skill_sets = tuple(sorted({p[2] for p in parsed}))
            issues.append(CatalogIssue("CONFLICT", code,
                                       f"Rows disagree (agencies {list(agencies)}, skills "
                                       f"{[list(s) for s in skill_sets]}); no row is chosen.", lines_used[1]))
            entries[code] = CatalogEntry(code=code, state="CONFLICT", agency=None, required_skills=(),
                                         citizen_label=first[4], complaint_family=first[5], dispatchable=None,
                                         candidate_agencies=agencies, candidate_skills=skill_sets,
                                         source_lines=lines_used)
            continue

        _, agency, skills, dispatchable, label, family = first
        state = "OK"
        if agency is None:
            issues.append(CatalogIssue("MISSING_AGENCY", code, "Agency cell is blank.", first[0]))
            state = "INCOMPLETE"
        if not skills:
            issues.append(CatalogIssue("MISSING_SKILL", code, "required_skill cell is blank.", first[0]))
            state = "INCOMPLETE"
        entries[code] = CatalogEntry(code=code, state=state, agency=agency, required_skills=skills,
                                     citizen_label=label, complaint_family=family, dispatchable=dispatchable,
                                     source_lines=lines_used)

    catalog = Catalog(path=path, entries=entries, issues=issues, sha256=sha256, rows_read=rows_read)
    if not has_dispatchable:
        log.warning("Catalog has no 'dispatchable' column; every valid code is treated as dispatchable.")
    blocking = [i for i in issues if i.code in ("CONFLICT", "MISSING_AGENCY", "MISSING_SKILL", "MALFORMED_CODE")]
    if strict and blocking:
        raise CatalogError("Catalog failed strict validation: " + "; ".join(
            f"{i.request_type}: {i.code}" for i in blocking))
    for issue in issues:
        log.warning("Catalog issue %s for %s: %s", issue.code, issue.request_type, issue.message)
    log.info("Loaded catalog %s: %d codes from %d rows (%d issues)",
             catalog.version, len(entries), rows_read, len(issues))
    return catalog


# ---------------------------------------------------------------------------
# Agency names from the matrix glossary (presentation only).
# ---------------------------------------------------------------------------
def load_agency_names(matrix_path: PathLike) -> Dict[str, str]:
    """Parse the '## Acronym glossary' table of ``NYC311_agency_matrix.md``.

    Returns ``{}`` if the file has no glossary; the router then simply leaves
    ``agency_name`` empty. Raises ``FileNotFoundError`` for a missing file so a
    misconfigured path is noticed.
    """
    names: Dict[str, str] = {}
    in_glossary = False
    for line in Path(matrix_path).read_text(encoding="utf-8").splitlines():
        if line.strip().lower().startswith("## acronym glossary"):
            in_glossary = True
            continue
        if in_glossary and line.startswith("## "):
            break
        if not in_glossary or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("Acronym",) or set(cells[0]) <= {"-", ":"}:
            continue
        names[cells[0].upper()] = cells[1]
    return names
