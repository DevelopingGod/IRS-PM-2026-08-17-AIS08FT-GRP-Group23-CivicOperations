import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from crew_dispatch.engine import optimize_plan
from crew_dispatch.fixtures import c11_record, node_latlon, t
from crew_dispatch.outputs import build_plan_document, route_geojson
from crew_dispatch.schemas import AFTER_THE_FACT_FIELDS, Crew, SchemaError, Snapshot
from crew_dispatch.upstream.c11_provider import VendoredC11Provider, get_provider, validate_c11_output
from crew_dispatch.viz.maps import render_comparison, render_plan_map

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "crew_dispatch"


def test_crew_schema_validation():
    with pytest.raises(SchemaError):
        Crew.from_record({"crew_id": "X", "agency": "DOT", "skills": [], "depot_lat": 40.7, "depot_lon": -73.9})
    with pytest.raises(SchemaError):
        Crew.from_record({"crew_id": "X", "agency": "DOT", "skills": ["ROAD_REPAIR"], "depot_lat": None, "depot_lon": -73.9})
    c = Crew.from_record({"crew_id": "X", "agency": "DOT", "skills": "ROAD_REPAIR|SIDEWALK_REPAIR", "depot_lat": 40.7, "depot_lon": -73.9})
    assert c.skills == {"ROAD_REPAIR", "SIDEWALK_REPAIR"} and c.work_time_budget_min == 420


def test_c11_contract_validator():
    good = [c11_record("a", "DOT", "ROAD_REPAIR", 40.7, -73.9, 1), c11_record("b", "DOT", "ROAD_REPAIR", 40.7, -73.9, 2),
            c11_record("h", "DOT", "ROAD_REPAIR", 40.7, -73.9, None, status="hold")]
    assert validate_c11_output(good) == []
    bad = [good[2], good[0]]
    assert any("after held" in i for i in validate_c11_output(bad))
    gap = [good[0], dict(good[1], overall_queue_position=3)]
    assert validate_c11_output(gap)
    appr = [dict(good[0], dispatch_approved=True)]
    assert validate_c11_output(appr)


def test_vendored_c11_fallback_positions(catalog):
    from crew_dispatch.config import load_config
    prov, log = get_provider(load_config(), catalog)
    assert isinstance(prov, VendoredC11Provider) and log
    rows = [{"request_id": "x1", "agency": "DOT", "request_type_code": "RT001", "created_date": t(8).isoformat()},
            {"request_id": "x2", "agency": "DOT", "request_type_code": "RT002", "created_date": t(8).isoformat()},
            {"request_id": "x3", "agency": "DOT", "request_type_code": "RT001", "created_date": t(8).isoformat(),
             "c11_queue_status": "hold"}]
    out = prov.prioritize(rows, t(9))
    assert validate_c11_output(out) == []
    assert out[0]["request_id"] == "x2"           # HIGH (cave-in) before MEDIUM (pothole) per C11's own order
    assert out[-1]["overall_queue_position"] is None
    assert all(r["dispatch_approved"] is False for r in out)


def test_no_forbidden_field_read_in_decision_code():
    """Static check: the optimiser code never indexes urgency/band/score fields."""
    forbidden = {"priority_band", "urgency_level", "priority_score", "c11_urgency", "recommended_urgency"}
    for f in ["engine.py", "optimize/instance.py", "optimize/ga.py", "optimize/greedy.py", "optimize/weights.py",
              "dynamic/controller.py"]:
        tree = ast.parse((SRC / f).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in forbidden, f"{f} references {node.value}"


def test_cpu_only_imports():
    import sys
    for mod in ("cupy", "torch", "numba.cuda"):
        assert mod not in sys.modules, f"{mod} must not be required"
    text = " ".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    assert "import cupy" not in text and "import torch" not in text


def test_plan_document_and_maps(net, router, cfg, catalog, crews, tmp_path):
    q = [c11_record(f"R{i}", "DOT", "ROAD_REPAIR", *node_latlon(net, i % 8, (3 * i) % 8), i + 1) for i in range(8)]
    q.append(c11_record("HLD", "DOT", "ROAD_REPAIR", *node_latlon(net, 2, 2), None, status="hold"))
    snap = Snapshot(now=t(9), queue=q, crews=crews, crew_status={})
    p = optimize_plan(snap, router, cfg, catalog, seed=1)
    doc = build_plan_document(p, snap, version=1, run_id="x", event={"type": "TEST", "time": t(9)}, cfg=cfg, data_versions={},
                              c11_info={}, prev_doc=None, job_states={}, crews={c.crew_id: c for c in crews}, geometry_file="g.json")
    geo = route_geojson(p, router, 1)
    from crew_dispatch.outputs import dumps
    doc = json.loads(dumps(doc))
    assert {"request", "dispatch"} <= set(doc["requests"][0])
    assert doc["held"] == ["HLD"] and doc["changes_from_previous"]["newly_assigned"]
    assert all(f["geometry"]["type"] == "LineString" for f in geo["features"])
    png = render_plan_map(doc, geo, net, tmp_path / "m.png", agency="DOT")
    assert png.exists() and png.stat().st_size > 20_000
    cmp_ = render_comparison(doc, geo, doc, geo, net, tmp_path / "c.png", agency="DOT")
    assert cmp_.exists()


REAL_ROSTER = ROOT / "data" / "crews" / "crews_v2_cf1.0.parquet"
REAL_REPLAY = ROOT / "data" / "replay" / "requests_windows.parquet"


@pytest.mark.skipif(not REAL_ROSTER.exists(), reason="generated rosters not present")
def test_generated_rosters_use_catalog_vocabulary(catalog):
    from crew_dispatch.catalog import vocabulary
    voc = vocabulary(catalog)
    for p in (ROOT / "data" / "crews").glob("crews_v2_cf*.parquet"):
        df = pd.read_parquet(p)
        for ag, sk in zip(df["agency"], df["skills"]):
            assert set(sk) <= voc[ag], (p.name, ag, sk)


@pytest.mark.skipif(not REAL_REPLAY.exists(), reason="replay data not present")
def test_replay_data_has_no_after_the_fact_columns():
    cols = set(pd.read_parquet(REAL_REPLAY).columns)
    assert not cols & set(AFTER_THE_FACT_FIELDS)
