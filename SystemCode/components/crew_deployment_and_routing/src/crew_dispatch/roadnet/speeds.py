"""Speed parsing and imputation for the local copy of the OSM drive network.

Hierarchy (see research/assumptions.md, A-SPD-*):
  1. valid OSM ``maxspeed`` tag (single value)                      -> ``osm_maxspeed``
  2. valid OSM ``maxspeed`` list (merged ways after simplification)  -> ``osm_maxspeed_list_mean``
  3. edge inside NYC and not a motorway/trunk class                  -> ``nyc_default_25mph``
     (NYC default limit is 25 mph where no limit is posted, in force since 2014-11-07)
  4. otherwise: median of valid tagged speeds of the same highway class in this graph
                                                                     -> ``highway_class_median``
  5. class with too few tagged edges                                 -> ``fallback_25mph``

Posted/legal speed is NOT travel speed. Effective travel speed = posted * speed_factor, where
speed_factor is an explicit, uncalibrated research parameter evaluated by sensitivity analysis.
"""
from __future__ import annotations

import ast
import re
import statistics

MPH_TO_MPS = 0.44704
KMH_PER_MPH = 1.609344
MIN_VALID_MPH = 5.0     # tags such as "2 mph" or "4 mph" are treated as invalid for vehicle routing
MAX_VALID_MPH = 70.0
NYC_DEFAULT_MPH = 25.0
FALLBACK_MPH = 25.0
MIN_CLASS_SAMPLES = 20
# Classes where the NYC 25 mph default is not applied: limited-access roads normally carry a posted
# limit, and an untagged one is better imputed from tagged edges of the same class.
NON_DEFAULT_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link"}

SPEED_SOURCES = ("osm_maxspeed", "osm_maxspeed_list_mean", "nyc_default_25mph",
                 "highway_class_median", "fallback_25mph")

_NUM_UNIT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*$")


def highway_class(raw) -> str:
    """First class of a possibly list-valued OSM ``highway`` tag."""
    if raw is None:
        return "unknown"
    s = str(raw).strip()
    if s.startswith("["):
        try:
            vals = ast.literal_eval(s)
            return str(vals[0]) if vals else "unknown"
        except (ValueError, SyntaxError):
            return "unknown"
    return s


def parse_maxspeed(raw):
    """Return ``(mph or None, status)``.

    status in {"missing", "unparseable", "invalid", "single", "list"}. A bare number is km/h
    (OSM convention); values outside [MIN_VALID_MPH, MAX_VALID_MPH] are invalid.
    """
    if raw is None or (isinstance(raw, float) and raw != raw) or str(raw).strip() == "":
        return None, "missing"
    s = str(raw).strip()
    values = [s]
    if s.startswith("["):
        try:
            values = [str(v) for v in ast.literal_eval(s)]
        except (ValueError, SyntaxError):
            return None, "unparseable"
    mph = []
    for v in values:
        m = _NUM_UNIT.match(v.lower())
        if not m:
            continue
        num = float(m.group(1))
        mph.append(num if m.group(2) == "mph" else num / KMH_PER_MPH)
    if not mph:
        return None, "unparseable"
    valid = [x for x in mph if MIN_VALID_MPH <= x <= MAX_VALID_MPH]
    if not valid:
        return None, "invalid"
    return statistics.fmean(valid), ("list" if len(values) > 1 else "single")


def impute_speeds(highways, maxspeeds, inside_nyc):
    """Assign a posted-speed estimate (mph) and a provenance label to every edge.

    Parameters are equal-length sequences. Returns (mph list, source list, report dict).
    """
    n = len(highways)
    classes = [highway_class(h) for h in highways]
    parsed = [parse_maxspeed(m) for m in maxspeeds]
    by_class: dict[str, list[float]] = {}
    for c, (mph, _) in zip(classes, parsed):
        if mph is not None:
            by_class.setdefault(c, []).append(mph)
    class_median = {c: statistics.median(v) for c, v in by_class.items() if len(v) >= MIN_CLASS_SAMPLES}

    speeds, sources = [0.0] * n, [""] * n
    status_counts: dict[str, int] = {}
    for i in range(n):
        mph, status = parsed[i]
        status_counts[status] = status_counts.get(status, 0) + 1
        if mph is not None:
            speeds[i], sources[i] = mph, ("osm_maxspeed" if status == "single" else "osm_maxspeed_list_mean")
        elif inside_nyc[i] and classes[i] not in NON_DEFAULT_CLASSES:
            speeds[i], sources[i] = NYC_DEFAULT_MPH, "nyc_default_25mph"
        elif classes[i] in class_median:
            speeds[i], sources[i] = class_median[classes[i]], "highway_class_median"
        else:
            speeds[i], sources[i] = FALLBACK_MPH, "fallback_25mph"
    report = {
        "edges": n,
        "maxspeed_parse_status": status_counts,
        "speed_source_counts": {s: sources.count(s) for s in SPEED_SOURCES},
        "class_median_mph": {c: round(v, 2) for c, v in sorted(class_median.items())},
        "class_tagged_samples": {c: len(v) for c, v in sorted(by_class.items())},
        "rules": {"min_valid_mph": MIN_VALID_MPH, "max_valid_mph": MAX_VALID_MPH,
                  "nyc_default_mph": NYC_DEFAULT_MPH, "non_default_classes": sorted(NON_DEFAULT_CLASSES),
                  "min_class_samples": MIN_CLASS_SAMPLES, "fallback_mph": FALLBACK_MPH},
    }
    return speeds, sources, report
