"""C05 Duplicate & Cross-Channel Detection. Owner: Nabeel Taresh.

Plug and play. One call, sensible defaults (production strategy `hybrid_evidence`):

    from components import duplicate                 # or: from components import c05_duplicate
    r = duplicate.run(facts, candidates)
    r.relation               # DuplicateRelation.NEW | DUPLICATE | RECURRENCE
    r.duplicate_of           # request_id of the open incident, when DUPLICATE
    r.corroboration_count    # distinct reporters on the incident
    r.explain()              # plain-English reasons

Inputs
    facts       RequestFacts. The new report: id, created_at, type code, address, BBL, lat/lon,
                channel, citizen_id. resolution_description is never an input.
    candidates  list[DuplicateCandidate]. Nearby earlier reports from the store (same type,
                inside about 300 m), each with group_id and is_open / closed_at.
    strategy    optional. One of STRATEGIES. Default PRODUCTION_STRATEGY, or env C05_STRATEGY.

Output
    DuplicateResult (contract v1.0.0): relation, duplicate_group_id, is_primary, duplicate_of,
    recurrence_of, match_confidence, corroboration_count, distinct_channel_count,
    auto_close_recommended, match_evidence. On any doubt: NEW, is_primary=True.

Who reads it
    C06 fake      corroboration_count lowers unfounded risk.
    C07           RECURRENCE (same place and type after closure) feeds escalation; at closure the
                  credibility update goes to every reporter in duplicate_group_id.
    C10 / C11     corroboration and is_primary feed urgency and the priority score; only the
                  primary is queued, so one crew goes per incident.
    C08 does not read C05 (it is location context only).

Safety rules (tested for every strategy)
    different types never merge; only OPEN candidates absorb a DUPLICATE; the same place after
    closure is RECURRENCE; coordinates beyond HARD_MAX_DISTANCE_M never merge.

Tuning: 25 m / 6 h globally (BBL keeps 48 h), plus HYBRID_TYPE_PARAMS per type. Evidence in
documents/C05_C06_changelog/RESULTS.md. Experiments live in tools/c05_c06_eval.py, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from enum import Enum
from typing import Callable, Optional
import math
import os
import re
import time

import numpy as np

from components.common import ComponentResult, Reason


# ============================================================================
# Types (contract v1.0.0)
# ============================================================================

class DuplicateRelation(str, Enum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"
    RECURRENCE = "RECURRENCE"


@dataclass
class RequestFacts:
    """Read-only slice of a request that C05 and C06 are allowed to see."""

    request_id: str
    created_at: datetime
    borough: str
    request_type_code: str
    complaint_type: str
    complaint_family: str
    channel: str
    descriptor: Optional[str] = None
    location_type: Optional[str] = None
    incident_address: Optional[str] = None
    incident_zip: Optional[str] = None
    address_type: Optional[str] = None
    bbl: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    citizen_id: Optional[str] = None
    free_text: Optional[str] = None


@dataclass
class DuplicateCandidate:
    """An existing nearby request. The caller (store or test) supplies these."""

    facts: RequestFacts
    group_id: str
    is_open: bool
    closed_at: Optional[datetime] = None
    reporters: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()


@dataclass
class DuplicateResult(ComponentResult):
    """C05 output. SAFE_DEFAULT: relation=NEW and is_primary=True."""

    relation: DuplicateRelation = DuplicateRelation.NEW
    duplicate_group_id: str = ""
    is_primary: bool = True
    duplicate_of: Optional[str] = None
    recurrence_of: Optional[str] = None
    match_confidence: float = 0.0
    corroboration_count: int = 1
    distinct_channel_count: int = 1
    auto_close_recommended: bool = False
    match_evidence: list[Reason] = field(default_factory=list)
    model_version: Optional[str] = None


# ============================================================================
# Config
# ============================================================================

# Open-incident window: two reports of the same type inside this span can be one incident.
OPEN_WINDOW_HOURS = 48.0


# After a crew closes the job, a later report at the same place is a recurrence.
RECURRENCE_WINDOW_DAYS = 30.0


# Distance used when address_type is ADDRESS and coordinates look usable.
ADDRESS_RADIUS_M = 150.0


# INTERSECTION / BLOCKFACE pins are noisy. Use a tighter cap and require extra evidence.
UNRELIABLE_RADIUS_M = 75.0


# Hard safety: if both points have coordinates beyond this, never merge.
# The store already filters at 300 m; this stops a far pair that still shares a street name.
HARD_MAX_DISTANCE_M = 500.0


# Pair score that must be reached before DUPLICATE or RECURRENCE.
MIN_MATCH_SCORE = 0.45


UNRELIABLE_ADDRESS_TYPES = frozenset({"INTERSECTION", "BLOCKFACE"})


FUZZY_STRONG = 0.88


FUZZY_WEAK = 0.72


# Spatio-temporal clustering: 48 h of time maps onto ADDRESS_RADIUS_M of space.
CLUSTER_EPS_M = 160.0


CLUSTER_MIN_SAMPLES = 2


# Tuned hybrid_evidence rule (C05 tuning on hand labels, 2026-10-14).
# A DUPLICATE needs an OPEN same-type candidate inside HYBRID_WINDOW_HOURS and
# location evidence: same BBL, or points within HYBRID_RADIUS_M, or (only when
# the location is weak) address text similarity >= HYBRID_FUZZY_CUTOFF.
# Values come from the tuning in RESULTS.md, Part 2.
HYBRID_RADIUS_M = 25.0         # no-BBL ADDRESS points must be this close


HYBRID_WINDOW_HOURS = 6.0       # window for radius and fuzzy links


HYBRID_BBL_NEEDS_WINDOW = False  # a BBL match keeps the 48 h OPEN_WINDOW_HOURS


HYBRID_FUZZY_CUTOFF = 0.95     # weak location only; tuned on synthetic weak-coordinate pairs (2026-10-15)


HYBRID_TRANSITIVE = False        # graph closure gave no gain; off


# Chronic-site rule (2026-10-15). When at least HYBRID_HOTSPOT_AT open reports
# link to the new report, the site is a hot spot. Then B joins only the
# HYBRID_GROUP_CAP most recent open reports. None turns the rule off.
# Experiment E1 (2026-10-15) failed: the cap dropped A-view F1 from 0.92 to 0.16-0.49. Kept off.
HYBRID_HOTSPOT_AT = None


HYBRID_GROUP_CAP = None


# Per-type override for hybrid_evidence: {request_type_code: (radius_m, window_h, bbl_needs_window)}.
# A type not in the dict uses the global values above. Tuned by tools/c05_c06_eval.py c05-exp --per-type
# on January 2026 NYC 311 silver pairs (2026-10-15, E5). Radius 0 = BBL (exact site) only.
# Types with < 30 January DUPLICATE pairs keep the global values.
HYBRID_TYPE_PARAMS: dict[str, tuple[float, float, bool]] = {
    "RT001": (0.0, 48.0, False),
    "RT020": (25.0, 12.0, False),
    "RT023": (25.0, 48.0, False),
    "RT024": (50.0, 48.0, False),
    "RT025": (0.0, 6.0, False),
    "RT042": (25.0, 6.0, True),
    "RT080": (0.0, 24.0, True),
    "RT082": (0.0, 24.0, True),
    "RT100": (25.0, 12.0, False),
}


PRODUCTION_STRATEGY = "hybrid_evidence"


STRATEGIES = (
    "exact_key",
    "radius_time",
    "fuzzy_address",
    "dbscan",
    "hdbscan",
    "graph",
    "hybrid_evidence",
    "hybrid_no_fuzzy",  # ablation
    "hybrid_legacy",    # pre-tuning hybrid_evidence, kept for comparison
)


# ============================================================================
# Geometry and address helpers
# ============================================================================

_STREET_MAP = (
    (r"\bSTREET\b", "ST"),
    (r"\bAVENUE\b", "AVE"),
    (r"\bBOULEVARD\b", "BLVD"),
    (r"\bPLACE\b", "PL"),
    (r"\bROAD\b", "RD"),
    (r"\bDRIVE\b", "DR"),
    (r"\bLANE\b", "LN"),
    (r"\bCOURT\b", "CT"),
    (r"\bPARKWAY\b", "PKWY"),
    (r"\bEAST\b", "E"),
    (r"\bWEST\b", "W"),
    (r"\bNORTH\b", "N"),
    (r"\bSOUTH\b", "S"),
)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def hours_between(a, b) -> float:
    return abs((a - b).total_seconds()) / 3600.0


def usable_bbl(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    # CSV round-trips turn BBL 2048330028 into "2048330028.0"; treat both as one key.
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    if not s or s in {"0", "0000000000", "<NA>", "nan", "None"}:
        return None
    return s


def normalize_address(text: str | None) -> str:
    if not text:
        return ""
    t = re.sub(r"[^A-Z0-9 ]+", " ", str(text).upper())
    for pat, repl in _STREET_MAP:
        t = re.sub(pat, repl, t)
    return " ".join(t.split())


def address_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_address(a), normalize_address(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def coords_of(facts) -> tuple[float, float] | None:
    if facts.latitude is None or facts.longitude is None:
        return None
    return float(facts.latitude), float(facts.longitude)


def pair_distance_m(a, b) -> float | None:
    ca, cb = coords_of(a), coords_of(b)
    if ca is None or cb is None:
        return None
    return haversine_m(ca[0], ca[1], cb[0], cb[1])


def address_type_of(facts, other=None) -> str:
    raw = facts.address_type or (other.address_type if other is not None else None) or "ADDRESS"
    return str(raw).upper()


def coords_are_reliable(address_type: str) -> bool:
    return address_type not in UNRELIABLE_ADDRESS_TYPES


def local_metres_and_hours(origin, other, metres_per_hour: float) -> tuple[float, float, float] | None:
    """Project `other` into a local (east_m, north_m, time_m) frame around origin."""
    oc, tc = coords_of(origin), coords_of(other)
    if oc is None or tc is None:
        return None
    east = haversine_m(oc[0], oc[1], oc[0], tc[1])
    if tc[1] < oc[1]:
        east = -east
    north = haversine_m(oc[0], oc[1], tc[0], oc[1])
    if tc[0] < oc[0]:
        north = -north
    time_m = hours_between(origin.created_at, other.created_at) * metres_per_hour
    return east, north, time_m


# ============================================================================
# Pair score
# ============================================================================

@dataclass
class PairMatch:
    candidate: DuplicateCandidate
    score: float
    distance_m: float | None
    hours: float
    address_sim: float
    bbl_match: bool
    evidence: list[Reason] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.candidate.is_open


def _reason(code: str, message: str, weight: float | None = None,
            evidence: dict | None = None, **extra) -> Reason:
    payload = dict(evidence or {})
    payload.update(extra)
    return Reason(code=code, message=message[:280], weight=weight, evidence=payload)


def pair_match(facts: RequestFacts, candidate: DuplicateCandidate, *, use_fuzzy: bool = True) -> PairMatch:
    """Score one candidate. Different types and far coordinates score 0 (hard safety)."""
    other = candidate.facts
    hours = hours_between(facts.created_at, other.created_at)
    dist = pair_distance_m(facts, other)
    sim = address_similarity(facts.incident_address, other.incident_address)
    a_bbl, b_bbl = usable_bbl(facts.bbl), usable_bbl(other.bbl)
    bbl_match = bool(a_bbl and b_bbl and a_bbl == b_bbl)
    empty = PairMatch(candidate, 0.0, dist, hours, sim, bbl_match, [])

    if facts.request_type_code != other.request_type_code:
        return empty

    if dist is not None and dist > HARD_MAX_DISTANCE_M:
        return empty

    atype = address_type_of(facts, other)
    reliable = coords_are_reliable(atype)
    radius = ADDRESS_RADIUS_M if reliable else UNRELIABLE_RADIUS_M
    score = 0.0
    evidence: list[Reason] = []

    if bbl_match:
        score += 0.50
        evidence.append(_reason(
            "BBL_KEY_MATCH",
            f"Same tax lot BBL {a_bbl} and the same request type",
            0.50, bbl=a_bbl,
        ))

    if dist is not None:
        if dist <= radius:
            geo = 0.40 + 0.45 * (1.0 - dist / max(radius, 1.0))
            score += geo
            evidence.append(_reason(
                "DISTANCE_WITHIN_RADIUS",
                f"{dist:.0f} m apart, inside the {radius:.0f} m {atype.lower()} limit",
                round(geo, 3), distance_m=round(dist, 1), radius_m=radius, address_type=atype,
            ))
        elif reliable and dist <= ADDRESS_RADIUS_M * 1.4:
            # Soft collar for ADDRESS only. Unreliable types do not get this.
            geo = 0.20 * (1.0 - (dist - radius) / (ADDRESS_RADIUS_M * 0.4))
            if geo > 0:
                score += geo
                evidence.append(_reason(
                    "DISTANCE_NEAR_RADIUS",
                    f"{dist:.0f} m apart, just outside the {radius:.0f} m limit",
                    round(geo, 3), distance_m=round(dist, 1),
                ))

    if use_fuzzy:
        if (dist is None or not reliable) and sim >= FUZZY_STRONG:
            score += 0.40
            evidence.append(_reason(
                "FUZZY_ADDRESS_STRONG",
                f"Addresses match closely ({sim:.2f}); coordinates are missing or weak",
                0.40, similarity=round(sim, 3),
            ))
        elif (dist is None or not reliable) and sim >= FUZZY_WEAK:
            score += 0.22
            evidence.append(_reason(
                "FUZZY_ADDRESS_WEAK",
                f"Addresses are similar ({sim:.2f})",
                0.22, similarity=round(sim, 3),
            ))
        elif sim >= FUZZY_STRONG and dist is not None and dist <= radius:
            score += 0.10
            evidence.append(_reason(
                "ADDRESS_TEXT_AGREES",
                f"Same street text ({sim:.2f}) and the points are close",
                0.10, similarity=round(sim, 3),
            ))

    if hours <= OPEN_WINDOW_HOURS:
        evidence.append(_reason(
            "WITHIN_OPEN_WINDOW",
            f"{hours:.1f} h apart, inside the {OPEN_WINDOW_HOURS:.0f} h open window",
            evidence={"hours": round(hours, 2)},
        ))
    elif hours <= RECURRENCE_WINDOW_DAYS * 24:
        evidence.append(_reason(
            "WITHIN_RECURRENCE_WINDOW",
            f"{hours:.1f} h apart, inside the {RECURRENCE_WINDOW_DAYS:.0f} day recurrence window",
            evidence={"hours": round(hours, 2)},
        ))

    return PairMatch(candidate, min(1.0, score), dist, hours, sim, bbl_match, evidence)


def in_open_window(match: PairMatch) -> bool:
    return match.hours <= OPEN_WINDOW_HOURS


def in_recurrence_window(match: PairMatch) -> bool:
    return match.hours <= RECURRENCE_WINDOW_DAYS * 24


# ============================================================================
# Strategies
# ============================================================================

StrategyFn = Callable[[RequestFacts, list[DuplicateCandidate]], list[PairMatch]]


def _same_type(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[DuplicateCandidate]:
    return [c for c in candidates if c.facts.request_type_code == facts.request_type_code]


def exact_key(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    """BBL + type. Fast and precise. Misses rows with no BBL (the acceptance fixture has none)."""
    key = usable_bbl(facts.bbl)
    if not key:
        return []
    out = []
    for c in _same_type(facts, candidates):
        if usable_bbl(c.facts.bbl) != key:
            continue
        m = pair_match(facts, c, use_fuzzy=False)
        if m.bbl_match and m.score > 0:
            out.append(m)
    return out


def radius_time(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    """Fixed radius + time window. The benchmark every other method must beat."""
    out = []
    for c in _same_type(facts, candidates):
        m = pair_match(facts, c, use_fuzzy=False)
        if m.distance_m is None:
            continue
        if m.score >= MIN_MATCH_SCORE:
            out.append(m)
    return out


def fuzzy_address(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    """Address text similarity. Helps BLOCKFACE / missing geocode. Safety still blocks far coordinates."""
    out = []
    for c in _same_type(facts, candidates):
        m = pair_match(facts, c, use_fuzzy=True)
        if m.address_sim <= 0 and m.distance_m is None:
            continue
        if m.score >= MIN_MATCH_SCORE:
            out.append(m)
    return out


def _cluster_matches(
    facts: RequestFacts,
    candidates: list[DuplicateCandidate],
    labels: np.ndarray,
) -> list[PairMatch]:
    """Keep candidates that share the new request's cluster (label of index 0)."""
    if labels[0] < 0:
        return []
    cluster = labels[0]
    out = []
    for i, c in enumerate(candidates, start=1):
        if labels[i] != cluster:
            continue
        m = pair_match(facts, c, use_fuzzy=True)
        if m.score > 0:
            out.append(m)
    return out


def _feature_matrix(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> np.ndarray | None:
    metres_per_hour = ADDRESS_RADIUS_M / OPEN_WINDOW_HOURS
    rows = [local_metres_and_hours(facts, facts, metres_per_hour)]
    for c in candidates:
        rows.append(local_metres_and_hours(facts, c.facts, metres_per_hour))
    if any(r is None for r in rows):
        return None
    return np.asarray(rows, dtype=float)


def dbscan_cluster(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    """DBSCAN in a metres + scaled-hours space. Falls back to radius/time if a point has no coordinates."""
    typed = _same_type(facts, candidates)
    X = _feature_matrix(facts, typed)
    if X is None or len(X) < 2:
        return radius_time(facts, candidates)
    from sklearn.cluster import DBSCAN

    labels = DBSCAN(eps=CLUSTER_EPS_M, min_samples=CLUSTER_MIN_SAMPLES).fit_predict(X)
    hits = _cluster_matches(facts, typed, labels)
    return hits if hits else radius_time(facts, candidates)


def _single_linkage_labels(X: np.ndarray, eps: float) -> np.ndarray:
    """Flat single-linkage cut at eps; groups of one become noise (-1).

    With min_samples=1 the HDBSCAN mutual-reachability distance equals the plain
    distance, so this matches cluster_selection_epsilon=eps for min_cluster_size=2.
    """
    from scipy.cluster.hierarchy import fcluster, linkage

    raw = fcluster(linkage(X, method="single"), t=eps, criterion="distance")
    counts = np.bincount(raw)
    return np.where(counts[raw] >= 2, raw, -1)


def _hdbscan_labels(X: np.ndarray) -> np.ndarray:
    """HDBSCAN labels. scikit-learn 1.9 can raise TypeError inside the epsilon
    search (sklearn/cluster/_hdbscan/_tree.pyx, epsilon_search) on larger inputs.
    Then use the equivalent single-linkage cut."""
    try:
        from sklearn.cluster import HDBSCAN
    except ImportError:
        return _single_linkage_labels(X, CLUSTER_EPS_M)
    try:
        return HDBSCAN(min_cluster_size=2, min_samples=1,
                       cluster_selection_epsilon=CLUSTER_EPS_M).fit_predict(X)
    except (TypeError, ValueError):
        return _single_linkage_labels(X, CLUSTER_EPS_M)


def hdbscan_cluster(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    """HDBSCAN for mixed density. Two-point groups are common at intake, so min_cluster_size=2."""
    typed = _same_type(facts, candidates)
    X = _feature_matrix(facts, typed)
    if X is None or len(X) < 2:
        return radius_time(facts, candidates)
    labels = _hdbscan_labels(X)
    hits = _cluster_matches(facts, typed, labels)
    return hits if hits else radius_time(facts, candidates)


def graph_components(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    """Pairwise edges above the score floor, then the connected component that contains the new request."""
    typed = _same_type(facts, candidates)
    matches = [pair_match(facts, c, use_fuzzy=True) for c in typed]
    edges = [m for m in matches if m.score >= MIN_MATCH_SCORE]
    if not edges:
        return []
    try:
        import networkx as nx

        g = nx.Graph()
        g.add_node("new")
        for m in edges:
            nid = m.candidate.facts.request_id
            g.add_node(nid)
            g.add_edge("new", nid, weight=m.score)
        # Also link high-scoring candidates to each other so cascades become one group.
        by_id = {m.candidate.facts.request_id: m.candidate for m in matches}
        ids = list(by_id)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                cross = pair_match(by_id[a].facts, by_id[b], use_fuzzy=True)
                if cross.score >= MIN_MATCH_SCORE:
                    g.add_edge(a, b, weight=cross.score)
        keep = set(nx.node_connected_component(g, "new")) - {"new"}
        return [m for m in edges if m.candidate.facts.request_id in keep]
    except ImportError:
        return edges


def hybrid_legacy(facts: RequestFacts, candidates: list[DuplicateCandidate], *, use_fuzzy: bool = True) -> list[PairMatch]:
    """Pre-tuning production blend (score >= MIN_MATCH_SCORE, then graph group). Kept for comparison."""
    typed = _same_type(facts, candidates)
    matches = [pair_match(facts, c, use_fuzzy=use_fuzzy) for c in typed]
    hits = [m for m in matches if m.score >= MIN_MATCH_SCORE]
    if not hits:
        return []
    grouped = graph_components(facts, [m.candidate for m in hits])
    return grouped if grouped else hits


def _location_evidence(a: RequestFacts, b: RequestFacts, m: PairMatch, *, radius_m: float,
                       fuzzy_cutoff: float | None, use_fuzzy: bool) -> str | None:
    """Return the rule that links a and b by place, or None."""
    if m.distance_m is not None and m.distance_m > HARD_MAX_DISTANCE_M:
        return None
    if m.bbl_match:
        return "bbl"
    weak = m.distance_m is None or not coords_are_reliable(address_type_of(a, b))
    if not weak and m.distance_m <= radius_m:
        return "radius"
    if weak and m.distance_m is not None and m.distance_m <= min(radius_m, UNRELIABLE_RADIUS_M):
        return "radius"
    if use_fuzzy and weak and fuzzy_cutoff is not None and m.address_sim >= fuzzy_cutoff:
        return "fuzzy"
    return None


def _hybrid_link(a: RequestFacts, cand: DuplicateCandidate, m: PairMatch, p: dict) -> str | None:
    if a.request_type_code != cand.facts.request_type_code:
        return None
    rule = _location_evidence(a, cand.facts, m, radius_m=p["radius_m"],
                              fuzzy_cutoff=p["fuzzy_cutoff"], use_fuzzy=p["use_fuzzy"])
    if rule is None:
        return None
    if cand.is_open:
        limit = p["window_h"] if (rule != "bbl" or p["bbl_needs_window"]) else OPEN_WINDOW_HOURS
        return rule if m.hours <= min(limit, OPEN_WINDOW_HOURS) else None
    # Closed candidate: only a recurrence, inside the recurrence window.
    return rule if in_recurrence_window(m) else None


def hybrid_evidence(facts: RequestFacts, candidates: list[DuplicateCandidate], *, use_fuzzy: bool = True,
                    radius_m: float | None = None, window_h: float | None = None,
                    bbl_needs_window: bool | None = None, fuzzy_cutoff: float | None = -1.0,
                    transitive: bool | None = None, hotspot_at: int | None = -1,
                    group_cap: int | None = -1, type_params: dict | None = None) -> list[PairMatch]:
    """Production blend (tuned 2026-10-14).

    Rules: same BBL, OR same-type points within radius_m, OR (weak location only)
    fuzzy address >= fuzzy_cutoff. OPEN candidates must also be inside window_h.
    Closed candidates can only give RECURRENCE (pick_relation). Optional graph
    closure joins candidates through each other; it is off by default because
    chains of near points merged distinct incidents.
    """
    tp = (HYBRID_TYPE_PARAMS if type_params is None else type_params).get(facts.request_type_code)
    if tp is not None and radius_m is None and window_h is None and bbl_needs_window is None:
        radius_m, window_h, bbl_needs_window = tp
    p = {
        "radius_m": HYBRID_RADIUS_M if radius_m is None else radius_m,
        "window_h": HYBRID_WINDOW_HOURS if window_h is None else window_h,
        "bbl_needs_window": HYBRID_BBL_NEEDS_WINDOW if bbl_needs_window is None else bbl_needs_window,
        "fuzzy_cutoff": HYBRID_FUZZY_CUTOFF if fuzzy_cutoff == -1.0 else fuzzy_cutoff,
        "use_fuzzy": use_fuzzy,
    }
    transitive = HYBRID_TRANSITIVE if transitive is None else transitive
    typed = _same_type(facts, candidates)
    hits: list[PairMatch] = []
    for c in typed:
        m = pair_match(facts, c, use_fuzzy=use_fuzzy)
        rule = _hybrid_link(facts, c, m, p)
        if rule is None:
            continue
        m.score = max(m.score, MIN_MATCH_SCORE)
        m.evidence.append(Reason(code="HYBRID_RULE", message=f"Linked by the {rule} rule",
                                 evidence={"rule": rule}))
        hits.append(m)
    if not transitive:
        return _chronic_cap(hits, HYBRID_HOTSPOT_AT if hotspot_at == -1 else hotspot_at,
                            HYBRID_GROUP_CAP if group_cap == -1 else group_cap)
    # Graph closure over OPEN candidates only: a candidate joins when it links to a hit.
    hit_ids = {m.candidate.facts.request_id for m in hits}
    rest = [c for c in typed if c.is_open and c.facts.request_id not in hit_ids]
    changed = True
    while changed and rest:
        changed = False
        for c in list(rest):
            for h in [m for m in hits if m.candidate.is_open]:
                cross = pair_match(h.candidate.facts, c, use_fuzzy=use_fuzzy)
                if _hybrid_link(h.candidate.facts, c, cross, p) is None:
                    continue
                m = pair_match(facts, c, use_fuzzy=use_fuzzy)
                if not in_open_window(m) or (m.distance_m is not None and m.distance_m > HARD_MAX_DISTANCE_M):
                    continue
                m.score = max(m.score, MIN_MATCH_SCORE)
                m.evidence.append(Reason(code="HYBRID_RULE", message="Linked through another open report",
                                         evidence={"rule": "graph", "via": h.candidate.facts.request_id}))
                hits.append(m)
                rest.remove(c)
                changed = True
                break
    return _chronic_cap(hits, HYBRID_HOTSPOT_AT if hotspot_at == -1 else hotspot_at,
                        HYBRID_GROUP_CAP if group_cap == -1 else group_cap)


def _chronic_cap(hits: list[PairMatch], hotspot_at: int | None, group_cap: int | None) -> list[PairMatch]:
    """Chronic-site rule. Many open links at one place mean a hot spot, not one incident.

    Then keep only the `group_cap` most recent open links. Closed links stay
    (they can only give RECURRENCE). The rule never adds a link.
    """
    if not hotspot_at or not group_cap:
        return hits
    open_hits = [m for m in hits if m.is_open and in_open_window(m)]
    if len(open_hits) < hotspot_at:
        return hits
    keep = {id(m) for m in sorted(open_hits, key=lambda m: m.hours)[:group_cap]}
    out = []
    for m in hits:
        if m.is_open and in_open_window(m):
            if id(m) not in keep:
                continue
            m.evidence.append(Reason(
                code="CHRONIC_SITE",
                message=f"{len(open_hits)} open reports here; linked to the {group_cap} most recent only",
                evidence={"open_links": len(open_hits), "group_cap": group_cap}))
        out.append(m)
    return out


def hybrid_no_fuzzy(facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    return hybrid_evidence(facts, candidates, use_fuzzy=False)


REGISTRY: dict[str, StrategyFn] = {
    "exact_key": exact_key,
    "radius_time": radius_time,
    "fuzzy_address": fuzzy_address,
    "dbscan": dbscan_cluster,
    "hdbscan": hdbscan_cluster,
    "graph": graph_components,
    "hybrid_evidence": hybrid_evidence,
    "hybrid_no_fuzzy": hybrid_no_fuzzy,
    "hybrid_legacy": hybrid_legacy,
}


def run_strategy(name: str, facts: RequestFacts, candidates: list[DuplicateCandidate]) -> list[PairMatch]:
    if name not in REGISTRY:
        raise ValueError(f"Unknown C05 strategy {name!r}. Choose one of {STRATEGIES}")
    return REGISTRY[name](facts, candidates)


def pick_relation(matches: list[PairMatch]) -> PairMatch | None:
    """OPEN hits in the 48 h window win. Otherwise a closed hit in the 30 day window is a recurrence."""
    open_hits = [m for m in matches if m.is_open and in_open_window(m)]
    if open_hits:
        return max(open_hits, key=lambda m: (m.score, -m.hours))
    closed_hits = [m for m in matches if (not m.is_open) and in_recurrence_window(m)]
    if closed_hits:
        return max(closed_hits, key=lambda m: (m.score, -m.hours))
    return None


# ============================================================================
# Entry point
# ============================================================================

VERSION = "0.1.0"


COMPONENT = "C05 duplicate and cross-channel"


def _group_id_for(facts: RequestFacts) -> str:
    parts = facts.request_id.split("-", 1)
    return "DG-" + (parts[1] if len(parts) == 2 else facts.request_id)


def _counts(facts: RequestFacts, chosen) -> tuple[int, int]:
    reporters = set(chosen.candidate.reporters)
    channels = set(chosen.candidate.channels)
    if facts.citizen_id:
        reporters.add(facts.citizen_id)
    if facts.channel:
        channels.add(facts.channel)
    return max(1, len(reporters)), max(1, len(channels))


def run(facts: RequestFacts, candidates: list[DuplicateCandidate],
        strategy: str | None = None) -> DuplicateResult:
    t0 = time.perf_counter()
    method = strategy or os.environ.get("C05_STRATEGY") or PRODUCTION_STRATEGY
    matches = run_strategy(method, facts, candidates or [])
    chosen = pick_relation(matches)
    latency = (time.perf_counter() - t0) * 1000

    if chosen is None:
        return DuplicateResult(
            component=COMPONENT, version=VERSION, latency_ms=latency,
            model_version=method, ok=True,
            relation=DuplicateRelation.NEW,
            duplicate_group_id=_group_id_for(facts),
            is_primary=True,
            match_confidence=0.0,
            corroboration_count=1,
            distinct_channel_count=1,
            match_evidence=[],
            reasons=[Reason(
                code="NO_OPEN_MATCH",
                message=f"No open {facts.complaint_type.lower()} report within 150 m in the last 48 h",
            )],
        )

    reporters, channels = _counts(facts, chosen)
    if chosen.is_open:
        auto_close = chosen.score >= 0.9 and reporters >= 2
        return DuplicateResult(
            component=COMPONENT, version=VERSION, latency_ms=latency,
            model_version=method, ok=True,
            relation=DuplicateRelation.DUPLICATE,
            duplicate_group_id=chosen.candidate.group_id,
            is_primary=False,
            duplicate_of=chosen.candidate.facts.request_id,
            match_confidence=round(float(chosen.score), 4),
            corroboration_count=reporters,
            distinct_channel_count=channels,
            auto_close_recommended=auto_close,
            match_evidence=chosen.evidence,
            reasons=[Reason(
                code="LINKED_OPEN_INCIDENT",
                message=(f"Same open incident as {chosen.candidate.facts.request_id} "
                         f"(score {chosen.score:.2f}, method {method})"),
                weight=round(float(chosen.score), 3),
                evidence={"strategy": method},
            )],
        )

    return DuplicateResult(
        component=COMPONENT, version=VERSION, latency_ms=latency,
        model_version=method, ok=True,
        relation=DuplicateRelation.RECURRENCE,
        duplicate_group_id=_group_id_for(facts),
        is_primary=True,
        recurrence_of=chosen.candidate.facts.request_id,
        match_confidence=round(float(chosen.score), 4),
        corroboration_count=1,
        distinct_channel_count=1,
        match_evidence=chosen.evidence,
        reasons=[Reason(
            code="SAME_PLACE_AFTER_CLOSURE",
            message=(f"Same place and type as closed request {chosen.candidate.facts.request_id}; "
                     "this is a recurrence, not a duplicate"),
            weight=round(float(chosen.score), 3),
            evidence={"strategy": method},
        )],
    )
