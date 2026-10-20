"""Command-line interface: in-process calls plus one real subprocess for ``python -m``."""

from __future__ import annotations

import io
import json
import subprocess
import sys

from civicops_agency.cli import main
from civicops_agency.simulator import make_co1

from conftest import CATALOG_PATH, ROOT

EXAMPLES = ROOT / "examples" / "inputs"


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_assign_single_file(capsys):
    code, out, _ = run(capsys, "assign", str(EXAMPLES / "valid_co1.json"), "--catalog", str(CATALOG_PATH))
    data = json.loads(out)
    assert code == 0 and data["status"] == "ASSIGNED" and data["agency"] == "DEP"


def test_assign_batch_file_returns_list(capsys):
    code, out, _ = run(capsys, "assign", str(EXAMPLES / "batch_co1.json"))
    data = json.loads(out)
    assert code == 0 and isinstance(data, list) and len(data) == 5


def test_assign_jsonl_with_a_bad_line(tmp_path, capsys):
    f = tmp_path / "in.jsonl"
    f.write_text(json.dumps(make_co1("RT001")) + "\n{broken\n" + json.dumps(make_co1("RT999")) + "\n",
                 encoding="utf-8")
    code, out, _ = run(capsys, "assign", str(f), "--compact")
    assert code == 0 and [d["status"] for d in json.loads(out)] == ["ASSIGNED", "INVALID", "UNRESOLVED"]


def test_assign_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(make_co1("RT100"))))
    code, out, _ = run(capsys, "assign", "-")
    assert code == 0 and json.loads(out)["agency"] == "DOHMH"


def test_assign_writes_output_file(tmp_path, capsys):
    target = tmp_path / "out.json"
    code, _, err = run(capsys, "assign", str(EXAMPLES / "batch_co1.json"), "-o", str(target))
    assert code == 0 and target.exists() and "Wrote 5 result(s)" in err
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 5


def test_fail_on_invalid_exit_code(capsys):
    code, _, _ = run(capsys, "assign", str(EXAMPLES / "batch_co1.json"), "--fail-on-invalid")
    assert code == 3


def test_unreadable_input_exit_code(tmp_path, capsys):
    code, _, err = run(capsys, "assign", str(tmp_path / "missing.json"))
    assert code == 2 and "cannot read input" in err


def test_policy_flags_change_outcome(capsys):
    _, out, _ = run(capsys, "assign", str(EXAMPLES / "referral_advisory_co1.json"), "--review-elevated-risk")
    assert json.loads(out)["status"] == "HELD"
    _, out, _ = run(capsys, "assign", str(EXAMPLES / "referral_advisory_co1.json"), "--no-knowledge", "--detail")
    assert json.loads(out)["detail"]["referral_advisory"] is None
    _, out, _ = run(capsys, "assign", str(EXAMPLES / "valid_co1.json"), "--min-confidence", "0.99")
    assert json.loads(out)["status"] == "HELD"


def test_catalog_check_ok_and_conflict(tmp_path, capsys):
    code, out, _ = run(capsys, "catalog-check")
    assert code == 0 and json.loads(out)["codes"] == 34
    bad = tmp_path / "request_catalog.csv"
    bad.write_text("request_type_code,agency,required_skill\nRT001,DOT,ROAD_REPAIR\nRT001,DEP,ROAD_REPAIR\n",
                   encoding="utf-8")
    code, out, _ = run(capsys, "catalog-check", "--catalog", str(bad))
    assert code == 1 and json.loads(out)["states"] == {"CONFLICT": 1}


def test_strict_catalog_refuses_to_start(tmp_path, capsys):
    bad = tmp_path / "request_catalog.csv"
    bad.write_text("request_type_code,agency,required_skill\nRT001,,ROAD_REPAIR\n", encoding="utf-8")
    code, _, err = run(capsys, "assign", str(EXAMPLES / "valid_co1.json"), "--catalog", str(bad), "--strict-catalog")
    assert code == 1 and "strict validation" in err


def test_detail_flag_adds_audit_block(capsys):
    _, out, _ = run(capsys, "assign", str(EXAMPLES / "valid_co1.json"))
    assert "detail" not in json.loads(out)
    _, out, _ = run(capsys, "assign", str(EXAMPLES / "valid_co1.json"), "--detail")
    assert "rules_applied" in json.loads(out)["detail"]


def test_rules_and_demo_commands(capsys):
    code, out, _ = run(capsys, "rules")
    assert code == 0 and "R14" in out
    code, out, _ = run(capsys, "demo")
    assert code == 0 and "valid_co1" in out and "UNRESOLVED" in out and "DUPLICATE_DETECTION" in out


def test_python_dash_m_entry_point():
    proc = subprocess.run([sys.executable, "-m", "civicops_agency", "assign", str(EXAMPLES / "unknown_code.json")],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "UNRESOLVED"
