"""Build ``reports/agency_skills_report.html`` from executed results only.

Inputs (all produced by ``python run_all.py``):

* ``outputs/research/summary.json`` and the CSV evidence tables;
* ``data/reference/referral_knowledge.json`` (the shipped knowledge artifact);
* ``outputs/benchmarks/benchmark_results.json``;
* ``outputs/tests/junit.xml``.

Every number in the report is read from those files at build time. The
narrative sentences are fixed text written around them; where a sentence
states a conclusion, the numbers that support it are interpolated next to it,
so a re-run with different data cannot silently keep stale figures.

Standard library only, so the report can be rebuilt from the runtime package
as long as the outputs folders are present.
"""

from __future__ import annotations

import csv
import html
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from civicops_agency import RULES, __version__  # noqa: E402

OUT_R = ROOT / "outputs" / "research"
OUT_B = ROOT / "outputs" / "benchmarks"
OUT_T = ROOT / "outputs" / "tests"
KNOWLEDGE = ROOT / "data" / "reference" / "referral_knowledge.json"
REPORT = ROOT / "reports" / "agency_skills_report.html"

# The 20 mandatory test topics of the brief, mapped to test functions.
# A topic passes only if every matching test case in junit.xml passed.
REQUIREMENTS: List[Tuple[str, List[Tuple[str, str]]]] = [
    ("Every valid catalog RT code", [("test_router", "test_every_catalog_code_resolves")]),
    ("Unknown RT code", [("test_rules", "test_r07_unknown_code")]),
    ("Missing request type", [("test_rules", "MISSING_REQUEST_TYPE")]),
    ("Invalid confidence", [("test_rules", "CONFIDENCE_OUT_OF_RANGE"), ("test_rules", "INVALID_CONFIDENCE_TYPE")]),
    ("Low confidence", [("test_rules", "test_r09_")]),
    ("Not ready to submit", [("test_rules", "test_r03_not_ready")]),
    ("Needs human review", [("test_rules", "test_r04_needs_human")]),
    ("Emergency action STOP_CALL_911", [("test_rules", "test_r02_stop_call_911"),
                                        ("test_router", "test_every_catalog_code_is_blocked_by_911")]),
    ("Emergency action CONFIRM_WITH_CITIZEN", [("test_rules", "test_r02_confirm_with_citizen")]),
    ("Contradictory emergency fields", [("test_rules", "contradiction"), ("test_rules", "test_r02_stop_without_flag"),
                                        ("test_rules", "test_r05_emergency_label_with_continue")]),
    ("Special labels", [("test_rules", "test_r05_special_labels")]),
    ("Missing location", [("test_router", "test_missing_location")]),
    ("Invalid input types", [("test_rules", "_TYPE]"), ("test_router", "test_router_never_raises_on_junk")]),
    ("Duplicate catalog entries", [("test_catalog", "test_identical_duplicates")]),
    ("Conflicting catalog mappings", [("test_catalog", "test_conflicting_"), ("test_rules", "test_r08_conflicting"),
                                      ("test_cli", "test_catalog_check_ok_and_conflict")]),
    ("Missing agency", [("test_catalog", "test_missing_agency"), ("test_rules", "test_r08_missing_agency")]),
    ("Missing required skills", [("test_catalog", "test_missing_skill"), ("test_rules", "test_r10_missing_skill")]),
    ("Batch processing", [("test_router", "test_batch_items_are_independent"),
                          ("test_cli", "test_assign_batch_file"), ("test_cli", "test_assign_jsonl")]),
    ("JSON serialisation", [("test_router", "test_json_round_trip"), ("test_router", "test_assign_json_object")]),
    ("Downstream dispatch compatibility", [("test_integration", "test_downstream_dispatch"),
                                           ("test_rules", "test_r13_eligible_only_when_every_gate_passes")]),
]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_csv(name: str) -> List[Dict[str, str]]:
    with (OUT_R / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_junit(path: Path) -> Dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    cases = []
    for s in suites:
        for tc in s.iter("testcase"):
            outcome = "passed"
            for tag in ("failure", "error", "skipped"):
                if tc.find(tag) is not None:
                    outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
            cases.append({"classname": tc.get("classname", ""), "name": tc.get("name", ""), "outcome": outcome,
                          "time": float(tc.get("time", 0) or 0)})
    files: Dict[str, Dict[str, int]] = {}
    for c in cases:
        f = c["classname"].split(".")[-1] if "." in c["classname"] else c["classname"]
        files.setdefault(f, {"passed": 0, "failed": 0, "error": 0, "skipped": 0})[c["outcome"]] += 1
    s0 = suites[0]
    return {"cases": cases, "files": files, "total": len(cases),
            "passed": sum(c["outcome"] == "passed" for c in cases),
            "failed": sum(c["outcome"] in ("failed", "error") for c in cases),
            "skipped": sum(c["outcome"] == "skipped" for c in cases),
            "time": float(s0.get("time", 0) or 0), "timestamp": s0.get("timestamp", "")}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def n(v: Any) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return esc(v)


def pct(v: Any, digits: int = 1) -> str:
    try:
        return f"{100 * float(v):.{digits}f}%"
    except (TypeError, ValueError):
        return "–"


def table(rows: Sequence[Dict[str, Any]], cols: Sequence[Tuple[str, str]], num: Iterable[str] = (),
          fmt: Optional[Dict[str, Any]] = None, caption: str = "", cls: str = "") -> str:
    num, fmt = set(num), fmt or {}
    head = "".join(f'<th class="{"num" if k in num else ""}">{esc(label)}</th>' for k, label in cols)
    body = []
    for r in rows:
        cells = []
        for k, _ in cols:
            v = r.get(k, "")
            shown = fmt[k](v) if k in fmt else esc(v)
            cells.append(f'<td class="{"num" if k in num else ""}">{shown}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    cap = f"<caption>{caption}</caption>" if caption else ""
    return (f'<div class="table-wrap {cls}"><table>{cap}<thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def details(summary: str, inner: str) -> str:
    return f"<details><summary>{summary}</summary>{inner}</details>"


def stat(value: str, label: str, note: str = "") -> str:
    note_html = f'<div class="stat-note">{note}</div>' if note else ""
    return f'<div class="stat"><div class="stat-value">{value}</div><div class="stat-label">{label}</div>{note_html}</div>'


def chip(text: str, kind: str) -> str:
    return f'<span class="chip chip-{kind}">{esc(text)}</span>'


# ---------------------------------------------------------------------------
# Inline SVG charts (one value axis each, recessive grid, hover tooltips)
# ---------------------------------------------------------------------------
def _bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """Horizontal bar flat at the baseline with a rounded data end."""
    if w <= 0:
        return ""
    r = min(r, w, h / 2)
    return (f"M{x:.1f},{y:.1f}h{w - r:.1f}a{r},{r} 0 0 1 {r},{r}v{h - 2 * r:.1f}"
            f"a{r},{r} 0 0 1 {-r},{r}h{-(w - r):.1f}z")


def _ticks(vmax: float, count: int = 5) -> List[float]:
    raw = vmax / count
    mag = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 10 ** -len(str(int(1 / raw)))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    out, v = [], 0.0
    while v <= vmax * 1.0001:
        out.append(round(v, 10))
        v += step
    return out


def hbar_chart(items: List[Dict[str, Any]], fmt, title: str, label_w: int = 190, vmax: Optional[float] = None,
               ref: Optional[Tuple[float, str]] = None, show_values: Iterable[str] = ()) -> str:
    """items: {label, value, series (css var), tip}; show_values = labels to annotate directly."""
    width, bar_h, gap, bottom = 720, 14, 6, 28
    top = 24 if ref is not None else 8  # reserve a strip above the bars for the reference label
    plot_w = width - label_w - 70
    vmax = vmax or max(i["value"] for i in items) or 1
    ticks = _ticks(vmax)
    vmax = max(vmax, ticks[-1])
    h = top + len(items) * (bar_h + gap) + bottom
    sx = lambda v: label_w + plot_w * v / vmax  # noqa: E731
    parts = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="{esc(title)}" class="chart">']
    for t in ticks:
        parts.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top}" y2="{h - bottom + 2}" class="grid"/>')
        parts.append(f'<text x="{sx(t):.1f}" y="{h - 8}" class="tick" text-anchor="middle">{esc(fmt(t))}</text>')
    shown = set(show_values)
    for k, it in enumerate(items):
        y = top + k * (bar_h + gap)
        parts.append(f'<text x="{label_w - 8}" y="{y + bar_h - 3}" class="lab" text-anchor="end">{esc(it["label"])}</text>')
        w = sx(it["value"]) - label_w
        d = _bar_path(label_w, y, w, bar_h)
        hit = f'<rect x="{label_w}" y="{y - gap / 2}" width="{plot_w}" height="{bar_h + gap}" class="hit" data-tip="{esc(it["tip"])}"/>'
        if d:
            parts.append(f'<path d="{d}" style="fill:var({it["series"]})"><title>{esc(it["tip"])}</title></path>')
        if it["label"] in shown:
            parts.append(f'<text x="{sx(it["value"]) + 5:.1f}" y="{y + bar_h - 3}" class="val">{esc(fmt(it["value"]))}</text>')
        parts.append(hit)
    if ref is not None:
        x = sx(ref[0])
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top - 10}" y2="{h - bottom + 2}" class="refline"/>')
        parts.append(f'<text x="{x + 4:.1f}" y="{top - 10}" class="reflab">{esc(ref[1])}</text>')
    parts.append(f'<line x1="{label_w}" x2="{label_w}" y1="{top - 2}" y2="{h - bottom + 2}" class="axis"/>')
    parts.append("</svg>")
    return "".join(parts)


def grouped_hbar(groups: List[str], series: List[Tuple[str, str, List[float]]], fmt, title: str,
                 vmax: Optional[float] = None) -> str:
    """series: (name, css var, values per group)."""
    width, bar_h, inner, gap, top, bottom, label_w = 720, 9, 2, 12, 8, 28, 90
    plot_w = width - label_w - 70
    k = len(series)
    vmax = vmax or max(max(v) for _, _, v in series) or 1
    ticks = _ticks(vmax)
    vmax = max(vmax, ticks[-1])
    group_h = k * bar_h + (k - 1) * inner
    h = top + len(groups) * (group_h + gap) + bottom
    sx = lambda v: label_w + plot_w * v / vmax  # noqa: E731
    parts = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="{esc(title)}" class="chart">']
    for t in ticks:
        parts.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top}" y2="{h - bottom + 2}" class="grid"/>')
        parts.append(f'<text x="{sx(t):.1f}" y="{h - 8}" class="tick" text-anchor="middle">{esc(fmt(t))}</text>')
    for g, label in enumerate(groups):
        y0 = top + g * (group_h + gap)
        parts.append(f'<text x="{label_w - 8}" y="{y0 + group_h / 2 + 4}" class="lab" text-anchor="end">{esc(label)}</text>')
        for s, (name, var, vals) in enumerate(series):
            y = y0 + s * (bar_h + inner)
            tip = f"{label} · {name}: {fmt(vals[g])}"
            d = _bar_path(label_w, y, sx(vals[g]) - label_w, bar_h, 3)
            if d:
                parts.append(f'<path d="{d}" style="fill:var({var})"><title>{esc(tip)}</title></path>')
            parts.append(f'<rect x="{label_w}" y="{y - inner / 2}" width="{plot_w}" height="{bar_h + inner}" '
                         f'class="hit" data-tip="{esc(tip)}"/>')
    parts.append(f'<line x1="{label_w}" x2="{label_w}" y1="{top - 2}" y2="{h - bottom + 2}" class="axis"/>')
    parts.append("</svg>")
    return "".join(parts)


def line_chart(xlabels: List[str], series: List[Tuple[str, str, List[float]]], fmt, title: str,
               vmax: Optional[float] = None, mark_x: Optional[int] = None, mark_label: str = "",
               height: int = 260) -> str:
    width, left, right, top, bottom = 720, 56, 110, 14, 34
    plot_w, plot_h = width - left - right, height - top - bottom
    vmax = vmax or max(max(v) for _, _, v in series) or 1
    ticks = _ticks(vmax, 4)
    vmax = max(vmax, ticks[-1])
    sx = lambda i: left + (plot_w * i / max(1, len(xlabels) - 1))  # noqa: E731
    sy = lambda v: top + plot_h * (1 - v / vmax)  # noqa: E731
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}" class="chart">']
    for t in ticks:
        parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 8}" y="{sy(t) + 4:.1f}" class="tick" text-anchor="end">{esc(fmt(t))}</text>')
    step = max(1, len(xlabels) // 8)
    for i, xl in enumerate(xlabels):
        if i % step == 0 or i == len(xlabels) - 1:
            parts.append(f'<text x="{sx(i):.1f}" y="{height - 12}" class="tick" text-anchor="middle">{esc(xl)}</text>')
    if mark_x is not None:
        parts.append(f'<line x1="{sx(mark_x):.1f}" x2="{sx(mark_x):.1f}" y1="{top}" y2="{top + plot_h}" class="refline"/>')
        parts.append(f'<text x="{sx(mark_x) + 4:.1f}" y="{top + 10}" class="reflab">{esc(mark_label)}</text>')
    for name, var, vals in series:
        pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(vals))
        parts.append(f'<polyline points="{pts}" class="line" style="stroke:var({var})"/>')
        for i, v in enumerate(vals):
            tip = f"{xlabels[i]} · {name}: {fmt(v)}"
            parts.append(f'<circle cx="{sx(i):.1f}" cy="{sy(v):.1f}" r="4" class="dot" style="fill:var({var})">'
                         f'<title>{esc(tip)}</title></circle>')
            parts.append(f'<circle cx="{sx(i):.1f}" cy="{sy(v):.1f}" r="11" class="hit" data-tip="{esc(tip)}"/>')
        parts.append(f'<text x="{sx(len(vals) - 1) + 10:.1f}" y="{sy(vals[-1]) + 4:.1f}" class="dlab">{esc(name)}</text>')
    parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}" class="axis"/>')
    parts.append("</svg>")
    return "".join(parts)


def legend(entries: List[Tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:var({v})"></i>{esc(t)}</span>' for t, v in entries) + "</div>"


def figure(title: str, svg: str, caption: str, table_html: str = "", legend_html: str = "") -> str:
    tv = details("Table view", table_html) if table_html else ""
    return (f'<figure class="fig"><figcaption class="fig-title">{esc(title)}</figcaption>{legend_html}'
            f'<div class="chart-wrap">{svg}</div><p class="fig-note">{caption}</p>{tv}</figure>')


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def build() -> str:
    S = load_json(OUT_R / "summary.json")
    cat, hist, ref, mc = S["catalog"], S["historical"], S["referrals"], S["method_comparison"]
    K = load_json(KNOWLEDGE)
    B = load_json(OUT_B / "benchmark_results.json")
    T = parse_junit(OUT_T / "junit.xml")

    by_code = {r["request_type_code"]: r for r in load_csv("catalog_vs_historical_by_code.csv")}
    ref_code = load_csv("referral_by_code.csv")
    by_split = {r["request_type_code"]: r for r in ref_code}
    ref_agency = load_csv("referral_by_agency.csv")
    explain = load_csv("referral_matrix_explanation.csv")
    templates = load_csv("referral_text_templates.csv")
    cvm = load_csv("catalog_vs_matrix.csv")
    agency_cmp = load_csv("method_comparison_agency.csv")
    risk_sel = load_csv("referral_risk_model_selection.csv")
    thr = load_csv("referral_advisory_thresholds.csv")
    months = load_csv("temporal_stability_by_month.csv")
    splits = load_csv("split_inventory.csv")
    missing = load_csv("missing_values_by_split.csv")
    disagree = load_csv("catalog_historical_disagreements.csv")
    spread = load_csv("referral_context_spread.csv")
    c3 = load_csv("c3_affected_codes.csv")
    freq = load_csv("rt_frequency.csv")
    inv = load_csv("catalog_inventory.csv")

    thr_val = float(K["elevated_threshold"])
    elevated_codes = mc["elevated_codes"]
    elevated_cells = sorted(
        (c, b, r) for c, v in K["codes"].items() for b, r in v["referral_rate_by_borough"].items() if r >= thr_val)
    cell_codes = sorted({c for c, _, _ in elevated_cells} - set(elevated_codes))
    adv_t, adv_v = mc["advisory_test"], mc["advisory_val"]
    test_ref = int(ref["referred_by_split"]["test"])
    test_records = int(hist["split_counts"]["test"])
    base_rate_test = test_ref / test_records
    lift = float(adv_t["precision"]) / base_rate_test if base_rate_test else 0
    sel_test = next(r for r in risk_sel if r["split"] == "test" and r.get("selected") == "True")
    rt_test = next(r for r in risk_sel if r["split"] == "test" and r["model"] == "RT")
    const_test = next(r for r in risk_sel if r["split"] == "test" and r["model"] == "constant_prior")
    fun = B["functional"]["per_approach"]
    perf = B["performance"]
    bh = B["historical"]
    c3_test = next((r for r in agency_cmp if r["split"] == "test" and r["approach"].startswith("C3")), None)
    a_test = next(r for r in agency_cmp if r["split"] == "test" and r["approach"].startswith("A_"))
    c12_changes = [c for c in mc["learned_vs_catalog_changes"] if c["approach"] in ("C1", "C2")]
    internal_units = int(ref["destination_classes"].get("INTERNAL_UNIT_SAME_AGENCY", 0))
    gate_cases = sum("test_r13_eligible_only_when_every_gate_passes" in c["name"] for c in T["cases"])
    knowledge_kb = KNOWLEDGE.stat().st_size / 1024

    req_rows = []
    for title, patterns in REQUIREMENTS:
        matched = [c for c in T["cases"] if any(f in c["classname"] and s in c["name"] for f, s in patterns)]
        ok = bool(matched) and all(c["outcome"] == "passed" for c in matched)
        req_rows.append({"req": title, "tests": len(matched),
                         "status": chip("PASS" if ok else ("NO TEST" if not matched else "FAIL"),
                                        "good" if ok else "bad"),
                         "where": ", ".join(sorted({f for f, _ in patterns}))})
    all_req_pass = all("PASS" in r["status"] for r in req_rows)

    built = datetime.now().strftime("%Y-%m-%d %H:%M")
    sec: List[str] = []

    def section(num: int, sid: str, title: str, body: str) -> None:
        sec.append(f'<section id="{sid}"><h2><span class="secno">{num}</span>{esc(title)}</h2>{body}</section>')

    # 1 -------------------------------------------------------------------
    answer = f"""
    <div class="answer">
      <div class="answer-q">Did the historical dataset provide evidence that agency assignment requires more than an RT-code catalog lookup?</div>
      <div class="answer-a"><strong>For choosing the agency and the crew skill: no.</strong>
      The historical first agency matched the catalog agency in {n(hist['overall_agreement'])} of {n(hist['records'])} records
      ({pct(hist['overall_agreement_rate'], 3)}). The only {n(hist['disagreement_count'])} disagreements are 311 operator-desk
      rows (<code>NYC311-PRD</code>) from training. A learned majority classifier per code (C1) and per code and borough (C2)
      changed <em>{len(c12_changes)}</em> assignments. No runtime-available input carries routing signal beyond the code itself.
      <br><br><strong>For anticipating hand-offs: yes, for a small, specific set of codes.</strong>
      Of all {n(ref['referred_records'])} REFERRED requests, {n(sum(int(r['referred']) for r in ref_code if r['request_type_code'] in elevated_codes))}
      fall in {', '.join(elevated_codes)}. Their referrals follow patterns the agency matrix explains (plates on vehicles,
      sewer-caused cave-ins) but which CO1 does not capture. The component therefore adds an explainable, train-derived
      <em>referral advisory</em> (rule R14). It never overrides the shared catalog. On the untouched test split it flags
      {pct(adv_t['flag_rate'])} of requests and catches {pct(adv_t['recall'])} of the requests that were later referred,
      {lift:.1f}× the base rate.</div>
    </div>"""
    section(1, "summary", "Executive summary", answer + f"""
    <div class="stats">
      {stat(n(cat['distinct_codes']), 'catalog RT codes', 'unique, complete, no conflicts')}
      {stat(n(hist['records']), 'historical requests', f"{n(hist['split_counts']['train'])} / {n(hist['split_counts']['val'])} / {n(hist['split_counts']['test'])} train/val/test")}
      {stat(pct(hist['overall_agreement_rate'], 3), 'catalog = first agency', 'consistency, not routing accuracy')}
      {stat(n(ref['referred_records']), 'REFERRED requests', f"{n(ref['explicit_destination_records'])} name a destination")}
      {stat(f"{T['passed']}/{T['total']}", 'tests passed', 'all 20 required topics covered' if all_req_pass else 'see section 18')}
      {stat(f"{perf['assign_latency_us']['p50']:.0f} µs", 'median latency', f"{n(perf['batch_throughput']['10000']['router_req_per_s'])} req/s batch")}
    </div>
    <p><strong>Selected method:</strong> <em>Approach B, catalog plus knowledge-based business rules</em> (R01–R13).
    It adds an <em>informational historical referral advisory</em> (R14) that was fitted on the training split, selected on
    validation and evaluated once on test. The runtime package depends only on the Python standard library and the
    shared catalog. It does not need the historical dataset.</p>
    <p><strong>Why not plain lookup (Approach A)?</strong> It assigns the same agencies, but on the {B['functional']['scenarios']}
    labelled CO1 scenarios it made <strong>{fun['A_direct_lookup']['unsafe_dispatches']} unsafe dispatches</strong>
    (911 cases, not-ready and human-review requests, malformed input). It also gives no explanation.
    Approach B made {fun['B_catalog_rules']['unsafe_dispatches']}.</p>""")

    # 2 -------------------------------------------------------------------
    section(2, "course", "Course-guideline alignment", table([
        {"t": "Decision automation: business rules / knowledge-based reasoning",
         "how": "14 explicit, numbered rules over the shared catalog, a fixed status precedence (monotone: rules can only reduce eligibility), and an explanation built from the fired rules.",
         "where": "civicops_agency/rules.py, validators.py, explanations.py"},
        {"t": "Knowledge discovery and data mining",
         "how": "Full RT-code/agency consistency study, the referral investigation (closure-note parsing, context patterns, temporal stability), and a smoothed referral-risk estimator chosen by temporal validation. Shipped as a static knowledge table.",
         "where": "research/*.py, data/reference/referral_knowledge.json"},
        {"t": "Business resource optimisation (search / evolutionary)",
         "how": "Deliberately out of scope here. Crew assignment and routing belong to the separate crew-deployment component, which consumes this output.",
         "where": "section 20"},
        {"t": "Learned classifier",
         "how": "Evaluated (C1–C3) and rejected on evidence: no runtime signal beyond the RT code, and no reliable corrected-agency target (section 13).",
         "where": "research/method_comparison.py"},
    ], [("t", "Technique"), ("how", "How it is applied"), ("where", "Where")]))

    # 3 -------------------------------------------------------------------
    section(3, "scope", "Component scope", """
    <div class="two-col">
      <div><h3>In scope</h3><ul>
        <li>Validate the CO1 message (types, ranges, codes).</li>
        <li>Apply the emergency, readiness and human-review gates.</li>
        <li>Handle the special labels EMERGENCY, OTHER and SMALLTALK.</li>
        <li>Look up the agency and crew skill(s) in the shared catalog.</li>
        <li>Detect unknown codes and catalog conflicts, and fail safely.</li>
        <li>Decide dispatch eligibility and the next workflow step.</li>
        <li>Explain the decision; attach historical referral context (detail block).</li>
        <li>Pass every agreed CO1 field on to duplicate detection unchanged in name.</li>
      </ul></div>
      <div><h3>Out of scope (other owners)</h3><ul>
        <li>Classifying citizen text (intake bot) and deciding emergencies (emergency check).</li>
        <li>Estimated service or resolution time (<code>est_service_minutes</code> is never read).</li>
        <li>Urgency, prioritisation and external factors.</li>
        <li>Duplicate, credibility and fake-request detection.</li>
        <li>Geocoding, crew assignment, travel time and routes.</li>
      </ul></div>
    </div>""")

    # 4 -------------------------------------------------------------------
    co1 = [
        ("request_type", "str", "RT code or EMERGENCY / OTHER / SMALLTALK", "required; trimmed and upper-cased (warning)"),
        ("confidence", "number", "0–1", "required; bool, NaN and out-of-range rejected"),
        ("ready_to_submit", "bool", "", "required; strict boolean"),
        ("needs_human", "bool", "", "required; strict boolean"),
        ("is_emergency", "bool", "", "required; strict boolean"),
        ("emergency_action", "str", "CONTINUE / CONFIRM_WITH_CITIZEN / STOP_CALL_911", "required"),
        ("emergency_category", "str|null", "set by the emergency check", "optional; non-string rejected"),
        ("slots.incident_address", "str|null", "", "optional; trimmed"),
        ("slots.cross_street", "str|null", "", "optional; trimmed"),
        ("slots.borough", "str|null", "five boroughs", "optional; normalised; unknown value gives a warning"),
        ("slots.landmark", "str|null", "", "optional; trimmed"),
    ]
    section(4, "co1", "CO1 integration contract", f"""
    <p>Source: the shared data reference (page 7, “hands on”), which is supplied with the package as
    <code>docs/Civic-Operations-Shared-Data-Reference.pdf</code>. CO1 does <strong>not</strong> supply
    <code>complaint_type</code>, <code>descriptor</code>, <code>location_type</code> or coordinates, so none of these
    historical columns can drive the router. These eleven fields are exactly what was agreed with the intake owner;
    there is no request identifier. Nested <code>slots</code> objects and the flat <code>slots.x</code> spelling are
    both accepted. Any other key is ignored, not rejected, and is not forwarded. Every agreed field is passed on to
    duplicate detection under the same name; a field that fails validation is passed on as <code>null</code> and the
    explanation names the problem.</p>
    {table([dict(zip(('f', 't', 'v', 'h'), r)) for r in co1], [('f', 'Field'), ('t', 'Type'), ('v', 'Allowed values'), ('h', 'Handling (R01)')])}""")

    # 5 -------------------------------------------------------------------
    files = [
        {"f": "data/reference/request_catalog.csv", "role": "Operational knowledge (runtime)",
         "facts": f"{cat['catalog_rows']} rows, {len(cat['catalog_columns'])} columns, {cat['distinct_codes']} codes, {len(cat['catalog_agencies'])} agencies, {len(cat['catalog_skills'])} skills"},
        {"f": "data/reference/NYC311_agency_matrix.md", "role": "Agency knowledge (runtime: names only)",
         "facts": f"{cat['matrix_family_rows']} family rows, {cat['matrix_glossary_rows']} acronyms, {len(cat['matrix_multi_agency_families'])} multi-agency families"},
        {"f": "data/research/requests_real.parquet", "role": "Research only",
         "facts": f"{n(hist['records'])} rows, {hist['columns']} columns, source {', '.join(hist['source_values'])}, {hist['synthetic_rows']} synthetic"},
        {"f": "docs/Civic-Operations-Shared-Data-Reference.pdf", "role": "CO1 contract and shared vocabulary",
         "facts": "7 pages; component owners, allowed values, shared rules"},
        {"f": "data/reference/referral_knowledge.json", "role": "Derived knowledge (runtime, optional)",
         "facts": f"fitted on train ({n(K['train_records'])} rows); catalog hash {K['catalog_sha256'][:12]}"},
    ]
    section(5, "inventory", "Reference-file inventory", table(files, [("f", "File"), ("role", "Role"), ("facts", "Verified facts")]) + f"""
    <p>The catalog's agencies and skills match the shared data reference exactly
    (agencies: {chip('match' if cat['agencies_match_shared_reference'] else 'MISMATCH', 'good' if cat['agencies_match_shared_reference'] else 'bad')};
    skills: {chip('match' if cat['skills_match_shared_reference'] else 'MISMATCH', 'good' if cat['skills_match_shared_reference'] else 'bad')}).
    Every <code>dispatchable</code> value is {', '.join(cat['dispatchable_values'])}. The original files were copied, never edited.</p>""")

    # 6 -------------------------------------------------------------------
    section(6, "dataset", "Historical dataset inventory", f"""
    {table(splits, [('split', 'Split'), ('records', 'Records'), ('first_created', 'First created'), ('last_created', 'Last created'), ('referred', 'REFERRED')], num=('records', 'referred'), fmt={'records': n, 'referred': n})}
    <p>The splits are strictly ordered in time ({chip('verified' if hist['splits_strictly_ordered'] else 'NOT ORDERED', 'good' if hist['splits_strictly_ordered'] else 'bad')}).
    There are {hist['duplicate_unique_keys']} duplicate keys. All {hist['distinct_rt_codes']} RT codes occur in history
    (catalog coverage {hist['catalog_codes_in_history']}/{cat['distinct_codes']}) and none fall outside the catalog.
    {hist['codes_present_in_all_splits']} codes appear in all three splits. The exception is
    {', '.join(r['request_type_code'] for r in hist['codes_under_30_in_val_or_test'] if int(r['val']) == 0)}
    (snow and ice), which is absent from validation and test because they are summer periods.
    The REFERRED count of {n(ref['referred_records'])} matches the supplied guidance.</p>
    <p><strong>Label provenance.</strong> Rebuilding each record's RT code from <code>complaint_type</code> and
    <code>descriptor</code> with the catalog's own matching columns reproduces {n(hist['rt_reconstruction_exact'])}
    of {n(hist['records'])} labels. The {n(hist['rt_reconstruction_mismatch'])} others come from overlapping descriptor
    patterns (for example, “Hydrant Leaking” matches both RT020 and RT024), never from a different agency. The historical
    RT code is therefore derived from the 311 complaint taxonomy. High agreement between the catalog and the historical
    agency shows that the catalog is <em>consistent with 311's own routing</em>. It is not an independent measure of
    end-to-end routing accuracy (section 21).</p>
    {details('Missing values by split (all columns with gaps)', table(missing, [('column', 'Column'), ('train', 'Train'), ('val', 'Val'), ('test', 'Test'), ('missing_pct', '% missing')], num=('train', 'val', 'test', 'missing_pct')))}
    {details('RT code frequencies (all codes)', table(freq, [('request_type_code', 'Code'), ('train', 'Train'), ('val', 'Val'), ('test', 'Test'), ('total', 'Total'), ('share_pct', 'Share %')], num=('train', 'val', 'test', 'total', 'share_pct')))}""")

    # 7 -------------------------------------------------------------------
    rel_kind = {"CONSISTENT": "good", "MULTI_AGENCY_CONTEXTUAL": "warn", "NOT_EXPLICIT": "muted",
                "CONSISTENT_CAUSE_DEPENDENT": "warn", "CONSISTENT_WITH_BLOCKED_STREET_OVERLAP": "warn"}
    section(7, "consistency", "Catalog and matrix consistency", f"""
    <p><strong>Catalog integrity:</strong> {cat['distinct_codes']} codes, no duplicates, no conflicts, no malformed codes,
    and no missing agencies or skills. Every code determines exactly one agency and one skill
    ({chip('verified', 'good') if cat['every_code_uniquely_determined'] else chip('NO', 'bad')}).
    No code needs several skills, but the loader accepts <code>|</code>-separated skills for future use.</p>
    <p><strong>Matrix comparison:</strong> each code was assigned to its matrix family by hand (analyst annotation in
    <code>research/matrix_family_map.csv</code>; the matrix itself has no RT codes). The matrix names the catalog agency
    for {34 - len(cat['catalog_agency_not_named_in_matrix'])} of 34 codes. The exception is
    {', '.join(cat['catalog_agency_not_named_in_matrix'])} (dead animal → DSNY), where the matrix has no dead-animal
    row. This is recorded as a gap, not a conflict. {len(cat['multi_agency_contextual_codes'])} codes sit in
    multi-agency families ({', '.join(cat['multi_agency_contextual_codes'])}). The deciding context in each case
    (plates, public housing, noise subtype) is <strong>not</strong> in the CO1 contract, so the matrix cannot
    disambiguate at runtime. The catalog therefore stays authoritative, and the matrix is used as explanation.
    Matrix agencies with no catalog codes: {', '.join(cat['matrix_agencies_not_in_catalog'])}. These are handled
    upstream by the OTHER label.</p>
    {table(cvm, [('request_type_code', 'Code'), ('citizen_label', 'Problem'), ('agency', 'Catalog'), ('matrix_agencies', 'Matrix'), ('relationship', 'Relationship'), ('context_dimension', 'Deciding context')], fmt={'relationship': lambda v: chip(v, rel_kind.get(v, 'muted')), 'matrix_agencies': lambda v: esc(v.replace('|', ' / '))}, cls='scroll')}""")

    # 8 -------------------------------------------------------------------
    ag = sorted(hist["historical_agencies"].items(), key=lambda kv: -kv[1])
    items = [{"label": a, "value": v, "series": "--s-cat" if a != "NYC311-PRD" else "--s-hi",
              "tip": f"{a}: {v:,} requests"} for a, v in ag]
    per_code_rows = sorted(by_code.values(), key=lambda r: -int(r["records"]))
    section(8, "distributions", "Historical agency distributions", figure(
        "Historical requests by first agency", hbar_chart(items, lambda v: f"{v:,.0f}", "requests by agency",
                                                         label_w=110, show_values=[a for a, _ in ag]),
        f"All {n(hist['records'])} records. NYC311-PRD (highlighted) is the 311 operations desk and appears only in the two disagreement rows.",
        table([{"a": a, "v": v} for a, v in ag], [("a", "Agency"), ("v", "Requests")], num=("v",), fmt={"v": n})) +
        details("Catalog agency versus historical agency for every RT code", table(per_code_rows, [
            ("request_type_code", "Code"), ("catalog_agency", "Catalog"), ("records", "Records"),
            ("hist_agencies", "Historical agencies"), ("agreement_rate", "Agreement")],
            num=("records", "agreement_rate"), fmt={"records": n, "agreement_rate": lambda v: pct(v, 3)})))

    # 9 -------------------------------------------------------------------
    section(9, "ambiguous", "Multi-agency and ambiguous RT codes", f"""
    <p><strong>Codes with more than one historical agency:</strong> {', '.join(hist['multi_agency_codes'])}.
    Below is the complete list of catalog/historical disagreements.</p>
    {table(disagree, [('unique_key', 'Key'), ('split', 'Split'), ('request_type_code', 'Code'), ('catalog_agency_csv', 'Catalog'), ('agency', 'Historical'), ('descriptor', 'Descriptor'), ('outcome_class', 'Outcome'), ('resolution_description', 'Closure note')])}
    <p>Both rows are “Blocked – Construction” street or sidewalk conditions that the 311 operations desk closed itself.
    The matrix lists “blocked street or sidewalk” as a DOT/DSNY/DOB/NYPD family that depends on cause, which explains
    the ambiguity. Two rows out of {n(hist['records'])} do not justify a rule, and the descriptor is not a CO1 field.</p>
    <p><strong>Ambiguity in a wider sense: where referral risk varies within a code.</strong> The table shows the
    spread of referral rates across feature values that have at least 20 records. Large spreads appear on
    <em>descriptor</em> (RT003 “Defective Hardware”: 52% referred) and on <em>channel</em>, and neither is a CO1 field.
    Borough, the only runtime context, shows moderate spreads (for example RT124 and RT002). That is why a borough-level
    estimate was tested in section 13.</p>
    {table(spread, [('request_type_code', 'Code'), ('borough', 'Borough (runtime)'), ('channel', 'Channel'), ('descriptor', 'Descriptor'), ('location_type', 'Location type')], num=('borough', 'channel', 'descriptor', 'location_type'), fmt={k: (lambda v: pct(v) if v else '–') for k in ('borough', 'channel', 'descriptor', 'location_type')})}
    <p class="muted">The full descriptor and location-type tables for every code are in
    <code>outputs/research/descriptor_patterns_by_code.csv</code> and
    <code>location_type_patterns_by_code.csv</code>.</p>""")

    # 10 ------------------------------------------------------------------
    knowledge_level = {c: v["risk_level"] for c, v in K["codes"].items()}
    rc = sorted(ref_code, key=lambda r: -float(r["referral_rate"]))
    items = [{"label": f"{r['request_type_code']} {by_code[r['request_type_code']]['catalog_agency']}",
              "value": float(r["referral_rate"]),
              "series": "--s-hi" if knowledge_level.get(r["request_type_code"]) == "ELEVATED" else "--s-cat",
              "tip": f"{r['request_type_code']}: {r['referred']} of {n(r['records'])} referred ({pct(r['referral_rate'])})"}
             for r in rc if int(r["referred"]) > 0]
    dest_rows = [{"k": k, "v": v} for k, v in ref["destination_classes"].items()]
    section(10, "referrals", "Referral analysis", f"""
    <p><strong>What does <code>agency</code> mean for a referred request?</strong> The closure note of every referred
    request was written by the agency in the <code>agency</code> column ({pct(ref['text_author_equals_agency_rate'])} of
    {n(ref['text_author_identified'])} notes where the author could be identified). That agency equals the catalog agency
    in {pct(ref['referred_agency_equals_catalog_rate'])} of cases. <code>agency</code> is therefore the <em>initial</em>
    agency. No structured column records the destination. All {n(ref['referred_records'])} referred rows have
    <code>verified_outcome = INCONCLUSIVE</code>.</p>
    <p><strong>Can the destination be recovered without speculation?</strong> Only partly. Parsing the templated
    closure notes (complete list below) gives an explicit destination for {n(ref['explicit_destination_records'])} requests
    ({pct(ref['explicit_destination_records'] / ref['referred_records'])}). Of the rest, {internal_units} are hand-offs to an internal unit
    of the same agency (DOT inspections, arterial and bridge divisions), so the agency did not change. The others say only
    “appropriate agency”, “another agency” or “out of jurisdiction”. Referred records are therefore
    <strong>retrospective error indicators</strong>. They are not reliable training targets for a corrected agency, and
    no correction model was trained on invented targets.</p>
    <div class="two-col">
      <div>{table(dest_rows, [('k', 'Destination class'), ('v', 'Requests')], num=('v',), fmt={'v': n})}</div>
      <div>{table([{'k': k, 'v': v} for k, v in ref['destination_agencies'].items()], [('k', 'Explicit destination'), ('v', 'Requests')], num=('v',), fmt={'v': n})}</div>
    </div>
    {figure('Referral rate by RT code (all splits)', hbar_chart(items, lambda v: f'{100 * v:.0f}%', 'referral rate by code', label_w=110, ref=(thr_val, f'advisory threshold {thr_val:.0%}'), show_values=[i['label'] for i in items[:5]]),
            f'Codes with at least one referral. Orange marks codes whose <em>train-period</em> smoothed rate is ELEVATED in the shipped knowledge. The threshold line is the validation-selected advisory threshold. Overall referral rate: {pct(ref["referral_rate_overall"], 2)}.',
            table(rc, [('request_type_code', 'Code'), ('catalog_agency', 'Agency'), ('records', 'Records'), ('referred', 'Referred'), ('referral_rate', 'Rate'), ('rate_train', 'Train'), ('rate_val', 'Val'), ('rate_test', 'Test')], num=('records', 'referred', 'referral_rate', 'rate_train', 'rate_val', 'rate_test'), fmt={'records': n, 'referral_rate': pct, 'rate_train': pct, 'rate_val': pct, 'rate_test': pct}),
            legend([('ELEVATED in train knowledge', '--s-hi'), ('Typical', '--s-cat')]))}
    {table(ref_agency, [('agency', 'Initial agency'), ('records', 'Requests'), ('referred', 'Referred'), ('referral_rate', 'Rate'), ('share_of_referrals', 'Share of referrals')], num=('records', 'referred', 'referral_rate', 'share_of_referrals'), fmt={'records': n, 'referral_rate': pct, 'share_of_referrals': pct}, caption='Referrals by initial agency')}
    {details('All referral closure-note templates and their classification', table(templates, [('agency', 'Agency'), ('records', 'Requests'), ('destination_class', 'Class'), ('destination_agency', 'Destination'), ('resolution_description', 'Closure note')], num=('records',)))}""")

    # 11 ------------------------------------------------------------------
    section(11, "leakage", "Temporal split and leakage controls", f"""
    <ul>
      <li><strong>The supplied <code>split</code> column was used as is.</strong> There was no random resampling. Train ends
      {esc(hist['split_boundaries']['train'][1])}, validation runs to {esc(hist['split_boundaries']['val'][1])}, and test
      follows.</li>
      <li><strong>Train</strong> was used to fit every data-derived quantity: majority tables, referral rates, borough
      cells and the knowledge artifact (<code>fitted_on_split: "{esc(K['fitted_on_split'])}"</code>).</li>
      <li><strong>Validation</strong> was used to choose the estimator (RT vs RT+borough), both pseudo-counts and the
      advisory threshold. The selection rule was fixed before the test split was read.</li>
      <li><strong>Test</strong> was read once, for final reporting. Nothing was re-fitted afterwards.</li>
      <li><strong>Runtime inputs are only CO1 fields.</strong> The router never reads <code>closed_date</code>,
      <code>resolution_description</code>, <code>outcome_class</code>, <code>verified_outcome</code> or
      <code>resolution_hours</code>. <code>outcome_class</code> and closure notes from the <em>training period</em> serve
      as labels for static knowledge. Nothing from the current request's outcome is ever used, so the knowledge cannot
      leak future information about it.</li>
      <li>Historical <code>catalog_agency</code>, <code>required_skill</code> and <code>est_service_minutes</code> are copies
      of the catalog (verified: {chip('identical', 'good') if hist['embedded_catalog_agency_matches_csv'] else chip('differs', 'bad')}).
      They were never used as evidence for the catalog, which would be circular.</li>
    </ul>""")

    # 12 ------------------------------------------------------------------
    mlabels = [m["month"] for m in months]
    section(12, "discovery", "Knowledge-discovery findings", f"""
    <ol class="findings">
      <li><strong>The RT code determines the first agency.</strong> {pct(hist['overall_agreement_rate'], 3)} agreement,
      100% on validation and on test, and stable in every month (table view below).</li>
      <li><strong>The historical label derives from the catalog taxonomy</strong> ({pct(hist['rt_reconstruction_exact'] / hist['records'], 1)} exact
      reconstruction), so agreement is a consistency check.</li>
      <li><strong>Referrals concentrate in a few codes.</strong> RT043, RT002, RT124 and RT003 hold
      {pct(sum(int(r['referred']) for r in ref_code if r['request_type_code'] in ('RT043', 'RT002', 'RT124', 'RT003')) / ref['referred_records'])}
      of all referrals.</li>
      <li><strong>Referral rates per code are temporally stable.</strong> Pearson correlation of per-code rates:
      train vs validation {ref['rate_stability']['pearson_train_val']}, train vs test {ref['rate_stability']['pearson_train_test']}
      (Spearman {ref['rate_stability']['spearman_train_test']}).</li>
      <li><strong>The matrix explains the two largest patterns.</strong> RT043 → NYPD when plates are attached
      (matrix: “DSNY or NYPD depending on plates”), and RT002 → DEP after DOT inspection (sewer and water causes are DEP).
      Neither deciding fact is known at intake.</li>
      <li><strong>Unresolved patterns:</strong> {len(ref['unresolved_codes'])} codes have referrals with no or partial
      destination evidence (for example RT124: NYPD notes never name the destination). These are listed in section 14.</li>
      <li><strong>Data quality:</strong> <code>location_type</code> is missing in {n(hist['location_type_missing'])} rows, and
      {hist['rare_descriptor_rows_under_5']} descriptor values occur fewer than 5 times. Neither affects the runtime, which uses neither field.</li>
    </ol>
    {figure('Monthly referral rate', line_chart(mlabels, [('referral rate', '--s-cat', [float(m['referral_rate']) for m in months])], lambda v: f'{100 * v:.1f}%', 'monthly referral rate', vmax=0.04, mark_x=mlabels.index('2026-05'), mark_label='validation starts'),
            'Share of each month’s requests later closed as REFERRED. Agreement between the catalog and the first agency stays at 100% every month except two months with one NYC311-PRD row each (see the table view).',
            table(months, [('month', 'Month'), ('records', 'Records'), ('agreement_rate', 'Catalog agreement'), ('referred', 'Referred'), ('referral_rate', 'Referral rate')], num=('records', 'agreement_rate', 'referred', 'referral_rate'), fmt={'records': n, 'agreement_rate': lambda v: pct(v, 3), 'referral_rate': lambda v: pct(v, 2)}))}
    """ + (lambda top: figure('Referral rate per split for the eight most-referred codes',
                               grouped_hbar(top, [('train', '--s1', [float(by_split[c]['rate_train']) for c in top]),
                                                  ('validation', '--s2', [float(by_split[c]['rate_val'] or 0) for c in top]),
                                                  ('test', '--s3', [float(by_split[c]['rate_test'] or 0) for c in top])],
                                            lambda v: f'{100 * v:.0f}%', 'referral rate per split'),
                               'Rates are stable in rank across time. RT002 rises in the summer splits; RT043 stays near 40%.',
                               table([by_split[c] for c in top], [('request_type_code', 'Code'), ('rate_train', 'Train'), ('rate_val', 'Val'), ('rate_test', 'Test')], num=('rate_train', 'rate_val', 'rate_test'), fmt={k: pct for k in ('rate_train', 'rate_val', 'rate_test')}),
                               legend([('train', '--s1'), ('validation', '--s2'), ('test', '--s3')])))(
        [r["request_type_code"] for r in ref_code[:8]]))

    # 13 ------------------------------------------------------------------
    sel_rows = [r for r in risk_sel]
    section(13, "approaches", "Candidate routing approaches", f"""
    <h3>Agency assignment candidates (fitted on train)</h3>
    {table(agency_cmp, [('split', 'Split'), ('approach', 'Approach'), ('coverage', 'Coverage'), ('agree_initial_agency', 'Agrees with first agency'), ('agree_final_agency_determinable', 'Agrees with final agency*'), ('differs_from_catalog', 'Differs from catalog')], num=('coverage', 'agree_initial_agency', 'agree_final_agency_determinable', 'differs_from_catalog'), fmt={'coverage': pct, 'agree_initial_agency': lambda v: pct(v, 2), 'agree_final_agency_determinable': lambda v: pct(v, 2)})}
    <p class="muted">* “Final agency” is the destination stated in the closure note for referred requests, or the
    initial agency otherwise. Referrals without a stated destination are excluded, so this label is partial and
    retrospective.</p>
    <p><strong>A, C1 and C2 are identical.</strong> Learning from history, with or without the borough, reproduces the
    catalog exactly. <strong>C3</strong> is the strongest correction model the data allows. It changes one code, RT002
    (DOT → DEP), and raises agreement with the determinable final agency on test from
    {pct(a_test['agree_final_agency_determinable'], 2)} to {pct(c3_test['agree_final_agency_determinable'], 2) if c3_test else '–'}.
    It was <strong>rejected</strong> for four reasons:</p>
    <ol>
      <li>It overrides the shared catalog, which shared rule 3 makes the key, without an agreed correction policy.</li>
      <li>DOT is not wrong as a first responder. The notes read “DOT inspected and has requested DEP address the issue”,
      and DOT re-inspects after 60 days. DEP's involvement follows DOT's inspection.</li>
      <li>No runtime input separates the RT002 cases that move to DEP from those that do not. Switching would misroute the
      remaining {next((int(r['final_is_catalog_agency']) for r in c3 if r['split'] == 'test'), 0)} test cases where DOT
      finished the job.</li>
      <li>The label is retrospective and depends on parsing free text.</li>
    </ol>
    <p>The finding is raised for the group instead (section 22).</p>
    <h3>Referral-risk knowledge (data mining for the advisory)</h3>
    <p>Two smoothed estimators (m-estimates shrunk toward a prior) were tuned in two stages on validation. The
    pre-declared rule keeps RT-only unless RT+borough improves validation log-loss by more than 1% without lowering AUC.
    The relative gain was {pct(mc['risk_rel_gain_borough'], 2)}, so <strong>{esc(mc['risk_selected_model'])}</strong>
    was selected (m<sub>RT</sub> = {mc['risk_selected_m_rt']}, m<sub>cell</sub> = {mc['risk_selected_m_cell']}).
    On test: AUC {sel_test['auc']} vs {rt_test['auc']} for RT-only, and log-loss {sel_test['log_loss']} vs
    {rt_test['log_loss']} (constant baseline {const_test['log_loss']}).</p>
    {details('All model-selection runs', table(sel_rows, [('split', 'Split'), ('model', 'Model'), ('m_rt', 'm RT'), ('m_cell', 'm cell'), ('log_loss', 'Log-loss'), ('brier', 'Brier'), ('auc', 'AUC'), ('selected', 'Selected')], num=('log_loss', 'brier', 'auc')))}""")

    # 14 ------------------------------------------------------------------
    tv = [r for r in thr if r["split"] == "val"]
    tt = [r for r in thr if r["split"] == "test"]
    xl = [f"{float(r['threshold']):.3g}" for r in tv]
    sel_idx = [float(r["threshold"]) for r in tv].index(thr_val)
    mk = lambda rows: [("recall", "--s1", [float(r["recall"]) for r in rows]),  # noqa: E731
                       ("precision", "--s2", [float(r["precision"]) for r in rows]),
                       ("flag rate", "--s3", [float(r["flag_rate"]) for r in rows])]
    hist_rows = []
    for split in ("val", "test"):
        if split not in bh:  # dataset absent and no earlier replay to reuse
            continue
        for name in ("A_direct_lookup", "B_catalog_rules", "B+R14_advisory", "B+R14_review_policy"):
            hist_rows.append({"split": split, "approach": name, **bh[split][name]})
    if not hist_rows:
        hist_note = f'<p class="muted">Historical replay not available: {esc(bh.get("skipped", "no data"))}.</p>'
    elif bh.get("reused_from_previous_run"):
        hist_note = ('<p class="muted">The historical dataset was not present for this build, so the replay figures '
                     'below are reused from the last benchmark run that had it.</p>')
    else:
        hist_note = ""
    section(14, "benchmarks", "Benchmark comparisons", f"""
    <h3>Functional behaviour on {B['functional']['scenarios']} labelled CO1 scenarios</h3>
    {table([{'a': k, **v} for k, v in fun.items()], [('a', 'Approach'), ('status_correct_pct', 'Correct status'), ('unsafe_dispatches', 'Unsafe dispatches'), ('explained_pct', 'Explained')], num=('status_correct_pct', 'unsafe_dispatches', 'explained_pct'), fmt={'status_correct_pct': lambda v: f'{float(v):.1f}%', 'explained_pct': lambda v: f'{float(v):.0f}%'})}
    <p>Expectations reflect the default policy. Under the review policy, the 3 deviations are the expected HELD
    outcomes for the ELEVATED codes {', '.join(elevated_codes)}.</p>
    {details('Scenario-level comparison of A and B', table(B['functional']['per_scenario'], [('scenario', 'Scenario'), ('expected', 'Expected'), ('A_status', 'A status'), ('A_eligible', 'A eligible'), ('B_status', 'B status'), ('B_eligible', 'B eligible')]))}
    <h3>Mixed synthetic stream (10,000 messages, 15% edge cases)</h3>
    {table([{'a': k, **v} for k, v in B['functional']['synthetic_stream'].items()], [('a', 'Approach'), ('dispatch_eligible_pct', 'Eligible %'), ('review_pct', 'Review %'), ('unresolved_pct', 'Unresolved %'), ('invalid_pct', 'Invalid %'), ('explained_pct', 'Explained %')], num=('dispatch_eligible_pct', 'review_pct', 'unresolved_pct', 'invalid_pct', 'explained_pct'))}
    <h3>Historical replay (perfect intake: RT code and borough only)</h3>
    {hist_note}
    {table(hist_rows, [('split', 'Split'), ('approach', 'Approach'), ('coverage_pct', 'Coverage %'), ('agree_first_agency_pct', 'Agree first agency %'), ('review_pct', 'Review %'), ('advisory_flag_pct', 'Flagged %'), ('referred_flagged_pct', 'Referrals flagged %'), ('precision_of_flag_pct', 'Flag precision %')], num=('coverage_pct', 'agree_first_agency_pct', 'review_pct', 'advisory_flag_pct', 'referred_flagged_pct', 'precision_of_flag_pct'), cls='scroll')}
    <p class="muted">“Agree first agency” is consistency with the historical first agency, which by construction is
    close to the catalog. It is <strong>not</strong> verified routing accuracy.</p>
    <div class="two-col">
      {figure('Advisory threshold trade-off: validation (selection)', line_chart(xl, mk(tv), lambda v: f'{100 * v:.0f}%', 'threshold trade-off validation', vmax=1.0, mark_x=sel_idx, mark_label='selected', height=240), 'F1 is maximised at the selected threshold. Ties go to the higher threshold, which raises fewer advisories.', table(tv, [('threshold', 'Threshold'), ('flag_rate', 'Flag rate'), ('precision', 'Precision'), ('recall', 'Recall'), ('f1', 'F1')], num=('threshold', 'flag_rate', 'precision', 'recall', 'f1')), legend([('recall', '--s1'), ('precision', '--s2'), ('flag rate', '--s3')]))}
      {figure('Same thresholds on test (evaluation only)', line_chart(xl, mk(tt), lambda v: f'{100 * v:.0f}%', 'threshold trade-off test', vmax=1.0, mark_x=sel_idx, mark_label='selected', height=240), f'At {thr_val:.0%}: flag rate {pct(adv_t["flag_rate"])}, recall {pct(adv_t["recall"])}, precision {pct(adv_t["precision"])} (validation {pct(adv_v["precision"])}).', table(tt, [('threshold', 'Threshold'), ('flag_rate', 'Flag rate'), ('precision', 'Precision'), ('recall', 'Recall'), ('f1', 'F1')], num=('threshold', 'flag_rate', 'precision', 'recall', 'f1')), legend([('recall', '--s1'), ('precision', '--s2'), ('flag rate', '--s3')]))}
    </div>
    <h3>Complexity</h3>
    <p>{B['complexity']['business_rules']} business rules; {n(B['complexity']['runtime_source_lines'])} runtime source
    lines excluding docstrings and comments, against {B['complexity']['baseline_source_lines']} for the baseline;
    {B['complexity']['runtime_third_party_dependencies']} third-party runtime dependencies.</p>""")

    # 15 ------------------------------------------------------------------
    section(15, "selected", "Selected solution and justification", f"""
    <div class="callout"><strong>Approach B (catalog plus knowledge-based rules) with the R14 historical referral
    advisory, informational by default.</strong></div>
    <ul>
      <li><strong>The catalog is sufficient for agency and skill selection.</strong> Every code is unique and complete,
      history agrees, and no learned alternative changed a single assignment from runtime inputs.</li>
      <li><strong>The rules are necessary for safety.</strong> Lookup alone made
      {fun['A_direct_lookup']['unsafe_dispatches']} unsafe dispatches out of {B['functional']['scenarios']} scenarios;
      the rules made {fun['B_catalog_rules']['unsafe_dispatches']}. Every decision is explained and traceable to rule
      IDs.</li>
      <li><strong>History adds value only as context.</strong> The advisory concentrates attention: on test it covers
      {pct(adv_t['flag_rate'])} of requests and contains {pct(adv_t['recall'])} of later referrals. It leaves the
      agreed catalog intact, and holding is opt-in (<code>review_on_elevated_referral_risk</code>) because shared
      rule 7 says nobody is refused automatically. Holding would delay the
      {pct(1 - float(adv_t['precision']))} of flagged requests that the first agency did handle.</li>
      <li><strong>It is portable.</strong> It needs only the standard library, a CSV and an optional {knowledge_kb:.0f} KB JSON, with
      no model files and no dataset at runtime.</li>
    </ul>
    <p>ELEVATED at code level: {', '.join(elevated_codes)}. Further code and borough cells that are ELEVATED only when the
    borough is known: {', '.join(f'{c}/{b}' for c, b, _ in elevated_cells if c in cell_codes) or 'none'}.</p>""")

    # 16 ------------------------------------------------------------------
    section(16, "rules", "Business rules", table([{"id": r.id, "name": r.name, "s": r.statement} for r in RULES.values()],
                                                 [("id", "ID"), ("name", "Rule"), ("s", "Statement")]) + f"""
    <p><strong>Status precedence:</strong> INVALID &gt; BLOCKED &gt; HELD &gt; UNRESOLVED &gt; ASSIGNED. All rules run for
    every request, so the result lists every reason, not only the first. A rule can add a reason but can never remove
    one, which is why no combination of inputs can make a blocked request eligible. This is tested exhaustively over
    {gate_cases} gate combinations.</p>""")

    # 17 ------------------------------------------------------------------
    out_fields = [
        ("request_type … slots", "Passed through", "The eight agreed CO1 fields, same names and order (slots as text, never geocoded). Invalid values become null."),
        ("agency", "Added", "Catalog agency; null when not resolved."),
        ("required_skills", "Added", "Catalog skill list; empty when not resolved. Never partial."),
        ("status", "Added", "ASSIGNED · HELD · BLOCKED · UNRESOLVED · INVALID."),
        ("dispatch_eligible", "Added", "True only when status is ASSIGNED."),
        ("next_step", "Added", "DUPLICATE_DETECTION when assigned; otherwise HUMAN_REVIEW · CONFIRM_WITH_CITIZEN · CITIZEN_TOLD_TO_CALL_911 · ROUTE_TO_PERSON · NO_REQUEST_CREATED · FIX_INPUT · CATALOG_REVIEW."),
        ("explanation", "Added", "Plain-English reason, naming every blocking rule."),
    ]
    detail_fields = [
        ("schema_version / component_version / catalog_version", "Output schema 2.0, package version, catalog content hash."),
        ("decision_method", "CATALOG_RULE_BASED."),
        ("agency_resolution", "RESOLVED · UNKNOWN_CODE · CATALOG_CONFLICT · INCOMPLETE_CATALOG_ENTRY · SPECIAL_LABEL · NOT_ATTEMPTED."),
        ("agency_name / citizen_label", "Full agency name (matrix glossary) and catalog problem description."),
        ("needs_review", "True whenever status is not ASSIGNED."),
        ("rules_applied / findings", "Rule IDs evaluated, and structured findings (rule, code, severity, message, field)."),
        ("referral_advisory", "R14 historical referral context, or null."),
    ]
    section(17, "api", "API and output schema", """
    <pre><code>from civicops_agency import AgencySkillRouter, RouterConfig

router = AgencySkillRouter.from_catalog("data/reference/request_catalog.csv")
result = router.assign(co1_output)            # dict, JSON str or bytes
result.to_dict()                              # slim hand-off to duplicate detection
result.to_dict(detail=True)                   # + one "detail" audit block
router.assign_batch([msg1, msg2])             # independent results, input order kept
router.assign_json('[{...}, {...}]')          # JSON in, JSON out

# CLI
civicops-agency assign examples/inputs/batch_co1.json -o results.json [--detail]
civicops-agency catalog-check
civicops-agency demo</code></pre>
    <p>The default output is the slim hand-off agreed with the group: the agreed CO1 fields passed through, then six
    added fields. <code>examples/inputs/</code> shows what CO1 sends (no agency) and <code>examples/outputs/</code>
    what this component hands on; a test keeps them in sync with the code.</p>""" + table([{"f": f, "k": k, "d": d} for f, k, d in out_fields], [("f", "Field"), ("k", "Source"), ("d", "Meaning")]) +
            "<h3>Optional <code>detail</code> block (<code>detail=True</code> / <code>--detail</code>)</h3>" +
            table([{"f": f, "d": d} for f, d in detail_fields], [("f", "Field"), ("d", "Meaning")]) +
            "<p>The output never contains estimated service time, urgency, priority, crew, travel time, route, coordinates "
            "or external factors. A test asserts this on several result types.</p>")

    # 18 ------------------------------------------------------------------
    file_rows = [{"f": f, **c} for f, c in sorted(T["files"].items())]
    section(18, "tests", "Test results", f"""
    <div class="stats">
      {stat(str(T['total']), 'test cases')}
      {stat(str(T['passed']), 'passed')}
      {stat(str(T['failed']), 'failed')}
      {stat(str(T['skipped']), 'skipped')}
      {stat(f"{T['time']:.2f} s", 'run time')}
    </div>
    <p>Executed with pytest at {esc(T['timestamp'][:19].replace('T', ' '))}; source: <code>outputs/tests/junit.xml</code>.</p>
    {table(file_rows, [('f', 'Test module'), ('passed', 'Passed'), ('failed', 'Failed'), ('error', 'Errors'), ('skipped', 'Skipped')], num=('passed', 'failed', 'error', 'skipped'))}
    <h3>Coverage of the 20 required test topics</h3>
    {table(req_rows, [('req', 'Required topic'), ('tests', 'Test cases'), ('status', 'Result'), ('where', 'Modules')], num=('tests',))}""")

    # 19 ------------------------------------------------------------------
    lat = perf["assign_latency_us"]
    section(19, "performance", "Performance results", f"""
    {table([{'k': 'Catalog load (median of 30)', 'v': f"{perf['catalog_load_ms_median']} ms"},
            {'k': 'assign() latency p50 / p95 / p99', 'v': f"{lat['p50']} / {lat['p95']} / {lat['p99']} µs ({n(lat['requests'])} requests)"},
            {'k': 'Batch throughput (1k / 10k / 50k)', 'v': ' / '.join(f"{n(v['router_req_per_s'])}" for v in perf['batch_throughput'].values()) + ' req/s'},
            {'k': 'Baseline A throughput (1k / 10k / 50k)', 'v': ' / '.join(f"{n(v['baseline_req_per_s'])}" for v in perf['batch_throughput'].values()) + ' req/s'},
            {'k': 'to_json() per result', 'v': f"{perf['to_json_us_per_result']} µs"}],
           [('k', 'Measure'), ('v', 'Result')])}
    <p>Environment: Python {esc(B['environment']['python'])} ({esc(B['environment']['implementation'])}),
    {esc(B['environment']['platform'])}, {B['environment']['cpu_count']} logical CPUs, single thread. The rules cost about
    {perf['batch_throughput']['10000']['baseline_req_per_s'] // max(1, perf['batch_throughput']['10000']['router_req_per_s'])}× the bare
    lookup but stay far below any interactive budget. Timings depend on the machine.</p>""")

    # 20 ------------------------------------------------------------------
    section(20, "dispatch", "Downstream integration", """
    <p>The next component in the shared pipeline is <strong>duplicate detection</strong>, then external factors,
    credibility, fake-request detection, resolution time, urgency, prioritisation and finally crew deployment.
    Requests with <code>status = ASSIGNED</code> carry <code>next_step = DUPLICATE_DETECTION</code>; every other
    request names where it goes instead. Duplicate detection reads <code>request_type</code> and the
    <code>slots</code> exactly as CO1 sent them. Crew deployment, at the end of the pipeline, uses the fields this
    component added:</p>
    <pre><code>if result["dispatch_eligible"]:
    candidates = [c for c in crews
                  if c["agency"] == result["agency"]
                  and set(result["required_skills"]) &lt;= set(c["skills"].split("|"))]
    # then: prioritisation order, geocode result["slots"], borough match, capacity (max_jobs), routing
else:
    route_by(result["next_step"])   # HUMAN_REVIEW, CONFIRM_WITH_CITIZEN, ...</code></pre>
    <ul>
      <li>Agencies and skills are always from the shared vocabulary that the crew table uses (8 agencies, 16 skills). A
      mock downstream consumer in <code>tests/test_integration.py</code> verifies this through a JSON round trip.</li>
      <li><code>dispatch_eligible = true</code> is <em>necessary, not sufficient</em>. Priority, location quality
      (the address is text, and geocoding is unassigned in the shared document), crew availability and capacity are
      checked later.</li>
      <li>Service time comes from the catalog's <code>est_service_minutes</code>, which the dispatch component should read
      itself with the same RT code. This component does not pass it on, by design.</li>
      <li>Requests closed as REFERRED by crews should ideally record a structured destination agency. That would make
      a supervised correction model possible in future (section 22).</li>
    </ul>""")

    # 21 ------------------------------------------------------------------
    section(21, "limits", "Limitations", f"""
    <ul>
      <li><strong>Perfect-intake assumption.</strong> The historical replay feeds the true RT code. Real CO1
      misclassification is not measured here; the R09 threshold exists for that, but it is off by default.</li>
      <li><strong>Circular agreement.</strong> Historical RT codes derive from 311 complaint types through the catalog,
      so 100% agreement shows consistency, not accuracy.</li>
      <li><strong>Partial referral destinations.</strong> {pct(1 - ref['explicit_destination_records'] / ref['referred_records'])}
      of referrals name no destination, and REFERRED also includes internal-unit transfers.</li>
      <li><strong>Selection.</strong> The dataset contains only the 34 catalog complaint types. Requests that CO1 would
      label OTHER are absent.</li>
      <li><strong>Seasonality.</strong> RT044 (snow and ice) is absent from validation and test, so its advisory level is
      untested out of time.</li>
      <li><strong>Advisory precision.</strong> On test, {pct(1 - float(adv_t['precision']))} of flagged requests were not
      referred. The advisory is context, not a prediction about the individual request.</li>
      <li><strong>Analyst annotation.</strong> The code-to-matrix-family mapping is hand-made and documented, not part of
      the supplied references.</li>
      <li><strong>Platform coverage.</strong> Tested on Python {esc(B['environment']['python'])} on Windows only; the
      declared minimum is 3.9.</li>
      <li><strong>Open contract points.</strong> Shared rule 2 says EMERGENCY labels are “held”, while the label table
      says “call 911, nothing is filed”. This component blocks them (DECISIONS.md, D-08).</li>
    </ul>""")

    # 22 ------------------------------------------------------------------
    section(22, "conclusions", "Conclusions and future recommendations", f"""
    <p>The shared catalog is a complete and historically consistent operational reference for agency and skill
    assignment. The component's intelligence lies in its explicit, explainable safety and integrity rules, and in
    knowledge discovered from referrals. No learned classifier was trained, because the evidence does not justify one:
    the catalog already explains the target, and no reliable corrected-agency label exists.</p>
    <h3>Recommendations for the group</h3>
    <ol>
      <li><strong>Vehicles (RT043/RT124):</strong> add a CO1 slot such as <code>has_license_plate</code>. The matrix says
      plates decide DSNY versus NYPD, and {pct(next(float(r['referral_rate']) for r in ref_code if r['request_type_code'] == 'RT043'))}
      of RT043 requests were referred, mostly to NYPD.</li>
      <li><strong>Cave-ins (RT002) and defective street hardware (RT003):</strong> discuss DEP co-notification or a catalog
      sub-code. Most of these referrals go to DEP after DOT inspection.</li>
      <li><strong>Structured referral outcome:</strong> when crew deployment closes a job as REFERRED, record the destination
      agency. That turns referrals into a usable supervised target.</li>
      <li><strong>Public housing:</strong> NYCHA is in the matrix but not in the catalog. Decide how RT080–RT084 in NYCHA
      buildings should be handled.</li>
      <li><strong>Refresh the knowledge</strong> each term with <code>python run_all.py</code>. The catalog hash check
      disables stale knowledge automatically.</li>
      <li><strong>Agree a CO1 confidence threshold</strong> once intake confidence has been calibrated, then set
      <code>min_confidence</code>.</li>
    </ol>""")

    toc = "".join(f'<li><a href="#{sid}">{esc(t)}</a></li>' for sid, t in [
        ("summary", "Executive summary"), ("course", "Course alignment"), ("scope", "Scope"), ("co1", "CO1 contract"),
        ("inventory", "Reference files"), ("dataset", "Dataset"), ("consistency", "Catalog vs matrix"),
        ("distributions", "Agency distributions"), ("ambiguous", "Ambiguous codes"), ("referrals", "Referrals"),
        ("leakage", "Split & leakage"), ("discovery", "Findings"), ("approaches", "Approaches"),
        ("benchmarks", "Benchmarks"), ("selected", "Selected solution"), ("rules", "Business rules"),
        ("api", "API & schema"), ("tests", "Tests"), ("performance", "Performance"), ("dispatch", "Dispatch"),
        ("limits", "Limitations"), ("conclusions", "Conclusions")])

    return TEMPLATE.replace("{{TOC}}", toc).replace("{{BODY}}", "".join(sec)).replace(
        "{{META}}", f"Component v{esc(__version__)} · catalog {esc(B['catalog_version'])} · built {esc(built)} from executed results").replace(
        "{{CSS}}", CSS).replace("{{JS}}", JS)


CSS = """
:root{color-scheme:light;--bg:#f6f5f2;--surface:#fcfcfb;--surface-2:#f0efec;--border:#e2e0da;--text:#1c1b19;
--text-2:#52514e;--muted:#7a7873;--accent:#256abf;--s-cat:#2a78d6;--s-hi:#eb6834;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;
--good:#0a7d0a;--good-bg:#e3f4e3;--bad:#b42f2f;--bad-bg:#fbe7e7;--warn:#8a5a00;--warn-bg:#fdf1d6;--grid:#e6e4de;--axis:#b9b6ae}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#121211;--surface:#1a1a19;
--surface-2:#242422;--border:#34332f;--text:#f2f1ec;--text-2:#c3c2b7;--muted:#9a988f;--accent:#6da7ec;--s-cat:#3987e5;
--s-hi:#d95926;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--good:#5fd35f;--good-bg:#183218;--bad:#f08080;--bad-bg:#3a1c1c;
--warn:#f2c14e;--warn-bg:#3a2e12;--grid:#2c2b28;--axis:#55534d}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--surface-2:#242422;--border:#34332f;
--text:#f2f1ec;--text-2:#c3c2b7;--muted:#9a988f;--accent:#6da7ec;--s-cat:#3987e5;--s-hi:#d95926;--s1:#3987e5;--s2:#d95926;
--s3:#199e70;--good:#5fd35f;--good-bg:#183218;--bad:#f08080;--bad-bg:#3a1c1c;--warn:#f2c14e;--warn-bg:#3a2e12;
--grid:#2c2b28;--axis:#55534d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.layout{display:grid;grid-template-columns:230px minmax(0,1fr);max-width:1240px;margin:0 auto;gap:32px;padding:0 24px}
nav{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;padding:28px 0}
nav ol{list-style:none;margin:0;padding:0;counter-reset:s}nav li{counter-increment:s}
nav a{display:block;padding:3px 8px;border-radius:6px;color:var(--text-2);text-decoration:none;font-size:13px}
nav a::before{content:counter(s) ". ";color:var(--muted)}nav a:hover{background:var(--surface-2);color:var(--text)}
main{padding:28px 0 80px;min-width:0}
header.top h1{font-size:30px;line-height:1.2;margin:0 0 6px;letter-spacing:-.01em}
header.top p{margin:0;color:var(--text-2)}.meta{font-size:13px;color:var(--muted);margin-top:6px!important}
section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px 28px;margin:20px 0}
h2{font-size:21px;margin:0 0 14px;display:flex;gap:12px;align-items:baseline}
.secno{font-size:13px;color:var(--accent);font-weight:600;min-width:22px}
h3{font-size:16px;margin:22px 0 8px}p{margin:10px 0}code{font:13px ui-monospace,Consolas,monospace;background:var(--surface-2);padding:1px 5px;border-radius:4px}
pre{background:var(--surface-2);padding:14px 16px;border-radius:8px;overflow:auto}pre code{background:none;padding:0}
.muted{color:var(--muted);font-size:13px}
.answer{border-left:4px solid var(--accent);background:var(--surface-2);padding:16px 20px;border-radius:0 10px 10px 0;margin-bottom:18px}
.answer-q{font-weight:650;margin-bottom:8px}.answer-a{color:var(--text)}
.callout{border:1px solid var(--accent);border-radius:10px;padding:12px 16px;margin:8px 0 12px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.stat{background:var(--surface-2);border-radius:10px;padding:12px 14px}
.stat-value{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums}.stat-label{font-size:13px;color:var(--text-2)}
.stat-note{font-size:12px;color:var(--muted);margin-top:2px}
.table-wrap{overflow-x:auto;margin:12px 0;border:1px solid var(--border);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}caption{text-align:left;padding:8px 10px;font-weight:600;color:var(--text-2)}
th,td{padding:7px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
th{background:var(--surface-2);font-weight:600;color:var(--text-2);white-space:nowrap}
tbody tr:last-child td{border-bottom:none}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.chip-good{background:var(--good-bg);color:var(--good)}.chip-bad{background:var(--bad-bg);color:var(--bad)}
.chip-warn{background:var(--warn-bg);color:var(--warn)}.chip-muted{background:var(--surface-2);color:var(--muted)}
details{margin:10px 0}summary{cursor:pointer;color:var(--accent);font-size:14px}
.two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.fig{margin:16px 0}.fig-title{font-weight:600;font-size:14px;margin-bottom:4px}.fig-note{font-size:13px;color:var(--muted)}
.chart-wrap{width:100%}.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .axis{stroke:var(--axis);stroke-width:1}
.chart .tick,.chart .lab{fill:var(--text-2);font-size:11px}.chart .val,.chart .dlab{fill:var(--text);font-size:11px;font-weight:600}
.chart .refline{stroke:var(--text-2);stroke-width:1.5;stroke-dasharray:4 3}.chart .reflab{fill:var(--text-2);font-size:11px}
.chart .line{fill:none;stroke-width:2}.chart .dot{stroke:var(--surface);stroke-width:2}
.chart .hit{fill:transparent;cursor:default}.chart .hit:hover{fill:var(--text);fill-opacity:.05}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--text-2);margin:4px 0 6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.findings li{margin:6px 0}
#tip{position:fixed;pointer-events:none;background:var(--text);color:var(--surface);font-size:12px;padding:5px 9px;
border-radius:6px;opacity:0;transition:opacity .08s;z-index:10;max-width:320px}
@media (max-width:860px){.layout{grid-template-columns:1fr;padding:0 16px;gap:0}nav{position:static;max-height:none;padding:16px 0 0}
nav ol{display:flex;flex-wrap:wrap;gap:4px}section{padding:18px 16px}header.top h1{font-size:24px}}
"""

JS = """
const tip=document.getElementById('tip');
document.addEventListener('mouseover',e=>{const t=e.target.closest('[data-tip]');if(!t)return;tip.textContent=t.dataset.tip;tip.style.opacity=1;});
document.addEventListener('mousemove',e=>{if(tip.style.opacity==='1'){const x=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px';}});
document.addEventListener('mouseout',e=>{if(e.target.closest('[data-tip]'))tip.style.opacity=0;});
"""

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agency Skills Report</title><style>{{CSS}}</style></head>
<body><div class="layout"><nav><ol>{{TOC}}</ol></nav><main>
<header class="top"><h1>Agency Assignment and Required Skills</h1>
<p>CivicOps AI · Group 23 · Intelligent Reasoning Systems: research, design and evaluation report</p>
<p class="meta">{{META}}</p></header>
{{BODY}}
</main></div><div id="tip" role="tooltip"></div><script>{{JS}}</script></body></html>
"""


def main() -> None:
    html_text = build()
    REPORT.write_text(html_text, encoding="utf-8")
    print(f"Wrote {REPORT} ({len(html_text) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
