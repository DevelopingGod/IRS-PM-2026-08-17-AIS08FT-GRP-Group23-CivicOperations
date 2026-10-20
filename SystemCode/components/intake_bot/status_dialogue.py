"""Intake bot, after filing: answers a resident's questions about their own requests.

The proposal (slide 5, step 6): "NLP Bot: answer status questions. If the citizen reports
that the problem persists" the request comes back. This module only reads the message; the
app decides what to do with the result and fetches the status.

Rules, not a model: a handful of intents, each with phrases and a negation check, so that
"it is not fixed" is never read as "it is fixed". Rules are the right tool here: the intents
are few and fixed, every decision can be explained, and there is no labelled data for them.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..common import ComponentResult, Reason

VERSION = "1.0.0"
COMPONENT = "Intake bot status dialogue"

REFERENCE = re.compile(r"\b(CO)[-\s]?([0-9A-Z]{6})\b", re.I)    # request references; resident IDs are NYC-XXXX-XXXX
NEGATION = re.compile(r"\b(not|n't|never|no longer|isn't|wasn't|hasn't|haven't|didn't|still not|nobody)\b", re.I)

# Checked in this order: a persisting problem matters more than a status question.
INTENTS = [
    ("PERSISTS", [r"\bstill (there|broken|happening|not|leaking|flooding|out|dark|blocked|open)\b",
                  r"\b(came|come|coming) back\b", r"\bagain\b", r"\bpersist", r"\bnot (been )?(fixed|repaired|resolved|done|cleared)\b",
                  r"\bnobody (came|fixed|showed)\b", r"\b(never|hasn't been|has not been|wasn't|was not) (fixed|repaired|resolved)\b",
                  r"\bsame problem\b", r"\breturned\b"]),
    ("FIXED", [r"\b(fixed|repaired|resolved|cleared|sorted)\b", r"\ball good\b", r"\bthank(s| you)\b"]),
    ("LIST", [r"\b(my|all) (requests|reports)\b", r"\blist\b", r"\bwhat have i reported\b", r"\bshow (me )?(my|all)\b"]),
    ("STATUS", [r"\bstatus\b", r"\bwhere\b", r"\bwhen\b", r"\bupdate\b", r"\bprogress\b", r"\bany news\b",
                r"\bhow long\b", r"\bwhat happened\b", r"\bwho\b", r"\bcrew\b", r"\btrack\b", r"\bcheck\b"]),
    ("GREETING", [r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\b"]),
]


@dataclass
class StatusTurn(ComponentResult):
    intent: str = "UNKNOWN"          # PERSISTS | FIXED | LIST | STATUS | GREETING | UNKNOWN
    reference: str | None = None     # "CO-1A2B3C" when the message names one
    matched: list[str] = field(default_factory=list)


def read(message: str) -> StatusTurn:
    t0 = time.perf_counter()
    text = " ".join(str(message or "").split())[:500]
    low = text.lower()
    reasons: list[Reason] = []
    ref = None
    if m := REFERENCE.search(text):
        ref = f"{m.group(1).upper()}-{m.group(2).upper()}"
        reasons.append(Reason("REFERENCE", f"The message names request {ref}"))
    intent, matched = "UNKNOWN", []
    for name, patterns in INTENTS:
        hits = [p for p in patterns if re.search(p, low)]
        if not hits:
            continue
        if name == "FIXED":
            # "fixed" under a negation is a persisting problem, not a thank you
            span = re.search("|".join(hits), low)
            before = low[max(0, span.start() - 25):span.start()] if span else ""
            if NEGATION.search(before):
                intent, matched = "PERSISTS", hits
                reasons.append(Reason("NEGATION", "'fixed' is negated, so the problem is still there"))
                break
        intent, matched = name, hits
        break
    if intent == "UNKNOWN" and ref:
        intent = "STATUS"                      # a bare reference is a status question
    reasons.append(Reason("INTENT", f"Read as {intent.lower()}"))
    return StatusTurn(component=COMPONENT, version=VERSION, latency_ms=(time.perf_counter() - t0) * 1000,
                      reasons=reasons, intent=intent, reference=ref, matched=matched)
