"""Small shared pieces for the four components Sankalp owns.

Deliberately tiny. There is no shared contracts package any more: each owner builds their
component their own way, and these four agree on nothing more than a Reason and a
ComponentResult so their explanations can be shown together in the portal.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class Reason:
    """One human-readable explanation. Written at the moment the decision is made, with
    the weight it actually contributed, so removing a reason changes the outcome."""

    code: str
    message: str
    weight: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        w = "" if self.weight is None else f"  ({self.weight:+g})"
        return f"{self.message}{w}"


@dataclass
class ComponentResult:
    """What every component here returns alongside its own fields."""

    component: str
    version: str
    ok: bool = True
    latency_ms: float = 0.0
    reasons: list[Reason] = field(default_factory=list)
    produced_at: datetime = field(default_factory=datetime.now)

    def explain(self) -> list[str]:
        return [str(r) for r in self.reasons]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["produced_at"] = self.produced_at.isoformat(timespec="seconds")
        return d


BOROUGHS = ["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"]
