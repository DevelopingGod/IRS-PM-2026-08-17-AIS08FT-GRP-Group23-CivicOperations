"""C01 NLP Intake Bot. Owner: Sankalp.

Turns what a citizen typed or said into a structured request: which of the 34 request
types it is, where it is, and what is still missing before it can be filed.

The model is MiniLM sentence embeddings with logistic regression on top, which won the
benchmark on both test slices (0.840 overall, 0.945 on the team's lines, 0.708 on the
harder ones) and runs in about 2 ms per message on a CPU. Keyword rules stay in as a
fallback, so the bot still answers if the model cannot be loaded.

Order matters: the emergency safeguard runs FIRST, before anything is parsed or stored.
"""
from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..emergency_check import c02_emergency
from ..common import ComponentResult, Reason

VERSION = "1.0.0"
COMPONENT = "C01 NLP intake bot"
ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "data" / "reference"
MODEL_PATH = ROOT / "models" / "c01_intent_minilm_logreg.joblib"
# The sentence encoder is fetched from Hugging Face once, at this exact revision, so a change
# published upstream can never silently change what the intake model reads.
ENCODER_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
SLOT_TAGGER_PATH = ROOT / "models" / "c01_slot_tagger"

def _abstain_threshold(default: float = 0.60) -> float:
    """Taken from the model manifest, so retuning the model also retunes the threshold
    instead of leaving a stale number hardcoded here."""
    try:
        import json
        return float(json.loads((ROOT / "models" / "c01_manifest.json").read_text())["_abstain_threshold"])
    except Exception:
        return default


ABSTAIN_BELOW = None            # set on first use, from the manifest

BOROUGHS = {"manhattan": "MANHATTAN", "brooklyn": "BROOKLYN", "bklyn": "BROOKLYN", "queens": "QUEENS",
            "bronx": "BRONX", "staten island": "STATEN ISLAND", "staten is": "STATEN ISLAND"}
SUFFIXES = ("street|st|avenue|ave|av|road|rd|boulevard|blvd|place|pl|parkway|pkwy|drive|dr|lane|ln|"
            "way|court|ct|terrace|ter|concourse|plaza|plz|square|sq|highway|hwy|expressway|expy|"
            "turnpike|tpke|crescent|loop|walk|row|alley|oval|circle|cir|bridge|esplanade|promenade")
STREET = r"(" + SUFFIXES + r")\b"
# Streets New Yorkers name without a suffix.
NAMED_STREETS = ("broadway", "bowery", "the bowery", "grand concourse", "esplanade")
DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west"}
ADDRESS = re.compile(r"\b(\d{1,5}(?:-\d{1,4})?[a-z]?\s+(?:[a-z0-9]+\s){0,3}(?:" + SUFFIXES + r"|"
                     + "|".join(NAMED_STREETS) + r")\b)", re.I)
# Words, with punctuation split off, but "37-12" (a Queens house number) and "42nd" kept whole.
TOKEN = re.compile(r"\d+-\d+[a-z]?|\w+(?:['’]\w+)*|[^\w\s]", re.I)
_SUFFIX_WORDS = set(SUFFIXES.split("|"))
CORNER = re.compile(r"\b(?:corner|intersection)\s+of\s+([a-z0-9 .'-]+?)\s+(?:and|&)\s+([a-z0-9 .'-]+?)"
                    r"(?=\s*[,.;!?]|\s+(?:in|on|is|are|was|near|has|have)\b|$)", re.I)
_TRAILING = re.compile(r"(?:[\s,.;:!?)(]+|\s+(?:ny|nyc|new york|usa|\d{5}))+$", re.I)
ON_STREET = re.compile(r"\b(?:on|at|near|outside|by|in front of)\s+((?:[a-z0-9]+\s){0,3}" + STREET + r")", re.I)
CROSS = re.compile(r"\b((?:[a-z0-9]+\s){0,3}" + STREET + r")\s*(?:and|&|at)\s*((?:[a-z0-9]+\s){0,3}" + STREET + r")", re.I)
TIME_REF = re.compile(r"\b(since [a-z ]{3,20}|for (?:a|two|three|four|\d+) (?:days?|weeks?|hours?|months?)|"
                      r"right now|all day|last night|this morning|every time it rains)\b", re.I)
LANDMARK = re.compile(r"\b(?:near|by|outside|opposite|in front of)\s+(?:the\s+)?"
                      r"(school|hospital|park|station|library|church|mosque|temple|playground|"
                      r"bus stop|subway|deli|bodega|pharmacy)\b", re.I)
HAZARD_WORDS = ["flooding", "sparking", "live wire", "collapsed", "leaking", "blocked", "exposed",
                "hanging", "sinking", "icy", "overflowing", "smoke", "fire"]


@dataclass
class Slots:
    borough: str | None = None
    incident_address: str | None = None
    cross_street: str | None = None
    landmark: str | None = None
    time_reference: str | None = None
    hazard_terms: list[str] = field(default_factory=list)


@dataclass
class IntakeResult(ComponentResult):
    request_type_code: str = "OTHER"
    confidence: float = 0.0
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    slots: Slots = field(default_factory=Slots)
    missing_slots: list[str] = field(default_factory=list)
    next_question: str | None = None
    ready_to_submit: bool = False
    needs_human: bool = False
    emergency: object = None


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict]:
    return {r["request_type_code"]: r for r in
            csv.DictReader(io.open(REFERENCE / "request_catalog.csv", encoding="utf-8"))}


@lru_cache(maxsize=1)
def _keywords() -> dict[str, set[str]]:
    return {r["request_type_code"]: set(r["keywords"].split())
            for r in csv.DictReader(io.open(REFERENCE / "intent_keywords.csv", encoding="utf-8"))}


@lru_cache(maxsize=1)
def _model():
    """Returns (encoder, classifier, labels) or None. Loading MiniLM takes a few seconds the
    first time; after that it is about 2 ms per message."""
    try:
        import joblib
        from sentence_transformers import SentenceTransformer
        bundle = joblib.load(MODEL_PATH)
        clf = bundle["classifier"]
        if not hasattr(clf, "multi_class"):
            clf.multi_class = "auto"
        return SentenceTransformer(bundle["encoder"], device="cpu", revision=ENCODER_REVISION), clf, bundle["labels"]
    except Exception:
        return None


def _keyword_intent(text: str) -> tuple[str, float]:
    words = set(re.findall(r"[a-z\-]+", text.lower()))
    scores = {code: len(words & kw) for code, kw in _keywords().items()}
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    return (best, scores[best] / total) if total else ("OTHER", 0.0)


@lru_cache(maxsize=1)
def _slot_tagger():
    """The word tagger that reads the address and the time phrase out of the message.

    It is a small language model fine tuned to label every word, and on messages
    labelled by hand it scores a span F1 of 0.72 on the location and time slots
    against 0.43 for the patterns below. It is optional on purpose. If the model
    folder is missing, or torch is not installed, this returns None and the patterns
    do the work, so the component still runs anywhere with nothing but the standard
    library plus pandas. See 06_Workspace_Tools/train_c01_slot_tagger.py for how it was measured.
    """
    try:
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer
        if not SLOT_TAGGER_PATH.exists():
            return None
        model = AutoModelForTokenClassification.from_pretrained(SLOT_TAGGER_PATH)  # nosec B615: a local folder, not a download
        model.eval()
        return model, AutoTokenizer.from_pretrained(SLOT_TAGGER_PATH), torch  # nosec B615: a local folder
    except Exception:
        return None


def _tagged_slots(text: str) -> dict[str, str] | None:
    """Runs the word tagger and returns the text of each slot it found, or None."""
    loaded = _slot_tagger()
    if loaded is None:
        return None
    try:
        model, tok, torch = loaded
        # Punctuation as its own word: "Avenue," left whole made the tagger drop the word.
        words = TOKEN.findall(text)
        if not words:
            return {}
        enc = tok([words], is_split_into_words=True, truncation=True,
                  max_length=64, return_tensors="pt")
        with torch.no_grad():
            best = model(**enc).logits.argmax(-1)[0]
        labels = model.config.id2label
        tags, prev = ["O"] * len(words), None
        for pos, wid in enumerate(enc.word_ids(0)):
            if wid is not None and wid != prev:
                tags[wid] = labels[int(best[pos])]
            prev = wid
        out: dict[str, str] = {}
        cur, start = None, 0
        for i, t in enumerate(tags + ["O"]):
            if cur and not t.startswith("I-" + cur):
                out.setdefault(cur, " ".join(words[start:i]))
                cur = None
            if t.startswith("B-"):
                cur, start = t[2:], i
        return out
    except Exception:
        return None


def _clean(span: str | None) -> str | None:
    """A slot without stray punctuation, state, country or zip code at the end."""
    if not span:
        return None
    out = _TRAILING.sub("", " ".join(span.split())).strip(" ,.;:-")
    return out or None


def _borough(text: str, hint: str | None) -> str | None:
    """The borough named in the message. A borough word that is part of a street name, like
    Manhattan Ave in Greenpoint, does not count; the last borough mentioned wins."""
    low = " ".join(TOKEN.findall(text.lower()))
    masked = re.sub(r"\b(" + "|".join(BOROUGHS) + r")\s+" + STREET, " ", low)
    found = [(m.start(), BOROUGHS[m.group(1)]) for m in re.finditer(r"\b(" + "|".join(BOROUGHS) + r")\b", masked)]
    return max(found)[1] if found else hint


def _complete_address(span: str, text: str) -> str:
    """The tagger sometimes stops one word early ("450 Atlantic" for "450 Atlantic Avenue").
    If the words right after the span are a direction, a numbered street or a street type,
    they belong to the address. A borough name at the end does not."""
    words = [w for w in TOKEN.findall(text)]
    span_words = TOKEN.findall(span)
    n = len(span_words)
    for i in range(len(words) - n + 1):
        if [w.lower() for w in words[i:i + n]] == [w.lower() for w in span_words]:
            j = i + n
            while j < len(words) and span_words and span_words[-1].lower() not in _SUFFIX_WORDS:
                w = words[j].lower()
                if w in _SUFFIX_WORDS or w in DIRECTIONS or re.fullmatch(r"\d+(st|nd|rd|th)", w):
                    span_words.append(words[j])
                    j += 1
                else:
                    break
            break
    while span_words and span_words[-1].lower() in set(BOROUGHS) | {"island", "the"}:
        span_words.pop()
    return " ".join(span_words)


def _fill_slots(text: str, borough_hint: str | None, reasons: list[Reason]) -> Slots:
    s = Slots()
    low = text.lower()
    s.borough = _borough(text, borough_hint)
    # The word tagger goes first where it is available, because it is roughly twice
    # as accurate on real messages. Anything it does not find falls through to the
    # patterns below, so a missing model only ever costs accuracy, never a crash.
    if tagged := _tagged_slots(text):
        s.incident_address = _clean(tagged.get("ADDRESS"))
        s.cross_street = _clean(tagged.get("CROSS_STREET"))
        s.time_reference = _clean(tagged.get("TIME"))
        if s.incident_address or s.time_reference:
            reasons.append(Reason("SLOTS_FROM_MODEL",
                                  "Address and time read by the word tagger"))
    # A cross street must look like a street, not a neighbourhood the tagger guessed at.
    if s.cross_street and not (re.search(r"\b(" + SUFFIXES + r")\b", s.cross_street, re.I) or
                               re.search(r"\b\d+(st|nd|rd|th)\b", s.cross_street, re.I) or
                               any(n in s.cross_street.lower() for n in NAMED_STREETS)):
        s.cross_street = None
    # "the corner of 5th Ave and 23rd St": the first street is the address, the second the cross.
    corner = CORNER.search(text)
    if corner and not s.incident_address:
        s.incident_address = _clean(_complete_address(corner.group(1), text))
        s.cross_street = s.cross_street or _clean(_complete_address(corner.group(2), text))
    if not s.cross_street and (m := CROSS.search(text)):
        s.cross_street = _clean(f"{m.group(1).strip()} and {m.group(3).strip()}")
    pattern = ADDRESS.search(text)
    if s.incident_address:
        s.incident_address = _clean(_complete_address(s.incident_address, text))
        # Where the pattern reads a longer address that contains the tagger's, use it.
        if pattern and s.incident_address and s.incident_address.lower() in pattern.group(1).lower() \
                and len(pattern.group(1)) > len(s.incident_address):
            s.incident_address = _clean(pattern.group(1))
    if not s.incident_address:
        if pattern:
            s.incident_address = _clean(pattern.group(1))
        elif m := ON_STREET.search(text):
            s.incident_address = _clean(m.group(1))
    if m := LANDMARK.search(text):
        s.landmark = m.group(1).lower()
    if not s.time_reference and (m := TIME_REF.search(text)):
        s.time_reference = m.group(1).strip()
    s.hazard_terms = [w for w in HAZARD_WORDS if w in low]
    found = [k for k, v in (("address", s.incident_address), ("cross street", s.cross_street),
                            ("landmark", s.landmark), ("borough", s.borough)) if v]
    if found:
        reasons.append(Reason("LOCATION_FOUND", "Location read from the message: " + ", ".join(found)))
    return s


def parse(utterance: str, borough_hint: str | None = None, history: list[str] | None = None) -> IntakeResult:
    """Parse one message. The full text of the conversation so far is used for slots, so a
    citizen can give the street in one turn and the problem in another."""
    t0 = time.perf_counter()
    reasons: list[Reason] = []
    # Never trust the input: any type, control characters, or a pasted essay.
    def tidy(x) -> str:
        return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(x or "")).split())[:1000]
    if isinstance(history, str):
        history = [history]
    whole = tidy(" ".join(tidy(h) for h in (history or [])) + " " + tidy(utterance))

    # 1. safety first, always, before anything is parsed or stored
    emg = c02_emergency.screen(whole)
    if emg.action == "STOP_CALL_911":
        reasons.append(Reason("EMERGENCY_STOP", "Stopped: this looks like an emergency, nothing was filed"))
        return IntakeResult(component=COMPONENT, version=VERSION,
                            latency_ms=(time.perf_counter() - t0) * 1000, reasons=reasons + emg.reasons,
                            request_type_code="EMERGENCY", confidence=emg.confidence,
                            ready_to_submit=False, needs_human=False, emergency=emg)

    # 2. intent
    code, conf, alts, method = "OTHER", 0.0, [], "keywords"
    loaded = _model()
    if loaded is not None:
        enc, clf, labels = loaded
        try:
            vec = enc.encode([whole], normalize_embeddings=True, show_progress_bar=False)
            probs = clf.predict_proba(vec)[0]
            order = probs.argsort()[::-1]
            code, conf = clf.classes_[order[0]], float(probs[order[0]])
            alts = [(str(clf.classes_[i]), round(float(probs[i]), 3)) for i in order[1:4]]
            method = "model"
        except Exception:
            loaded = None
    if loaded is None:
        code, conf = _keyword_intent(whole)
        reasons.append(Reason("MODEL_UNAVAILABLE", "The model did not load, so keyword rules were used"))

    label = _catalog().get(code, {}).get("citizen_label", code)
    reasons.append(Reason("INTENT", f"Read as '{label}' ({method}, confidence {conf:.2f})", weight=conf,
                          evidence={"alternatives": alts}))

    # 3. abstain rather than guess confidently at something we do not recognise
    global ABSTAIN_BELOW
    if ABSTAIN_BELOW is None:
        ABSTAIN_BELOW = _abstain_threshold()
    needs_human = conf < ABSTAIN_BELOW or code in ("OTHER", "SMALLTALK")
    if conf < ABSTAIN_BELOW:
        reasons.append(Reason("LOW_CONFIDENCE",
                              f"Below the {ABSTAIN_BELOW:.2f} threshold, so a person reads this rather than "
                              "the system guessing a crew"))

    # 4. slots and the next question
    slots = _fill_slots(whole, borough_hint, reasons)
    if code in ("OTHER", "SMALLTALK"):
        # nothing a crew can be sent to, so do not ask a citizen for a street address
        missing, question = [], None
        reasons.append(Reason("NOT_DISPATCHABLE",
                              "This is not something a crew can be sent to, so it goes to a person "
                              "rather than asking for a location"))
    else:
        missing = [n for n, v in (("incident_address", slots.incident_address),
                                  ("borough", slots.borough)) if not v]
        question = None
        if missing:
            question = ("Which street is it on, and roughly what number?" if "incident_address" in missing
                        else "Which borough is that in?")
    ready = not missing and not needs_human and code not in ("OTHER", "SMALLTALK", "EMERGENCY")

    return IntakeResult(component=COMPONENT, version=VERSION,
                        latency_ms=(time.perf_counter() - t0) * 1000, reasons=reasons,
                        request_type_code=code, confidence=round(conf, 3), alternatives=alts,
                        slots=slots, missing_slots=missing, next_question=question,
                        ready_to_submit=ready, needs_human=needs_human, emergency=emg)


run = parse
