"""C12 Admin Q&A (formerly C14). Owner: Nabeel Taresh.

Plug and play. One call, sensible defaults (production strategy `hybrid`):

    from components import admin_qa                  # or: from components import c12_admin_qa
    a = admin_qa.run(admin_qa.AdminQuestion("How many open potholes are in Brooklyn?", "ADM-1"), rows)
    a.matched_intent    # COUNT | TOP_N | AVERAGE | TREND | LIST | WHY_RANKED | UNKNOWN
    a.answer_text       # "There are 14 open pothole requests in Brooklyn."
    a.table             # list of dicts, at most 200 rows
    a.filters, a.confidence, a.suggestions

Inputs
    q        AdminQuestion(question 3-300 chars, admin_id).
    rows     the FULL read-only admin snapshot (DataFrame or list of dicts / objects).
             Required columns: request_id, label, request_type, agency, borough, urgency_level,
             queue_position, status, created_at, crew, credibility_score, eta_low_hours,
             eta_high_hours. Optional: closed_at, resolution_status, complaint_family,
             priority_band, priority_reasons.
    history  optional cleaned 311 history (request_id, request_type, complaint_family, borough,
             agency, created_at, closed_at). Used for TREND and resolution-time AVERAGE.
    strategy rules | ml_intent | hybrid (default). Env C12_STRATEGY also works.
    now      optional reference time for "last week". Default: the newest created_at in rows.

Output
    AdminAnswer: meta, matched_intent, filters, answer_text, table (<= 200 rows), confidence 0-1,
    suggestions (2-3 follow-up questions on every answer). An empty result says which filter value
    is missing and which values exist. SAFE DEFAULT: UNKNOWN, empty table, example questions.

Safety
    Read-only: the input is copied, never changed. No SQL: fixed pandas code runs a validated
    query plan, so there is no query text to leak or inject into.
    No personal data. credibility_score appears only as an aggregate over 5 or more requests,
    never per request or per citizen. Write, schema, and prompt-injection questions get UNKNOWN.

Who calls it
    The admin portal (Sankalp's API) passes its read-only snapshot. No other component reads
    the answer. Evidence: documents/C12_changelog/RESULTS.md. Eval: tools/c12_admin_qa_eval.py.
"""
from __future__ import annotations

import difflib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Optional

import numpy as np
import pandas as pd

VERSION = "1.1.0"
COMPONENT = "C12 admin Q&A"
CONTRACT_VERSION = "1.0.0"
STRATEGIES = ("rules", "ml_intent", "hybrid")
PRODUCTION_STRATEGY = "hybrid"
INTENTS = ("COUNT", "TOP_N", "AVERAGE", "TREND", "LIST", "WHY_RANKED", "UNKNOWN")
MAX_TABLE_ROWS = 200
MIN_CONFIDENCE = 0.5
MIN_GROUP_FOR_CREDIBILITY = 5
CLOSED_STATUSES = ("RESOLVED", "INVALID", "SUBSIDED", "REFERRED")
REQUIRED_COLUMNS = ("request_id", "label", "request_type", "agency", "borough", "urgency_level",
                    "queue_position", "status", "created_at", "crew", "credibility_score",
                    "eta_low_hours", "eta_high_hours")
OPTIONAL_COLUMNS = ("closed_at", "resolution_status", "complaint_family", "priority_band", "priority_reasons")
# Columns an answer table may show. credibility_score is never shown per row.
SAFE_LIST_COLUMNS = ("request_id", "label", "borough", "agency", "status", "urgency_level",
                     "queue_position", "priority_band", "created_at", "crew", "eta_low_hours", "eta_high_hours")
SUGGESTIONS = [
    "How many open potholes are there in Brooklyn?",
    "What are the top 5 request types in Queens?",
    "What is the average ETA for open no-heat requests?",
    "Show the weekly trend of noise requests in the Bronx.",
    "Why is request NYC-66542625 ranked where it is?",
]


# ============================================================================ contract
@dataclass
class AdminQuestion:
    question: str
    admin_id: str

    def valid(self) -> bool:
        return isinstance(self.question, str) and 3 <= len(self.question.strip()) <= 300 and bool(self.admin_id)


@dataclass
class AdminAnswer:
    meta: dict
    matched_intent: str
    answer_text: str
    filters: dict = field(default_factory=dict)
    table: list = field(default_factory=list)
    confidence: float = 0.0
    suggestions: list = field(default_factory=list)
    # Internal, for evaluation only: request_ids the answer was computed from. Not shown in the UI.
    selected_ids: list = field(default_factory=list, repr=False)

    def explain(self) -> list[str]:
        return [f"intent {self.matched_intent} ({self.confidence:.2f})", f"filters {self.filters}", self.answer_text]


# ============================================================================ safety
_UNSAFE = [
    r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|rule|previous|above|prompt)",
    r"\b(system prompt|you are now|act as|jailbreak|developer mode)\b",
    r"\b(drop|delete|truncate|insert|update|alter|create|attach|pragma|grant|vacuum)\b\s+(table|from|into|database|all|every|the|\w+\s+set)",
    r"(;|--|/\*)\s*(drop|delete|select|insert|update)",
    r"^\s*select\b.+\bfrom\b",
    r"\b(show|print|give|reveal|display|tell)\b.{0,30}\b(sql|query you|schema|table names?|password|api key|token)\b",
    r"\b(change|set|mark|close|reassign|edit|modify|move)\b.{0,40}\b(request|status|priority|queue|crew)\b.{0,30}\b(to|as|from)\b",
    # Write verbs aimed at a record or field. Strong verbs need only a target; weak ones need "to/as" or to lead the sentence.
    r"\b(change|update(?!\s+(on|for|about))|edit|modify|delete|remove|erase|reassign|unassign|reopen|escalate|"
    r"downgrade|overwrite|cancel|wipe|purge)\b.{0,50}\b(nyc-\d+|urgency|status|crews?|priority|band|queue|requests?|"
    r"tickets?|records?|rows?|data|labels?|types?|agency|borough|eta|resolution)\b",
    r"\b(set|assign|close|mark|move|bump|raise|lower|flag)\b.{0,50}\b(nyc-\d+|urgency|status|crews?|priority|band|queue|"
    r"requests?|tickets?|records?|label|agency|eta)\b.{0,30}(\b(to|as)\b|=)",
    r"^\s*(please\s+|can you\s+|could you\s+)?(set|assign|close|mark|move|bump|flag|change|update|edit|delete|remove)\b",
    r"\b(phone|e-?mail|home address|name|names|identity|contact)\b.{0,30}\b(citizen|reporter|caller|complainant|resident)s?\b",
    r"\b(who|which (citizen|person|resident|reporter))\b.{0,20}\b(reported|filed|submitted|complained)\b",
    r"\bcredibility\b.{0,40}\b(each|every|individual|per (citizen|person|reporter|request)|citizen|reporter|person|of request|for request|nyc-\d+)",
    r"\b(citizen|reporter|person)\b.{0,30}\bcredibility\b",
]
_UNSAFE_RE = [re.compile(p, re.I) for p in _UNSAFE]


def is_unsafe(question: str) -> bool:
    return any(r.search(question) for r in _UNSAFE_RE)


# ============================================================================ slot extraction
BOROUGHS = {"BROOKLYN": r"brooklyn|\bbk\b", "QUEENS": r"queens", "MANHATTAN": r"manhattan",
            "BRONX": r"\bbronx\b", "STATEN ISLAND": r"staten island|\bsi\b"}
AGENCIES = ("NYPD", "HPD", "DSNY", "DOT", "DEP", "DPR", "DOB", "DOHMH")
FAMILIES = {"HOUSING": r"housing", "SANITATION": r"sanitation", "STREETS": r"street (condition|defect)s?|streets? family",
            "WATER_SEWER": r"water (and|&) sewer|water/sewer", "PARKS_TREES": r"parks?|trees? family",
            "BUILDINGS": r"buildings? family|building complaints?", "HEALTH": r"health"}
# Order matters: more specific phrases first. Values are request_type codes.
TYPE_WORDS = [
    (r"blocked hydrant|illegal(ly)? park|parking", ["RT123"]), (r"blocked driveway|driveway", ["RT122"]),
    (r"abandoned (vehicle|car)", ["RT124"]), (r"derelict (vehicle|car)", ["RT043"]),
    (r"pothole", ["RT001"]), (r"sinkhole|cave-?in", ["RT002"]), (r"street ?(light|lite|lamp)s?", ["RT004"]),
    (r"traffic (signal|light)s?", ["RT005"]), (r"sidewalk|curb", ["RT006"]), (r"water main", ["RT020"]),
    (r"catch basin|drain", ["RT021"]), (r"street flooding|flooding", ["RT022"]),
    (r"sewer backup|manhole", ["RT023"]), (r"hydrant", ["RT024"]), (r"water pressure|dirty water", ["RT025"]),
    (r"missed (garbage|collection|recycling|pickup)|garbage", ["RT040"]), (r"dumping", ["RT041"]),
    (r"litter|dirty street", ["RT042"]), (r"snow|\bice\b", ["RT044"]), (r"dead animal", ["RT045"]),
    (r"dead (or dying )?trees?|dying trees?", ["RT062"]), (r"fallen (tree|branch)|branch(es)? down", ["RT060"]),
    (r"overgrown|on (the )?wires", ["RT061"]), (r"no heat|heating|\bheat\b|hot water|radiators?", ["RT080"]),
    (r"leaks?\b", ["RT081"]), (r"plumbing", ["RT082"]), (r"pests?|mold|roach", ["RT083"]),
    (r"electrical", ["RT084"]), (r"\brats?\b|rodents?", ["RT100"]),
    (r"residential noise|noise from (a )?(residence|neighbou?r)|loud neighbou?rs?", ["RT120"]),
    (r"street noise|noise from (the )?(street|business|vehicle)", ["RT121"]),
    (r"\bnoise\b", ["RT120", "RT121"]), (r"illegal construction|unsafe building|construction", ["RT140"]),
    (r"elevators?", ["RT141"]),
]
URGENCY = r"\b(critical|high|medium|low)\b(?=[- ](urgency|priority)|\s+\w+\s+(urgency|priority))|\b(urgency|priority)\s+(critical|high|medium|low)\b|\b(critical|high|medium|low)[- ]urgency\b"
STATUS_WORDS = [(r"waiting (for|on) (a )?(dispatch|crew)|awaiting (dispatch|a crew)|not (yet )?(dispatched|assigned)|"
                 r"unassigned|no crew (yet|assigned)", ["SUBMITTED", "PROCESSED"]), (r"dispatched", "DISPATCHED"), (r"on[- ]site", "ON_SITE"), (r"\bsubmitted\b", "SUBMITTED"),
                (r"\bopen\b|unresolved|outstanding|pending|still waiting|in the queue|not (yet )?closed", "open"),
                (r"\bclosed\b|resolved|completed|\bfixed\b", "closed")]
RESOLUTIONS = {"UNFOUNDED": r"unfounded", "DUPLICATE": r"duplicates?\b", "GENUINE": r"genuine",
               "SUBSIDED": r"subsided", "NO_ACCESS": r"no access", "ADMINISTRATIVE": r"administrative(ly)?"}
BANDS = {"ACT_NOW": r"act[ _]now", "NEXT": r"\bnext\b band|band next", "PLANNED": r"\bplanned\b", "MONITOR": r"\bmonitor\b"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
GROUPS = {"borough": "borough", "boroughs": "borough", "agency": "agency", "agencies": "agency",
          "type": "label", "types": "label", "category": "label", "categories": "label", "kind": "label",
          "family": "complaint_family", "families": "complaint_family", "urgency": "urgency_level",
          "status": "status", "statuses": "status", "crew": "crew", "crews": "crew", "band": "priority_band",
          "reason": "resolution_status", "reasons": "resolution_status", "outcome": "resolution_status",
          "outcomes": "resolution_status"}


def _month_range(now: datetime, m: int) -> tuple[datetime, datetime]:
    y = now.year if m <= now.month else now.year - 1
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), m % 12 + 1, 1)
    return start, end


_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
       r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YR = r"(?:,?\s*(20\d\d))?"
_TO = r"\s*(?:-|\u2013|to|through|until|till|and)\s*"


def _day(now: datetime, mon: str, d: str, y: Optional[str]) -> datetime:
    """A month/day with no year means its latest date on or before the snapshot's newest day."""
    m = [x[:3] for x in MONTHS].index(mon[:3]) + 1
    return datetime(int(y) if y else (now.year if (m, int(d)) <= (now.month, now.day) else now.year - 1), m, int(d))


def extract_dates(q: str, now: datetime) -> tuple[Optional[datetime], Optional[datetime]]:
    """[start, end) on created_at. Relative dates count from `now` (the newest created_at in the snapshot)."""
    t = q.lower()
    day0 = datetime(now.year, now.month, now.day)
    end, one = day0 + timedelta(days=1), timedelta(days=1)
    if "yesterday" in t:
        return day0 - one, day0
    if "today" in t:
        return day0, end
    m = re.search(r"(last|past|previous)\s+(\d+|\w+)\s+days?", t)
    if m:
        n = int(m.group(2)) if m.group(2).isdigit() else NUM_WORDS.get(m.group(2), 0)
        if n:
            return now - timedelta(days=n), now
    if re.search(r"(last|past|previous) week", t):
        return now - timedelta(days=7), now
    if "this week" in t:
        return day0 - timedelta(days=day0.weekday()), now
    if "this month" in t:
        return datetime(now.year, now.month, 1), now
    if re.search(r"(last|previous) month", t):
        first = datetime(now.year, now.month, 1)
        prev = first - one
        return datetime(prev.year, prev.month, 1), first
    if re.search(r"\b(last|previous) year\b", t):
        return datetime(now.year - 1, 1, 1), datetime(now.year, 1, 1)
    if re.search(r"\b(this year|year to date|ytd)\b", t):
        return datetime(now.year, 1, 1), end
    m = re.search(r"(\d{4}-\d{2}-\d{2})" + _TO + r"(\d{4}-\d{2}-\d{2})", t)
    if m:
        return datetime.fromisoformat(m.group(1)), datetime.fromisoformat(m.group(2)) + one
    m = re.search(rf"\b{_MON}\s+{_DAY}{_YR}{_TO}{_MON}\s+{_DAY}{_YR}", t)  # Jan 5 - Mar 31 2026
    if m:
        y = m.group(3) or m.group(6)
        return _day(now, m.group(1), m.group(2), y), _day(now, m.group(4), m.group(5), m.group(6) or y) + one
    m = re.search(rf"\b{_MON}\s+{_DAY}{_TO}{_DAY}{_YR}", t)  # Oct 1-7 [2026]
    if m:
        return _day(now, m.group(1), m.group(2), m.group(4)), _day(now, m.group(1), m.group(3), m.group(4)) + one
    m = re.search(rf"\b(?:between|from)\s+{_MON}{_YR}{_TO}{_MON}{_YR}", t)  # between Jan and Mar
    if m:
        y2 = m.group(4) or m.group(2)
        a = _day(now, m.group(1), "1", m.group(2) or y2)
        b = _day(now, m.group(3), "1", y2)
        if b < a and not y2:
            a = a.replace(year=a.year - 1)
        return a, datetime(b.year + (b.month == 12), b.month % 12 + 1, 1)
    m = re.search(rf"\b(?:since|after|from|starting)\s+{_MON}\s+{_DAY}\b{_YR}", t)
    if m:
        return _day(now, m.group(1), m.group(2), m.group(3)), end
    m = re.search(rf"\bon\s+{_MON}\s+{_DAY}\b{_YR}", t)
    if m:
        d = _day(now, m.group(1), m.group(2), m.group(3))
        return d, d + one
    m = re.search(r"\b(?:since|after|from)\s+(\d{4}-\d{2}-\d{2})", t)
    if m:
        return datetime.fromisoformat(m.group(1)), end
    m = re.search(r"\b(20\d\d)" + _TO + r"(20\d\d)\b", t)
    if m:
        return datetime(int(m.group(1)), 1, 1), datetime(int(m.group(2)) + 1, 1, 1)
    m = re.search(r"\bsince\s+(20\d\d)\b", t)
    if m:
        return datetime(int(m.group(1)), 1, 1), end
    m = re.search(rf"\b{_MON}\s+(20\d\d)\b", t)
    if m:
        a = _day(now, m.group(1), "1", m.group(2))
        return a, datetime(a.year + (a.month == 12), a.month % 12 + 1, 1)
    m = re.search(r"\b(?:from|in|during|for|of)\s+(20\d\d)\b", t)
    if m:
        return datetime(int(m.group(1)), 1, 1), datetime(int(m.group(1)) + 1, 1, 1)
    m = re.search(rf"\bsince\s+{_MON}\b", t)
    if m:
        return _month_range(now, [x[:3] for x in MONTHS].index(m.group(1)[:3]) + 1)[0], now
    m = re.search(rf"\b(in|during|for|of)\s+{_MON}\b", t)
    if m:
        return _month_range(now, [x[:3] for x in MONTHS].index(m.group(2)[:3]) + 1)
    return None, None


CREW_ID = r"\b(?!nyc-)([a-z]+(?:_[a-z]+)*-\d{1,3})\b"


def extract_slots(question: str, now: datetime) -> dict:
    """Filters plus plan fields (group_by, n, metric, period, request_id). Same code for all strategies."""
    q = question.lower()
    f: dict[str, Any] = {}
    for b, pat in BOROUGHS.items():
        if re.search(pat, q):
            f["borough"] = b
            break
    for a in AGENCIES:
        if re.search(rf"\b{a.lower()}\b", q):
            f["agency"] = a
            break
    for pat, codes in TYPE_WORDS:
        if re.search(pat, q):
            f["request_type"] = codes if len(codes) > 1 else codes[0]
            break
    if "request_type" not in f:
        for fam, pat in FAMILIES.items():
            if re.search(pat, q):
                f["complaint_family"] = fam
                break
    m = re.search(URGENCY, q)
    if m:
        f["urgency_level"] = next(g for g in m.groups() if g and g not in ("urgency", "priority")).upper()
    for pat, s in STATUS_WORDS:
        if re.search(pat, q):
            f["status"] = s
            break
    m = re.search(CREW_ID, q)
    if m:
        f["crew"] = m.group(1).upper()
        if f.get("status") == "DISPATCHED" and re.search(r"dispatched (to|for)", q):
            del f["status"]  # "dispatched to <crew>" names the crew, not a status
    for res, pat in RESOLUTIONS.items():
        if re.search(rf"\b{pat}", q):
            f["resolution_status"] = res
            break
    for band, pat in BANDS.items():
        if re.search(pat, q):
            f["priority_band"] = band
            break
    d0, d1 = extract_dates(question, now)
    if d0 is not None:
        f["date_from"], f["date_to"] = d0.isoformat(timespec="seconds"), d1.isoformat(timespec="seconds")
    plan: dict[str, Any] = {"filters": f}
    m = re.search(r"\b(nyc-\d{6,10})\b", q) or re.search(r"\brequest\s+#?(\d{6,10})\b", q)
    if m:
        rid = m.group(1).upper()
        plan["request_id"] = rid if rid.startswith("NYC-") else "NYC-" + rid
    m = re.search(r"\b(?:top|first|show|list|busiest)\s+(\d+|" + "|".join(NUM_WORDS) + r")\b", q) or \
        re.search(r"\b(\d+|" + "|".join(NUM_WORDS) + r")\s+most\b", q)
    if m:
        v = m.group(1)
        plan["n"] = int(v) if v.isdigit() else NUM_WORDS[v]
    m = re.search(r"\b(?:by|per|each|which|what|across|every)\s+(?:the\s+)?(" + "|".join(GROUPS) + r")\b", q) or \
        re.search(r"\b(" + "|".join(GROUPS) + r")\s+(?:has|have|had|gets?|got|with)\s+the\s+most\b", q)
    if m:
        plan["group_by"] = GROUPS[m.group(1)]
    elif m2 := re.search(r"\b(?:top|busiest|most common|most frequent|most active|biggest)\s+(?:\d+\s+|(?:" +
                         "|".join(NUM_WORDS) + r")\s+)?(" + "|".join(GROUPS) + r")\b", q):
        plan["group_by"] = GROUPS[m2.group(1)]
    elif re.search(r"most common|most frequent|top \d+|top (three|five|ten)|biggest|busiest", q):
        plan["group_by"] = "label"
    if re.search(r"credibility", q):
        plan["metric"] = "credibility"
    elif re.search(r"resol|to close|closed?\b.*\bhow long|how long.*\b(take|took|to fix|to close)|turnaround", q):
        plan["metric"] = "resolution_hours"
    elif re.search(r"\beta\b|expected|estimated|wait|how long", q):
        plan["metric"] = "eta"
    plan["period"] = "month" if re.search(r"month", q) else "day" if re.search(r"\bdaily\b|per day|by day|each day", q) else "week"
    return plan


# ============================================================================ intent: rules
_INTENT_RULES = [
    ("WHY_RANKED", r"\bwhy\b.*\b(rank|ranked|position|queue|priority|first|top|ahead|above|below)\b|\b(queue position|ranking) (of|for)\b|explain the (rank|priority)"),
    ("TREND", r"\btrend|over time|per (week|month|day)|by (week|month|day)|each (week|month|day)|weekly|monthly|\bdaily\b|week by week|month by month|going up|going down"),
    ("AVERAGE", r"\baverage|\bmean\b|typical|how long|\beta\b|turnaround|median"),
    ("TOP_N", r"\btop\b|busiest|most active|most common|most frequent|\bmost\b|biggest|breakdown|\b(by|per|across) (borough|agency|type|urgency|status|crew|family)"),
    ("COUNT", r"how many|number of|\bcount\b|how much|total"),
    ("LIST", r"\b(list|show|display|which requests|what requests|give me|find|look ?up|details|status of)\b|nyc-\d+"),
]


def rules_intent(question: str) -> tuple[str, float]:
    q = question.lower()
    for intent, pat in _INTENT_RULES:
        if re.search(pat, q):
            return intent, 0.9
    return "UNKNOWN", 0.2


# ============================================================================ intent: ml (TF-IDF + logreg)
_TRAIN = {
    "COUNT": ["how many {t} are there", "number of {t} {b}", "count the {s} {t}", "how many {s} {t} {b}",
              "total {t} {d}", "count of {t} requests {d}", "how many requests {b}", "tell me the number of {s} cases"],
    "TOP_N": ["top {n} {t} types {b}", "most common requests {b}", "which agency has the most {s} requests",
              "breakdown of {t} by borough", "biggest categories {d}", "rank the boroughs by {t}",
              "most frequent complaint types {d}", "what are the leading request types"],
    "AVERAGE": ["average eta for {t}", "how long does {t} take to resolve", "mean resolution time {b}",
                "typical wait for {s} {t}", "average credibility {b}", "what is the expected time for {t}",
                "median hours to close {t}", "average time to fix {t} {d}"],
    "TREND": ["trend of {t} {d}", "{t} per week", "monthly {t} {b}", "is {t} going up", "daily count of {t}",
              "show {t} over time", "weekly requests {b}", "how did {t} change month by month"],
    "LIST": ["list {s} {t} {b}", "show me {s} {t}", "which requests are {s} {b}", "display {t} for crew",
             "give me the {s} requests", "details of request {r}", "find {t} {d}", "look up {r}"],
    "WHY_RANKED": ["why is {r} ranked first", "why is {r} at this position", "explain the priority of {r}",
                   "why is this request ahead of the others", "why is {r} top of the queue",
                   "what puts {r} in this place in the queue", "reason for the queue position of {r}",
                   "why did {r} get high priority"],
    "UNKNOWN": ["write me a poem", "what is the weather", "tell me a joke", "who won the game", "hello there",
                "translate this to spanish", "what is the meaning of life", "recommend a restaurant",
                "how do I bake bread", "sing a song", "what time is it in tokyo", "book a flight"],
}
_FILL = dict(t=["potholes", "noise requests", "no heat", "rats", "street lights", "illegal parking", "requests"],
             b=["in brooklyn", "in queens", "in the bronx", ""], s=["open", "closed", "high urgency", ""],
             d=["last week", "in march", "this month", ""], n=["3", "5", "10"], r=["NYC-66542625", "request 66552355"])


@lru_cache(maxsize=1)
def _ml_model():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline, make_union
    rng = np.random.default_rng(2026)
    X, y = [], []
    for intent, temps in _TRAIN.items():
        for t in temps:
            for _ in range(6):
                X.append(t.format(**{k: rng.choice(v) for k, v in _FILL.items()}))
                y.append(intent)
    vec = make_union(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
                     TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True))
    model = make_pipeline(vec, LogisticRegression(C=8.0, max_iter=2000))
    model.fit(X, y)
    return model


def ml_intent(question: str) -> tuple[str, float]:
    model = _ml_model()
    p = model.predict_proba([question.lower()])[0]
    i = int(np.argmax(p))
    return str(model.classes_[i]), float(p[i])


# ============================================================================ plan validation (hybrid)
PLAN_KEYS = {"intent", "filters", "group_by", "metric", "n", "period", "request_id"}
FILTER_KEYS = {"borough", "agency", "request_type", "complaint_family", "urgency_level", "status", "crew",
               "priority_band", "resolution_status", "date_from", "date_to"}


def validate_plan(plan: dict) -> list[str]:
    """Return the problems with a JSON query plan. Empty list = safe to execute."""
    errs = []
    if set(plan) - PLAN_KEYS:
        errs.append(f"unknown keys {sorted(set(plan) - PLAN_KEYS)}")
    if plan.get("intent") not in INTENTS:
        errs.append("bad intent")
    f = plan.get("filters", {})
    if not isinstance(f, dict) or set(f) - FILTER_KEYS:
        errs.append("bad filters")
    for k, v in (f or {}).items():
        vals = v if isinstance(v, list) else [v]
        if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_ :\-.]{1,40}", x) for x in vals):
            errs.append(f"bad value for {k}")
    if plan.get("group_by") not in (None, *set(GROUPS.values())):
        errs.append("bad group_by")
    if plan.get("metric") not in (None, "eta", "resolution_hours", "credibility"):
        errs.append("bad metric")
    if not (1 <= int(plan.get("n", 5)) <= 50):
        errs.append("bad n")
    if plan.get("period") not in (None, "day", "week", "month"):
        errs.append("bad period")
    return errs


# ============================================================================ data
def to_frame(rows: Any) -> pd.DataFrame:
    """Copy the snapshot into a DataFrame. The caller's object is never changed."""
    if rows is None:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    if isinstance(rows, pd.DataFrame):
        df = rows.copy(deep=True)
    else:
        recs = [r if isinstance(r, dict) else dict(vars(r)) for r in rows]
        df = pd.DataFrame([dict(r) for r in recs])
    for c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
        if c not in df.columns:
            df[c] = None
    for c in ("created_at", "closed_at"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ("queue_position", "credibility_score", "eta_low_hours", "eta_high_hours"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["status"] = df["status"].astype("string").str.upper()
    return df


def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    m = pd.Series(True, index=df.index)
    for k, v in f.items():
        if k in ("date_from", "date_to"):
            continue
        if k not in df.columns:
            return df.iloc[0:0]
        if k == "status" and v in ("open", "closed"):
            closed = df["status"].isin(CLOSED_STATUSES)
            m &= closed if v == "closed" else ~closed
        elif isinstance(v, list):
            m &= df[k].isin(v)
        else:
            m &= df[k].astype("string").str.upper() == str(v).upper()
    if "date_from" in f:
        m &= (df["created_at"] >= pd.Timestamp(f["date_from"])) & (df["created_at"] < pd.Timestamp(f["date_to"]))
    return df[m.fillna(False)]


def _queue_order(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(_qn=df["queue_position"].isna()).sort_values(["_qn", "queue_position", "created_at", "request_id"]).drop(columns="_qn")


def _period_key(s: pd.Series, period: str) -> pd.Series:
    if period == "month":
        return s.dt.strftime("%Y-%m")
    if period == "day":
        return s.dt.strftime("%Y-%m-%d")
    return (s - pd.to_timedelta(s.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")


# ============================================================================ execution: pandas
def execute_pandas(plan: dict, df: pd.DataFrame, hist: Optional[pd.DataFrame]) -> dict:
    """Fixed code runs the plan. Returns {kind, value, frame, ids}."""
    intent, f = plan["intent"], plan.get("filters", {})
    src = _source(plan, df, hist)
    sel = apply_filters(src, f)
    if intent == "WHY_RANKED":
        sel = df[df["request_id"] == plan["request_id"]] if plan.get("request_id") else \
            _queue_order(apply_filters(df, f).dropna(subset=["queue_position"])).head(1)
        return {"frame": sel, "ids": sel["request_id"].tolist()}
    if intent == "COUNT":
        return {"value": int(len(sel)), "ids": sel["request_id"].tolist()}
    if intent == "TOP_N":
        g = plan.get("group_by") or "label"
        c = sel.groupby(g, dropna=True).size().reset_index(name="count")
        c = c.sort_values(["count", g], ascending=[False, True]).head(int(plan.get("n", 5)))
        return {"frame": c.rename(columns={g: "group"}), "ids": sel["request_id"].tolist()}
    if intent == "AVERAGE":
        sel, col = _metric_rows(plan, sel)
        g = plan.get("group_by")
        if g:
            c = sel.groupby(g)[col].agg(["mean", "size"]).reset_index().rename(columns={g: "group", "size": "count"})
            return {"frame": c.sort_values("group"), "ids": sel["request_id"].tolist(), "metric_col": col}
        return {"value": float(sel[col].mean()) if len(sel) else None, "count": int(len(sel)),
                "ids": sel["request_id"].tolist(), "metric_col": col}
    if intent == "TREND":
        k = _period_key(sel["created_at"].dropna(), plan.get("period", "week"))
        c = k.value_counts().sort_index().reset_index()
        c.columns = ["period", "count"]
        return {"frame": c, "ids": sel["request_id"].tolist()}
    if intent == "LIST":
        if plan.get("request_id"):
            sel = df[df["request_id"] == plan["request_id"]]
        sel = _queue_order(sel)
        return {"frame": sel, "ids": sel["request_id"].tolist()}
    raise ValueError(intent)


def _source(plan: dict, df: pd.DataFrame, hist: Optional[pd.DataFrame]) -> pd.DataFrame:
    """History is used for TREND and resolution AVERAGE when it has every filter column."""
    wants = plan["intent"] == "TREND" or (plan["intent"] == "AVERAGE" and plan.get("metric") == "resolution_hours")
    if wants and hist is not None and len(hist):
        if all(k in hist.columns or k in ("date_from", "date_to") for k in plan.get("filters", {})) and \
                plan.get("group_by") in (None, *hist.columns):
            return hist
    return df


def _metric_rows(plan: dict, sel: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    metric = plan.get("metric") or "eta"
    if metric == "resolution_hours":
        sel = sel[sel["closed_at"].notna()]
        sel = sel.assign(resolution_hours=(sel["closed_at"] - sel["created_at"]).dt.total_seconds() / 3600)
        return sel, "resolution_hours"
    if metric == "credibility":
        return sel[sel["credibility_score"].notna()], "credibility_score"
    sel = sel[sel["eta_low_hours"].notna() & sel["eta_high_hours"].notna()]
    return sel.assign(eta_mid_hours=(sel["eta_low_hours"] + sel["eta_high_hours"]) / 2), "eta_mid_hours"


# ============================================================================ formatting
def _fmt_n(x: float) -> str:
    return f"{x:,.1f}" if abs(x - round(x)) > 1e-9 else f"{int(round(x)):,}"


def describe(f: dict, df: pd.DataFrame) -> str:
    words = []
    st = f.get("status")
    if st:
        words.append("/".join(st).lower() if isinstance(st, list) else st.lower().replace("_", " "))
    if f.get("urgency_level"):
        words.append(f"{f['urgency_level']} urgency")
    rt = f.get("request_type")
    if rt:
        if isinstance(rt, list):
            words.append("noise" if set(rt) == {"RT120", "RT121"} else "/".join(rt))
        else:
            lab = df.loc[df["request_type"] == rt, "label"].dropna()
            words.append(lab.iloc[0].lower() if len(lab) else rt)
    elif f.get("complaint_family"):
        words.append(f["complaint_family"].lower().replace("_", " "))
    s = " ".join(words + ["requests"])
    if f.get("borough"):
        s += f" in {f['borough'].title()}"
    if f.get("agency"):
        s += f" for {f['agency']}"
    if f.get("crew"):
        s += f" on crew {f['crew']}"
    if f.get("priority_band"):
        s += f" in band {f['priority_band']}"
    if f.get("resolution_status"):
        s += f" closed as {f['resolution_status']}"
    if f.get("date_from"):
        s += f" created from {f['date_from'][:10]} to {f['date_to'][:10]}"
    return s


def _jsonable(v):
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat(timespec="seconds")
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if pd.isna(v) else round(float(v), 2)
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
        return None
    return v


def _records(frame: pd.DataFrame, cols) -> list[dict]:
    cols = [c for c in cols if c in frame.columns]
    out = frame[cols].head(MAX_TABLE_ROWS)
    return [{c: _jsonable(v) for c, v in zip(cols, r)} for r in out.itertuples(index=False, name=None)]


def format_answer(plan: dict, res: dict, df: pd.DataFrame) -> tuple[str, list[dict]]:
    intent, f = plan["intent"], plan.get("filters", {})
    what = describe(f, df)
    if intent == "COUNT":
        n = res["value"]
        if n == 1:
            what = what.replace("requests", "request", 1)
        return f"There {'is' if n == 1 else 'are'} {_fmt_n(n)} {what}.", [{"count": n}]
    if intent == "TOP_N":
        fr = res["frame"]
        if fr.empty:
            return f"No {what} found.", []
        g = (plan.get("group_by") or "label").replace("_", " ").replace("label", "request type")
        items = ", ".join(f"{k} ({_fmt_n(c)})" for k, c in zip(fr["group"], fr["count"]))
        return f"Top {len(fr)} by {g} for {what}: {items}.", _records(fr, ["group", "count"])
    if intent == "AVERAGE":
        col = res.get("metric_col", "eta_mid_hours")
        name = {"eta_mid_hours": "ETA (middle of the range)", "resolution_hours": "resolution time",
                "credibility_score": "credibility score"}[col]
        unit = "" if col == "credibility_score" else " hours"
        if "frame" in res:
            fr = res["frame"]
            if col == "credibility_score":
                fr = fr[fr["count"] >= MIN_GROUP_FOR_CREDIBILITY]
            fr = fr.assign(mean=fr["mean"].round(1))
            items = ", ".join(f"{k} {_fmt_n(m)}{unit}" for k, m in zip(fr["group"], fr["mean"]))
            return (f"Average {name} for {what}: {items}." if len(fr) else f"Not enough {what} to show an average."), \
                _records(fr, ["group", "mean", "count"])
        if res["value"] is None or (col == "credibility_score" and res["count"] < MIN_GROUP_FOR_CREDIBILITY):
            return f"Not enough {what} to show an average {name}.", []
        v = round(res["value"], 1)
        return f"The average {name} for {what} is {_fmt_n(v)}{unit}, over {_fmt_n(res['count'])} requests.", \
            [{"metric": name, "value": v, "count": res["count"]}]
    if intent == "TREND":
        fr = res["frame"]
        if fr.empty:
            return f"No {what} found for a trend.", []
        pk = fr.loc[fr["count"].idxmax()]
        per = plan.get("period", "week")
        txt = (f"{what[0].upper() + what[1:]} per {per}: {len(fr)} periods, from {_fmt_n(fr['count'].iloc[0])} "
               f"in {fr['period'].iloc[0]} to {_fmt_n(fr['count'].iloc[-1])} in {fr['period'].iloc[-1]}; "
               f"the peak is {_fmt_n(pk['count'])} in {pk['period']}.")
        return txt, _records(fr, ["period", "count"])
    if intent == "LIST":
        fr = res["frame"]
        if fr.empty:
            return f"No {what} found.", []
        shown = min(len(fr), MAX_TABLE_ROWS)
        return (f"Found {_fmt_n(len(fr))} {what}. The table shows {_fmt_n(shown)}, in queue order."
                if plan.get("request_id") is None else f"Request {plan['request_id']}: status {fr['status'].iloc[0]}."), \
            _records(fr, SAFE_LIST_COLUMNS)
    if intent == "WHY_RANKED":
        fr = res["frame"]
        if fr.empty:
            return "I cannot find that request in the snapshot.", []
        r = fr.iloc[0]
        if pd.isna(r.get("queue_position")):
            return f"Request {r['request_id']} is not in the open queue (status {r['status']}).", _records(fr, ["request_id", "status"])
        reasons = r.get("priority_reasons")
        why = str(reasons) if isinstance(reasons, str) and reasons else \
            f"urgency {r['urgency_level']}, waiting since {pd.Timestamp(r['created_at']).date()}"
        band = f", band {r['priority_band']}" if isinstance(r.get("priority_band"), str) else ""
        return (f"Request {r['request_id']} is at queue position {_fmt_n(r['queue_position'])}{band}. Reasons: {why}."), \
            _records(fr, ["request_id", "label", "urgency_level", "queue_position", "priority_band", "priority_reasons", "created_at"])
    return "", []


# ============================================================================ empty results and follow-ups
DIAG_ORDER = ("request_type", "complaint_family", "borough", "agency", "urgency_level", "crew", "priority_band",
              "resolution_status",
              "status", "date_from")
EMPTY_CONFIDENCE_FACTOR = 0.8


def _phrase(k: str, v: Any, df: pd.DataFrame) -> str:
    """How one filter reads after 'none are' / 'no'."""
    if k == "request_type":
        return describe({k: v}, df)[: -len(" requests")]
    if k == "complaint_family":
        return str(v).lower().replace("_", " ")
    if k == "borough":
        return f"in {str(v).title()}"
    if k == "agency":
        return f"for {v}"
    if k == "urgency_level":
        return f"{v} urgency"
    if k == "crew":
        return f"on crew {v}"
    if k == "priority_band":
        return f"in band {v}"
    if k == "status":
        return "/".join(v).lower() if isinstance(v, list) else str(v).lower().replace("_", " ")
    if k == "resolution_status":
        return f"closed as {v}"
    lo, hi = df["created_at"].min(), df["created_at"].max()
    return "in that date range" + (f" (this data runs {lo:%Y-%m-%d} to {hi:%Y-%m-%d})" if pd.notna(lo) else "")


def _values_exist(src: pd.DataFrame, k: str, v: Any) -> bool:
    if k == "date_from" or (k == "status" and v in ("open", "closed")):
        return True
    if k not in src.columns:
        return False
    vals = v if isinstance(v, list) else [v]
    have = set(src[k].dropna().astype(str).str.upper())
    return any(str(x).upper() in have for x in vals)


def _alternatives(src: pd.DataFrame, k: str, v: Any, prior: dict) -> list[str]:
    """Up to 3 values of the same field that do exist, given the other filters."""
    if k == "date_from":
        return ["a wider date range"]
    if k == "status" and v in ("open", "closed"):
        return ["closed" if v == "open" else "open"]
    col = "label" if k == "request_type" else k
    if col not in src.columns:
        return []
    base = apply_filters(src, prior)
    vc = base[col].dropna().astype(str).value_counts()
    skip = {str(x).upper() for x in (v if isinstance(v, list) else [v])}
    return [x for x in vc.index if x.upper() not in skip][:3]


def explain_empty(plan: dict, df: pd.DataFrame, hist: Optional[pd.DataFrame]) -> tuple[str, list[str], Optional[str]]:
    """Say which filter empties the result, and offer close values that exist."""
    f = plan.get("filters", {})
    src = _source(plan, df, hist)
    tail = {"COUNT": ", so the count is 0.", "TOP_N": ", so there is nothing to rank.",
            "AVERAGE": ", so there is no average.", "TREND": ", so there is no trend.", "LIST": ", so there is nothing to list."}
    end = tail.get(plan["intent"], ".")
    keys = [k for k in DIAG_ORDER if k in f]
    for k in keys:
        if not _values_exist(src, k, f[k]):
            alts = _alternatives(src, k, f[k], {})
            return f"This data has no {_phrase(k, f[k], df)} requests{end}", alts, k
    prior: dict = {}
    for k in keys:
        step = dict(prior, **({k: f[k]} if k != "date_from" else {"date_from": f["date_from"], "date_to": f["date_to"]}))
        if apply_filters(src, step).empty:
            alts = _alternatives(src, k, f[k], prior)
            have = describe(prior, df) if prior else "requests"
            verb = "were created" if k == "date_from" else "are"
            return f"There are {have}, but none {verb} {_phrase(k, f[k], df)}{end}", alts, k
        prior = step
    if plan["intent"] == "AVERAGE":
        return f"There are {describe(f, df)}, but none have a value for this average{end}", [], None
    return f"No {describe(f, df)} found{end}", [], None


def retry_questions(plan: dict, df: pd.DataFrame, key: Optional[str], alts: list[str]) -> list[str]:
    """After an empty result: the same question with the empty filter swapped for a value that exists."""
    f = {k: v for k, v in plan.get("filters", {}).items()}
    out = []
    if key is None:
        out = [f"How many {describe({k: v for k, v in f.items() if k != 'status'}, df)} are there?"]
        if plan.get("intent") == "AVERAGE" and (plan.get("metric") or "eta") == "eta":
            out.insert(0, f"What is the average resolution time for {describe(f, df)}?")
        return out
    for alt in alts or [None]:
        g = dict(f)
        if key == "date_from" or alt is None or alt == "a wider date range":
            g.pop("date_from", None), g.pop("date_to", None)
            if key != "date_from":
                g.pop(key, None)
        elif key == "request_type":
            code = df.loc[df["label"] == alt, "request_type"].dropna()
            if code.empty:
                continue
            g[key] = code.iloc[0]
        else:
            g[key] = alt
        out.append(f"How many {describe(g, df)} are there?")
    return out[:3]


def follow_ups(plan: dict, df: pd.DataFrame, res: dict) -> list[str]:
    """2-3 follow-up questions on the same filters. Each one is a question C12 can answer."""
    f = {k: v for k, v in plan.get("filters", {}).items() if k not in ("date_from", "date_to")}
    what = describe(f, df)
    has_type = "request_type" in f
    if not has_type:
        top = f"What are the top 5 request types for {what}?"
    elif "borough" not in f:
        top = f"Which borough has the most {what}?"
    else:
        top = f"Which urgency level has the most {what}?"
    count = f"How many {what} are there?"
    trend = f"Show the weekly trend of {what}."
    eta = f"What is the average resolution time for {what}?" if f.get("status") == "closed" or \
        f.get("status") in CLOSED_STATUSES else f"What is the average ETA for {what}?"
    intent = plan["intent"]
    if intent == "COUNT":
        out = [top, trend, eta]
    elif intent == "TOP_N":
        out = [count, trend, eta]
    elif intent == "AVERAGE":
        by = "borough" if "borough" not in f else "agency"
        metric = "resolution time" if plan.get("metric") == "resolution_hours" else "ETA"
        out = [count, f"Average {metric} by {by} for {what}", trend]
    elif intent == "TREND":
        out = [count, top, f"Show the monthly trend of {what}." if plan.get("period") != "month" else eta]
    elif intent == "LIST":
        ids = res.get("ids") or []
        out = [count, top] + ([f"Why is {ids[0]} ranked where it is?"] if ids else [])
    else:  # WHY_RANKED
        fr = res.get("frame")
        b = fr["borough"].iloc[0].title() if fr is not None and len(fr) and isinstance(fr["borough"].iloc[0], str) else None
        out = [f"List the open requests in {b}" if b else "List the open requests",
               f"What are the top 5 request types in {b}?" if b else "What are the top 5 request types?",
               "How many open high urgency requests are there?"]
    return out[:3]


# ============================================================================ unknown words
VOCAB_COLUMNS = ("borough", "label", "agency", "status", "crew", "resolution_status", "complaint_family",
                 "priority_band", "urgency_level")
REQ_NOUN = r"(?:complaints?|requests?|tickets?|reports?|calls?|jobs?|cases?|issues?|problems?|outages?|incidents?)"
PLACE_CUE = r"\b(?:borough|city|town|county|neighbou?rhood|area|district) of\s+([A-Za-z]+)"
_GENERIC = set("""a an the all any of in on at to for from by and or vs versus is are was were be been has have had do does
did how what which who when where why many much more most less least number count total show list give me find tell
new old urgent other these those this that my our their each every some no not yet still right now week weeks month
months year years day days last past next first since between until per nyc york 311 city eta id please data snapshot
history file queue current recent active can could would will should it its there here we you us i any reported filed
came get need tally overall just only also than then ones one oldest newest latest earliest""".split())


@lru_cache(maxsize=1)
def _code_vocab() -> frozenset:
    """Every word the parser itself knows (patterns, group words, months, training phrases)."""
    text = " ".join([p for p, _ in TYPE_WORDS] + list(FAMILIES.values()) + list(BOROUGHS.values()) +
                    [p for p, _ in STATUS_WORDS] + list(BANDS.values()) + list(RESOLUTIONS.values()) +
                    [p for _, p in _INTENT_RULES] + list(GROUPS) + MONTHS + list(NUM_WORDS) + [URGENCY] +
                    [t for v in _TRAIN.values() for t in v] + [x for v in _FILL.values() for x in v] + SUGGESTIONS)
    text = re.sub(r"\\[a-z]", " ", text.lower())  # "rats?" also yields "rat"; month abbreviations count as known
    return frozenset(re.findall(r"[a-z]{2,}", text) + re.findall(r"([a-z]{2,})s\?", text) +
                     [m[:3] for m in MONTHS] + ["sept"]) | _GENERIC


def check_terms(question: str, df: pd.DataFrame) -> tuple[str, list[str], list[str]]:
    """Find words that name an entity (a type before 'complaints', a place after 'borough of', a capitalised
    word, a crew id) and are not in the snapshot vocabulary. Close matches (difflib >= 0.8) are mapped to the
    known value and reported; the rest are returned as unknown so the answer can decline instead of dropping them."""
    vals = {c: set(df[c].dropna().astype(str).str.upper()) for c in VOCAB_COLUMNS if c in df.columns}
    data_words = sorted({w for vs in vals.values() for v in vs for w in re.findall(r"[a-z]+", v.lower()) if len(w) > 2})
    vocab = _code_vocab() | set(data_words)
    q, notes, unknown = question, [], []

    def swap(old: str, new: str):
        nonlocal q
        q = re.sub(rf"\b{re.escape(old)}\b", new, q, flags=re.I)
        notes.append(f"'{old}' as {new.upper()}")

    crews = sorted(x.lower() for x in vals.get("crew", ()))
    for c in dict.fromkeys(re.findall(CREW_ID, q.lower())):
        if c not in crews and not re.fullmatch(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*-\d+", c):
            hit = difflib.get_close_matches(c, crews, 1, 0.85)
            swap(c, hit[0]) if hit else unknown.append(c.upper())
    cands = re.findall(rf"\b([A-Za-z]+)\s+{REQ_NOUN}\b", q) + re.findall(PLACE_CUE, q, re.I)
    cands += [m.group(0) for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", q)
              if m.start() > 0 and not re.search(r"[.?!:]\s*$", q[:m.start()])]
    cands += [w for w in re.findall(r"\b[a-z]{4,}\b", q) if w not in vocab and w.rstrip("s") not in vocab
              and difflib.get_close_matches(w, data_words, 1, 0.85)]  # plain lower-case typos
    for w in dict.fromkeys(cands):
        if w.lower() in vocab or w.lower().rstrip("s") in vocab:
            continue
        hit = difflib.get_close_matches(w.lower(), data_words, 1, 0.8)
        swap(w, hit[0]) if hit else unknown.append(w)
    return q, notes, unknown


# ============================================================================ entry point
def _meta(t0: float, strategy: str, status: str = "OK") -> dict:
    return {"component_id": "C12", "component_version": VERSION, "model_version": strategy, "status": status,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "produced_at": datetime.now().isoformat(timespec="seconds"), "contract_version": CONTRACT_VERSION}


def _unknown(t0, strategy, text="I can answer counts, top lists, averages, trends, lists, and queue reasons "
                                "about the request snapshot. Try one of these.", status="OK") -> AdminAnswer:
    return AdminAnswer(meta=_meta(t0, strategy, status), matched_intent="UNKNOWN", answer_text=text,
                       confidence=0.0, suggestions=list(SUGGESTIONS))


def make_plan(question: str, strategy: str, now: datetime) -> tuple[dict, float]:
    slots = extract_slots(question, now)
    if strategy == "rules":
        intent, conf = rules_intent(question)
    elif strategy == "ml_intent":
        intent, conf = ml_intent(question)
    else:  # hybrid: rules first; the classifier fills in when no rule fires
        intent, conf = rules_intent(question)
        if intent == "UNKNOWN":
            mi, mc = ml_intent(question)
            if mi != "UNKNOWN" and mc >= 0.6 and (slots["filters"] or slots.get("request_id")):
                intent, conf = mi, min(mc, 0.8)
    plan = {"intent": intent, **slots}
    if intent == "WHY_RANKED" and not plan.get("request_id") and not re.search(r"\b(first|top)\b", question.lower()):
        conf = min(conf, 0.4)
    return plan, conf


def run(q: AdminQuestion, rows: Any, history: Any = None, strategy: Optional[str] = None,
        now: Optional[datetime] = None) -> AdminAnswer:
    t0 = time.perf_counter()
    method = strategy or os.environ.get("C12_STRATEGY") or PRODUCTION_STRATEGY
    if method not in STRATEGIES:
        raise ValueError(f"Unknown C12 strategy {method!r}. Choose one of {STRATEGIES}")
    try:
        if not isinstance(q, AdminQuestion) or not q.valid():
            return _unknown(t0, method, "Please ask a question of 3 to 300 characters.")
        if is_unsafe(q.question):
            return _unknown(t0, method, "I can only read the request snapshot. I cannot change data, show "
                                        "personal or per-citizen data, or follow other instructions.")
        df = to_frame(rows)
        hist = None if history is None else to_frame(history)
        ref = now or (df["created_at"].max() if df["created_at"].notna().any() else datetime.now())
        question, notes, unknown = check_terms(q.question, df)
        plan, conf = make_plan(question, method, pd.Timestamp(ref).to_pydatetime())
        if plan["intent"] == "UNKNOWN" or conf < MIN_CONFIDENCE:
            return _unknown(t0, method)
        if unknown:  # never drop a filter silently and answer for the whole table
            terms = ", ".join(f"'{w}'" for w in unknown)
            return AdminAnswer(meta=_meta(t0, method), matched_intent=plan["intent"],
                               filters=dict(plan["filters"], unknown_terms=unknown), confidence=0.3,
                               suggestions=list(SUGGESTIONS[:3]),
                               answer_text=f"I don't recognise {terms}: it is not a borough, request type, agency, crew "
                                           f"or status in this NYC 311 snapshot. There are no matching requests, so I "
                                           f"will not give a number for it.")
        if method == "hybrid":
            errs = validate_plan(plan)
            if errs:
                return _unknown(t0, method, "I could not build a safe query for that question.")
        res = execute_pandas(plan, df, hist)
        text, table = format_answer(plan, res, df)
        suggestions = follow_ups(plan, df, res)
        if not res.get("ids") and plan["intent"] in ("COUNT", "TOP_N", "AVERAGE", "TREND", "LIST"):
            text, alts, key = explain_empty(plan, df, hist)
            wider = dict(plan, filters={k: v for k, v in plan.get("filters", {}).items()
                                        if k != key and not (key == "date_from" and k == "date_to")})
            suggestions = list(dict.fromkeys(retry_questions(plan, df, key, alts) + follow_ups(wider, df, {})))[:3]
            if alts:
                text += " Values that do exist: " + ", ".join(alts) + "."
            conf = conf * EMPTY_CONFIDENCE_FACTOR
        shown = dict(plan["filters"])
        for k in ("group_by", "n", "metric", "request_id"):
            if k in plan and (k != "n" or plan["intent"] in ("TOP_N", "LIST")) and \
                    (k != "metric" or plan["intent"] == "AVERAGE") and (k != "group_by" or plan["intent"] in ("TOP_N", "AVERAGE")):
                shown[k] = plan[k]
        if plan["intent"] == "TREND":
            shown["period"] = plan.get("period", "week")
        if notes:
            text = "Note: I read " + ", ".join(notes) + ". " + text
            shown["read_as"] = notes
        return AdminAnswer(meta=_meta(t0, method), matched_intent=plan["intent"], filters=shown,
                           answer_text=text, table=table[:MAX_TABLE_ROWS], confidence=round(float(conf), 3),
                           suggestions=suggestions, selected_ids=list(res.get("ids", [])))
    except Exception as e:  # never crash the portal; never echo internals
        return _unknown(t0, method, f"I could not answer that safely ({type(e).__name__}).", status="DEGRADED")
