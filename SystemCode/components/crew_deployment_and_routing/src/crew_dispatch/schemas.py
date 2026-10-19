"""Data contracts of the Dispatch component (see research/integration_contract.md)."""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

SCHEMA_VERSION = "civicops-dispatch-plan/1.0"
C11_QUEUE_STATUSES = ("provisional", "provisional_review", "hold")
# Fields Dispatch must NOT use for any decision (strict priority-input rule).
FORBIDDEN_PRIORITY_FIELDS = ("priority_band", "urgency_level", "priority_score", "c11_urgency",
                             "recommended_urgency", "queue_position", "agency_position", "base_priority_score",
                             "aging_bonus", "default_urgency")
# After-the-fact NYC311 columns: labels at most, never inputs (project split rule + Dispatch extension).
AFTER_THE_FACT_FIELDS = ("closed_date", "resolution_description", "outcome_class", "verified_outcome",
                         "resolution_hours", "status", "resolution_action_updated_date",
                         "log_resolution_hours", "resolution_valid")


class DispatchState(str, Enum):
    UNASSIGNED = "UNASSIGNED"
    PLANNED = "PLANNED"
    APPROVED_NOT_STARTED = "APPROVED_NOT_STARTED"
    EN_ROUTE = "EN_ROUTE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    PENDING_REVIEW = "PENDING_REVIEW"
    HELD = "HELD"
    CANCELLED = "CANCELLED"


LOCKED_STATES = {DispatchState.APPROVED_NOT_STARTED, DispatchState.EN_ROUTE, DispatchState.IN_PROGRESS}
TERMINAL_STATES = {DispatchState.COMPLETED, DispatchState.CANCELLED}

# Allowed execution-state transitions (anything else is rejected by the controller).
ALLOWED_TRANSITIONS = {
    DispatchState.UNASSIGNED: {DispatchState.PLANNED, DispatchState.PENDING_REVIEW, DispatchState.HELD, DispatchState.CANCELLED},
    DispatchState.PLANNED: {DispatchState.UNASSIGNED, DispatchState.PLANNED, DispatchState.APPROVED_NOT_STARTED,
                            DispatchState.PENDING_REVIEW, DispatchState.HELD, DispatchState.CANCELLED},
    DispatchState.PENDING_REVIEW: {DispatchState.UNASSIGNED, DispatchState.PLANNED, DispatchState.HELD, DispatchState.CANCELLED},
    DispatchState.HELD: {DispatchState.UNASSIGNED, DispatchState.PENDING_REVIEW, DispatchState.PLANNED, DispatchState.CANCELLED},
    DispatchState.APPROVED_NOT_STARTED: {DispatchState.EN_ROUTE, DispatchState.CANCELLED, DispatchState.UNASSIGNED},
    DispatchState.EN_ROUTE: {DispatchState.IN_PROGRESS, DispatchState.CANCELLED, DispatchState.UNASSIGNED},
    DispatchState.IN_PROGRESS: {DispatchState.COMPLETED, DispatchState.CANCELLED},
    DispatchState.COMPLETED: set(),
    DispatchState.CANCELLED: set(),
}
# Transitions out of a locked state back to UNASSIGNED (crew breakdown, staff override) are only
# accepted from an authoritative staff/Crew-Portal event, never from the optimiser.


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Crew:
    crew_id: str
    agency: str
    skills: frozenset
    depot_lat: float
    depot_lon: float
    work_time_budget_min: float
    home_borough: str | None = None

    @classmethod
    def from_record(cls, r: dict, default_budget: float = 420.0) -> "Crew":
        try:
            skills = r["skills"]
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.replace("|", ",").split(",") if s.strip()]
            budget = r.get("work_time_budget_min")
            budget = default_budget if budget is None or (isinstance(budget, float) and math.isnan(budget)) else float(budget)
            crew = cls(crew_id=str(r["crew_id"]), agency=str(r["agency"]).strip(), skills=frozenset(str(s) for s in skills),
                       depot_lat=float(r.get("depot_lat", r.get("current_latitude"))),
                       depot_lon=float(r.get("depot_lon", r.get("current_longitude"))),
                       work_time_budget_min=budget, home_borough=r.get("borough") or r.get("home_borough"))
        except (KeyError, TypeError, ValueError) as e:
            raise SchemaError(f"invalid crew record {r.get('crew_id')!r}: {e}") from None
        if not crew.skills:
            raise SchemaError(f"crew {crew.crew_id} has no skills")
        if crew.work_time_budget_min < 0:
            raise SchemaError(f"crew {crew.crew_id} has a negative budget")
        if not (math.isfinite(crew.depot_lat) and math.isfinite(crew.depot_lon)):
            raise SchemaError(f"crew {crew.crew_id} has no valid depot coordinates")
        return crew

    def to_dict(self) -> dict:
        return {"crew_id": self.crew_id, "agency": self.agency, "skills": sorted(self.skills),
                "depot_lat": self.depot_lat, "depot_lon": self.depot_lon,
                "work_time_budget_min": self.work_time_budget_min, "home_borough": self.home_borough}


@dataclass
class LockedJob:
    """A commitment the optimiser must not change (APPROVED_NOT_STARTED, EN_ROUTE or IN_PROGRESS)."""
    request_id: str
    crew_id: str
    state: DispatchState
    lat: float
    lon: float
    service_min: float
    est_travel_min: float = 0.0
    since: datetime | None = None          # time of the latest state change (departure / start)
    approval_ref: str | None = None


@dataclass
class CrewStatus:
    """Operational state of a crew at the planning instant."""
    crew_id: str
    available: bool = True
    lat: float | None = None               # current position (None -> depot)
    lon: float | None = None
    duty_start: datetime | None = None
    duty_end: datetime | None = None
    worked_min: float = 0.0                # productive minutes already consumed (completed work)
    locked: list = field(default_factory=list)   # ordered LockedJob list
    note: str | None = None


@dataclass
class Snapshot:
    """Complete optimiser input for one planning instant."""
    now: datetime
    queue: list                            # C11 output records (preserved as given)
    crews: list                            # Crew
    crew_status: dict                      # crew_id -> CrewStatus
    job_states: dict = field(default_factory=dict)        # request_id -> DispatchState
    previous_assignment: dict = field(default_factory=dict)  # request_id -> crew_id (unlocked future work)
    previous_routes: dict = field(default_factory=dict)      # crew_id -> [request_id, ...]

    def frozen_queue(self) -> list:
        return copy.deepcopy(self.queue)


def minutes_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


def add_minutes(t: datetime, m: float) -> datetime:
    return t + timedelta(minutes=float(m))
