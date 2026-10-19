"""Operating-hours policy (design option C, assumption A-TIME-1).

No per-crew shifts are modelled. Each agency has one operating window (config ``operating_hours``);
a 24-hour agency instead gives every crew a duty period of ``duty_minutes`` from its start.
Requests that arrive outside the window are queued and planned at the next opening, which is when
C11 is re-run so waiting time grows realistically.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def _hm(s: str) -> time | None:
    if s == "24:00":
        return None
    h, m = (int(x) for x in s.split(":"))
    return time(h, m)


def agency_hours(cfg: dict, agency: str) -> dict:
    oh = cfg["operating_hours"]
    spec = dict(oh["default"])
    spec.update(oh.get(agency, {}))
    return spec


def is_24h(cfg: dict, agency: str) -> bool:
    spec = agency_hours(cfg, agency)
    return spec["open"] == "00:00" and spec["close"] == "24:00"


def service_period(cfg: dict, agency: str, t: datetime) -> tuple:
    """(start, end) of the period in which work for a request known at time t is planned."""
    if t.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    tz = ZoneInfo(cfg["operating_hours"].get("timezone", "America/New_York"))
    local = t.astimezone(tz)
    spec = agency_hours(cfg, agency)
    if is_24h(cfg, agency):
        return local, local + timedelta(minutes=float(spec.get("duty_minutes", 480)))
    min_left = timedelta(minutes=float(cfg["operating_hours"].get("min_service_period_min", 120)))

    def window(day):
        o = datetime.combine(day, _hm(spec["open"]), tz)
        c_t = _hm(spec["close"])
        c = datetime.combine(day + timedelta(days=1), time(0, 0), tz) if c_t is None else datetime.combine(day, c_t, tz)
        return o, c

    o, c = window(local.date())
    if o <= local < c and c - local >= min_left:
        return local, c
    if local < o:
        return o, c
    return window(local.date() + timedelta(days=1))


def is_open(cfg: dict, agency: str, t: datetime) -> bool:
    if is_24h(cfg, agency):
        return True
    s, e = service_period(cfg, agency, t)
    return s <= t.astimezone(s.tzinfo) < e
