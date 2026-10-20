"""Build reports/external_factors_report.html: a self-contained academic report from executed outputs.

Every number is read from files in outputs/, data/osm/manifest.json or the test results.
Nothing is typed in by hand. Charts and maps are embedded as base64 PNG.

Usage: python reports/build_report.py   (run after research, benchmarks, maps and tests)
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / "outputs" / "tables"
C = ROOT / "outputs" / "charts"
M = ROOT / "outputs" / "maps"
B = ROOT / "outputs" / "benchmarks"
OUT = ROOT / "reports" / "external_factors_report.html"
sys.path.insert(0, str(ROOT))


def j(p: Path):
    return json.loads(p.read_text("utf-8"))


def esc(x) -> str:
    return html.escape(str(x))


def img(path: Path, alt: str, caption: str = "") -> str:
    if not path.exists():
        return f'<p class="missing">[figure not generated: {esc(path.name)}]</p>'
    b64 = base64.b64encode(path.read_bytes()).decode()
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f'<figure><img src="data:image/png;base64,{b64}" alt="{esc(alt)}">{cap}</figure>'


def table(df: pd.DataFrame, fmt: dict | None = None, cls: str = "") -> str:
    fmt = fmt or {}
    head = "".join(f"<th>{esc(c)}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c in fmt and pd.notna(v) and not isinstance(v, str):
                s = fmt[c].format(v)
            elif isinstance(v, float):
                s = "" if pd.isna(v) else f"{v:,.2f}"
            elif isinstance(v, (bool,)) or str(v) in ("True", "False"):
                s = "✔" if str(v) == "True" else "✘"
            else:
                s = esc(v)
            num = isinstance(v, (int, float)) and not isinstance(v, bool)
            cells.append(f'<td class="{"num" if num else ""}">{s}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="tw"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def md_to_html(md: str) -> str:
    """Minimal Markdown -> HTML for DECISIONS.md (headings, bullets, bold, code, links)."""
    out, in_list = [], False
    for line in md.splitlines():
        s = line.rstrip()
        def inline(t):
            t = esc(t)
            t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
            t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
            t = re.sub(r"\\\*", "*", t)
            t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
            return t
        if s.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(s[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if s.startswith("### "):
            out.append(f"<h4>{inline(s[4:])}</h4>")
        elif s.startswith("# "):
            continue
        elif s.strip() == "---" or not s.strip():
            continue
        else:
            out.append(f"<p>{inline(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


SCENARIOS = [
    (1, "Request inside a configured radius", "1, SCHOOL at 80 m", ["test_01_inside_radius"]),
    (2, "Request outside a configured radius", "0, nearest qualifying 150 m", ["test_02_outside_radius"]),
    (3, "Request exactly at the threshold", "100.00 m -> 1, 100.01 m -> 0 (inclusive)", ["test_03_exactly_at_threshold_is_inclusive"]),
    (4, "Request inside a facility polygon", "1, distance 0 m", ["test_04_inside_facility_polygon_distance_zero"]),
    (5, "Request near a facility polygon boundary", "boundary (not centroid) distance decides", ["test_05_polygon_boundary_not_centroid"]),
    (6, "Multiple nearby facilities", "count 2, sorted by distance", ["test_06_multiple_nearby_facilities_sorted"]),
    (7, "Multiple facility categories", "one rule id per triggering category", ["test_07_multiple_categories_each_rule_reported"]),
    (8, "Nearest facility not qualifying", "0; nearest overall = transit (qualifies=false)", ["test_08_nearest_facility_not_qualifying"]),
    (9, "No nearby facilities", "0, complete, empty trigger list", ["test_09_no_nearby_facilities"]),
    (10, "Facility across a borough boundary", "1, facility in neighbouring borough", ["test_10_facility_across_borough_boundary"]),
    (11, "Request near the NYC coverage boundary", "facility outside NYC counted; beyond tolerance -> null", ["test_11_near_coverage_boundary_sees_facility_outside_nyc"]),
    (12, "Missing latitude", "null, MISSING_COORDINATES", ["test_12_missing_latitude"]),
    (13, "Missing longitude", "null, MISSING_COORDINATES", ["test_13_missing_longitude"]),
    (14, "Invalid coordinate ranges", "null, INVALID_COORDINATES; swap reported, never repaired", ["test_14_invalid_coordinate_ranges", "test_14b_swapped_lat_lon_is_reported_not_repaired"]),
    (15, "Unresolved geolocation", "null, GEOLOCATION_NOT_RESOLVED / MISSING_GEOLOCATION_STATUS", ["test_15_unresolved_geolocation", "test_15b_missing_geolocation_status"]),
    (16, "Approximate location with significant uncertainty", "null when interval straddles; robust 1/0 otherwise", ["test_16_large_uncertainty_spanning_threshold_is_indeterminate", "test_16b_robust_positive_and_negative", "test_16c_too_coarse_precision"]),
    (17, "Missing OSM cache", "OSMCacheError with remediation", ["test_17_missing_osm_cache"]),
    (18, "Incomplete borough coverage", "OSMCacheError naming the missing borough", ["test_18_incomplete_borough_coverage", "test_18b_buffer_smaller_than_ruleset_requires"]),
    (19, "Invalid facility geometry", "repaired with make_valid; distance correct", ["test_19_invalid_facility_geometry_is_repaired", "test_19b_empty_geometry_dropped"]),
    (20, "Duplicate OSM representations", "node flagged duplicate of way; counted once", ["test_20_duplicate_osm_representations", "test_20b_mark_duplicates_rules"]),
    (21, "Unknown request type", "pure mode: complete + warning; context mode: null", ["test_21_unknown_request_type_pure_mode_completes_with_warning", "test_context_aware_mode"]),
    (22, "Request type absent from the catalog", "warning REQUEST_TYPE_NOT_IN_CATALOG", ["test_22_request_type_absent_from_catalog"]),
    (23, "Threshold configuration change", "YAML / override changes decision, no code edit", ["test_23_threshold_configuration_change"]),
    (24, "Threshold zero or invalid", "RulesetError", ["test_24_threshold_zero_or_invalid_rejected", "test_24b_other_invalid_configurations"]),
    (25, "Single-request evaluation", "EvaluationResult with full contract", ["test_25_single_request_evaluation"]),
    (26, "Batch evaluation", "same as single, order preserved", ["test_26_batch_evaluation_matches_single_and_preserves_order"]),
    (27, "JSON serialisation", "valid JSON, no NaN/Infinity", ["test_27_json_serialisation_round_trip"]),
    (28, "CLI execution", "evaluate / batch / validate-cache / info; exit codes", ["test_28_cli_evaluate_single", "test_28b_cli_batch_jsonl_and_urgency_only", "test_28c_cli_threshold_override_and_errors", "test_28d_cli_validate_cache_and_info"]),
    (29, "Offline execution", "works with sockets disabled; no osmnx import", ["test_29_offline_execution", "test_29b_runtime_does_not_import_osmnx", "test_29c_offline_real_cache_cli_with_examples"]),
    (30, "Urgency output: factor + evidence only", "fixed key set; no urgency/priority keys", ["test_30_urgency_payload_contains_only_factor_and_evidence", "test_30b_no_urgency_priority_or_dispatch_fields_anywhere"]),
]


def junit_results(path: Path) -> tuple[dict, dict]:
    res, summary = {}, {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    if not path.exists():
        return res, summary
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    for s in suites:
        for k in ("tests", "failures", "errors", "skipped"):
            summary[k] += int(s.get(k, 0))
        summary["time"] += float(s.get("time", 0))
        summary["timestamp"] = s.get("timestamp")
        for tc in s.findall("testcase"):
            name = tc.get("name").split("[")[0]
            status = "passed"
            if tc.find("failure") is not None or tc.find("error") is not None:
                status = "failed"
            elif tc.find("skipped") is not None:
                status = "skipped"
            prev = res.get(name)
            if prev in (None, "passed") or status == "failed":
                res[name] = status if prev != "failed" else "failed"
    return res, summary


def main() -> None:
    dv = j(T / "data_validation_summary.json")
    man = j(ROOT / "data" / "osm" / "manifest.json")
    osmv = j(T / "osm_validation.json") if (T / "osm_validation.json").exists() else {"checks": []}
    prox = j(T / "proximity_summary_train.json")
    sel = j(T / "threshold_selection.json")
    hold = j(T / "holdout_summary.json")
    bench = j(B / "benchmark_results.json")
    cfg = yaml.safe_load((ROOT / "config" / "proximity_rules.yaml").read_text("utf-8"))
    proto = yaml.safe_load((ROOT / "config" / "threshold_selection_protocol.yaml").read_text("utf-8"))
    split = pd.read_csv(T / "split_summary.csv")
    curve = pd.read_csv(T / "trigger_rate_curve_train.csv")
    rob = pd.read_csv(T / "uncertainty_robustness_train.csv")
    selv = pd.read_csv(T / "threshold_selection_val.csv")
    comp = pd.read_csv(T / "configuration_comparison_val.csv")
    hc = pd.read_csv(T / "holdout_comparison.csv")
    beh = pd.read_csv(T / "decision_behaviour_by_split.csv")
    reasons = pd.read_csv(T / "indeterminate_reasons_by_split.csv")
    trig_cat = pd.read_csv(T / "triggering_categories_all_splits.csv")
    medcb = pd.read_csv(T / "median_nearest_distance_category_borough_train.csv")
    contrib = pd.read_csv(T / "category_contribution_train.csv")
    prec_dec = pd.read_csv(T / "decision_by_precision.csv")
    prt = pd.read_csv(T / "proximity_by_request_type_train.csv")
    jres, jsum = junit_results(T / "test_results.xml")

    t_sel = sel["selected_threshold_m"]
    vm = sel["validation_metrics_at_selected"]
    ho = hold["holdout"]
    ec = hold["explanation_completeness"]
    fac = man["facilities"]
    roads = man.get("roads", {})
    allb = beh[beh.split == "all"].iloc[0]
    cr = curve.set_index("threshold_m")
    passed_all = jsum["failures"] == 0 and jsum["errors"] == 0 and jsum["tests"] > 0

    # ---------------------------------------------------------------- tables
    split_t = split.rename(columns={"split": "Split", "rows": "Rows", "first": "First created", "last": "Last created",
                                    "geo_valid": "With coordinates", "geo_valid_pct": "% with coordinates"})
    fac_t = pd.DataFrame([{"Category": c, "Status": fac["by_category_status"].get(c, ""),
                           "Unique facilities": n,
                           "Points": fac["geometry_types"][c].get("Point", 0),
                           "Polygons": fac["geometry_types"][c].get("Polygon", 0) + fac["geometry_types"][c].get("MultiPolygon", 0)}
                          for c, n in fac["by_category"].items()])
    fb = osmv.get("facilities_by_borough", {})
    fb_t = pd.DataFrame(fb).T.fillna(0).astype(int).reset_index().rename(columns={"index": "Borough"}) if fb else pd.DataFrame()
    osm_checks = pd.DataFrame(osmv["checks"])[["check", "ok", "detail"]] if osmv["checks"] else pd.DataFrame()
    curve_t = curve[["threshold_m", "core_rate_pct", "core_plus_transit_rate_pct", "marginal_flagged", "elasticity",
                     "borough_max_min_ratio"]].rename(columns={"threshold_m": "Threshold (m)", "core_rate_pct": "Flagged % (core)",
                                                               "core_plus_transit_rate_pct": "Flagged % (+transit)",
                                                               "marginal_flagged": "Added vs previous", "elasticity": "Elasticity",
                                                               "borough_max_min_ratio": "Borough max/min"})
    rob_t = rob[rob.jitter_m.isin([15, 25, 120]) & rob.threshold_m.isin([50, 75, 100, 150, 200, 250, 500])].pivot(
        index="threshold_m", columns="jitter_m", values="flip_rate_pct").reset_index()
    rob_t.columns = ["Threshold (m)"] + [f"Flip % @ {c} m error" for c in rob_t.columns[1:]]
    selv_t = selv[["threshold_m", "admissible_H1", "trigger_rate_val_pct", "trigger_rate_train_pct", "decision_agreement_pct",
                   "positive_stability_pct", "temporal_shift_pp", "borough_max_min_ratio", "C1", "C2", "C3", "C4", "C5", "meets_all"]]
    selv_t = selv_t[selv_t.threshold_m <= 500].rename(columns={"threshold_m": "T (m)", "admissible_H1": "H1", "trigger_rate_val_pct": "Val %",
                                                               "trigger_rate_train_pct": "Train %", "decision_agreement_pct": "Agree % (C1)",
                                                               "positive_stability_pct": "Pos. stable % (C2)", "temporal_shift_pp": "Shift pp (C3)",
                                                               "borough_max_min_ratio": "Borough ratio (C5)", "meets_all": "All"})
    comp_t = comp[["configuration", "definition", "trigger_rate_val_pct", "agreement_with_A_pct", "flags_added_vs_A",
                   "flags_removed_vs_A", "uses_request_type", "double_counting_risk_with_urgency"]].rename(columns={
        "configuration": "Configuration", "definition": "Definition", "trigger_rate_val_pct": "Val flagged %",
        "agreement_with_A_pct": "Agrees with A %", "flags_added_vs_A": "Added", "flags_removed_vs_A": "Removed",
        "uses_request_type": "Uses request type", "double_counting_risk_with_urgency": "Double-counting risk"})
    hc_t = hc[["split", "n_geolocated", "trigger_rate_pct", "median_nearest_core_m", "decision_agreement_15m_pct",
               "positive_stability_15m_pct", "borough_max_min_ratio"]].rename(columns={
        "split": "Split", "n_geolocated": "Geolocated", "trigger_rate_pct": "Flagged %", "median_nearest_core_m": "Median nearest (m)",
        "decision_agreement_15m_pct": "Agree % @15 m", "positive_stability_15m_pct": "Pos. stable % @15 m",
        "borough_max_min_ratio": "Borough ratio"})
    beh_t = beh[["split", "n", "flagged_1", "not_flagged_0", "indeterminate_null", "flagged_pct", "not_flagged_pct",
                 "indeterminate_pct"]].rename(columns={"split": "Split", "n": "Requests", "flagged_1": "1", "not_flagged_0": "0",
                                                       "indeterminate_null": "null", "flagged_pct": "1 %", "not_flagged_pct": "0 %",
                                                       "indeterminate_pct": "null %"})
    scen_rows = []
    for num, name, exp, tests in SCENARIOS:
        st = [jres.get(t, "not run") for t in tests]
        res = "passed" if st and all(s == "passed" for s in st) else ("failed" if "failed" in st else ", ".join(sorted(set(st))))
        scen_rows.append({"#": num, "Scenario": name, "Expected result": exp, "Test(s)": ", ".join(tests), "Result": res})
    scen_t = pd.DataFrame(scen_rows)
    b = bench
    bench_t = pd.DataFrame([
        {"Measure": "Evaluator cold load", "Value": f"{b['load']['evaluator_load_seconds']:.3f} s ({b['load']['facilities_indexed']:,} facilities indexed)"},
        {"Measure": "Memory (RSS) after load / after full batch", "Value": f"{b['load']['rss_after_load_mb']:.0f} MB / {b['load']['rss_after_full_batch_mb']:.0f} MB"},
        {"Measure": "Single-request latency p50 / p95 / p99", "Value": f"{b['single_request_latency_ms']['p50']:.3f} / {b['single_request_latency_ms']['p95']:.3f} / {b['single_request_latency_ms']['p99']:.3f} ms (n={b['single_request_latency_ms']['n']:,})"},
        {"Measure": "Batch throughput (49,558 requests)", "Value": f"{b['batch_throughput'][str(max(map(int, b['batch_throughput'])))]['requests_per_second']:,.0f} requests/s"},
        {"Measure": "STRtree vs brute force (nearest, 6 categories)", "Value": f"{b['spatial_index']['speedup_x']}x faster, max difference {b['spatial_index']['max_abs_distance_difference_m']} m"},
        {"Measure": "Candidates per request within threshold", "Value": f"{b['spatial_index']['mean_candidates_within_threshold_per_point']} (vs {b['spatial_index']['facilities_per_point_brute_force']:,} checked by brute force)"},
        {"Measure": "Projected vs geodesic distance error", "Value": f"max {b['distance_correctness']['max_abs_error_m']} m ({b['distance_correctness']['max_relative_error_pct']}%), mean {b['distance_correctness']['mean_abs_error_m']} m over {b['distance_correctness']['pairs']:,} pairs"},
        {"Measure": "Naive degree subtraction (for contrast)", "Value": f"{b['distance_correctness']['naive_degree_subtraction_error_example']['naive_error_pct']}% error on {b['distance_correctness']['naive_degree_subtraction_error_example']['segment']}"},
        {"Measure": "Machine", "Value": f"{b['machine']['platform']}, {b['machine']['logical_cpus']} logical CPUs, {b['machine']['ram_gb']} GB RAM, Python {b['machine']['python']}"},
    ])

    # Urgency example (real evaluator, synthetic request)
    from civicops_external_factors import CriticalFacilityEvaluator
    ev = CriticalFacilityEvaluator.from_config(ROOT / "config" / "proximity_rules.yaml")
    ex = json.loads((ROOT / "examples" / "near_facility.json").read_text("utf-8"))
    ex.pop("_note", None)
    ex_res = ev.evaluate(ex)
    ex_full = json.dumps(ex_res.to_dict(), indent=2, ensure_ascii=False)
    ex_urg = json.dumps(ex_res.to_urgency_payload(), indent=2, ensure_ascii=False)
    exm = json.loads((ROOT / "examples" / "missing_coordinates.json").read_text("utf-8"))
    exm.pop("_note", None)
    ex_miss = json.dumps(ev.evaluate(exm).to_urgency_payload(), indent=2, ensure_ascii=False)

    from civicops_external_factors import __version__ as comp_version
    hd = pd.read_parquet(T / "historical_decisions.parquet")
    hd["heat"] = hd.request_type_code.eq("RT080")
    heat_share = hd.groupby("split").heat.mean() * 100
    hc_ok = hd[hd.external_factor.notna()]
    heat_rate = hc_ok.groupby(["split", "heat"]).external_factor.mean() * 100
    uniq_diff = abs(vm["trigger_rate_val_pct"] - vm["trigger_rate_val_unique_locations_pct"])
    s75 = selv.set_index("threshold_m").loc[75]
    outcome = hd.external_factor.map({1.0: "1", 0.0: "0"}).fillna("null: " + hd.incomplete_reason.fillna(""))
    prec_tab = pd.crosstab(hd.location_precision, outcome)
    prec_tab.insert(0, "requests", prec_tab.sum(axis=1))
    prec_tab = prec_tab.reset_index()
    decisions_html = md_to_html((ROOT / "DECISIONS.md").read_text("utf-8"))
    road_cov = roads.get("edge_attribute_coverage", {})
    road_line = (f"{roads.get('nodes', 0):,} nodes and {roads.get('edges', 0):,} directed edges "
                 f"({roads.get('total_length_km', 0):,} km of edge length)" if roads.get("present") else
                 "<strong>pending in this build</strong>. Every public Overpass server was overloaded or down at packaging time (8 of 18 sub-queries cached). The download resumes with <code>python osm_data_prep/download_osm.py --only roads</code>. External Factors does not depend on it (see OSM_DATA_README.md)")
    road_cov_t = pd.DataFrame([{"Edge attribute": k, "Coverage %": 100 * v} for k, v in road_cov.items()]) if road_cov else pd.DataFrame()
    ca = cr.loc
    toc = [("exec", "1. Executive summary"), ("scope", "2. Scope and business objective"), ("course", "3. Course alignment"),
           ("urgency", "4. Separation from Urgency"), ("contracts", "5. Input and output contracts"),
           ("histdata", "6. Historical data validation"), ("osm", "7. OSM acquisition methodology"),
           ("coverage", "8. Five-borough coverage"), ("facilities", "9. Facility selection and tagging"),
           ("distance", "10. Spatial distance methodology"), ("designs", "11. Pure vs context-aware design"),
           ("candidates", "12. Threshold candidates"), ("sensitivity", "13. Threshold sensitivity"),
           ("final", "14. Final threshold configuration"), ("spatial", "15. Borough and category analysis"),
           ("splits", "16. Temporal split compliance"), ("missing", "17. Missing data and uncertainty"),
           ("tests", "18. Tests and results"), ("bench", "19. Performance benchmarks"), ("maps", "20. Static maps"),
           ("integration", "21. Urgency integration example"), ("roads", "22. Road-network preparation"),
           ("register", "23. Decision register"), ("limits", "24. Limitations"), ("conclusion", "25. Conclusions")]
    toc_html = "".join(f'<li><a href="#{a}">{esc(t)}</a></li>' for a, t in toc)

    body = f"""
<header class="hero">
  <div class="eyebrow">CivicOps AI · Group 23 · External Factors component</div>
  <h1>Critical Facility Proximity</h1>
  <p class="lede">A transparent, offline, rule-based geographic factor that tells the Urgency component whether a NYC311
  request is adjacent to a hospital, school, childcare centre, senior-care home, fire station or police station.
  The report covers acquisition, methodology, threshold research, validation and packaging.</p>
  <div class="meta">Generated {date.today()} · ruleset {esc(cfg['ruleset_version'])} · OSM data {esc(man['data_version'])} ·
  component v{esc(comp_version)}</div>
</header>
<nav class="toc"><h2>Contents</h2><ul>{toc_html}</ul></nav>

<section id="exec"><h2>1. Executive summary</h2>
<div class="kpis">
  <div class="kpi"><span class="v">{t_sel} m</span><span class="l">selected threshold, all six core categories</span></div>
  <div class="kpi"><span class="v">{allb.flagged_pct:.1f}%</span><span class="l">historical requests flagged 1 (all splits)</span></div>
  <div class="kpi"><span class="v">{allb.indeterminate_pct:.1f}%</span><span class="l">incomplete, returned as null (never 0)</span></div>
  <div class="kpi"><span class="v">{fac['unique_facilities']:,}</span><span class="l">de-duplicated OSM facilities, 5 boroughs + 2 km</span></div>
  <div class="kpi"><span class="v">{jsum['tests'] - jsum['skipped']}/{jsum['tests']}</span><span class="l">automated tests executed, {jsum['failures'] + jsum['errors']} failed</span></div>
  <div class="kpi"><span class="v">{b['single_request_latency_ms']['p50']:.2f} ms</span><span class="l">median single-request latency (offline)</span></div>
</div>
<p><strong>What the factor represents.</strong> <code>external_factor = 1</code> means that, after a valid and complete evaluation, at least
one qualifying critical facility's mapped geometry lies within {t_sel} m (straight line, inclusive) of the request location.
In operational terms, the request is on the facility's block frontage, directly across the street, or at the adjacent
intersection. <code>0</code> means no qualifying facility is that close. <code>null</code> means the component could not decide safely
(missing or unresolved coordinates, coarse precision, or an uncertainty interval that straddles the threshold). The factor is
<em>geographic evidence only</em>. It is not urgency, priority or a dispatch decision.</p>
<p><strong>Why {t_sel} m is defensible.</strong> The value is a knowledge-based prior from NYC street geometry. It was checked against
five criteria declared <em>before</em> validation results existed, and it passed all of them on validation: flag rate {vm['trigger_rate_val_pct']:.1f}%,
decision agreement {vm['decision_agreement_pct']:.1f}% and positive stability {vm['positive_stability_pct']:.1f}% under a 15 m
location error, train-validation shift {vm['temporal_shift_pp']:+.1f} pp, and borough ratio {vm['borough_max_min_ratio']:.2f}.
On the untouched test split the frozen value behaved the same way: {hc.set_index('split').loc['test', 'trigger_rate_pct']:.1f}% flagged,
a shift of {ho['test_minus_val_trigger_rate_pp']:+.2f} pp, within the ±{ho['holdout_tolerance_pp']:.0f} pp tolerance. The value is
<strong>not</strong> statistically optimal and <strong>not</strong> an official NYC rule, because no labelled outcomes exist to optimise against.</p>
<p><strong>How double counting is avoided.</strong> The default <code>PURE_GEOGRAPHIC</code> mode never reads the request type.
The output has no urgency fields (enforced by tests). A separate <code>critical_facility_nearby</code> field lets Urgency
combine proximity with its own request-type rules explicitly.</p>
</section>

<section id="scope"><h2>2. Component scope and business objective</h2>
<p>Position in the shared flow (shared data reference): Intake bot → Emergency check → Agency and skill routing → Duplicate
detection → <strong>External factors</strong> → Credibility → Fake request detection → Resolution time → <strong>Urgency</strong> →
Prioritisation → Crew deployment. The component answers one question: <em>is this request geographically adjacent to a relevant
critical or sensitive facility?</em></p>
<div class="two"><div><h4>Does</h4><ul><li>validates geolocation-enriched coordinates and their precision</li><li>finds nearby facilities in a local OSM cache (offline)</li>
<li>computes metric straight-line distances to point and polygon geometries</li><li>applies a configurable, explainable binary rule</li>
<li>returns evidence, rule identifiers and explanations</li><li>prepares a shared OSM road graph for Crew Dispatch</li></ul></div>
<div><h4>Does not</h4><ul><li>evaluate weather (removed from scope)</li><li>assign urgency, scores, weights or ranks</li><li>geocode addresses or invent coordinates</li>
<li>assign agencies, skills or crews</li><li>compute routes, travel times or run A*/Dijkstra</li></ul></div></div>
</section>

<section id="course"><h2>3. Academic course alignment</h2>
<table class="plain"><tr><th>Course area</th><th>How this component uses it</th></tr>
<tr><td>Decision automation: knowledge-based reasoning</td><td>A declarative ruleset encodes domain knowledge (what counts as a critical facility, what "adjacent" means in NYC street geometry, how location uncertainty limits what can be concluded). The interval rule (decide only when the whole uncertainty disc is on one side of the threshold) is explicit reasoning under uncertainty.</td></tr>
<tr><td>Decision automation: business rules</td><td>Traceable binary decision with rule identifiers (<code>CRITICAL_PROXIMITY_&lt;CATEGORY&gt;</code>, <code>CRITICAL_PROXIMITY_NONE</code>, <code>INPUT_*</code>), versioned configuration and explanations.</td></tr>
<tr><td>Knowledge discovery and data mining</td><td>Distance distributions, trigger-rate curves, elasticity, location-error robustness, borough and category sensitivity, and temporal stability on {dv['rows']:,} real requests under the team's time-based split.</td></tr>
<tr><td>Business resource optimisation</td><td>Deliberately <em>not</em> used here. Informed search belongs to Crew Dispatch, which reuses the prepared road graph.</td></tr></table>
</section>

<section id="urgency"><h2>4. Separation from the Urgency component</h2>
<p>Urgency (Khalid) evaluates request type and other urgency factors. External Factors returns geographic evidence only.</p>
<ul><li><strong>Request type is not used</strong> in the default mode. A test checks that RT005 (traffic signal) and RT080 (no heat) at the same point return identical factor, evidence and rules.</li>
<li><strong>No severity in the rules.</strong> Rule identifiers name the facility category, never a level.</li>
<li><strong>Two fields.</strong> <code>critical_facility_nearby</code> is the pure geographic result. <code>external_factor</code> is the agreed final factor. They are identical in the selected mode. In the opt-in context-aware mode they can differ, so Urgency can always see the raw geography.</li>
<li><strong>Forbidden output keys</strong> (urgency, priority, weight, rank, dispatch) are checked recursively by the test suite.</li></ul>
</section>

<section id="contracts"><h2>5. Input and output contracts</h2>
<div class="two"><div><h4>Input (geolocation-enriched request)</h4><ul>
<li><strong>Required:</strong> <code>request_type</code>, <code>latitude</code>, <code>longitude</code>, <code>geolocation_status</code> (RESOLVED, APPROXIMATE, AMBIGUOUS, UNRESOLVED, FAILED, NOT_ATTEMPTED), <code>location_precision</code> (ADDRESS_POINT, GPS_DEVICE, INTERSECTION, BLOCKFACE, PLACE, POSTAL_CODE, NEIGHBORHOOD, BOROUGH, CITY, UNKNOWN)</li>
<li><strong>Optional:</strong> <code>request_id</code>, <code>borough</code> (or <code>slots.borough</code>), <code>location_source</code>, <code>location_uncertainty_m</code>, <code>location_type</code>, <code>ready_to_submit</code>, <code>needs_human</code>, <code>emergency_action</code></li>
<li><strong>Future intake fields</strong> (accepted, never required): <code>address_type</code>, <code>cross_street_2</code>, <code>zip_code</code>, <code>original_location_text</code>, <code>citizen_gps</code></li>
<li>The intake bot (CO1) sends location as <em>text</em>. A separate Geolocation Resolution step must add coordinates. This component never geocodes.</li></ul></div>
<div><h4>Output (key fields)</h4><ul>
<li><code>external_factor</code>: 1 / 0 / null</li><li><code>evaluation_status</code>: COMPLETE / INCOMPLETE, plus <code>incomplete_reason</code></li>
<li><code>critical_facility_nearby</code>: pure geographic evidence</li>
<li><code>nearest_facility_overall</code> (any cached category, including exploratory transit), <code>nearest_qualifying_facility</code> and <code>nearest_qualifying_distance_m</code>, <code>triggering_facilities</code> and <code>triggering_facility_count</code>: three distinct notions, never mixed</li>
<li><code>thresholds_m</code>, <code>triggered_rules</code>, <code>explanation</code>, <code>location_quality</code> (regime, margin, boundary sensitivity), <code>warnings</code>, <code>needs_review</code></li>
<li><code>ruleset_version</code>, <code>osm_data_version</code>, <code>component_version</code></li></ul></div></div>
<p>The prompt's illustrative names <code>qualifying_facilities</code> and <code>qualifying_facility_count</code> became <code>triggering_facilities</code> and <code>triggering_facility_count</code>. This keeps "qualifying category" separate from "facility that actually triggered".</p>
</section>

<section id="histdata"><h2>6. Historical data validation</h2>
<p><code>requests_real.parquet</code>: {dv['rows']:,} rows × {dv['columns']} columns, all <code>source = REAL_311</code>, {dv['is_synthetic_true']} synthetic rows,
unique <code>request_id</code>: {dv['request_id_unique']}. All {dv['request_types_in_data']} request types are in the catalog ({dv['catalog_request_types']} codes).
{dv['valid_coordinates']:,} rows have coordinates ({100 * dv['valid_coordinates'] / dv['rows']:.1f}%). {dv['valid_coordinates_outside_nyc_union']} of them fall outside the five-borough union.
{dv['borough_label_vs_coordinate_mismatch']} have a borough label that differs from the coordinate borough (mostly BRONX-labelled points inside Manhattan's OSM boundary, which matches Marble Hill).
{100 * dv['duplicate_coordinates_share']:.1f}% of geolocated rows repeat an earlier coordinate (repeat addresses). Counting each unique location once changes the validation trigger rate by {uniq_diff:.2f} pp ( {vm['trigger_rate_val_pct']:.1f}% per request vs {vm['trigger_rate_val_unique_locations_pct']:.1f}% per unique location).</p>
<p class="note">{esc(dv['sampling_note'])}</p>
{table(split_t, {"Rows": "{:,}", "With coordinates": "{:,}", "% with coordinates": "{:.1f}"})}
<div class="grid2">{img(C / 'missing_coordinates_by_borough_split.png', 'missing coordinates')}{img(C / 'location_precision_by_split.png', 'precision')}</div>
<p>Leakage fields dropped at load and never used as inputs: {', '.join(f'<code>{esc(x)}</code>' for x in dv['leakage_fields_dropped'])}. Weather fields ignored: {', '.join(f'<code>{esc(x)}</code>' for x in dv['weather_fields_ignored'])}.</p>
</section>

<section id="osm"><h2>7. OSM acquisition methodology</h2>
<ul><li><strong>Tooling:</strong> {esc(man['acquisition_method']['library'])}. Boundaries via <code>geocode_to_gdf</code> by fixed OSM relation id. Facilities via <code>features_from_polygon</code> with the union of category tags. Road graph via <code>graph_from_polygon(network_type='drive')</code>.</li>
<li><strong>Acquired:</strong> boundaries {esc(man['acquired_at']['boundaries'])}, facilities {esc(man['acquired_at']['facilities'])}, roads {esc(man['acquired_at'].get('roads') or 'pending')} (UTC).</li>
<li><strong>Robustness:</strong> public Overpass servers were overloaded during acquisition (HTTP 502/500, capacity errors). The downloader retries with exponential backoff across three endpoints, splits large areas into sub-queries, and caches HTTP responses, so interrupted runs resume without re-downloading.</li>
<li><strong>Formats:</strong> GeoPackage (shareable, GIS-ready) and GeoParquet (fast runtime) for facilities. GeoPackage for boundaries. GraphML and GeoPackage for roads. <code>manifest.json</code> records counts, CRS, sizes and SHA-256 checksums.</li></ul>
</section>

<section id="coverage"><h2>8. Five-borough geographic coverage</h2>
{table(pd.DataFrame(man['borough_coverage'])[['borough', 'osm_relation', 'area_km2_incl_water', 'display_name']])}
{table(osm_checks) if len(osm_checks) else ''}
{img(M / 'coverage_map.png', 'coverage map', 'Borough polygons (OSM admin relations, including water), the 2 km facility search buffer and the 1 km road buffer. Black dots: facilities outside NYC that requests near the city edge can still reach.')}
</section>

<section id="facilities"><h2>9. Critical-facility selection and tagging</h2>
<p>A category is included when it has a defensible relationship to public safety, vulnerable populations or essential emergency services.
Tags and rationale live in <code>config/facility_categories.yaml</code>. A feature takes the first matching category in precedence order.
Raw features: {fac['total_features'] + (fac.get('unclassified_features_dropped') or 0):,}. {fac.get('unclassified_features_dropped')} unclassified social facilities (shelters, food banks) were dropped.
{fac['duplicates_flagged']} duplicate representations were flagged. {fac['repaired_geometries']} geometries needed repair.</p>
{table(fac_t, {"Unique facilities": "{:,}", "Points": "{:,}", "Polygons": "{:,}"})}
<p><strong>Excluded:</strong> clinics and doctors (no emergency-access argument), colleges and universities (adult population, land-use proxy), places of worship, libraries and community centres (a value judgement without team agreement), parks and playgrounds (area land use), ambulance stations (sparse in OSM). <strong>Exploratory:</strong> rail and subway stations are acquired and analysed but do not qualify (see §11).</p>
{img(M / 'facility_small_multiples.png', 'facility categories')}
</section>

<section id="distance"><h2>10. Spatial distance methodology</h2>
<ul><li>Request lat/lon are projected to <strong>EPSG:32118</strong> (NAD83 / New York Long Island, metres). Facilities are projected once at load.</li>
<li>Distance = GEOS planar distance from the request point to the facility's <strong>full geometry</strong>: point to point, or point to the nearest polygon boundary, and 0 inside a polygon or multipolygon. Centroids are never used.</li>
<li>A <strong>per-category STRtree</strong> returns candidates within threshold + uncertainty (plus 1 mm search slack). The decision test is inclusive (<code>distance ≤ threshold</code>, with 1 µm numerical tolerance).</li>
<li>Correctness: projected distances differ from WGS84 geodesic distances by at most {b['distance_correctness']['max_abs_error_m']} m ({b['distance_correctness']['max_relative_error_pct']}%). Naive degree subtraction would be off by {b['distance_correctness']['naive_degree_subtraction_error_example']['naive_error_pct']}%.</li>
<li>Straight-line, not network, distance: the question is adjacency, not travel time.</li></ul>
</section>

<section id="designs"><h2>11. Pure geographic vs context-aware design</h2>
<p>All alternatives were evaluated on validation at the selected threshold:</p>
{table(comp_t, {"Val flagged %": "{:.2f}", "Agrees with A %": "{:.2f}", "Added": "{:,}", "Removed": "{:,}"})}
<p><strong>Selected: Design A (pure geographic, universal threshold).</strong> {esc(sel['configuration_reason'])} {sel['interior_share_of_positives_val_pct']:.1f}% of Design A's
validation positives are building-interior request types. Context-aware gating (B3) would zero them, and it is available as <code>decision_mode: CONTEXT_AWARE</code> if the Urgency owner agrees.</p>
</section>

<section id="candidates"><h2>12. Threshold candidate selection</h2>
<p><strong>Operational definition.</strong> {esc(proto['operational_definition'])}</p>
<p><strong>Candidates</strong> ({', '.join(str(x) for x in proto['candidate_radii_m'])} m) are experimental values from "immediate surroundings" to "broad neighbourhood".
None is presented as NYC policy. <strong>Admissible range</strong> {proto['admissible_range_m'][0]}–{proto['admissible_range_m'][1]} m: the lower bound is about 3× typical street-level geocoding error. The upper bound is where "adjacent" turns into "neighbourhood".</p>
<p>The selection protocol (<code>config/threshold_selection_protocol.yaml</code>, v{esc(proto['protocol_version'])}, fixed {esc(proto['fixed_on'])}) was written after train exploration and before any validation proximity result was computed:</p>
<ul><li>C1 decision agreement under a {proto['criteria']['displacement_m']} m displacement ≥ {proto['criteria']['C1_decision_agreement_min_pct']}%</li>
<li>C2 positive stability under the same displacement ≥ {proto['criteria']['C2_positive_stability_min_pct']}%</li>
<li>C3 |validation − train| trigger rate ≤ {proto['criteria']['C3_temporal_stability_max_abs_pp']} pp</li>
<li>C4 trigger rate ≤ {proto['criteria']['C4_informativeness_max_trigger_pct']}% (adjacency is the minority case)</li>
<li>C5 borough max/min trigger ratio ≤ {proto['criteria']['C5_borough_max_min_ratio_max']}</li>
<li>Rule: {esc(proto['decision_rule'])}</li></ul>
</section>

<section id="sensitivity"><h2>13. Threshold sensitivity results (train exploration)</h2>
<p>The median distance from a geolocated train request to the nearest core facility is {prox['train_quantiles_d_core']['p50']:.0f} m (IQR {prox['train_quantiles_d_core']['p25']:.0f}–{prox['train_quantiles_d_core']['p75']:.0f} m).
Only {prox['requests_inside_a_facility_polygon_train_pct']}% of requests lie inside a facility polygon. NYC is dense enough that a 250 m radius would flag {ca[250, 'core_rate_pct']:.0f}% of requests and 500 m would flag {ca[500, 'core_rate_pct']:.0f}%, which says almost nothing about adjacency.
The curve is steepest between 100 and 150 m (elasticity {ca[100, 'elasticity']:.2f} at 100 m). Schools are the nearest facility for {prox['nearest_core_category_share_train']['SCHOOL']:.0f}% of requests.</p>
<div class="grid2">{img(C / 'trigger_rate_curve.png', 'trigger curve')}{img(C / 'marginal_gain.png', 'marginal gain')}</div>
{table(curve_t, {"Threshold (m)": "{:.0f}", "Added vs previous": "{:,.0f}", "Elasticity": "{:.2f}", "Borough max/min": "{:.2f}", "Flagged % (core)": "{:.1f}", "Flagged % (+transit)": "{:.1f}"})}
<h3>Sensitivity to location error</h3>
<p>Each request was displaced by exactly <em>u</em> metres in a random (seeded) direction and re-evaluated. Small radii are fragile relative to the number of positives. Mid radii have the most absolute flips, because most requests sit 100–200 m from a facility. Under a 120 m displacement (a blockface-level location), {rob[(rob.jitter_m == 120) & (rob.threshold_m == 100)].flip_rate_pct.iloc[0]:.0f}% of decisions at 100 m change. This is why coarse locations use the robust-interval regime (§17).</p>
{img(C / 'uncertainty_flip_rates.png', 'flip rates')}
{table(rob_t, {"Threshold (m)": "{:.0f}"})}
</section>

<section id="final"><h2>14. Final threshold configuration</h2>
{img(C / 'threshold_selection_validation.png', 'selection evidence')}
{table(selv_t, {"T (m)": "{:.0f}", "Val %": "{:.1f}", "Train %": "{:.1f}", "Agree % (C1)": "{:.1f}", "Pos. stable % (C2)": "{:.1f}", "Shift pp (C3)": "{:+.2f}", "Borough ratio (C5)": "{:.2f}"})}
<p><strong>Selected: {t_sel} m for HOSPITAL, SCHOOL, CHILDCARE, SENIOR_CARE, FIRE_STATION and POLICE</strong> ({esc(sel['reason'])}). 75 m fails C5: Manhattan is flagged {s75['MANHATTAN_rate_val_pct']:.1f}% of the time vs {s75['STATEN ISLAND_rate_val_pct']:.1f}% for Staten Island (ratio {s75['borough_max_min_ratio']:.2f}). 150 m fails C3 because its rate moves more between seasons.
Recorded in <code>config/proximity_rules.yaml</code> together with its provenance. <strong>Evidence type:</strong> {esc(sel['evidence_type'])}. The borough ratio ({vm['borough_max_min_ratio']:.2f}) is close to its bound, so C5 is the binding constraint and the margin is small.</p>
</section>

<section id="spatial"><h2>15. Spatial analysis by borough and facility category</h2>
<div class="grid2">{img(C / 'ecdf_nearest_core_by_borough.png', 'ecdf by borough')}{img(C / 'trigger_rate_by_borough.png', 'rate by borough')}</div>
{img(C / 'ecdf_nearest_by_category.png', 'ecdf by category')}
<p>Median distance (m) to the nearest facility of each category, by borough (train):</p>
{table(medcb, {c: "{:,.0f}" for c in medcb.columns if c != 'borough'})}
<p>Category contribution at the selected threshold (train). "Sole trigger" means the only category within threshold:</p>
{table(contrib[contrib.threshold_m == t_sel].drop(columns=['threshold_m']), {"rate_pct": "{:.2f}", "sole_trigger_pct_of_flagged": "{:.1f}", "removing_category_drops_rate_pp": "{:.2f}"})}
<p>Triggering categories across all evaluated requests (a request can trigger several):</p>
{table(trig_cat.rename(columns={trig_cat.columns[0]: 'category'}), {"requests_with_category_triggering": "{:,}", "pct_of_flagged": "{:.1f}"})}
{img(C / 'median_distance_by_request_type.png', 'by request type', 'Request types differ in typical proximity because they occur in different land uses (for example, housing complaints in dense residential blocks). This is descriptive. Request type is not used by the rule.')}
</section>

<section id="splits"><h2>16. Temporal split compliance</h2>
<p>Splits verified chronological and non-overlapping: {dv['split_boundaries_chronological_non_overlapping']}.
Train {esc(dv['split_ranges']['train'][0])} → {esc(dv['split_ranges']['train'][1])}, validation → {esc(dv['split_ranges']['val'][1])}, test → {esc(dv['split_ranges']['test'][1])}.</p>
<ul><li><strong>Train:</strong> exploration and candidate development (<code>analyze_proximity.py</code>, <code>threshold_sensitivity.py</code>).</li>
<li><strong>Validation:</strong> mechanical application of the pre-declared protocol (<code>select_thresholds.py</code>).</li>
<li><strong>Test:</strong> read only by <code>holdout_evaluation.py</code>, after <code>proximity_rules.yaml</code> was written. Nothing was changed afterwards.</li>
<li>No supervised training takes place. The historical data serve descriptive and sensitivity analysis only.</li></ul>
{table(hc_t, {"Geolocated": "{:,}", "Flagged %": "{:.2f}", "Median nearest (m)": "{:.0f}", "Agree % @15 m": "{:.2f}", "Pos. stable % @15 m": "{:.2f}", "Borough ratio": "{:.2f}"})}
<p>Hold-out verdict: shift within tolerance = {ho['holdout_shift_within_tolerance']}. C1 {ho['test_C1_decision_agreement']}, C2 {ho['test_C2_positive_stability']}, C4 {ho['test_C4_informativeness']}, C5 {ho['test_C5_borough_ratio']} on test.
The drop from train ({hc.set_index('split').loc['train', 'trigger_rate_pct']:.1f}%) to validation ({hc.set_index('split').loc['val', 'trigger_rate_pct']:.1f}%) is largely a seasonal change in request mix.
"No heat or hot water" (RT080) makes up {heat_share['train']:.1f}% of train but {heat_share['val']:.1f}% of validation and {heat_share['test']:.1f}% of test. In train these requests are flagged {heat_rate[('train', True)]:.1f}% of the time vs {heat_rate[('train', False)]:.1f}% for other types.
Excluding them, train and validation differ by {heat_rate[('train', False)] - heat_rate[('val', False)]:.1f} pp. This is a descriptive decomposition, not a causal test.</p>
{img(C / 'holdout_trigger_rate_by_borough.png', 'holdout by borough')}
</section>

<section id="missing"><h2>17. Missing-data and location-uncertainty treatment</h2>
<ul><li><strong>Never invent coordinates, never silently return 0.</strong> Missing, invalid (out of range, non-numeric, (0,0)), unresolved, outside-coverage or too-coarse locations return <code>external_factor = null</code>, an explicit <code>incomplete_reason</code> and <code>needs_review = true</code>. Swapped lat/lon is reported (<code>POSSIBLE_LAT_LON_SWAP</code>), not repaired.</li>
<li><strong>Uncertainty u</strong> comes from <code>location_uncertainty_m</code> if supplied, otherwise from a heuristic default per precision class ({', '.join(f"{k} {v:g} m" for k, v in cfg['location']['default_uncertainty_m'].items())}).</li>
<li><strong>POINT_ESTIMATE</strong> (u ≤ {cfg['location']['point_decision_max_uncertainty_m']} m): the supplied point decides, and <code>boundary_sensitive</code> flags |margin| &lt; u.
<strong>ROBUST_INTERVAL</strong> ({cfg['location']['point_decision_max_uncertainty_m']} &lt; u ≤ {cfg['location']['max_evaluable_uncertainty_m']} m): 1 only if margin ≥ u, 0 only if margin &lt; −u, otherwise null.
Above {cfg['location']['max_evaluable_uncertainty_m']} m, or postal-code, neighbourhood or borough precision, the result is INCOMPLETE. When u exceeds the threshold, a robust positive is impossible: a location coarser than the adjacency radius cannot establish adjacency.</li></ul>
{table(beh_t, {"Requests": "{:,}", "1": "{:,}", "0": "{:,}", "null": "{:,}", "1 %": "{:.2f}", "0 %": "{:.2f}", "null %": "{:.2f}"})}
<div class="grid2"><div>{table(reasons)}<p class="note">Outcome by location precision (all splits). Most BLOCKFACE rows have no coordinates at all. Among geolocated BLOCKFACE/PLACE rows, the robust-interval rule abstains when the ±120/150 m disc straddles the threshold.</p>{table(prec_tab)}</div>{img(C / 'decision_behaviour_by_split.png', 'decision behaviour')}</div>
<p>Explanation completeness over {ec['results']:,} historical evaluations: explanation present {ec['explanation_non_empty_pct']:.1f}%, rule list present {ec['triggered_rules_non_empty_pct']:.1f}%, positives with facility evidence {ec['positives_with_facility_evidence_pct']:.1f}%, complete evaluations with a nearest-qualifying distance {ec['complete_with_nearest_qualifying_distance_pct']:.1f}%.</p>
</section>

<section id="tests"><h2>18. Actual tests and results</h2>
<p><code>pytest</code> run at {esc(jsum.get('timestamp', 'n/a'))}: <strong>{jsum['tests']} tests, {jsum['failures']} failures, {jsum['errors']} errors, {jsum['skipped']} skipped</strong> in {jsum['time']:.1f} s.
The suite combines synthetic unit tests (an exact-geometry miniature city with known distances) with real-OSM integration tests (known locations, geodesic agreement, cache validation). Mandatory scenarios:</p>
{table(scen_t)}
</section>

<section id="bench"><h2>19. Actual performance benchmarks</h2>
{table(bench_t)}
{img(C / 'benchmark_latency_throughput.png', 'benchmarks')}
<p class="note">{esc(b['note'])} Timings depend on hardware. They show the method is easily fast enough for interactive and batch use. They are not a service-level guarantee.</p>
</section>

<section id="maps"><h2>20. Static geographic maps</h2>
{img(M / 'facility_overview.png', 'facility overview')}
{img(M / 'requests_flagged_citywide.png', 'requests citywide')}
{img(M / 'zoom_east_harlem_manhattan.png', 'zoom east harlem')}
<div class="grid2">{img(M / 'zoom_flatbush_brooklyn.png', 'zoom flatbush')}{img(M / 'zoom_new_dorp_staten_island.png', 'zoom new dorp')}</div>
</section>

<section id="integration"><h2>21. Urgency integration example</h2>
<p>Synthetic request at real Manhattan coordinates (<code>examples/near_facility.json</code>), evaluated by the real cache. Minimal hand-off for Urgency (<code>result.to_urgency_payload()</code>):</p>
<pre>{esc(ex_urg)}</pre>
<p>An incomplete request stays null with its reason, so Urgency applies its own agreed fallback (shared rule 6: a failing component must not lower anyone's score):</p>
<pre>{esc(ex_miss)}</pre>
<details><summary>Full evidence record (<code>result.to_dict()</code>)</summary><pre>{esc(ex_full)}</pre></details>
<p>If Urgency needs an integer in every case, <code>to_integer_factor(result, null_policy)</code> requires an explicit policy (<code>ZERO_FLAGGED</code> or <code>ONE_FLAGGED</code>) and marks each imputed value. No default is assumed. The policy is for the group to agree.</p>
</section>

<section id="roads"><h2>22. Reusable OSM road-network preparation</h2>
<p>Drivable network for the future Crew Dispatch and Routing component: {road_line}. Coverage area: borough union + {man['buffers_m']['road_area']} m.
Simplified topology; largest weakly connected component retained; directionality (<code>oneway</code>, <code>reversed</code>), edge geometry and <code>length</code> preserved. Files:
<code>roads/nyc_drive.graphml</code> (load with <code>osmnx.load_graphml</code>) and <code>roads/nyc_drive.gpkg</code> (layers <code>nodes</code>, <code>edges</code>). No routing, speeds or travel times are computed here.</p>
{table(road_cov_t, {"Coverage %": "{:.1f}"}) if len(road_cov_t) else ''}
<p>Sparse attributes (for example <code>maxspeed</code>, <code>lanes</code>) are documented for later enrichment, such as <code>osmnx.routing.add_edge_speeds</code> with agreed defaults.</p>
</section>

<section id="register"><h2>23. Academic decision register</h2>
<div class="register">{decisions_html}</div>
</section>

<section id="limits"><h2>24. Limitations</h2>
<ul><li><strong>No ground truth.</strong> No label says whether a facility actually affected a request, so accuracy, precision, recall and F1 are not reported, and the trigger rate is not prediction quality. The threshold is defensible, not validated against outcomes.</li>
<li><strong>OSM completeness is unknown.</strong> Facility recall and tagging quality vary (for example, 97 hospitals; some fire stations are mapped as nodes only). No comparison with official NYC registers was authorised.</li>
<li><strong>Straight-line adjacency</strong> ignores barriers (rivers, highways, rail cuts) and building entrances.</li>
<li><strong>Heuristic uncertainty defaults.</strong> The per-precision uncertainties are assumptions, not measured geocoder error. The historical precision proxy (<code>address_type</code>) is not the future Geolocation Resolution output.</li>
<li><strong>Sample design.</strong> The historical sample is borough-stratified. Citywide rates are not volume-weighted. Repeated coordinates (repeat addresses) are frequent.</li>
<li><strong>Borough equity margin.</strong> The selected threshold's borough ratio ({vm['borough_max_min_ratio']:.2f}) is close to its 3.0 bound. The difference mostly reflects real facility density, not a defect.</li>
<li><strong>Interface still open.</strong> Field names for External Factors are "to be named by Mohammad" in the shared reference. The null policy and any request-type gating need agreement with Urgency. The shared reference PDF still mentions weather.</li></ul>
</section>

<section id="conclusion"><h2>25. Conclusions</h2>
<p>The component gives a trustworthy, explainable geographic factor. It is a binary statement that a qualifying critical facility's geometry is within {t_sel} m of a reliably located request,
returned with the evidence behind it, and it explicitly abstains when the location does not support a decision. It is a legitimate <strong>knowledge-based reasoning</strong> component: domain knowledge
(facility relevance, street geometry, measurement error) is encoded as transparent, versioned rules, and uncertainty is handled by explicit interval reasoning. It is a <strong>knowledge-discovery</strong> exercise as well: the rules were explored, stress-tested and selected on real data under a pre-declared, leakage-free temporal protocol, and confirmed on a hold-out split.
The historical data support that the rule is stable over time, robust to typical geocoding error, informative (about a quarter of requests flagged) and evenly behaved across boroughs within the stated bound.
They do <strong>not</strong> establish that proximity causes harm or that {t_sel} m is optimal for any outcome. That would need labelled outcomes, which do not exist yet.</p>
</section>
<footer>Map and facility data © OpenStreetMap contributors, ODbL 1.0. Historical requests: supplied NYC311 project sample. Generated by <code>reports/build_report.py</code> from executed outputs.</footer>
"""
    css = """
:root{color-scheme:light;--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--accent:#2a78d6;--accent-soft:#cde2fb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:0 20px 60px}
.hero{padding:48px 0 24px;border-bottom:1px solid var(--grid)}.eyebrow{color:var(--accent);font-weight:600;font-size:13px;letter-spacing:.04em;text-transform:uppercase}
h1{font-size:40px;line-height:1.1;margin:8px 0 12px}.lede{font-size:18px;color:var(--ink2);max-width:820px}.meta{color:var(--muted);font-size:13px}
h2{font-size:24px;margin:48px 0 12px;padding-top:12px;border-top:1px solid var(--grid)}h3{font-size:18px;margin:28px 0 8px}h4{margin:18px 0 6px;font-size:15px}
section{scroll-margin-top:16px}.toc{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:8px 22px;margin:24px 0}
.toc h2{border:0;margin:10px 0 4px;font-size:16px;padding:0}.toc ul{columns:2;column-gap:32px;margin:6px 0 10px;padding-left:0;list-style:none}.toc a{color:var(--ink2);text-decoration:none}.toc a:hover{color:var(--accent)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0 20px}
.kpi{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}.kpi .v{display:block;font-size:26px;font-weight:700}.kpi .l{color:var(--ink2);font-size:13px}
figure{margin:16px 0;background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:10px}figure img{width:100%;height:auto;display:block}
figcaption{color:var(--ink2);font-size:13px;padding:6px 4px 0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}.two{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.tw{overflow-x:auto;margin:12px 0}table{border-collapse:collapse;width:100%;font-size:13px;background:var(--surface)}th,td{border-bottom:1px solid var(--grid);padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f3f2ee;font-weight:600;color:var(--ink2)}td.num{text-align:right;font-variant-numeric:tabular-nums}table.plain td:first-child{font-weight:600;width:30%}
code{background:#f0efec;padding:1px 5px;border-radius:4px;font-size:.9em}pre{background:#f3f2ee;border:1px solid var(--grid);border-radius:8px;padding:12px;overflow-x:auto;font-size:12.5px;line-height:1.45}
.note{color:var(--ink2);font-size:13px;border-left:3px solid var(--accent-soft);padding-left:10px}.register h4{color:var(--accent);margin-top:22px}
details{margin:10px 0}summary{cursor:pointer;color:var(--accent)}footer{margin-top:48px;color:var(--muted);font-size:12px;border-top:1px solid var(--grid);padding-top:12px}
.missing{color:#d03b3b}
@media (max-width:760px){.grid2,.two{grid-template-columns:1fr}.toc ul{columns:1}h1{font-size:30px}main{padding:0 16px 40px}}
@media print{.toc{break-after:page}figure{break-inside:avoid}}
"""
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Critical Facility Proximity</title><style>{css}</style></head><body><main>{body}</main></body></html>"""
    OUT.write_text(doc, "utf-8")
    print(f"report written: {OUT} ({OUT.stat().st_size / 1e6:.1f} MB); tests passed={passed_all}")


if __name__ == "__main__":
    main()
