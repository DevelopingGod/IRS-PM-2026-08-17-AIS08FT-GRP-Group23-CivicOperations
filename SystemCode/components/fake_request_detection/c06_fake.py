"""C06 Fake & Unfounded Request Risk. Owner: Nabeel Taresh.

Plug and play. One call, sensible defaults (production strategy `hybrid_rules_logreg`):

    from components import fake_risk                 # or: from components import c06_fake
    r = fake_risk.run(facts, dup, cred, recent_reports_by_citizen_1h=0)
    r.unfounded_probability  # 0..1, or None on failure
    r.recommended_action     # FakeAction.PROCEED | LOWER_PRIORITY | HOLD_FOR_REVIEW
    r.risk_band              # LOW | MEDIUM | HIGH
    r.explain()

Inputs
    facts   RequestFacts (from components.c05_duplicate).
    dup     DuplicateResult from C05, or None.
    cred    any object with credibility_score and observation_count, for example Sankalp's
            components.c06_credibility.assess(...) result, or CredibilitySnapshot. None = first-time.
    recent_reports_by_citizen_1h   burst count; BURST_FLAG_AT or more sets the burst flag.
    strategy  optional. One of STRATEGIES. Default PRODUCTION_STRATEGY, or env C06_STRATEGY.

Output
    FakeRiskResult (contract v1.0.0): unfounded_probability, risk_band, recommended_action,
    reporter_burst_flag, contributing_factors.

Who reads it
    C10 urgency and C11 prioritisation read recommended_action and unfounded_probability to move a
    request down the queue. HOLD_FOR_REVIEW goes to an operator. C07 keeps the credibility history.
    C08 does not read C06.

Safety rules (tested for every strategy)
    there is no REJECT; a failed model never lowers priority; label columns such as
    resolution_description are never features; more corroboration never raises the risk;
    a first-time reporter without a burst keeps PROCEED below FIRST_TIME_LOWER_FLOOR.

Label: verified_outcome=UNFOUNDED is a crew-note proxy, not proven fraud. Evidence in
documents/C05_C06_changelog/RESULTS.md. Experiments live in tools/c05_c06_eval.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
import csv
import math
import os
import time

import numpy as np

from components.c05_duplicate import DuplicateResult, RequestFacts
from components.common import ComponentResult, Reason


# ============================================================================
# Types (contract v1.0.0)
# ============================================================================

class FakeAction(str, Enum):
    PROCEED = "PROCEED"
    LOWER_PRIORITY = "LOWER_PRIORITY"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"  # operator decides; never auto-reject


class RiskBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class CredibilitySnapshot:
    """The C07/C06-credibility fields C06 fake actually reads."""

    credibility_score: float = 50.0
    observation_count: int = 0
    credibility_band: str = "NEW"


@dataclass
class FakeRiskResult(ComponentResult):
    """C06 output. SAFE_DEFAULT: unfounded_probability=None and action=PROCEED."""

    unfounded_probability: Optional[float] = None
    risk_band: Optional[RiskBand] = None
    recommended_action: FakeAction = FakeAction.PROCEED
    reporter_burst_flag: bool = False
    contributing_factors: list[Reason] = field(default_factory=list)
    model_version: Optional[str] = None


# ============================================================================
# Config
# ============================================================================

PRODUCTION_STRATEGY = "hybrid_rules_logreg"


STRATEGIES = (
    "rules",
    "logreg",
    "gbm",
    "hybrid_rules_logreg",
    "logreg_no_burst",  # ablation
)


BURST_FLAG_AT = 5  # reports in the last hour; acceptance test uses 5


# Probability bands. HOLD_FOR_REVIEW is review, not a refusal.
BAND_HIGH = 0.55


BAND_MEDIUM = 0.28


# First-time reporters: do not HOLD on type base rate alone.
FIRST_TIME_HOLD_FLOOR = 0.70


# Cold-start rule (2026-10-15). A first-time reporter without a burst keeps
# PROCEED unless p >= FIRST_TIME_LOWER_FLOOR. Set on the val split: the lowest
# floor with first-time FPR <= 0.30. Test: first-time FPR 0.83 -> 0.33.
FIRST_TIME_LOWER_FLOOR = 0.60


URGENT_LEVELS = frozenset({"HIGH", "CRITICAL"})


# Columns that must never enter features (label leakage / future / eval-only).
LEAKAGE_COLUMNS = frozenset({
    "resolution_description",
    "outcome_class",
    "verified_outcome",
    "closed_date",
    "resolution_hours",
    "log_resolution_hours",
    "resolution_valid",
    "status",
    "hidden_honesty",
})


def band_and_action(probability: float, *, burst: bool, first_time: bool) -> tuple[RiskBand, FakeAction]:
    p = max(0.0, min(1.0, probability))
    if p >= BAND_HIGH:
        band, action = RiskBand.HIGH, FakeAction.HOLD_FOR_REVIEW
    elif p >= BAND_MEDIUM:
        band, action = RiskBand.MEDIUM, FakeAction.LOWER_PRIORITY
    else:
        band, action = RiskBand.LOW, FakeAction.PROCEED
    if first_time and not burst and action == FakeAction.HOLD_FOR_REVIEW and p < FIRST_TIME_HOLD_FLOOR:
        action = FakeAction.LOWER_PRIORITY
        band = RiskBand.MEDIUM
    if first_time and not burst and action == FakeAction.LOWER_PRIORITY and p < FIRST_TIME_LOWER_FLOOR:
        action = FakeAction.PROCEED
    return band, action


# ============================================================================
# Features
# ============================================================================

REPO = Path(__file__).resolve().parents[2]


REFERENCE = REPO / "data" / "reference"


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=None)
def catalog() -> dict[str, dict]:
    with open(REFERENCE / "request_catalog.csv", encoding="utf-8") as f:
        return {r["request_type_code"]: r for r in csv.DictReader(f)}


@lru_cache(maxsize=None)
def rows(name: str) -> list[dict]:
    with open(REFERENCE / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def unfounded_base_rates() -> dict[str, float]:
    """P(unfounded) ≈ 1 - P(crew confirmed), from data/reference/outcome_base_rates.csv."""
    out = {}
    for row in rows("outcome_base_rates.csv"):
        confirmed = _as_float(row.get("p_confirmed"), 0.5)
        out[row["request_type_code"]] = max(0.02, min(0.98, 1.0 - confirmed))
    return out


@lru_cache(maxsize=1)
def global_unfounded_rate() -> float:
    rates = list(unfounded_base_rates().values())
    return sum(rates) / len(rates) if rates else 0.45


def type_unfounded_rate(code: str) -> float:
    return unfounded_base_rates().get(code, global_unfounded_rate())


def is_urgent_type(code: str) -> bool:
    row = catalog().get(code) or {}
    return str(row.get("default_urgency", "")).upper() in {"HIGH", "CRITICAL"}


@dataclass(frozen=True)
class FeatureVector:
    names: tuple[str, ...]
    values: tuple[float, ...]
    notes: dict[str, float]

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.names, self.values))


FEATURE_NAMES: tuple[str, ...] = (
    "unfounded_base_rate",
    "hour_frac",
    "is_weekend",
    "unreliable_address",
    "log_corroboration",
    "extra_channels",
    "credibility_01",
    "log_observations",
    "burst_count_norm",
    "burst_flag",
    "is_urgent_type",
    "borough_manhattan",
    "borough_brooklyn",
    "borough_queens",
    "borough_bronx",
    "borough_staten",
)


def extract_features(
    facts: RequestFacts,
    dup: DuplicateResult | None,
    cred: Any | None,
    recent_reports_by_citizen_1h: int,
    *,
    include_burst: bool = True,
) -> FeatureVector:
    for col in LEAKAGE_COLUMNS:
        if hasattr(facts, col):
            raise AssertionError(f"C06 must not read leakage field {col}")

    corroboration = int(dup.corroboration_count) if dup is not None else 1
    channels = int(dup.distinct_channel_count) if dup is not None else 1
    cred_score = float(cred.credibility_score) if cred is not None else 50.0
    observations = int(cred.observation_count) if cred is not None else 0
    burst_n = max(0, int(recent_reports_by_citizen_1h))
    burst_flag = 1.0 if (include_burst and burst_n >= 5) else 0.0
    hour = facts.created_at.hour if facts.created_at is not None else 12
    weekend = 1.0 if facts.created_at is not None and facts.created_at.weekday() >= 5 else 0.0
    atype = (facts.address_type or "ADDRESS").upper()
    borough = (facts.borough.value if hasattr(facts.borough, "value") else str(facts.borough)).upper()

    notes = {
        "unfounded_base_rate": type_unfounded_rate(facts.request_type_code),
        "corroboration_count": float(corroboration),
        "distinct_channel_count": float(channels),
        "credibility_score": cred_score,
        "observation_count": float(observations),
        "recent_reports_by_citizen_1h": float(burst_n),
    }
    values = (
        notes["unfounded_base_rate"],
        hour / 23.0,
        weekend,
        1.0 if atype in {"INTERSECTION", "BLOCKFACE"} else 0.0,
        math.log1p(corroboration),
        float(max(0, channels - 1)),
        cred_score / 100.0,
        math.log1p(observations),
        (burst_n / 10.0) if include_burst else 0.0,
        burst_flag,
        1.0 if is_urgent_type(facts.request_type_code) else 0.0,
        1.0 if borough == "MANHATTAN" else 0.0,
        1.0 if borough == "BROOKLYN" else 0.0,
        1.0 if borough == "QUEENS" else 0.0,
        1.0 if borough == "BRONX" else 0.0,
        1.0 if borough == "STATEN ISLAND" else 0.0,
    )
    return FeatureVector(FEATURE_NAMES, values, notes)


def repo_root() -> Path:
    return REPO


def assert_no_leakage(columns: list[str]) -> None:
    bad = LEAKAGE_COLUMNS.intersection(columns)
    if bad:
        raise ValueError(f"C06 feature table contains leakage columns: {sorted(bad)}")


# ============================================================================
# Strategies
# ============================================================================

try:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:  # pragma: no cover
    HAS_SKLEARN = False


@dataclass
class ScoreBreakdown:
    probability: float
    factors: list[Reason]
    method: str


def _reason(code: str, message: str, weight: float | None = None, **evidence) -> Reason:
    return Reason(code=code, message=message[:280], weight=weight, evidence=evidence)


def apply_corroboration_adjustment(p: float, dup: DuplicateResult | None) -> tuple[float, list[Reason]]:
    """Monotone safety: more distinct reporters always lowers unfounded probability."""
    n = int(dup.corroboration_count) if dup is not None else 1
    ch = int(dup.distinct_channel_count) if dup is not None else 1
    before = p
    p = p / (1.0 + 0.35 * math.log(max(n, 1)))
    reasons = []
    if n >= 2:
        reasons.append(_reason(
            "CORROBORATED_BY_OTHERS",
            f"{n} distinct reporters described the same incident, so it is less likely unfounded",
            round(p - before, 3), corroboration_count=n,
        ))
    if ch >= 2:
        after = p * 0.90
        reasons.append(_reason(
            "CROSS_CHANNEL",
            f"Filed on {ch} channels, which is harder to fake in one burst",
            round(after - p, 3), distinct_channel_count=ch,
        ))
        p = after
    return max(0.0, min(1.0, p)), reasons


def rules_score(
    facts: RequestFacts,
    dup: DuplicateResult | None,
    cred: Any | None,
    recent_reports_by_citizen_1h: int,
    *,
    include_burst: bool = True,
) -> ScoreBreakdown:
    p = type_unfounded_rate(facts.request_type_code)
    factors = [_reason(
        "TYPE_BASE_RATE",
        f"About {p:.0%} of decided {facts.complaint_type} reports close as unfounded",
        round(p - 0.5, 3), request_type_code=facts.request_type_code,
    )]
    if cred is not None and cred.observation_count >= 3:
        shift = (50.0 - cred.credibility_score) / 250.0
        p = max(0.02, min(0.98, p + shift))
        factors.append(_reason(
            "REPORTER_HISTORY",
            f"Credibility {cred.credibility_score:.0f} after {cred.observation_count} decided reports",
            round(shift, 3), credibility_score=cred.credibility_score,
        ))
    if include_burst and recent_reports_by_citizen_1h >= BURST_FLAG_AT:
        bump = min(0.25, 0.05 * recent_reports_by_citizen_1h)
        p = min(0.98, p + bump)
        factors.append(_reason(
            "REPORTER_BURST",
            f"{recent_reports_by_citizen_1h} reports from this citizen in one hour",
            round(bump, 3), recent_reports_by_citizen_1h=recent_reports_by_citizen_1h,
        ))
    p, extra = apply_corroboration_adjustment(p, dup)
    return ScoreBreakdown(p, factors + extra, "rules")


def _load_training_matrix(include_burst: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Build a leakage-safe train matrix from the committed sample. Returns X, y, split-codes.

    Labels are verified_outcome on decided rows only. resolution_description is never a column.
    """
    import pandas as pd


    root = repo_root()
    processed = root / "data" / "processed" / "requests_real.parquet"
    sample = root / "data" / "sample" / "requests_real_sample.csv"
    links_parq = root / "data" / "processed" / "synthetic_reporter_links.parquet"
    links_csv = root / "data" / "sample" / "synthetic_reporter_links_sample.csv"
    if processed.exists():
        df = pd.read_parquet(processed)
    elif sample.exists():
        df = pd.read_csv(sample)
    else:
        return None
    links = links_parq if links_parq.exists() else links_csv
    if "verified_outcome" not in df.columns:
        return None
    decided = df[df["verified_outcome"].isin(["GENUINE", "UNFOUNDED"])].copy()
    if decided.empty:
        return None
    if links.exists():
        lk = pd.read_parquet(links) if links.suffix == ".parquet" else pd.read_csv(links)
        decided = decided.merge(lk, on="request_id", how="left")
    decided["created_at"] = pd.to_datetime(decided["created_at"])
    decided = decided.sort_values("created_at")
    # Burst count from earlier rows of the same synthetic citizen (shared population only).
    burst = np.zeros(len(decided), dtype=float)
    if "citizen_id" in decided.columns:
        times = decided["created_at"].to_numpy()
        cids = decided["citizen_id"].astype(str).to_numpy()
        for i in range(len(decided)):
            cid = cids[i]
            if cid in {"nan", "None"}:
                continue
            t = times[i]
            window_start = t - np.timedelta64(1, "h")
            burst[i] = int(np.sum((cids[:i] == cid) & (times[:i] >= window_start)))
    # Weak corroboration: other decided rows, same type, same window, already filed.
    nearby = np.ones(len(decided), dtype=float)
    if {"sample_window_id", "request_type_code"}.issubset(decided.columns):
        key = decided["sample_window_id"].astype(str) + "|" + decided["request_type_code"].astype(str)
        nearby = key.groupby(key).cumcount().to_numpy() + 1
    y = (decided["verified_outcome"].to_numpy() == "UNFOUNDED").astype(int)
    base = decided["request_type_code"].map(type_unfounded_rate).fillna(0.45).to_numpy()
    hour = decided["created_at"].dt.hour.to_numpy() / 23.0
    weekend = (decided["created_at"].dt.dayofweek.to_numpy() >= 5).astype(float)
    atype = decided.get("address_type", pd.Series(["ADDRESS"] * len(decided))).fillna("ADDRESS").astype(str).str.upper()
    unreliable = atype.isin(["INTERSECTION", "BLOCKFACE"]).astype(float).to_numpy()
    borough = decided["borough"].astype(str).str.upper()
    urgent = decided["request_type_code"].map(lambda c: 1.0 if is_urgent_type(c) else 0.0).to_numpy()
    X = np.column_stack([
        base,
        hour,
        weekend,
        unreliable,
        np.log1p(nearby.astype(float)),
        np.zeros(len(decided)),  # extra_channels unknown at train time
        np.full(len(decided), 0.50),  # credibility unknown; stay neutral
        np.zeros(len(decided)),
        (burst / 10.0) if include_burst else np.zeros(len(decided)),
        ((burst >= 5).astype(float) if include_burst else np.zeros(len(decided))),
        urgent,
        (borough == "MANHATTAN").astype(float),
        (borough == "BROOKLYN").astype(float),
        (borough == "QUEENS").astype(float),
        (borough == "BRONX").astype(float),
        (borough == "STATEN ISLAND").astype(float),
    ])
    split = decided["split"].astype(str).to_numpy() if "split" in decided.columns else np.array(["train"] * len(decided))
    return X, y, split


@dataclass
class _Fitted:
    scaler: object
    model: object
    method: str
    feature_names: tuple[str, ...]
    coef: np.ndarray | None


@lru_cache(maxsize=4)
def _fit(method: str, include_burst: bool) -> _Fitted | None:
    if not HAS_SKLEARN:
        return None
    loaded = _load_training_matrix(include_burst=include_burst)
    if loaded is None:
        return None
    X, y, split = loaded
    train = split == "train"
    val = split == "val"
    if train.sum() < 30 or len(np.unique(y[train])) < 2:
        train = np.ones(len(y), dtype=bool)
        val = train
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X[train])

    def _calibrate(est, X_raw, scaled: bool):
        if val.sum() < 20 or len(np.unique(y[val])) < 2:
            return est
        Xv = scaler.transform(X[val]) if scaled else X[val]
        try:
            cal = CalibratedClassifierCV(est, method="sigmoid", cv="prefit")
            cal.fit(Xv, y[val])
            return cal
        except Exception:
            return est

    if method == "logreg":
        base = LogisticRegression(max_iter=400, class_weight="balanced", solver="lbfgs")
        base.fit(Xs, y[train])
        coef = base.coef_[0]
        model = _calibrate(base, X, scaled=True)
    else:
        base = HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.08, max_iter=80, l2_regularization=0.1, random_state=2026,
        )
        base.fit(X[train], y[train])
        coef = None
        model = _calibrate(base, X, scaled=False)
        scaler = None
    return _Fitted(scaler, model, method, FEATURE_NAMES, coef)


def _predict_fitted(fitted: _Fitted, vector) -> float:
    x = np.asarray(vector.values, dtype=float).reshape(1, -1)
    if fitted.scaler is not None:
        x = fitted.scaler.transform(x)
    if hasattr(fitted.model, "predict_proba"):
        return float(fitted.model.predict_proba(x)[0, 1])
    return float(fitted.model.predict(x)[0])


def _coefficient_factors(fitted: _Fitted, vector) -> list[Reason]:
    if fitted.coef is None:
        notes = vector.notes
        return [_reason(
            "MODEL_SCORE",
            "Gradient boosting probability after calibration",
            evidence={"method": fitted.method},
        ), _reason(
            "TYPE_BASE_RATE",
            f"Type base rate {notes['unfounded_base_rate']:.2f}",
            round(notes["unfounded_base_rate"] - 0.5, 3),
        )]
    factors = []
    x = np.asarray(vector.values, dtype=float)
    # Approximate signed contribution in z-space: coef * z-score
    if fitted.scaler is not None:
        z = (x - fitted.scaler.mean_) / np.clip(fitted.scaler.scale_, 1e-6, None)
    else:
        z = x
    contrib = fitted.coef * z
    order = np.argsort(-np.abs(contrib))
    readable = {
        "unfounded_base_rate": "Type base rate (how often this problem is unfounded)",
        "log_corroboration": "Number of distinct reporters",
        "burst_flag": "Burst of reports in one hour",
        "burst_count_norm": "How many reports this citizen filed in one hour",
        "credibility_01": "Reporter credibility",
        "is_urgent_type": "High-urgency request type",
        "unreliable_address": "Intersection or blockface pin",
        "extra_channels": "Extra intake channels",
        "is_weekend": "Weekend filing",
        "hour_frac": "Hour of day",
        "log_observations": "How many past decided reports this citizen has",
    }
    for i in order[:6]:
        if abs(contrib[i]) < 0.02:
            continue
        name = FEATURE_NAMES[i]
        factors.append(_reason(
            name.upper(),
            readable.get(name, name.replace("_", " ")),
            round(float(contrib[i]), 3),
            feature=name, value=round(float(x[i]), 4),
        ))
    if not factors:
        factors.append(_reason("TYPE_BASE_RATE", "Type base rate used by the model",
                               round(float(vector.notes["unfounded_base_rate"] - 0.5), 3)))
    return factors


def model_score(
    method: str,
    facts: RequestFacts,
    dup: DuplicateResult | None,
    cred: Any | None,
    recent_reports_by_citizen_1h: int,
    *,
    include_burst: bool = True,
) -> ScoreBreakdown:
    vector = extract_features(facts, dup, cred, recent_reports_by_citizen_1h, include_burst=include_burst)
    fitted = _fit(method, include_burst)
    if fitted is None:
        return rules_score(facts, dup, cred, recent_reports_by_citizen_1h, include_burst=include_burst)
    p = _predict_fitted(fitted, vector)
    factors = _coefficient_factors(fitted, vector)
    if include_burst and recent_reports_by_citizen_1h >= BURST_FLAG_AT:
        factors.append(_reason(
            "REPORTER_BURST",
            f"{recent_reports_by_citizen_1h} reports from this citizen in one hour",
            0.1, recent_reports_by_citizen_1h=recent_reports_by_citizen_1h,
        ))
    p, extra = apply_corroboration_adjustment(p, dup)
    return ScoreBreakdown(max(0.0, min(1.0, p)), factors + extra, method)


def hybrid_rules_logreg(
    facts: RequestFacts,
    dup: DuplicateResult | None,
    cred: Any | None,
    recent_reports_by_citizen_1h: int,
) -> ScoreBreakdown:
    """Production: calibrated logistic regression, with the rule layer still written onto reasons."""
    model = model_score("logreg", facts, dup, cred, recent_reports_by_citizen_1h)
    rules = rules_score(facts, dup, cred, recent_reports_by_citizen_1h)
    # Average in probability space, then re-apply corroboration (already in both).
    p = 0.65 * model.probability + 0.35 * rules.probability
    seen = set()
    factors = []
    for r in model.factors + rules.factors:
        if r.code in seen:
            continue
        seen.add(r.code)
        factors.append(r)
    return ScoreBreakdown(max(0.0, min(1.0, p)), factors, "hybrid_rules_logreg")


def run_strategy(
    name: str,
    facts: RequestFacts,
    dup: DuplicateResult | None,
    cred: Any | None,
    recent_reports_by_citizen_1h: int,
) -> ScoreBreakdown:
    if name not in STRATEGIES:
        raise ValueError(f"Unknown C06 strategy {name!r}. Choose one of {STRATEGIES}")
    if name == "rules":
        return rules_score(facts, dup, cred, recent_reports_by_citizen_1h)
    if name == "logreg":
        return model_score("logreg", facts, dup, cred, recent_reports_by_citizen_1h)
    if name == "gbm":
        return model_score("gbm", facts, dup, cred, recent_reports_by_citizen_1h)
    if name == "logreg_no_burst":
        return model_score("logreg", facts, dup, cred, recent_reports_by_citizen_1h, include_burst=False)
    return hybrid_rules_logreg(facts, dup, cred, recent_reports_by_citizen_1h)


# ============================================================================
# Entry point
# ============================================================================

VERSION = "0.1.0"


COMPONENT = "C06 fake and unfounded risk"


def run(facts: RequestFacts, dup: DuplicateResult | None, cred: Any | None,
        recent_reports_by_citizen_1h: int = 0, strategy: str | None = None) -> FakeRiskResult:
    t0 = time.perf_counter()
    method = strategy or os.environ.get("C06_STRATEGY") or PRODUCTION_STRATEGY
    burst_n = max(0, int(recent_reports_by_citizen_1h or 0))
    burst_flag = burst_n >= BURST_FLAG_AT
    first_time = cred is None or cred.observation_count == 0

    breakdown = run_strategy(method, facts, dup, cred, burst_n)
    p = float(breakdown.probability)
    band, action = band_and_action(p, burst=burst_flag, first_time=first_time)
    assert action in (FakeAction.PROCEED, FakeAction.LOWER_PRIORITY, FakeAction.HOLD_FOR_REVIEW)

    factors = list(breakdown.factors)
    if not factors:
        factors = [Reason(code="TYPE_BASE_RATE", message="No extra signals; type base rate used")]

    return FakeRiskResult(
        component=COMPONENT, version=VERSION,
        latency_ms=(time.perf_counter() - t0) * 1000,
        model_version=method, ok=True,
        unfounded_probability=round(max(0.0, min(1.0, p)), 4),
        risk_band=band,
        recommended_action=action,
        reporter_burst_flag=burst_flag,
        contributing_factors=factors,
        reasons=list(factors),
    )
