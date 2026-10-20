"""The starting backlog: real NYC 311 requests, run through every component once.

Run once by the tech team from the command line (py -m app.backend.manage import-backlog). It
takes, for each of the five boroughs, its most recent requests in our real 311 sample that have a
known type and a valid location, so the backlog covers the whole city evenly. The sample was
collected in separate hours per borough, so each borough is replayed on its own: its real order
and spacing are kept, and its newest request lands a few minutes before now. Every imported
request says so (source = "NYC 311 backlog replay", with its original 311 key and original time).

Each request goes through the full pipeline exactly as a resident's would, except the Intake bot,
because 311 already recorded the type and the address. Its reporter is the synthetic person
linked to it, whose other real 311 outcomes feed Credibility and escalation; the outcome of the
request itself is never used.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd

from . import data, db, service
from .plugins.base import LIVE, Call

SOURCE = "NYC 311 backlog replay"


def _meta(key: str, value=None):
    with db.conn() as c:
        if value is None:
            row = c.execute("select value from meta where key = ?", (key,)).fetchone()
            return json.loads(row["value"]) if row else None
        c.execute("insert or replace into meta values (?,?)", (key, json.dumps(value)))
        return value


def status() -> dict | None:
    return _meta("backlog_import")


def import_backlog(count: int = 150, force: bool = False) -> dict:
    if status() and not force:
        raise RuntimeError("The backlog was already imported. Use --force to import again.")
    cols = ["request_id", "unique_key", "created_at", "request_type_code", "complaint_type", "descriptor",
            "descriptor_2", "incident_address", "incident_zip", "borough", "latitude", "longitude",
            "open_data_channel_type", "geo_valid"]
    r = pd.read_parquet(data.PROC / "requests_real.parquet", columns=cols)
    cat = data.catalog()
    r = r[r.request_type_code.isin(cat) & r.geo_valid.astype(bool) & r.incident_address.notna()
          & r.borough.isin(["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"])]
    per = max(1, count // 5)
    r = r.sort_values("created_at").groupby("borough", group_keys=False).tail(per)
    end = data.now_new_york().replace(tzinfo=None) - timedelta(minutes=12)
    shifts = {b: end - pd.Timestamp(g.created_at.max()).to_pydatetime() for b, g in r.groupby("borough")}
    r = r.assign(replayed=[pd.Timestamp(t).to_pydatetime() + shifts[b] for t, b in zip(r.created_at, r.borough)])
    r = r.sort_values("replayed")
    links = pd.read_parquet(data.PROC / "synthetic_reporter_links.parquet").set_index("request_id").citizen_id
    earlier: list[dict] = []
    filed = []
    for row in r.itertuples():
        created = row.replayed.replace(tzinfo=service.NY)
        label = cat[row.request_type_code]["citizen_label"]
        text = " ".join(str(x) for x in (row.complaint_type, row.descriptor, row.descriptor_2) if isinstance(x, str) and x)
        req = {"text": text, "history": [], "request_type": row.request_type_code, "label": label, "confidence": 1.0,
               "ready_to_submit": True, "needs_human": False, "is_emergency": False, "emergency_action": "CONTINUE",
               "emergency_category": "NONE", "missing": [],
               "slots": {"incident_address": row.incident_address.title(), "borough": row.borough,
                         "latitude": float(row.latitude), "longitude": float(row.longitude),
                         "zip": str(row.incident_zip)[:5] if isinstance(row.incident_zip, str) else None,
                         "location_precision": "ADDRESS_POINT", "location_source": "NYC 311 record"},
               "citizen_key": links.get(row.request_id), "source": SOURCE, "source_request_id": row.request_id,
               "source_key": str(row.unique_key), "source_created_at": str(row.created_at),
               "channel": str(row.open_data_channel_type or "UNKNOWN")}
        note = Call("Intake bot + Emergency check", LIVE, 0.0, [], False, "",
                    [f"Imported from NYC 311 record {row.unique_key}: type, address and channel as 311 recorded them",
                     f"Originally filed {row.created_at:%d %b %Y %H:%M}; replayed with its real spacing in time"])
        rid = service.file_request(req, [note], created=created, earlier=earlier)
        q = db.load_json("requests", rid)
        earlier.append({**q, "request_id": rid, "status": "OPEN", "closed_at": None})
        filed.append(rid)
    info = {"imported": len(filed), "per_borough": per, "source": SOURCE,
            "from": str(r.created_at.min()), "to": str(r.created_at.max()),
            "shifted_by_hours": {b: round(v.total_seconds() / 3600, 1) for b, v in shifts.items()},
            "at": data.now_new_york().isoformat(timespec="seconds")}
    _meta("backlog_import", info)
    return info
