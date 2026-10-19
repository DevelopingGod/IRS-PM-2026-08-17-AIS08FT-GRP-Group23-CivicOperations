"""The plugin contract every team component is wired in through.

A plugin is a thin adapter around a component that its owner built. The adapter
never holds the owner's logic. It only reads shared fields from the request, calls
the owner's code, writes shared fields back, and falls back to the safe default the
team agreed in the component specification when the owner's code is missing, fails,
or returns the wrong shape.

Every call is measured: which outcome it had, how long it took, and what it wrote.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

LIVE, MISSING, ERROR, ABLATED, OFF = "LIVE", "MISSING", "ERROR", "ABLATED", "OFF"


@dataclass
class Call:
    """One measured call of one plugin on one request."""
    plugin: str
    outcome: str
    ms: float
    wrote: list[str]
    halt: bool = False
    note: str = ""
    why: list[str] = field(default_factory=list)   # the component's own reasons, shown on screen


@dataclass
class Plugin:
    name: str                                  # the component's name, never a number
    owner: str
    stage: str                                 # "turn" runs on every chat message, "full" once the request is ready,
                                               # "queue" over the whole ranked queue (Crew deployment)
    reads: list[str]
    writes: list[str]
    safe_default_text: str                     # the spec's wording, shown on screen
    safe_default: Callable[[dict], dict]
    run: Callable[[dict], dict] | None = None  # None means the owner's code is not available yet
    source: str = ""                           # where the owner's code came from, with its pinned version
    unit: str = ""                             # plugins that can only be switched off together share a unit

    @property
    def available(self) -> bool:
        return self.run is not None


@dataclass
class RunResult:
    request: dict
    calls: list[Call] = field(default_factory=list)
    halted_by: str | None = None


def call_plugin(p: Plugin, request: dict, ablate: bool = False, off: bool = False) -> tuple[dict, Call]:
    """Run one plugin with every rule of the contract applied."""
    t0 = time.perf_counter()
    if off:
        out, outcome, note = p.safe_default(request), OFF, "switched off by the tech team"
    elif ablate:
        out, outcome, note = p.safe_default(request), ABLATED, "switched to its safe default for measurement"
    elif not p.available:
        out, outcome, note = p.safe_default(request), MISSING, "no code from the owner yet"
    else:
        try:
            out = p.run(request)
            missing = [f for f in p.writes if f not in out]
            if missing:
                # A teammate's output changed shape. It must never look LIVE while wrong.
                out, outcome, note = p.safe_default(request), ERROR, f"missing fields {missing}"
            else:
                outcome, note = LIVE, ""
        except Exception as e:  # noqa: BLE001  any failure falls back, the pipeline never stops on an error
            out, outcome, note = p.safe_default(request), ERROR, f"{type(e).__name__}: {e}"[:300]
    ms = (time.perf_counter() - t0) * 1000
    halt = bool(out.pop("halt", False))
    why = [str(w) for w in (out.pop("_why", None) or [])]
    if outcome != LIVE:
        why = [f"Safe default: {p.safe_default_text}"] + ([note] if note else [])
    return out, Call(p.name, outcome, round(ms, 2), sorted(out.keys()), halt, note, why)


def run_pipeline(plugins: list[Plugin], request: dict, stage: str,
                 ablate: set[str] | None = None, off: set[str] | None = None) -> RunResult:
    """Run every plugin of a stage in order. A halt stops the run at once.
    `off` are components the tech team switched off; `ablate` are switched off for a measurement."""
    ablate, off = ablate or set(), off or set()
    result = RunResult(request=dict(request))
    for p in plugins:
        if p.stage != stage:
            continue
        unit = p.unit or p.name
        out, call = call_plugin(p, result.request, ablate=unit in ablate, off=unit in off)
        result.request.update(out)
        result.calls.append(call)
        if call.halt:
            result.halted_by = p.name
            break
    return result


def unit_names(plugins: list[Plugin]) -> list[str]:
    """The units that can be switched off for measurement, in pipeline order."""
    seen: list[str] = []
    for p in plugins:
        u = p.unit or p.name
        if p.available and p.stage in ("turn", "full") and u not in seen:   # queue stage plans run apart
            seen.append(u)
    return seen


def jsonable(value: Any) -> Any:
    """Turn dataclasses and other objects into plain JSON values."""
    import dataclasses
    import datetime as dt
    import enum
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()                      # numpy numbers
    if isinstance(value, float) and value != value or value in (float("inf"), float("-inf")):
        return None                               # JSON has no NaN or infinity: a missing value is null
    return value
