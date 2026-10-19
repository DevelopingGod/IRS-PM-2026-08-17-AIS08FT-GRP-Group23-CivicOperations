"""The plugin contract's rules, each pinned by a test."""
from datetime import datetime

import pytest

from app.backend.plugins.base import (ABLATED, ERROR, LIVE, MISSING, Plugin, call_plugin,
                                      run_pipeline, unit_names)


def make(name, run=None, writes=("x",), stage="full", unit=""):
    return Plugin(name, "tester", stage, [], list(writes), "the safe default",
                  lambda r: {w: "default" for w in writes}, run, unit=unit)


def test_live_output_is_used():
    out, call = call_plugin(make("A", run=lambda r: {"x": 1}), {})
    assert call.outcome == LIVE and out == {"x": 1}


def test_missing_code_falls_back_to_safe_default():
    out, call = call_plugin(make("A"), {})
    assert call.outcome == MISSING and out == {"x": "default"}


def test_an_error_never_stops_the_pipeline():
    def boom(r):
        raise RuntimeError("owner code broke")
    out, call = call_plugin(make("A", run=boom), {})
    assert call.outcome == ERROR and out == {"x": "default"} and "owner code broke" in call.note


def test_wrong_shape_is_an_error_not_live():
    # A teammate's output changed shape. It must never look LIVE while wrong.
    out, call = call_plugin(make("A", run=lambda r: {"y": 1}), {})
    assert call.outcome == ERROR and out == {"x": "default"} and "missing fields" in call.note


def test_halt_stops_the_run_and_later_plugins_never_run():
    ran = []
    first = make("Emergency", run=lambda r: {"x": 1, "halt": True})
    second = make("Later", run=lambda r: ran.append(1) or {"x": 2})
    res = run_pipeline([first, second], {}, "full")
    assert res.halted_by == "Emergency" and ran == [] and len(res.calls) == 1


def test_only_plugins_of_the_stage_run():
    turn = make("Intake", run=lambda r: {"x": 1}, stage="turn")
    full = make("Routing", run=lambda r: {"x": 2}, stage="full")
    res = run_pipeline([turn, full], {}, "turn")
    assert [c.plugin for c in res.calls] == ["Intake"]


def test_ablation_uses_the_safe_default_and_says_so():
    p = make("Urgency", run=lambda r: {"x": "real"}, unit="Urgency + Prioritisation")
    res = run_pipeline([p], {}, "full", ablate={"Urgency + Prioritisation"})
    assert res.calls[0].outcome == ABLATED and res.request["x"] == "default"


def test_units_skip_missing_plugins_and_group_pairs():
    plugins = [make("Intake", run=lambda r: {"x": 1}, unit="Pair"),
               make("Emergency", run=lambda r: {"x": 1}, unit="Pair"),
               make("Missing one")]
    assert unit_names(plugins) == ["Pair"]


def test_times_without_a_timezone_are_refused():
    from app.backend.service import _iso
    with pytest.raises(ValueError):
        _iso(datetime(2026, 10, 15, 9, 30))
