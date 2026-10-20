"""Address suggestions, "use my location", and map coordinates for a typed address.

The index is the 31,124 distinct addresses that appear, with coordinates, in our real NYC 311
sample (data/processed/requests_real.parquet). It is not a full city geocoder: an address
nobody has reported before is not in it, and the form then keeps the address as typed,
with no map pin. Coordinates found here are marked with where they came from.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

import numpy as np
import pandas as pd

from . import data

# How people write it -> how NYC 311 writes it.
WORDS = {"ave": "avenue", "av": "avenue", "st": "street", "rd": "road", "blvd": "boulevard",
         "pl": "place", "pkwy": "parkway", "dr": "drive", "ln": "lane", "ct": "court",
         "ter": "terrace", "hwy": "highway", "expy": "expressway", "sq": "square", "plz": "plaza",
         "e": "east", "w": "west", "n": "north", "s": "south", "bway": "broadway"}
SOURCE = "NYC 311 address index"


def normalise(text: str | None) -> str:
    """'450 Atlantic Ave.' and '450 ATLANTIC AVENUE' become the same string. Ordinals are
    dropped because 311 writes '79 STREET', not '79th Street'."""
    words = re.findall(r"\d+-\d+|\w+", (text or "").lower())
    out = []
    for w in words:
        w = re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", w)
        out.append(WORDS.get(w, w))
    return " ".join(out)


def _title(addr: str) -> str:
    return " ".join(w if w[0].isdigit() else w.capitalize() for w in addr.lower().split())


@lru_cache(maxsize=1)
def index() -> pd.DataFrame:
    try:
        r = pd.read_parquet(data.PROC / "requests_real.parquet",
                            columns=["incident_address", "borough", "incident_zip", "latitude", "longitude"])
    except Exception:
        return pd.DataFrame(columns=["address", "borough", "zip", "latitude", "longitude", "key"])
    r = r.dropna(subset=["incident_address", "latitude", "longitude", "borough"])
    r = r[r.borough.isin(["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"])]
    r = r.drop_duplicates(["incident_address", "borough"]).reset_index(drop=True)
    return pd.DataFrame({"address": r.incident_address.map(_title), "borough": r.borough,
                         "zip": r.incident_zip.astype(str).str[:5], "latitude": r.latitude.astype(float),
                         "longitude": r.longitude.astype(float), "key": r.incident_address.map(normalise)})


def _row(r) -> dict:
    return {"incident_address": r.address, "borough": r.borough, "zip": r.zip,
            "latitude": round(r.latitude, 6), "longitude": round(r.longitude, 6),
            "label": f"{r.address}, {r.borough.title()} {r.zip}".strip(),
            "location_precision": "ADDRESS_POINT", "location_source": SOURCE}


def search(q: str, borough: str | None = None, limit: int = 8) -> list[dict]:
    """Every typed word must start a word of the address. Exact starts rank first."""
    key = normalise(q)
    if len(key) < 2:
        return []
    idx = index()
    if borough:
        idx = idx[idx.borough == borough.upper()]
    words = key.split()
    hit = idx.key.str.contains(r"\b" + re.escape(words[0]), regex=True)
    for w in words[1:]:
        hit &= idx.key.str.contains(r"\b" + re.escape(w), regex=True)
    found = idx[hit].copy()
    if found.empty:
        return []
    found["rank"] = (~found.key.str.startswith(key)).astype(int)
    return [_row(r) for r in found.sort_values(["rank", "key"]).head(limit).itertuples()]


def lookup(address: str | None, borough: str | None) -> dict | None:
    """Coordinates for an address the intake read from a message, if it is in the index."""
    if not address or not borough:
        return None
    idx = index()
    hit = idx[(idx.key == normalise(address)) & (idx.borough == borough.upper())]
    return _row(next(hit.itertuples())) if len(hit) else None


def nearest(lat: float, lon: float, max_m: float = 150.0) -> dict | None:
    """The closest known address to a point, for "use my location"."""
    idx = index()
    if idx.empty or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    dy = (idx.latitude.to_numpy() - lat) * 111_320
    dx = (idx.longitude.to_numpy() - lon) * 111_320 * math.cos(math.radians(lat))
    d = np.hypot(dx, dy)
    i = int(d.argmin())
    if d[i] > max_m:
        return None
    out = _row(next(idx.iloc[[i]].itertuples()))
    out["distance_m"] = round(float(d[i]), 1)
    return out
