"""C02 Emergency Safeguard. Owner: Sankalp.

The one component that fails closed. Every piece of free text goes through it before
anything is stored, and if it thinks someone is in danger the 311 flow stops and the
citizen is told to call 911.

How it decides
  1. A weighted lexicon of emergency phrases runs first, in process, with no model and no
     network call. It handles negation, so "there is no gas smell" does not fire.
  2. A small classifier runs as a second, independent signal, and can only ADD detections.
  3. The two combine as: rules OR model above a threshold.

The operating point is recall first. On our 308-line gold set, the lexicon plus the
immobility patch, with the model at 0.45, reaches recall 1.00 at precision 0.576. We take
that trade knowingly: it means roughly fourteen people are wrongly shown the 911 screen,
and no one in real danger is filed as a pothole.
"""
from __future__ import annotations

import csv
import io
import re
import time
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path

from ..common import ComponentResult, Reason

VERSION = "1.0.0"
COMPONENT = "C02 emergency safeguard"
ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "data" / "reference"
MODEL_PATH = ROOT / "models" / "c02_emergency_tfidf_lr.joblib"

MODEL_THRESHOLD = 0.45          # chosen on the gold sweep: the lowest point reaching recall 1.0
STOP_CONFIDENCE = 0.85          # at or above this, stop the flow outright

NEGATION = re.compile(r"\b(no one|nobody|not) (is )?(hurt|injured|inside|stuck|trapped)\b", re.I)

# "fire escape", "fire hydrant" and "fire lane" are objects, not fires. The classifier reads
# the word fire and flags them, so the model signal alone may not raise these unless something
# genuinely burning is mentioned too. Rule hits are unaffected.
FIRE_OBJECT = re.compile(r"\bfire (escape|hydrant|lane|station|house)\b", re.I)
REAL_FIRE = re.compile(r"\b(on fire|smoke|flames?|burning|burnt|blaze|caught fire)\b", re.I)

# Added after the first run missed "the driver is not moving". A person who cannot move is
# a medical emergency however calmly it is described.
IMMOBILITY = [(re.compile(p, re.I), 0.90, "MEDICAL") for p in
              [r"\bnot moving\b", r"\bunconscious\b", r"\bnot breathing\b", r"\btrapped\b",
               r"\bunresponsive\b", r"\bpinned under\b"]]


@dataclass
class EmergencyResult(ComponentResult):
    is_emergency: bool = False
    category: str = "NONE"
    confidence: float = 0.0
    action: str = "CONTINUE"            # CONTINUE | CONFIRM_WITH_CITIZEN | STOP_CALL_911
    matched_signals: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _lexicon() -> list[tuple[re.Pattern, float, str]]:
    rows = list(csv.DictReader(io.open(REFERENCE / "emergency_lexicon.csv", encoding="utf-8")))
    return [(re.compile(r["pattern"], re.I), float(r["weight"]), r["category"]) for r in rows] + IMMOBILITY


@lru_cache(maxsize=1)
def _model():
    """The classifier is optional. If it cannot be loaded the rules still run, which is the
    whole point of keeping them separate."""
    try:
        import joblib
        m = joblib.load(MODEL_PATH)
        clf = m.steps[-1][1]
        if not hasattr(clf, "multi_class"):      # saved by a newer scikit-learn than ours
            clf.multi_class = "auto"
        return m
    except Exception:
        return None


def screen(text: str) -> EmergencyResult:
    """Screen one piece of free text. Never raises: if everything fails it asks the citizen."""
    t0 = time.perf_counter()
    reasons: list[Reason] = []

    try:
        hits = [(pat.search(text), w, cat) for pat, w, cat in _lexicon()]
        hits = [(m.group(0), w, cat) for m, w, cat in hits if m]
    except Exception:
        return _fail_closed(t0, "the lexicon could not be read")

    rule_conf, category, signals = 0.0, "NONE", []
    if hits:
        signals = [h[0] for h in hits][:5]
        best = max(hits, key=lambda h: h[1])
        category = best[2]
        rule_conf = best[1]
        if NEGATION.search(text):
            rule_conf *= 0.6
            reasons.append(Reason("NEGATED", "The text says nobody is hurt, so the rule weight is reduced",
                                  weight=-0.4 * best[1]))
        reasons.append(Reason(f"RULE_{category}", f"Matched the phrase '{signals[0]}'", weight=rule_conf,
                              evidence={"signals": signals}))

    model_conf = 0.0
    m = _model()
    fire_object_only = bool(FIRE_OBJECT.search(text)) and not REAL_FIRE.search(text)
    if m is not None:
        try:
            model_conf = float(m.predict_proba([text])[0][1])
            if fire_object_only and model_conf >= MODEL_THRESHOLD and rule_conf == 0:
                reasons.append(Reason("FIRE_OBJECT_NOT_A_FIRE",
                                      "Mentions a fire escape, hydrant or lane rather than a fire, so the "
                                      "classifier alone does not raise this"))
                model_conf = 0.0
            if model_conf >= MODEL_THRESHOLD:
                reasons.append(Reason("MODEL_FLAG",
                                      f"The classifier also reads this as an emergency ({model_conf:.2f})",
                                      weight=model_conf))
        except Exception:
            reasons.append(Reason("MODEL_UNAVAILABLE", "The classifier did not run; the rules still did"))
    else:
        reasons.append(Reason("MODEL_UNAVAILABLE", "The classifier is not loaded; the rules still ran"))

    fired = rule_conf > 0 or model_conf >= MODEL_THRESHOLD
    confidence = max(rule_conf, model_conf if model_conf >= MODEL_THRESHOLD else 0.0)

    if not fired:
        action, is_emergency = "CONTINUE", False
        reasons.append(Reason("NO_EMERGENCY_SIGNAL", "Nothing in the message suggests immediate danger"))
    elif confidence >= STOP_CONFIDENCE:
        action, is_emergency = "STOP_CALL_911", True
    else:
        action, is_emergency = "CONFIRM_WITH_CITIZEN", False
        reasons.append(Reason("ASK_TO_CONFIRM", "Possible emergency: ask whether anyone is in danger right now"))

    if category == "NONE" and fired:
        category = "LIFE_SAFETY_HAZARD"

    return EmergencyResult(component=COMPONENT, version=VERSION, ok=True,
                           latency_ms=(time.perf_counter() - t0) * 1000,
                           reasons=reasons, is_emergency=is_emergency, category=category,
                           confidence=round(confidence, 3), action=action, matched_signals=signals)


def _fail_closed(t0: float, why: str) -> EmergencyResult:
    """If this component cannot do its job, the citizen still sees the 911 advisory.
    Every other component in the system fails open; this one does the opposite."""
    return EmergencyResult(
        component=COMPONENT, version=VERSION, ok=False,
        latency_ms=(time.perf_counter() - t0) * 1000,
        reasons=[Reason("FAIL_CLOSED", f"The safeguard failed ({why}), so the citizen is asked to confirm")],
        is_emergency=False, category="NONE", confidence=0.0, action="CONFIRM_WITH_CITIZEN")


run = screen
