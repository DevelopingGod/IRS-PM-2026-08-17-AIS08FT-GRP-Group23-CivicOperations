"""Human-readable explanations for proximity decisions (no urgency language)."""

from __future__ import annotations

from typing import Optional

from .schemas import INCOMPLETE_REASONS, FacilityEvidence

CATEGORY_LABELS = {
    "HOSPITAL": "hospital", "SCHOOL": "school", "CHILDCARE": "childcare/kindergarten",
    "SENIOR_CARE": "nursing home/senior-care facility", "FIRE_STATION": "fire station",
    "POLICE": "police station", "TRANSIT_STATION": "rail/subway station",
}


def _fac(f: FacilityEvidence) -> str:
    label = CATEGORY_LABELS.get(f.facility_category, f.facility_category.lower())
    name = f" '{f.name}'" if f.name else " (unnamed in OSM)"
    where = "the request lies inside its mapped footprint" if f.distance_m == 0 else f"{f.distance_m:.0f} m away"
    return f"{label}{name}, {where}"


def explain_positive(triggering: list, regime: str, uncertainty_m: Optional[float]) -> str:
    top = triggering[0]
    more = len(triggering) - 1
    s = (f"A qualifying critical facility is within its configured geographic threshold: "
         f"{_fac(top)} (threshold {top.threshold_m:g} m).")
    if more > 0:
        cats = sorted({CATEGORY_LABELS.get(f.facility_category, f.facility_category) for f in triggering})
        s += f" {more} other qualifying facilit{'y' if more == 1 else 'ies'} also within threshold ({', '.join(cats)})."
    if regime == "ROBUST_INTERVAL":
        s += (f" The location is approximate (+/-{uncertainty_m:.0f} m), but the facility is within the "
              f"threshold for every point in that uncertainty radius.")
    s += " This is geographic evidence only; it does not set urgency."
    return s


def explain_negative(nearest_q: Optional[FacilityEvidence], regime: str, uncertainty_m: Optional[float]) -> str:
    if nearest_q is None:
        s = "No qualifying critical facility exists in the cached data for the active categories."
    else:
        s = (f"No qualifying critical facility is within its configured threshold. The nearest qualifying "
             f"facility is a {_fac(nearest_q)} (threshold {nearest_q.threshold_m:g} m).")
    if regime == "ROBUST_INTERVAL":
        s += f" This holds for every point within the location uncertainty (+/-{uncertainty_m:.0f} m)."
    return s


def explain_incomplete(reason: str, detail: str = "") -> str:
    base = INCOMPLETE_REASONS.get(reason, reason)
    s = f"Evaluation incomplete ({reason}): {base}"
    if detail:
        s += f" {detail}"
    s += " The factor is null, not 0, so Urgency can apply its own agreed fallback."
    return s


def explain_not_applicable(request_type: str, label: str = "") -> str:
    what = f"{request_type} ({label})" if label else request_type
    return (f"Context-aware mode: request type {what} describes a building-interior condition, so "
            f"street-level facility proximity is not applied (factor 0). The pure geographic evidence "
            f"is still reported in critical_facility_nearby.")
