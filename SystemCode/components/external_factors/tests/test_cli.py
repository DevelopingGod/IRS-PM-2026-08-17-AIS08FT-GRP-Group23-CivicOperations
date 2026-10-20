"""CLI execution (28) and offline execution (29)."""

import json
import socket
import subprocess
import sys

import pytest

from conftest import ROOT, req


def run_cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "civicops_external_factors", *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, timeout=180)


def test_28_cli_evaluate_single(tmp_path, synth_dir, synth_config):
    inp = tmp_path / "req.json"
    inp.write_text(json.dumps(req(1000, 920, request_id="CLI-1")))
    p = run_cli("evaluate", "--config", synth_config, "--osm-data-dir", synth_dir, "--input", inp)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["request_id"] == "CLI-1" and out["external_factor"] == 1


def test_28b_cli_batch_jsonl_and_urgency_only(tmp_path, synth_dir, synth_config):
    inp = tmp_path / "reqs.jsonl"
    inp.write_text("\n".join(json.dumps(r) for r in [req(1000, 920, request_id="a"),
                                                       req(5000, 1000, request_id="b"),
                                                       req(1000, 920, request_id="c") | {"latitude": None}]))
    out = tmp_path / "out.jsonl"
    p = run_cli("batch", "--config", synth_config, "--osm-data-dir", synth_dir, "--input", inp,
                "--output", out, "--urgency-only")
    assert p.returncode == 0, p.stderr
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["external_factor"] for r in rows] == [1, 0, None]
    assert json.loads(p.stdout)["requests"] == 3


def test_28c_cli_threshold_override_and_errors(tmp_path, synth_dir, synth_config):
    inp = tmp_path / "req.json"
    inp.write_text(json.dumps(req(1000, 920)))          # 80 m
    p = run_cli("evaluate", "--config", synth_config, "--osm-data-dir", synth_dir, "--input", inp, "--threshold-m", 50)
    assert p.returncode == 0 and json.loads(p.stdout)["external_factor"] == 0
    p = run_cli("evaluate", "--config", synth_config, "--osm-data-dir", tmp_path / "nope", "--input", inp)
    assert p.returncode == 3 and "OSM cache error" in p.stderr
    p = run_cli("evaluate", "--config", synth_config, "--osm-data-dir", synth_dir, "--input", inp, "--threshold-m", 0)
    assert p.returncode == 2


def test_28d_cli_validate_cache_and_info(synth_dir, synth_config):
    p = run_cli("validate-cache", "--config", synth_config, "--osm-data-dir", synth_dir, "--no-checksums")
    assert json.loads(p.stdout)["evaluator_loads"] is True
    p = run_cli("info", "--config", synth_config, "--osm-data-dir", synth_dir)
    assert p.returncode == 0 and "CRITICAL_PROXIMITY_NONE" in p.stdout


def test_29_offline_execution(monkeypatch, synth_dir, synth_config):
    def no_network(*a, **k):
        raise OSError("network disabled for offline test")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    from civicops_external_factors import CriticalFacilityEvaluator
    ev = CriticalFacilityEvaluator.from_config(synth_config, osm_data_dir=synth_dir)
    assert ev.evaluate(req(1000, 920)).external_factor == 1


def test_29b_runtime_does_not_import_osmnx():
    code = ("import sys; import civicops_external_factors as c; "
            "c.CriticalFacilityEvaluator; print('osmnx' in sys.modules, 'requests' in sys.modules)")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert p.stdout.strip() == "False False", p.stdout + p.stderr


@pytest.mark.skipif(not (ROOT / "data" / "osm" / "facilities" / "critical_facilities.parquet").exists(),
                    reason="real OSM cache not present")
def test_29c_offline_real_cache_cli_with_examples():
    p = run_cli("evaluate", "--input", ROOT / "examples" / "near_facility.json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["external_factor"] == 1
