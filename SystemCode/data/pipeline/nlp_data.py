"""Step 4: synthetic NLP training data for C01 (intent + slots), C02 (profanity)
and C03 (emergency).

The TRAINING utterances are generated from templates, varied with real NYC
street names, typos, voice-style fillers and mixed casing. The TEST set that
matters is data/reference/nlp_gold_test.csv: written by hand, never generated,
never trained on. Report results on it, because a model can score 99% on its
own templates and still fail on real speech.

Output: data/processed/synthetic_nlp_utterances.parquet with columns
  text, intent (RTxxx | EMERGENCY | SMALLTALK | OTHER), emergency_category,
  profanity (NONE | MILD | SEVERE), tokens, bio_tags, borough, split
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .config import PROCESSED, REFERENCE, SEED

PROBLEMS: dict[str, list[str]] = {
    "RT001": ["a pothole", "a huge pothole", "a deep hole in the road", "potholes all over the street", "a crater in the road that damaged my tire"],
    "RT002": ["a sinkhole", "the road caving in", "a cave-in on the street", "the pavement collapsed into a hole", "the street is sinking"],
    "RT003": ["a broken manhole cover", "the road surface is cracked and uneven", "a raised steel plate on the road", "loose asphalt everywhere"],
    "RT004": ["the street light is out", "a broken streetlight", "the street lamp keeps flickering", "it is completely dark, the lights on the street are not working", "a dim street light"],
    "RT005": ["the traffic light is not working", "the signal is stuck on red", "a broken traffic signal", "the pedestrian walk signal is dead", "traffic lights are flashing at the intersection"],
    "RT006": ["a broken sidewalk", "the sidewalk is cracked and lifted", "a damaged curb", "the pavement slab is broken, people keep tripping", "a missing chunk of curb"],
    "RT020": ["a water main break", "water gushing out of the street", "a water leak coming up from the road", "water pouring out of a crack in the street"],
    "RT021": ["a clogged catch basin", "the storm drain is blocked", "the drain is clogged with leaves", "the drain outside is overflowing", "the sewer grate is blocked and water is pooling"],
    "RT022": ["street flooding", "the whole street is flooded", "water is flooding the road", "the intersection is under water", "flooding after the rain"],
    "RT023": ["a sewer backup", "sewage coming up from the manhole", "the sewer is backing up into my basement", "a manhole overflowing", "raw sewage on the street"],
    "RT024": ["an open fire hydrant", "a fire hydrant running", "the hydrant is leaking", "a hydrant spraying water", "someone opened the hydrant"],
    "RT025": ["brown water from the tap", "dirty water coming out of the faucet", "very low water pressure", "the tap water smells weird", "no water pressure"],
    "RT040": ["my garbage was not picked up", "missed trash collection", "the recycling was not collected", "they skipped our pickup", "bulk trash left behind by the sanitation truck"],
    "RT041": ["illegal dumping", "someone dumped a mattress on the sidewalk", "a pile of construction debris dumped", "people dumping tires", "old furniture dumped on the corner"],
    "RT042": ["a dirty street", "litter everywhere", "overflowing trash on the sidewalk", "the street has not been swept", "garbage all over the block"],
    "RT043": ["a derelict car", "an abandoned wrecked car with no plates", "a junk car sitting for weeks", "a burned out vehicle", "a car with no plates and flat tires"],
    "RT044": ["the sidewalk is not shoveled", "icy sidewalk", "snow not cleared", "ice on the road", "a snow pile blocking the crosswalk"],
    "RT045": ["a dead animal", "a dead cat on the street", "a dead raccoon", "a dead bird on the sidewalk", "a dead rat in front of the building"],
    "RT060": ["a fallen tree", "a tree branch fell down", "a big limb broke off the tree", "a tree fell across the road", "a cracked branch about to fall"],
    "RT061": ["tree branches hitting the power lines", "an overgrown tree blocking the street sign", "branches hanging over the sidewalk", "tree branches touching my windows"],
    "RT062": ["a dead tree", "a dying tree", "the tree has no leaves and looks dead", "a rotten tree"],
    "RT080": ["no heat in my apartment", "no hot water", "the heating is not working", "our building has no heat", "the radiator is cold and it is freezing"],
    "RT081": ["water leaking from the ceiling", "a leak from the apartment upstairs", "water dripping through the ceiling", "a heavy leak in the bathroom"],
    "RT082": ["a broken toilet", "the sink is clogged", "a plumbing problem", "the bathtub drain is blocked", "a leaking pipe under the sink"],
    "RT083": ["mold in the bathroom", "roaches in the apartment", "bed bugs", "mice in my kitchen", "the hallway is filthy and unsanitary"],
    "RT084": ["the outlets are not working", "no electricity in my apartment", "the lights keep going out in the building", "a broken light switch", "power outage in the building"],
    "RT100": ["rats", "rats running around", "a rat infestation", "rodents in the park", "rats coming out of the garbage"],
    "RT120": ["loud music from my neighbor", "my neighbors are partying all night", "banging and pounding from upstairs", "loud noise from the apartment next door"],
    "RT121": ["loud music from a bar", "a car blasting music", "people yelling on the street", "noise from a restaurant", "a car honking nonstop"],
    "RT122": ["a car blocking my driveway", "someone parked in my driveway", "my driveway is blocked", "I cannot get my car out, a vehicle is blocking the driveway"],
    "RT123": ["a car parked at the fire hydrant", "illegal parking", "a truck double parked", "a car parked on the sidewalk", "someone parked in the bus stop"],
    "RT124": ["an abandoned car", "a car that has not moved in weeks", "an abandoned vehicle with plates", "a car left on the street for a month"],
    "RT140": ["illegal construction", "construction work without a permit", "the building is shaking from construction", "work going on after hours without permits", "unsafe scaffolding"],
    "RT141": ["the elevator is broken", "the elevator is not working", "our only elevator is out of service", "the lift is stuck"],
}
EMERGENCY_UTTERANCES = {
    "MEDICAL": ["my father collapsed and is not breathing", "someone is unconscious on the sidewalk", "a man is having a heart attack",
                "my friend overdosed please help", "a woman fell and is bleeding badly", "my neighbor is having a seizure"],
    "FIRE": ["the building next door is on fire", "there are flames coming out of the window", "a car is on fire on my street",
             "smoke is coming out of the basement and it is spreading"],
    "GAS_LEAK": ["I smell gas in the hallway", "there is a strong gas smell in my apartment", "gas leak in the building", "it smells like gas outside"],
    "CRIME_IN_PROGRESS": ["someone is breaking into my car right now", "there is a robbery happening at the store", "I heard shots fired",
                          "a man with a knife is threatening people", "someone is trying to break in to my apartment"],
    "VIOLENCE_OR_THREAT": ["my neighbor is being attacked", "someone is beating a woman outside", "a man threatened to kill me",
                           "there is a fight and someone got stabbed"],
    "LIFE_SAFETY_HAZARD": ["there is a live wire down on the street", "power lines are on the ground and sparking",
                           "a child is trapped in the elevator", "the building wall looks like it is about to collapse"],
}
SMALLTALK = ["hi", "hello there", "thank you", "thanks a lot", "who are you", "what can you do", "ok", "bye", "good morning", "help"]
OTHER = ["I want to pay a parking ticket", "how do I renew my passport", "what are the library hours", "I lost my wallet on the bus",
         "I want to complain about my taxi driver", "when is the next election", "my landlord raised the rent"]
TIME_REFS = ["since this morning", "for two days", "since last night", "for a week now", "right now", "since yesterday", "all day"]
OPENERS = ["", "", "hi, ", "hello, ", "I want to report ", "I'd like to report ", "please help, ", "can you send someone, ", "good morning, "]
FILLERS = ["um ", "uh ", "so ", "like ", "yeah ", "okay so "]
BOROUGH_WORDS = {"MANHATTAN": ["manhattan", "the city"], "BROOKLYN": ["brooklyn", "bk"], "QUEENS": ["queens"],
                 "BRONX": ["the bronx", "bronx"], "STATEN ISLAND": ["staten island", "SI"]}


def _typo(word: str, rng) -> str:
    if len(word) < 4 or rng.random() > 0.5:
        return word
    i = int(rng.integers(1, len(word) - 1))
    op = rng.integers(0, 3)
    if op == 0:
        return word[:i] + word[i + 1:]
    if op == 1:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return word[:i] + word[i] + word[i:]


class _Builder:
    """Builds a sentence while recording character spans for slot labels."""

    def __init__(self):
        self.text, self.spans = "", []

    def add(self, s: str, label: str | None = None):
        start = len(self.text)
        self.text += s
        if label and s.strip():
            lead = len(s) - len(s.lstrip())
            self.spans.append((start + lead, start + len(s.rstrip()), label))


def _bio(text: str, spans) -> tuple[list[str], list[str]]:
    tokens, tags = [], []
    for m in re.finditer(r"\S+", text):
        tag = "O"
        for s, e, lab in spans:
            if m.start() >= s and m.end() <= e:
                tag = ("B-" if m.start() == s else "I-") + lab
        tokens.append(m.group())
        tags.append(tag)
    return tokens, tags


def _addresses(real: pd.DataFrame) -> pd.DataFrame:
    a = real[["borough", "incident_address"]].dropna().copy()
    a["incident_address"] = a["incident_address"].str.replace(r"\s+", " ", regex=True).str.strip()
    a = a[a["incident_address"].str.match(r"^\d+[A-Z-]* [A-Z0-9 ]+$", na=False)]
    a["house"] = a["incident_address"].str.extract(r"^(\S+)")
    a["street"] = a["incident_address"].str.replace(r"^\S+\s+", "", regex=True).str.title()
    return a.drop_duplicates("incident_address")


def generate(real: pd.DataFrame, per_intent: int = 520, rng=None) -> pd.DataFrame:
    rng = rng or np.random.default_rng(SEED + 10)
    addr = _addresses(real)
    streets_by_b = {b: g for b, g in addr.groupby("borough")}
    profan = pd.read_csv(REFERENCE / "profanity_lexicon.csv")
    rows = []

    def location(b: _Builder, borough: str):
        g = streets_by_b[borough].sample(2, random_state=int(rng.integers(1e9)))
        house, street, cross = g.iloc[0]["house"], g.iloc[0]["street"], g.iloc[1]["street"]
        form = rng.integers(0, 6)
        if form == 0:
            b.add(" at "); b.add(f"{house} {street}", "ADDRESS")
        elif form == 1:
            b.add(" on "); b.add(street, "ADDRESS"); b.add(" near "); b.add(cross, "CROSS_STREET")
        elif form == 2:
            b.add(" outside my building on "); b.add(street, "ADDRESS")
        elif form == 3:
            b.add(" at the corner of "); b.add(street, "ADDRESS"); b.add(" and "); b.add(cross, "CROSS_STREET")
        elif form == 4:
            b.add(" in front of "); b.add(f"{house} {street}", "ADDRESS")
        if rng.random() < 0.55:
            b.add(" in " if rng.random() < 0.7 else ", "); b.add(rng.choice(BOROUGH_WORDS[borough]), "BOROUGH")

    boroughs = list(streets_by_b)
    for code, phrases in PROBLEMS.items():
        for _ in range(per_intent):
            borough = rng.choice(boroughs)
            b = _Builder()
            voice = rng.random() < 0.3
            if voice:
                b.add(rng.choice(FILLERS))
            b.add(rng.choice(OPENERS))
            pre = rng.choice(["", "there is ", "there's ", "we have ", "", "i see "])
            b.add(pre)
            b.add(rng.choice(phrases), "HAZARD" if code in ("RT002", "RT020", "RT022", "RT023", "RT060") else None)
            if rng.random() < 0.85:
                location(b, borough)
            if rng.random() < 0.35:
                b.add(" "); b.add(rng.choice(TIME_REFS), "TIME")
            if rng.random() < 0.3:
                b.add(rng.choice([" please", ", please send someone", ", it's dangerous", " thanks"]))
            prof = "NONE"
            if rng.random() < 0.12:
                t = profan.sample(1, random_state=int(rng.integers(1e9))).iloc[0]
                prof = t["severity"]
                b.add(rng.choice([f", this is {t['term']}", f" {t['term']}", f". you {t['term']} never fix anything"]))
            text = b.text.strip()
            text = text.lower() if rng.random() < 0.45 else text[0].upper() + text[1:]
            if rng.random() < 0.25:
                text = " ".join(_typo(w, rng) for w in text.split(" "))
                # typos can break span alignment, so the slot tags are recomputed only for untouched tokens
            tokens, tags = _bio(text, b.spans if text.lower() == b.text.strip().lower() else [])
            rows.append({"text": text, "intent": code, "emergency_category": "NONE", "profanity": prof,
                         "tokens": tokens, "bio_tags": tags, "has_slot_labels": bool(b.spans) and text.lower() == b.text.strip().lower(),
                         "borough": borough, "is_voice_style": voice})
    for cat, utts in EMERGENCY_UTTERANCES.items():
        for _ in range(per_intent // 2):
            u = rng.choice(utts)
            b = _Builder(); b.add(rng.choice(["", "help ", "please ", "hurry "])); b.add(u)
            if rng.random() < 0.6:
                location(b, rng.choice(boroughs))
            text = b.text.strip()
            text = text.lower() if rng.random() < 0.5 else text
            tokens, tags = _bio(text, b.spans)
            rows.append({"text": text, "intent": "EMERGENCY", "emergency_category": cat, "profanity": "NONE",
                         "tokens": tokens, "bio_tags": tags, "has_slot_labels": True, "borough": None, "is_voice_style": False})
    for label, pool in (("SMALLTALK", SMALLTALK), ("OTHER", OTHER)):
        for _ in range(per_intent // 2):
            text = rng.choice(pool)
            tokens, tags = _bio(text, [])
            rows.append({"text": text, "intent": label, "emergency_category": "NONE", "profanity": "NONE",
                         "tokens": tokens, "bio_tags": tags, "has_slot_labels": True, "borough": None, "is_voice_style": False})
    df = pd.DataFrame(rows).drop_duplicates("text").reset_index(drop=True)
    df["split"] = np.where(rng.random(len(df)) < 0.85, "train", "val")  # the real test set is the hand-written gold file
    df["is_synthetic"] = True
    return df


def main() -> None:
    real = pd.read_parquet(PROCESSED / "requests_real.parquet")
    df = generate(real)
    df.to_parquet(PROCESSED / "synthetic_nlp_utterances.parquet", index=False)
    print(f"synthetic_nlp_utterances: {len(df):,} rows, {df['intent'].nunique()} intents")
    print(df.sample(8, random_state=1)[["text", "intent"]].to_string())


if __name__ == "__main__":
    main()
