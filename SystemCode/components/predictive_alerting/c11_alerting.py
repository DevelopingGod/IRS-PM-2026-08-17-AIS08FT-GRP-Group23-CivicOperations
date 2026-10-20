"""C11 Advanced Predictive Alerting. Owner: Sankalp.

Warns the city that tomorrow will be busy, before the calls arrive, so crews can be moved
rather than scrambled.

Three arms were compared on a time-based split, and then again on storm days only, because
a model that is good on average and useless in a storm is no use at all:

    B0  weekday and season baseline, no weather          MAE 72.3   storm MAE 88.1
    B1  Poisson regression with weather                  MAE 83.1   storm MAE 97.8
    B2  gradient boosting with a Poisson loss            MAE 60.6   storm MAE 76.3   <- chosen

The Poisson model came out worse than the baseline, which is worth stating plainly rather
than hiding: one global linear model cannot span families that run from several hundred
noise reports a day down to single-figure health ones. Boosting handles that; the linear
model gets dragged apart by it.

Severity comes from lift over the weekday baseline, not from the raw count, because forty
noise reports is an ordinary Tuesday and forty drain reports is a bad night.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..common import ComponentResult, Reason

VERSION = "1.0.0"
COMPONENT = "C11 predictive alerting"
ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "models" / "c11_demand_model.joblib"

SEVERITY = [(2.2, "WARNING"), (1.8, "WATCH"), (1.3, "INFO")]


@dataclass
class Alert:
    borough: str
    complaint_family: str
    expected_count: float
    baseline_count: float
    lift: float
    severity: str
    drivers: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (f"{self.severity}: {self.borough.title()} {self.complaint_family.replace('_', ' ').lower()}, "
                f"{self.expected_count:.0f} expected against a normal {self.baseline_count:.0f} "
                f"(x{self.lift:.1f})")


@dataclass
class ForecastResult(ComponentResult):
    borough: str = ""
    for_date: str = ""
    alerts: list[Alert] = field(default_factory=list)


@lru_cache(maxsize=1)
def _bundle():
    try:
        import joblib
        return joblib.load(MODEL_PATH)
    except Exception:
        return None


def forecast(borough: str, when: date, weather: dict, families: list[str] | None = None) -> ForecastResult:
    """`weather` needs wx_precip_mm, wx_snow_cm, wx_tmax_c, wx_tmin_c and wx_gust_kmh.

    On failure this returns an EMPTY alert list with ok=False, and the dashboard shows that
    as failed. Silence must never be mistaken for calm."""
    t0 = time.perf_counter()
    b = _bundle()
    if b is None:
        return ForecastResult(component=COMPONENT, version=VERSION, ok=False,
                              latency_ms=(time.perf_counter() - t0) * 1000,
                              borough=borough, for_date=str(when),
                              reasons=[Reason("MODEL_UNAVAILABLE",
                                              "The demand model is not loaded, so no forecast was produced. "
                                              "This shows as failed rather than as a quiet day.")])

    model, baseline = b["model"], b["baseline"]
    fams = families or sorted({k[1] for k in baseline})
    dow, month = when.weekday(), when.month

    rows = [dict(borough=borough, complaint_family=f, dow=dow, month=month,
                 wx_precip_mm=weather.get("wx_precip_mm", 0.0), wx_snow_cm=weather.get("wx_snow_cm", 0.0),
                 wx_tmax_c=weather.get("wx_tmax_c", 15.0), wx_tmin_c=weather.get("wx_tmin_c", 8.0),
                 wx_gust_kmh=weather.get("wx_gust_kmh", 20.0)) for f in fams]
    X = pd.DataFrame(rows)[b["features_cat"] + b["features_num"]]
    expected = model.predict(X)

    drivers = []
    if weather.get("wx_precip_mm", 0) >= 10:
        drivers.append(f"{weather['wx_precip_mm']:.0f} mm of rain forecast")
    if weather.get("wx_snow_cm", 0) >= 2:
        drivers.append(f"{weather['wx_snow_cm']:.0f} cm of snow forecast")
    if weather.get("wx_gust_kmh", 0) >= 60:
        drivers.append(f"gusts to {weather['wx_gust_kmh']:.0f} km/h")
    if weather.get("wx_tmin_c", 10) <= 0:
        drivers.append("freezing overnight")

    alerts, reasons = [], []
    for f, exp in zip(fams, expected):
        base = baseline.get((borough, f, dow))
        if not base or base <= 0:
            continue
        lift = float(exp) / float(base)
        sev = next((name for cut, name in SEVERITY if lift >= cut), None)
        if sev:
            alerts.append(Alert(borough=borough, complaint_family=f, expected_count=round(float(exp), 1),
                                baseline_count=round(float(base), 1), lift=round(lift, 2),
                                severity=sev, drivers=drivers or ["weekday and seasonal pattern"]))
    alerts.sort(key=lambda a: -a.lift)

    if alerts:
        for a in alerts[:5]:
            reasons.append(Reason(f"ALERT_{a.severity}", str(a), weight=a.lift,
                                  evidence={"drivers": a.drivers}))
    else:
        reasons.append(Reason("NOTHING_UNUSUAL", "Nothing is far enough above normal to warn about"))

    return ForecastResult(component=COMPONENT, version=VERSION, ok=True,
                          latency_ms=(time.perf_counter() - t0) * 1000, reasons=reasons,
                          borough=borough, for_date=str(when), alerts=alerts)


run = forecast
