"""C06 Escalation, Credibility and Outcome Verification. Owner: Sankalp.

Two halves that share one store.

At intake it answers: has this person been right before, and is this the same problem
coming back again? At closure it answers: what did the crew actually find, and what does
that change for everyone who reported it.

Credibility is a Beta distribution with a Beta(2,2) prior, so a new citizen starts at
exactly 50 and no single report can swing them to an extreme. Older evidence decays with
a 180-day half-life, which is what makes a damaged score recoverable.

The guardrail is absolute and enforced here rather than left to the caller: this score can
move a request down a queue. It can never downgrade a physical hazard and it can never
stop a crew being sent.
"""
from __future__ import annotations

import csv
import io as _io
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from ..common import ComponentResult, Reason

REFERENCE = Path(__file__).resolve().parents[2] / "data" / "reference"

VERSION = "1.0.0"
COMPONENT = "C06 credibility and escalation"

# Both chosen by the search in 06_Workspace_Tools/sweep_c06_credibility.py, over 24 settings scored
# against all four gates at once. Minimising unfairness alone is a trap: a very strong
# prior drives every score towards 50, which looks perfectly fair and says nothing.
PRIOR_STRENGTH = 8.0                 # how much evidence it takes to move away from neutral
SENSITIVITY = 0.8                    # how far a given outperformance moves the score
GLOBAL_CONFIRM_RATE = 0.489          # across all decided NYC 311 rows in our sample
HALF_LIFE_DAYS = 180.0
RECURRENCE_WINDOW_DAYS = 90
SUPERVISOR_AT = 3                    # closures at the same place and type before escalating
CENTRAL_AT = 5

BANDS = [(0, 35, "LOW"), (35, 65, "NEUTRAL"), (65, 101, "TRUSTED")]


@dataclass
class Observation:
    """One past report by this citizen, after a crew closed it."""
    outcome: str                      # GENUINE | UNFOUNDED | INCONCLUSIVE
    closed_at: datetime
    request_type: str | None = None   # so the base rate for that kind of report can be used


@dataclass
class CredibilityResult(ComponentResult):
    credibility_score: float = 50.0
    credibility_band: str = "NEW"
    observation_count: int = 0
    effective_evidence: float = 0.0
    escalation_flag: bool = False
    escalation_level: str = "NONE"
    recurrence_count: int = 0
    evidence_trail: list[str] = field(default_factory=list)


def _weight(closed_at: datetime, now: datetime) -> float:
    """Evidence from 180 days ago counts half as much as evidence from today."""
    days = max(0.0, (now - closed_at).total_seconds() / 86400)
    return 0.5 ** (days / HALF_LIFE_DAYS)


@lru_cache(maxsize=1)
def _base_rates() -> dict[str, float]:
    """How often a crew confirms each request type, from the real data.

    This matters more than anything else in this component. Sewer backups are confirmed
    0.8% of the time and potholes 89%. Counting raw hits and misses would therefore brand
    anyone who reports sewers a liar, and anyone who reports potholes a saint, which says
    nothing about either of them. So a citizen is compared against what a typical report of
    the kind THEY make actually achieves.
    """
    try:
        rows = csv.DictReader(_io.open(REFERENCE / "outcome_base_rates.csv", encoding="utf-8"))
        return {r["request_type_code"]: float(r["p_confirmed"]) for r in rows}
    except Exception:
        return {}


def expected_rate(history: list["Observation"]) -> float:
    """The confirmation rate a typical person would get, reporting what this person reports."""
    rates = _base_rates()
    vals = [rates.get(o.request_type, GLOBAL_CONFIRM_RATE) for o in history
            if o.outcome in ("GENUINE", "UNFOUNDED")]
    return sum(vals) / len(vals) if vals else GLOBAL_CONFIRM_RATE


def score_citizen(history: list["Observation"], now: datetime | None = None) -> tuple[float, float, float, int]:
    """Returns (score out of 100, alpha, beta, usable observations).

    50 means exactly as often confirmed as expected for the kind of reports this person
    makes. Above 50 is better than expected, below is worse. A new citizen is exactly 50.
    """
    now = now or datetime.now()
    r_exp = expected_rate(history)
    a, b, used = PRIOR_STRENGTH * r_exp, PRIOR_STRENGTH * (1 - r_exp), 0
    for obs in history:
        if obs.outcome == "INCONCLUSIVE":
            continue                   # the crew could not tell, so it teaches us nothing
        w = _weight(obs.closed_at, now)
        if obs.outcome == "GENUINE":
            a += w
        else:
            b += w
        used += 1
    post = a / (a + b)
    # Compare on the log-odds scale rather than as a raw difference.
    #
    # The obvious mapping, a straight line either side of the expected rate, is not
    # symmetric: its slope is 50/(1-r_exp) above expectation and 50/r_exp below. Someone
    # who reports hard-to-confirm problems has a low expected rate, so the same
    # outperformance earns them a gentler slope and a lower score. That alone cost
    # honest reporters of hard problems 2.2 points against equally honest reporters of
    # easy ones, with no difference in the underlying evidence.
    #
    # Log-odds has the same slope everywhere, so beating expectation by a given factor
    # is worth the same wherever you start. 50 still means exactly as expected.
    lo_post = math.log(max(post, 1e-6) / max(1 - post, 1e-6))
    lo_exp = math.log(max(r_exp, 1e-6) / max(1 - r_exp, 1e-6))
    score = 100.0 / (1.0 + math.exp(-SENSITIVITY * (lo_post - lo_exp)))
    return max(0.0, min(100.0, score)), a, b, used


def assess(citizen_id: str | None,
           history: list[Observation] | None = None,
           closures_same_place_and_type: list[str] | None = None,
           now: datetime | None = None) -> CredibilityResult:
    """Called at intake. `closures_same_place_and_type` is the list of earlier request ids
    that were closed at this location for this request type inside the window."""
    t0 = time.perf_counter()
    now = now or datetime.now()
    history = history or []
    recurrences = closures_same_place_and_type or []
    reasons: list[Reason] = []

    if citizen_id is None:
        reasons.append(Reason("ANONYMOUS", "Reported without an account, so the neutral score of 50 is used"))
        return CredibilityResult(component=COMPONENT, version=VERSION,
                                 latency_ms=(time.perf_counter() - t0) * 1000, reasons=reasons)

    score, a, b, used = score_citizen(history, now)
    band = "NEW" if used == 0 else next(n for lo, hi, n in BANDS if lo <= score < hi)

    if used == 0:
        reasons.append(Reason("NEW_CITIZEN", "First report from this person, so they start at neutral 50"))
    else:
        good = sum(1 for o in history if o.outcome == "GENUINE")
        exp = expected_rate(history)
        reasons.append(Reason("HISTORY",
                              f"{good} of {used} earlier reports were confirmed by a crew, against "
                              f"{exp:.0%} expected for the kinds of problem they report",
                              weight=round(score - 50, 1),
                              evidence={"alpha": round(a, 2), "beta": round(b, 2),
                                        "expected_rate": round(exp, 3)}))
        reasons.append(Reason("BASE_RATE_FAIR",
                              "Compared against their own report types, so reporting problems that are "
                              "often gone by the time a crew arrives does not count against them"))
        if used >= 3 and score < 35:
            reasons.append(Reason("LOW_BAND", "Below the low band: this moves the request down the queue, "
                                              "and can never stop a crew being sent"))

    level, flag = "NONE", False
    if len(recurrences) >= CENTRAL_AT:
        level, flag = "CENTRAL_AUTHORITY", True
    elif len(recurrences) >= SUPERVISOR_AT:
        level, flag = "SUPERVISOR", True
    if flag:
        reasons.append(Reason("RECURRENCE",
                              f"The same problem has been closed here {len(recurrences)} times in the last "
                              f"{RECURRENCE_WINDOW_DAYS} days, so it escalates to {level.lower().replace('_', ' ')}",
                              evidence={"requests": recurrences[:10]}))

    return CredibilityResult(component=COMPONENT, version=VERSION,
                             latency_ms=(time.perf_counter() - t0) * 1000, reasons=reasons,
                             credibility_score=round(score, 1), credibility_band=band,
                             observation_count=used, effective_evidence=round(a + b - 4, 2),
                             escalation_flag=flag, escalation_level=level,
                             recurrence_count=len(recurrences), evidence_trail=recurrences[:10])


# --------------------------------------------------------------------------- closure half
CREW_OUTCOME_TO_VERIFIED = {
    "VALID_RESOLVED": "GENUINE",
    "VALID_NEEDS_FOLLOWUP": "GENUINE",
    "SUBSIDED": "GENUINE",          # the citizen was right; it had cleared by the time the crew arrived
    "INVALID_NOT_FOUND": "UNFOUNDED",
    "WRONG_AGENCY": "INCONCLUSIVE",
    "NO_ACCESS": "INCONCLUSIVE",
}


@dataclass
class OutcomeResult(ComponentResult):
    verified_outcome: str = "INCONCLUSIVE"
    citizens_updated: list[str] = field(default_factory=list)
    credibility_deltas: dict[str, float] = field(default_factory=dict)


def verify_outcome(crew_outcome: str,
                   reporter_ids: list[str],
                   histories: dict[str, list[Observation]] | None = None,
                   now: datetime | None = None,
                   request_type: str | None = None) -> OutcomeResult:
    """Called when a crew closes a job. Updates EVERY reporter in the duplicate group, not
    just whoever happened to report first.

    Pass `request_type` so the new outcome is judged against the confirmation rate for
    that kind of problem, exactly as `assess` will judge it at the next report. Without
    it the change shown here and the change the citizen actually gets would differ."""
    t0 = time.perf_counter()
    now = now or datetime.now()
    histories = histories or {}
    verified = CREW_OUTCOME_TO_VERIFIED.get(crew_outcome, "INCONCLUSIVE")
    reasons = [Reason("CREW_SAID", f"The crew reported {crew_outcome.replace('_', ' ').lower()}, "
                                   f"which counts as {verified.lower()}")]
    if crew_outcome == "SUBSIDED":
        reasons.append(Reason("SUBSIDED_IS_GENUINE",
                              "It had cleared before the crew arrived, so the citizen was still right"))

    deltas: dict[str, float] = {}
    if verified == "INCONCLUSIVE":
        reasons.append(Reason("NO_CREDIBILITY_CHANGE",
                              "Nothing conclusive was found, so nobody's standing changes"))
    else:
        for cid in reporter_ids:
            hist = list(histories.get(cid, []))
            before, *_ = score_citizen(hist, now)
            hist.append(Observation(outcome=verified, closed_at=now, request_type=request_type))
            after, *_ = score_citizen(hist, now)
            deltas[cid] = round(after - before, 2)
        if deltas:
            reasons.append(Reason("UPDATED",
                                  f"{len(deltas)} reporter(s) updated, "
                                  f"{'+' if verified == 'GENUINE' else ''}{list(deltas.values())[0]:g} each",
                                  evidence={"deltas": deltas}))

    return OutcomeResult(component=COMPONENT, version=VERSION,
                         latency_ms=(time.perf_counter() - t0) * 1000, reasons=reasons,
                         verified_outcome=verified, citizens_updated=list(deltas),
                         credibility_deltas=deltas)


run = assess
