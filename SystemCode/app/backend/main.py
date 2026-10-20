"""The HTTP API behind the four portals.

Who may call what is decided here, on the server, never by the page:
  public    the landing page's city pulse, the problem catalog, address suggestions, sign up and sign in
  resident  report a problem, follow their own requests, the status conversation
  staff     the ranked queue, crew assignment, escalation approval, Admin questions, alerts
  crew      its own jobs only, and closing them
  tech      component health and switches, measurements, accounts
Run: py -m uvicorn app.backend.main:app --port 8000
Config comes only from environment variables, so it deploys unchanged.
"""
from __future__ import annotations

import contextlib
import json
import io
import os
import threading
from functools import lru_cache

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import accounts, addresses, backlog, data, db, service, switches
from .plugins.base import jsonable
from .plugins.registry import SWITCHABLE

app = FastAPI(title="Civic Operations")
app.add_middleware(CORSMiddleware,
                   allow_origins=os.environ.get("CIVICOPS_ORIGINS", "http://localhost:5173").split(","),
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Authorization"])


@app.on_event("startup")
def startup() -> None:
    db.init()
    accounts.init()
    switches.init()
    addresses.index()
    if os.environ.get("CIVICOPS_WARMUP", "1") == "1":
        # Load the language models now, so nobody waits for the first reply.
        with contextlib.redirect_stderr(io.StringIO()):
            from components import c01_nlp_bot
            c01_nlp_bot.parse("warm up the models")
        # Load the road network and make the first crew plan, so the first screen has one.
        threading.Thread(target=service.crew_plan, daemon=True).start()


# ------------------------------------------------------------------ who is calling
def _token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_user(authorization: str | None = Header(default=None)) -> dict:
    u = accounts.from_token(_token(authorization))
    if not u:
        raise HTTPException(401, "Please sign in.")
    return u


def role(*allowed: str):
    def check(u: dict = Depends(current_user)) -> dict:
        if u["role"] not in allowed:
            raise HTTPException(403, "This page is not part of your account.")
        return u
    return check


resident = role("resident")
staff = role("staff")
crew = role("crew")
tech = role("tech")


# ------------------------------------------------------------------ public
@app.get("/api/public/pulse")
def pulse():
    """Open problems as blurred dots: about 100 m precision, family and urgency only. No reference,
    words or reporter, so it is safe to show anyone."""
    out = []
    for q in service.queue(record=False)["items"]:
        s = q.get("slots") or {}
        if s.get("latitude") is not None and not q.get("on_hold"):
            out.append({"lat": round(s["latitude"], 3), "lon": round(s["longitude"], 3),
                        "family": data.catalog().get(q.get("request_type") or "", {}).get("complaint_family"),
                        "urgency": q.get("urgency_level")})
    return out


@app.get("/api/public/stats")
def public_stats():
    with db.conn() as c:
        counts = {r["status"]: r["n"] for r in c.execute("select status, count(*) n from requests group by status")}
        stops = c.execute("select count(*) n from emergency_events").fetchone()["n"]
    return {"open": sum(v for k, v in counts.items() if k != "CLOSED"), "closed": counts.get("CLOSED", 0),
            "emergency_stops": stops, "components": 12}


@app.get("/api/catalog")
def request_types():
    return [{"code": k, "label": v["citizen_label"], "family": v["complaint_family"], "agency": v["agency"],
             "urgency": v["default_urgency"], "dispatchable": v.get("dispatchable") == "true"}
            for k, v in data.catalog().items()]


@app.get("/api/addresses")
def address_search(q: str = Query(min_length=1, max_length=120), borough: str | None = None):
    return addresses.search(q, borough)


@app.get("/api/addresses/near")
def address_near(lat: float, lon: float):
    hit = addresses.nearest(lat, lon)
    if not hit:
        raise HTTPException(404, "No known address within 150 m of that point.")
    return hit


class RegisterIn(BaseModel):
    email: str = Field(max_length=200)


class LoginIn(BaseModel):
    user_id: str = Field(max_length=40)
    pin: str | None = Field(default=None, max_length=12)


@app.post("/api/auth/register")
def register(body: RegisterIn):
    try:
        return accounts.register_resident(body.email)
    except accounts.AuthError as e:
        raise HTTPException(400, str(e))


@app.post("/api/auth/login")
def login(body: LoginIn):
    try:
        return accounts.sign_in(body.user_id, body.pin)
    except accounts.AuthError as e:
        raise HTTPException(401, str(e))


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    accounts.sign_out(_token(authorization))
    return {"signed_out": True}


@app.get("/api/auth/me")
def me(u: dict = Depends(current_user)):
    out = {"user_id": u["id"], "role": u["role"], "name": u["name"], "crew_id": u["crew_id"]}
    if u["role"] == "resident":
        out["profile"] = accounts.resident_profile(u["id"])
    if u["role"] == "crew":
        out["crew"] = data.crews().get(u["crew_id"])
    return out


# ------------------------------------------------------------------ residents
class ChatIn(BaseModel):
    text: str = Field(max_length=2000)
    session_id: str | None = None
    borough_hint: str | None = None
    confirm: str | None = None


@app.post("/api/chat")
def chat(body: ChatIn, u: dict = Depends(resident)):
    if not body.text.strip():
        raise HTTPException(400, "Please type a message.")
    return jsonable(service.chat(body.text, body.session_id, u["id"], body.borough_hint, body.confirm))


class Place(BaseModel):
    incident_address: str | None = Field(default=None, max_length=200)
    borough: str | None = None
    cross_street: str | None = Field(default=None, max_length=200)
    latitude: float | None = None
    longitude: float | None = None
    zip: str | None = None
    location_precision: str | None = None
    location_source: str | None = None


class ReportIn(BaseModel):
    request_type: str | None = None
    description: str = Field(default="", max_length=2000)
    place: Place
    confirm: str | None = None


@app.post("/api/report")
def report(body: ReportIn, u: dict = Depends(resident)):
    return jsonable(service.report(body.model_dump(), u["id"]))


@app.get("/api/me/requests")
def my_requests(u: dict = Depends(resident)):
    return jsonable(service.my_requests(u["id"]))


@app.get("/api/me/requests/{rid}")
def my_request(rid: str, u: dict = Depends(resident)):
    q = db.load_json("requests", rid)
    if not q or q.get("citizen_key") != u["id"]:
        raise HTTPException(404, "None of your requests has that reference.")
    return jsonable({**service.tracker(rid), "journey": service.journey(rid)})


class TrackIn(BaseModel):
    text: str = Field(max_length=500)
    current: str | None = None


@app.post("/api/track/chat")
def track_chat(body: TrackIn, u: dict = Depends(resident)):
    return jsonable(service.track_chat(u["id"], body.text, body.current))


# ------------------------------------------------------------------ city staff
@app.get("/api/staff/queue")
def queue(u: dict = Depends(staff)):
    return jsonable(service.queue())


@app.get("/api/staff/requests/{rid}")
def detail(rid: str, u: dict = Depends(staff)):
    d = service.detail(rid)
    if not d:
        raise HTTPException(404, "No request with that reference.")
    d.pop("citizen_key", None)          # staff see a credibility score, never who the reporter is
    return jsonable(d)


@app.get("/api/staff/crews")
def crews(agency: str | None = None, borough: str | None = None, u: dict = Depends(staff)):
    out = list(data.crews().values())
    if agency:
        out = [c for c in out if c["agency"] == agency]
    if borough:
        out = [c for c in out if c["borough"] == borough.upper()]
    with db.conn() as c:
        load = {r["crew_id"]: r["n"] for r in c.execute(
            "select crew_id, count(*) n from crew_jobs where outcome is null group by crew_id")}
    return [{**c, "open_jobs": load.get(c["crew_id"], 0)} for c in out]


class AssignIn(BaseModel):
    crew_id: str


@app.post("/api/staff/requests/{rid}/assign")
def assign(rid: str, body: AssignIn, u: dict = Depends(staff)):
    try:
        return service.assign(rid, body.crew_id, u["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/staff/plan")
def crew_plan(wait: float = 0, u: dict = Depends(staff)):
    return jsonable(service.crew_plan(min(max(wait, 0), 15)))


class RouteIn(BaseModel):
    crew_id: str
    request_ids: list[str]


@app.post("/api/staff/plan/approve")
def approve_route(body: RouteIn, u: dict = Depends(staff)):
    try:
        return service.approve_route(body.crew_id, body.request_ids, u["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/map/streets")
def map_streets(south: float, west: float, north: float, east: float, u: dict = Depends(role("staff", "crew", "tech"))):
    """Real streets for a zoomed map window, from the NYC road network. Windows larger than a borough are refused."""
    if not (0 < north - south <= 0.35 and 0 < east - west <= 0.45):
        raise HTTPException(400, "Zoom in to a borough or smaller to see streets.")
    from . import maps
    return maps.streets(south, west, north, east)


@app.post("/api/staff/requests/{rid}/unassign")
def unassign(rid: str, u: dict = Depends(staff)):
    try:
        return jsonable(service.unassign(rid, u["id"]))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/staff/plan/approve-all")
def approve_all_routes(u: dict = Depends(staff)):
    try:
        return service.approve_all_routes(u["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/staff/requests/{rid}/confirm-impact")
def confirm_impact(rid: str, u: dict = Depends(staff)):
    q = service.confirm_impact(rid, u["id"])
    if not q:
        raise HTTPException(400, "No school or hospital was found near this request.")
    return {"request_id": rid, "sensitive_site_type": q["sensitive_site_type"], "sensitive_impact_verified": True}


@app.post("/api/staff/requests/{rid}/approve-escalation")
def approve_escalation(rid: str, u: dict = Depends(staff)):
    q = service.approve_escalation(rid, u["id"])
    if not q:
        raise HTTPException(400, "This request has no escalation to approve.")
    return {"request_id": rid, "escalation_level": q["escalation_level"], "escalation_approved": True}


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@app.post("/api/staff/requests/{rid}/note")
def staff_note(rid: str, body: NoteIn, u: dict = Depends(staff)):
    service.add_note(rid, u["id"], body.text)
    return {"ok": True}


class AskIn(BaseModel):
    question: str = Field(max_length=300)


@app.post("/api/staff/ask")
def ask(body: AskIn, u: dict = Depends(staff)):
    return service.ask(body.question)


@lru_cache(maxsize=1)
def _weather() -> pd.DataFrame:
    w = pd.read_parquet(data.PROC / "daily_demand_weather.parquet")
    return w.drop_duplicates(["borough", "day"])


@app.get("/api/staff/alerts")
def alerts(borough: str = "BROOKLYN", day: str | None = None, u: dict = Depends(staff)):
    """Predictive alerting, fed the recorded weather for that day."""
    if not switches.is_on("Predictive alerting"):
        return {"borough": borough.upper(), "day": day, "weather": {}, "switched_off": True,
                "result": {"alerts": [], "ok": False, "note": "Switched off by the tech team."}}
    from components import c11_alerting
    w = _weather()
    w = w[w.borough == borough.upper()].dropna(subset=["wx_precip_mm", "wx_tmax_c"])
    row = w[w.day.astype(str).str[:10] == day].head(1) if day else w.sort_values("day").tail(1)
    if row.empty:
        raise HTTPException(404, "No weather recorded for that borough and day.")
    r = row.iloc[0]
    weather = {k: float(r[k]) for k in ("wx_precip_mm", "wx_snow_cm", "wx_tmax_c", "wx_tmin_c", "wx_gust_kmh")}
    when = pd.Timestamp(r["day"]).date()
    with contextlib.redirect_stderr(io.StringIO()):
        res = c11_alerting.forecast(borough.upper(), when, weather)
    return jsonable({"borough": borough.upper(), "day": str(when), "weather": weather, "result": res})


@app.get("/api/staff/storm-days")
def storm_days(borough: str = "BROOKLYN", limit: int = 6, u: dict = Depends(staff)):
    w = _weather()
    w = w[w.borough == borough.upper()].sort_values("wx_precip_mm", ascending=False).head(limit)
    return [{"day": str(pd.Timestamp(d).date()), "rain_mm": float(p)} for d, p in zip(w.day, w.wx_precip_mm)]


# ------------------------------------------------------------------ crews
@app.get("/api/crew/jobs")
def crew_jobs(u: dict = Depends(crew)):
    return jsonable(service.crew_jobs(u["crew_id"]))


class CloseIn(BaseModel):
    outcome: str


@app.post("/api/crew/jobs/{rid}/close")
def close(rid: str, body: CloseIn, u: dict = Depends(crew)):
    try:
        return jsonable(service.close_job(rid, body.outcome, u["crew_id"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ------------------------------------------------------------------ tech team
@app.get("/api/tech/health")
def health(u: dict = Depends(tech)):
    return jsonable(service.health())


class SwitchIn(BaseModel):
    name: str
    enabled: bool


@app.put("/api/tech/components")
def set_switch(body: SwitchIn, u: dict = Depends(tech)):
    try:
        switches.set_switch(body.name, body.enabled, u["id"], set(SWITCHABLE))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": body.name, "enabled": body.enabled}


@app.post("/api/tech/measure")
def run_measure(u: dict = Depends(tech)):
    return service.measure()


@app.get("/api/tech/measure")
def get_measure(u: dict = Depends(tech)):
    return service.last_measure() or {}


@app.get("/api/tech/users")
def users(u: dict = Depends(tech)):
    return accounts.list_users()


class NewUserIn(BaseModel):
    role: str
    name: str = Field(max_length=60)
    crew_id: str | None = None


@app.post("/api/tech/users")
def create_user(body: NewUserIn, u: dict = Depends(tech)):
    try:
        return accounts.create_user(body.role, body.name, u["id"], body.crew_id)
    except accounts.AuthError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tech/users/{uid}/reset-pin")
def reset_pin(uid: str, u: dict = Depends(tech)):
    try:
        return accounts.reset_pin(uid, u["id"])
    except accounts.AuthError as e:
        raise HTTPException(400, str(e))


class ActiveIn(BaseModel):
    active: bool


@app.post("/api/tech/users/{uid}/active")
def set_active(uid: str, body: ActiveIn, u: dict = Depends(tech)):
    if uid == u["id"] and not body.active:
        raise HTTPException(400, "You cannot switch off your own account.")
    accounts.set_active(uid, body.active)
    return {"user_id": uid, "active": body.active}


@app.get("/api/tech/crews")
def all_crews(u: dict = Depends(tech)):
    return list(data.crews().values())


@app.get("/api/tech/system")
def system(u: dict = Depends(tech)):
    cards = data.REF / "experiment_cards.json"      # built by 06_Workspace_Tools/build_experiment_cards.py
    experiments = json.loads(cards.read_text(encoding="utf-8")) if cards.exists() else []
    return jsonable({"backlog": backlog.status(), "pins": service.pins(), "experiments": experiments})
