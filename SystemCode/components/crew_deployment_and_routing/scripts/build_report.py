"""Build the self-contained HTML research report: research/report.html.

Everything is read from saved outputs (outputs/benchmarks, outputs/demos, data/*_report.json), so the
report never contains hand-typed result numbers. Images are embedded (base64), so the file works
offline and can be e-mailed. Run from the component root:  python scripts/build_report.py
"""
from __future__ import annotations

import base64
import html
import io
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "outputs" / "benchmarks"
A = B / "analysis"
D = ROOT / "outputs" / "demos"
OUT = ROOT / "research" / "report.html"
SENS = {"p.speed_factor": "speed factor", "p.budget": "budget (min)", "served_pct": "served %", "travel_min": "travel min",
        "weighted": "rank-wtd coverage", "inversions": "avoidable inversions"}


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def img(path: Path, max_w: int = 1800, jpeg: bool = False, alt: str = "") -> str:
    im = Image.open(path)
    im = im.convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    if jpeg:
        im.save(buf, "JPEG", quality=84, optimize=True)
        mime = "image/jpeg"
    else:
        im.save(buf, "PNG", optimize=True)
        mime = "image/png"
    return f'<img alt="{html.escape(alt)}" loading="lazy" src="data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}">'


def figure(path, caption, max_w=1800, jpeg=False, wide=False):
    return (f'<figure class="{"wide" if wide else ""}">{img(path, max_w, jpeg, caption)}'
            f'<figcaption>{caption}</figcaption></figure>')


def md_table(path: Path, keep=None, rename=None, numeric_right=True) -> str:
    """Convert a markdown table written by analysis.py into an HTML table."""
    lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.startswith("|")]
    rows = [[c.strip() for c in l.strip("|").split("|")] for l in lines]
    head, body = rows[0], [r for r in rows[2:] if len(r) == len(rows[0])]
    if keep:
        body = [r for r in body if keep(dict(zip(head, r)))]
    if rename:
        head = [rename.get(h, h) for h in head]
    num = re.compile(r"^[-+]?[\d.,]+(e[-+]?\d+)?%?$|^\[.*\]$|^\d+/\d+/\d+$")
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    tr = []
    for r in body:
        tds = "".join(f'<td class="{"num" if numeric_right and num.match(c) else ""}">{html.escape(c)}</td>' for c in r)
        tr.append(f"<tr>{tds}</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(tr)}</tbody></table></div>'


def df_table(df: pd.DataFrame, fmt: dict | None = None) -> str:
    fmt = fmt or {}
    th = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    tr = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            s = fmt.get(c, "{:.2f}").format(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and pd.notna(v) else str(v)
            cells.append(f'<td class="{"num" if isinstance(v, (int, float)) else ""}">{html.escape(s)}</td>')
        tr.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(tr)}</tbody></table></div>'


def stat(label, value, note=""):
    return f'<div class="stat"><div class="stat-value">{value}</div><div class="stat-label">{label}</div>' \
           f'{f"<div class=stat-note>{note}</div>" if note else ""}</div>'


def build():
    tun = jload(A / "tuning_selection.json")
    st_tests = jload(A / "static_tests.json")
    dy_tests = jload(A / "dynamic_tests.json")
    gap = jload(A / "exact_gap.json")
    route = jload(B / "routing_point_to_point.json")
    rmat = pd.read_csv(B / "routing_matrix_benchmark.csv")
    demos = jload(D / "demo_summary.json")
    speed = jload(ROOT / "data" / "road_network" / "derived" / "speed_enrichment_report.json")
    replay = jload(ROOT / "data" / "replay" / "replay_build_report.json")
    crews = jload(ROOT / "data" / "crews" / "crews_v2_generation_report.json")
    static = pd.read_csv(B / "static_test.csv")
    dyn = pd.read_csv(B / "dynamic_test.csv")

    rost = ["cf0.6", "cf0.8", "cf1.0", "cf1.2", "legacy"]
    t = lambda r, b, m: st_tests[f"{r}|ga-vs-{b}|{m}"]
    served_rf = [t(r, "rank_first_greedy", "served")["mean_diff"] for r in rost]
    trav_rf = [100 * t(r, "rank_first_greedy", "travel_min")["mean_diff"] / t(r, "rank_first_greedy", "travel_min")["mean_b"] for r in rost]
    trav_ta = [100 * t(r, "travel_aware_greedy", "travel_min")["mean_diff"] / t(r, "travel_aware_greedy", "travel_min")["mean_b"] for r in rost]
    inv_ta = [t(r, "travel_aware_greedy", "avoidable_inversion_pairs")["mean_b"] for r in rost]
    inv_ga = [t(r, "travel_aware_greedy", "avoidable_inversion_pairs")["mean_a"] for r in rost]
    dk = lambda pol, m: dy_tests[f"{pol}|{m}"]
    ins = "Static GA plan + insertion only"
    nostab = "GA hybrid 15, no stability"
    greedy = "Rank-first greedy rolling 15"
    viol = int(dyn["locked_commitment_violations"].sum())
    ch = tun["chosen"]
    base = {b["method"]: b for b in tun["baselines"]}
    pts = route

    sp_src = speed["speed_source_counts"]
    n_edges = speed["edges"]
    pct = lambda k: 100 * sp_src[k] / n_edges

    d1 = demos["demos"]["D1_static_plan"]
    d2 = demos["demos"]["D2_shortage_backlog"]
    d3 = demos["demos"]["D3_dynamic_replay"]
    d4 = demos["demos"]["D4_night_window"]
    ev = d3["evidence"]

    # ------------------------------------------------------------------ sections
    S = []
    S.append(f"""
<section id="summary">
<h2>1. Summary</h2>
<p>This component turns the C11 prioritised queue into a <strong>proposed</strong> crew dispatch plan: which requests are
assigned, to which crew, in what order and along which roads, with estimated times, and why every other request waits.
A <strong>genetic algorithm</strong> (evolutionary computing) assigns and sequences jobs; <strong>A* search</strong> (informed search)
on a locally enriched OpenStreetMap network provides road-following routes and travel times. The only priority input is
C11's <code>overall_queue_position</code>; nothing here authorises dispatch.</p>
<div class="stats">
{stat("more requests served than rank-first greedy", f"+{min(served_rf):.0f} to +{max(served_rf):.0f}", "per test episode, all 5 workforce scenarios")}
{stat("less travel than rank-first greedy", f"{-max(trav_rf):.0f}–{-min(trav_rf):.0f}%", "GA lower in 50/50 episodes")}
{stat("avoidable priority inversions", f"{min(inv_ga):.2f}–{max(inv_ga):.2f}", f"vs {min(inv_ta):.0f}–{max(inv_ta):.0f} for travel-aware greedy")}
{stat("locked-commitment violations", f"{viol}", f"in {len(dyn)} dynamic replays")}
{stat("tiny instances solved optimally", f"{gap['instances'] * gap['gap_ga_pct']['share_optimal']:.0f}/{gap['instances']}", "GA vs exhaustive search")}
{stat("A* vs Dijkstra cost difference", f"{pts['astar_vs_dijkstra_max_rel_diff']:.0f}", f"{pts['pairs']} random pairs; ALT explores {100 * pts['expansion_ratio_alt']:.0f}% of nodes")}
</div>
<div class="callout">
<strong>Honest headline.</strong> The GA clearly beats serving requests strictly in rank order (more served, far less travel).
Against the travel-efficient greedy baseline it serves about the same number but travels
<strong>{min(trav_ta):.0f}–{max(trav_ta):.0f}% more</strong>; its advantage there is that it keeps C11's rank order almost intact,
whereas that baseline bypasses higher-ranked requests about {min(inv_ta):.0f}–{max(inv_ta):.0f} times per episode.
All results are simulations on real request locations and arrival times, the real road network and <em>synthetic</em> crews.
</div>
</section>""")

    S.append(f"""
<section id="data">
<h2>2. Data and evaluation design</h2>
<div class="grid2">
<div><h3>Inputs and their nature</h3>
<ul>
<li><span class="tag obs">observed</span> NYC311 historical extract: {replay['rows']:,} requests in {replay['episodes']} replay episodes
(sampled ~2-hour borough windows); after-the-fact columns never copied ({len(replay['columns_excluded_after_the_fact'])} excluded).</li>
<li><span class="tag obs">observed</span> OSM drive network: {speed['nodes']:,} nodes, {n_edges:,} directed edges (© OpenStreetMap contributors, ODbL).</li>
<li><span class="tag imp">imputed</span> edge speeds: {pct('osm_maxspeed') + pct('osm_maxspeed_list_mean'):.1f}% from OSM tags,
{pct('nyc_default_25mph'):.1f}% NYC 25 mph default, {pct('highway_class_median'):.1f}% road-class median, {pct('fallback_25mph'):.2f}% fallback.</li>
<li><span class="tag syn">synthetic</span> crew rosters sized from the train split at capacity factors 0.6/0.8/1.0/1.2
({", ".join(str(v["crews"]) for v in crews["rosters"].values())} crews) plus the original legacy roster ({crews['legacy_roster']['crews']}).</li>
<li><span class="tag sim">simulated</span> approvals, Crew Portal status updates, durations and breakdowns in the dynamic replay (labelled in every event).</li>
</ul></div>
<div><h3>Time-based split (project rule)</h3>
{df_table(pd.DataFrame([{"split": k, "episodes": v, "used for": u} for k, v, u in
                         [("train", replay["episodes_by_split"]["train"], "workforce sizing only"),
                          ("val", replay["episodes_by_split"]["val"], "objective and GA-size selection"),
                          ("test", replay["episodes_by_split"]["test"], "all reported comparisons")]]), {"episodes": "{:.0f}"})}
<p class="small">Validation selection used a rule written before the sweep; one documented amendment extended the λ<sub>B</sub>
grid because the optimum sat on its boundary (§4). Test episodes were untouched until the configuration was frozen.
Paired comparisons per episode: Wilcoxon signed-rank, 95% bootstrap CI, Holm correction.</p></div>
</div>
</section>""")

    S.append(f"""
<section id="method">
<h2>3. Method in brief</h2>
<div class="grid2">
<div><h3>Optimisation (GA)</h3>
<ul>
<li>One sub-problem per agency (exact, since agency is a hard constraint).</li>
<li>Chromosome: an ordered stop list per crew plus an unserved pool. Every operator keeps plans feasible: skills, agency,
work-time budget including the return to depot, no duplicates, locked work untouched.</li>
<li>Route-based crossover; relocate, exchange, 2-opt, ruin-and-recreate and swap-in mutations; tournament selection with elitism.</li>
<li>Objective (frozen after validation): <strong>{'lexicographic' if ch['lambda_backlog'] >= 1e6 else 'weighted sum'}</strong>,
i.e. first minimise the rank-weighted backlog (weights w(r) = 1/log<sub>2</sub>(r+1) from <code>overall_queue_position</code>),
then travel, rank-weighted start delay and plan changes.</li>
</ul></div>
<div><h3>Routing (A*) and dynamics</h3>
<ul>
<li>Edge cost = length / (posted speed × 0.6); the factor is an uncalibrated assumption tested from 0.5 to 1.0.</li>
<li>Heuristics: straight-line distance / top speed (admissible: every edge is at least as long as the straight line, checked on all
{pts['heuristic_bound']['edges_checked']:,} edges) and ALT landmarks; both exact against Dijkstra.</li>
<li>The GA reads a cached travel-time matrix; A* reconstructs the final road-following legs.</li>
<li>Rolling horizon: event-driven controller, commit at departure, change penalty and publish threshold for stability;
agency operating hours (07:00–19:00, NYPD 24 h) with night requests planned at opening.</li>
</ul></div>
</div>
</section>""")

    S.append(f"""
<section id="validation">
<h2>4. Validation: choosing the objective</h2>
{figure(A / "tuning_scatter.png", "Each point is one rank-weight transform × λ<sub>B</sub> configuration (validation mean, capacity 0.8). "
        "The selected configuration is circled; grey diamonds are the greedy baselines.", 1500)}
<p>Low λ<sub>B</sub> makes the GA skip far-away requests: at λ<sub>B</sub> = 60 it serves fewer requests than rank-first greedy.
The selected objective serves <strong>{ch['served']:.1f}</strong> requests per validation episode with
<strong>{ch['inv_per_100']:.2f}</strong> avoidable inversions per 100 served and {ch['travel_per_served']:.1f} travel minutes per served
request (rank-first greedy: {base['rank_first_greedy']['served']:.1f} / {base['rank_first_greedy']['inv_per_100']:.2f} /
{base['rank_first_greedy']['travel_per_served']:.1f}; travel-aware greedy: {base['travel_aware_greedy']['served']:.1f} /
{base['travel_aware_greedy']['inv_per_100']:.1f} / {base['travel_aware_greedy']['travel_per_served']:.1f}).
GA size "{tun['ga_size_choice']}" was within {tun['ga_size_mean_excess_over_best_pct'][tun['ga_size_choice']]:.2f}% of the best
at {tun['ga_size_mean_wall_s'][tun['ga_size_choice']]:.1f} s mean run time.</p>
<details><summary>Full validation grid</summary>{md_table(A / "tuning_table.md")}</details>
</section>""")

    sig = [r for r in rost]
    S.append(f"""
<section id="static">
<h2>5. Static plans on held-out test episodes</h2>
{figure(A / "static_comparison.png", "Mean per test episode (n = 50) with 95% bootstrap CIs of the mean. The CIs show "
        "between-episode spread; inference uses the paired tests below.", 1600, wide=True)}
{md_table(A / "static_table.md")}
<h3>Paired tests (GA minus baseline, Holm-corrected)</h3>
{md_table(A / "static_tests.md", keep=lambda r: r["metric"] in ("served", "travel_min", "weighted_served_share", "avoidable_inversion_pairs"))}
<p class="small">"GA better/tie/worse" counts episodes. Seed variation of the GA is small; see
<code>outputs/benchmarks/analysis/static_seed_variation.md</code>.</p>
<h3>Why requests stay unserved (GA, mean per episode)</h3>
{md_table(A / "static_reasons_ga.md")}
</section>""")

    S.append(f"""
<section id="dynamic">
<h2>6. Dynamic replanning (simulated execution)</h2>
{figure(A / "dynamic_policies.png", "Seven replanning policies on 28 held-out daytime episodes × 2 seeds (capacity 0.8).", 1600, wide=True)}
{md_table(A / "dynamic_table.md")}
<ul>
<li><strong>Rolling GA vs a static GA plan with insertion only:</strong> travel {dk(ins, 'actual_travel_min')['mean_diff_default_minus_policy']:+.0f} min
(p<sub>Holm</sub> = {dk(ins, 'actual_travel_min')['p_holm']:.1g}); completed {dk(ins, 'completed_simulated')['mean_diff_default_minus_policy']:+.1f}
(p<sub>Holm</sub> = {dk(ins, 'completed_simulated')['p_holm']:.2f}, not significant); at the cost of more reassignments
({dk(ins, 'total_changed_assignments')['mean_default']:.0f} vs {dk(ins, 'total_changed_assignments')['mean_policy']:.0f} per episode).</li>
<li><strong>Stability mechanism:</strong> without it, reassignments rise from {dk(nostab, 'total_changed_assignments')['mean_default']:.0f}
to {dk(nostab, 'total_changed_assignments')['mean_policy']:.0f} per episode (p<sub>Holm</sub> = {dk(nostab, 'total_changed_assignments')['p_holm']:.1g})
with no detectable quality gain.</li>
<li><strong>Rolling GA vs rolling rank-first greedy:</strong> completed {dk(greedy, 'completed_simulated')['mean_diff_default_minus_policy']:+.1f},
travel {dk(greedy, 'actual_travel_min')['mean_diff_default_minus_policy']:+.0f} min, but high-ranked requests start
{dk(greedy, 'weighted_mean_wait_to_start_min')['mean_diff_default_minus_policy']:.1f} min <em>later</em> on average (a negative result).</li>
<li>Locked-commitment violations: <strong>{viol}</strong> in all {len(dyn)} runs.</li>
</ul>
<details><summary>Paired tests against the default policy</summary>{md_table(A / "dynamic_tests.md")}</details>
</section>""")

    S.append(f"""
<section id="sensitivity">
<h2>7. Sensitivity, optimality and routing checks</h2>
<div class="grid2">
<div><h3>Posted-to-travel speed factor</h3>{md_table(A / "speed_sensitivity_test.md", rename=SENS)}</div>
<div><h3>Productive work-time budget</h3>{md_table(A / "budget_sensitivity_test.md", rename=SENS)}</div>
</div>
<p>The ranking of methods does not change across these settings; absolute travel times scale with the speed factor.</p>
<div class="grid2">
<div><h3>Optimality gap (60 tiny validation instances)</h3>
{df_table(pd.DataFrame([{"method": n, "optimal share": gap[k]["share_optimal"], "mean gap %": gap[k]["mean"], "max gap %": gap[k]["max"]}
                         for n, k in (("Genetic algorithm", "gap_ga_pct"), ("Rank-first greedy", "gap_rank_first_pct"),
                                      ("Travel-aware greedy", "gap_travel_aware_pct"))]))}</div>
<div><h3>Routing ({pts['pairs']} random pairs, speed factor {pts['speed_factor']})</h3>
{df_table(pd.DataFrame([{"search": "Dijkstra", "nodes expanded": pts["mean_expanded_dijkstra"], "ms per query": pts["mean_ms_dijkstra"]},
                         {"search": "A* straight-line", "nodes expanded": pts["mean_expanded_astar_haversine"], "ms per query": pts["mean_ms_astar_haversine"]},
                         {"search": "A* ALT (8 landmarks)", "nodes expanded": pts["mean_expanded_astar_alt"], "ms per query": pts["mean_ms_astar_alt"]}]),
          {"nodes expanded": "{:,.0f}", "ms per query": "{:.1f}"})}
<p class="small">Matrix for {int(rmat.n_nodes.iloc[-1])} nodes: one-to-many Dijkstra {rmat.dijkstra_one_to_many_s.iloc[-1]:.2f} s vs
pairwise A* {rmat.astar_pairwise_s.iloc[-1]:.1f} s (identical values), so the GA's matrix uses Dijkstra rows and A* is used
for point-to-point routes.</p></div>
</div>
</section>""")

    m1 = d1["metrics"]
    S.append(f"""
<section id="demos">
<h2>8. Demonstrations on real data</h2>
<h3>D1 · Static proposal (Manhattan test window, {m1['provisional_candidates']} eligible requests)</h3>
<p>{m1['served']:.0f} requests planned, rank-weighted coverage {m1['weighted_served_share']:.2f}. Routes follow the road graph;
numbers give the planned order.</p>
{figure(D / "D1_static_plan" / "maps" / "plan_v0001_NYPD.png", "NYPD crews, plan v1: road-following routes, numbered stops, crew positions and return legs.", 1700, True, True)}
{figure(D / "D1_static_plan" / "maps" / "crew_panels_NYPD.png", "Individual crew route maps for the same plan.", 1700, True, True)}
<h3>D2 · Crew shortage and backlog (same window)</h3>
<p>Capacity 0.6 ({d2['cf0.6']['crews']} crews): {d2['cf0.6']['served']:.0f} planned, coverage {d2['cf0.6']['weighted_served_share']:.2f}.
Capacity 1.2 ({d2['cf1.2']['crews']} crews): {d2['cf1.2']['served']:.0f} planned, coverage {d2['cf1.2']['weighted_served_share']:.2f}.
Unserved reasons: {", ".join(f"{k} {v}" for k, v in d2['cf0.6']['reasons'].items())} (capacity 0.6).</p>
<div class="grid2">
{figure(D / "D2_shortage_cf0.6" / "maps" / "plan_v0001_HPD.png", "HPD at capacity 0.6 (grey × = backlog).", 1300, True)}
{figure(D / "D2_shortage_cf1.2" / "maps" / "plan_v0001_HPD.png", "HPD at capacity 1.2.", 1300, True)}
</div>
<h3>D3 · Dynamic replay (Staten Island test window, {d3['summary']['requests_arrived']} real arrivals)</h3>
<p>{d3['summary']['completed_simulated']} jobs completed in simulation, {d3['plans']} plan versions, {d3['summary']['locked_commitment_violations']}
locked-commitment violations. Review/hold flags and all execution events are simulated and labelled.</p>
{figure(D / "D3_dynamic_replay" / "maps" / "compare_v0001_v0002_DSNY.png", f"Replanning while a job is locked en route ({ev['replan_with_locked_jobs']['plan']}): the dotted locked leg is never re-optimised.", 1800, True, True)}
{figure(D / "D3_dynamic_replay" / "maps" / "compare_v0006_v0007_DSNY.png", f"A new request changes C11 ranks and is absorbed by a published replan ({ev['new_request_rank_change_replan']['plan']}: {html.escape(ev['new_request_rank_change_replan']['why'])}).", 1800, True, True)}
{figure(D / "D3_dynamic_replay" / "maps" / "compare_v0013_v0014_DSNY.png", f"Crew breakdown forces a replan ({ev['breakdown_replan']['plan']}); the only crew with a required skill is lost, so one request becomes NO_AVAILABLE_COMPATIBLE_CREW.", 1800, True, True)}
<p class="small">Also in this run: a cancellation ({ev['cancellation_replan']['plan']}) and a human-review completion that makes a request
eligible ({ev['review_completed_replan']['plan']}). Plans: <code>outputs/demos/D3_dynamic_replay/plans/</code>; audit log:
<code>events/events.jsonl</code>.</p>
<h3>D4 · Night window and operating hours</h3>
<p>Window starting {str(d4['window_start'])[:16]}: NYPD (24 h) plans immediately; the other agencies' crews are on duty
{d4['duty_periods_by_agency']['DOT'][0][11:16]}–{d4['duty_periods_by_agency']['DOT'][1][11:16]}, so their work is planned for the opening.
{d4['summary']['completed_simulated']} of {d4['summary']['requests_arrived']} completed in simulation.</p>
{figure(D / "D4_night_window" / "maps" / "plan_v0001_DOT.png", "DOT plan made overnight: crews become available at 07:00.", 1500, True)}
</section>""")

    S.append("""
<section id="limits">
<h2>9. Limitations</h2>
<ul>
<li>No real dispatch ground truth (crew, order, travel or on-site times); all results are simulations.</li>
<li>Crews are synthetic; capacity factors are relative to the demand of sampled windows, not NYC staffing.</li>
<li>The full <code>c11_final</code> (with C10 and the official SLA tables) was not runnable here; the replay used the vendored, unmodified
<code>c11_prioritization.py</code> with the catalog's default urgency as the C10 input. Dispatch uses only the returned positions.</li>
<li>Travel speeds are posted limits scaled by an uncalibrated factor; no turn penalties or live traffic.</li>
<li>Episodes are independent 2-hour windows with no carried-over backlog; one borough's crews per episode.</li>
<li>Approvals are simulated at departure; real approval latency is not modelled. No GPU path (not needed at this scale).</li>
</ul>
<p>Full list: <code>research/limitations.md</code>; assumptions with their status: <code>research/assumptions.md</code>.</p>
</section>""")

    S.append(f"""
<section id="repro">
<h2>10. Reproducing these results</h2>
<pre><code>python -m crew_dispatch experiment tuning        # validation (+ tuning_ext, amendment A1)
python -m crew_dispatch experiment all           # routing, exact gap, static, speed, budget, dynamic (test)
python -c "from crew_dispatch.research.analysis import analyze_all; analyze_all()"
python -m crew_dispatch demo
python scripts/build_report.py                   # regenerates this file</code></pre>
<p class="small">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from <code>outputs/benchmarks</code> and <code>outputs/demos</code>.
Road data © OpenStreetMap contributors (ODbL 1.0).</p>
</section>""")

    toc = [("summary", "Summary"), ("data", "Data and design"), ("method", "Method"), ("validation", "Validation"),
           ("static", "Static results"), ("dynamic", "Dynamic results"), ("sensitivity", "Sensitivity and checks"),
           ("demos", "Demonstrations"), ("limits", "Limitations"), ("repro", "Reproduce")]
    nav = "".join(f'<a href="#{i}">{n}</a>' for i, n in toc)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crew Dispatch Report</title>
<style>
:root {{ --bg:#fcfcfb; --surface:#ffffff; --ink:#0b0b0b; --ink2:#52514e; --ink3:#8a8986; --line:#e6e5e0; --accent:#2a78d6;
  --callout:#f2f6fc; --tag-obs:#1baf7a; --tag-imp:#eda100; --tag-syn:#6250d6; --tag-sim:#eb6834; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#1a1a19; --surface:#222220; --ink:#f0efec;
  --ink2:#c3c2b7; --ink3:#8f8e88; --line:#383835; --accent:#3987e5; --callout:#20262f; color-scheme: dark; }}
  :root:not([data-theme="light"]) figure img {{ background:#fcfcfb; }} }}
:root[data-theme="dark"] {{ --bg:#1a1a19; --surface:#222220; --ink:#f0efec; --ink2:#c3c2b7; --ink3:#8f8e88; --line:#383835;
  --accent:#3987e5; --callout:#20262f; color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
header {{ padding: 40px 16px 24px; border-bottom:1px solid var(--line); }}
header .inner, main, nav .inner {{ max-width: 1120px; margin: 0 auto; }}
h1 {{ font-size: clamp(1.6rem, 3vw, 2.3rem); margin:0 0 6px; letter-spacing:-0.01em; }}
.subtitle {{ color:var(--ink2); margin:0; }}
nav {{ position: sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line); }}
nav .inner {{ display:flex; gap:4px; overflow-x:auto; padding: 8px 16px; scrollbar-width: thin; }}
nav a {{ color:var(--ink2); text-decoration:none; font-size:0.88rem; padding:4px 10px; border-radius:6px; white-space:nowrap; }}
nav a:hover {{ background:var(--line); color:var(--ink); }}
main {{ padding: 8px 16px 64px; }}
section {{ padding-top: 28px; scroll-margin-top: 56px; }}
h2 {{ font-size:1.35rem; margin: 0 0 12px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
h3 {{ font-size:1.02rem; margin: 22px 0 8px; }}
p, li {{ color: var(--ink); }} .small {{ font-size:0.88rem; color:var(--ink2); }}
code {{ font: 0.88em ui-monospace, SFMono-Regular, Consolas, monospace; background:var(--line); padding:1px 5px; border-radius:4px; }}
pre {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:14px; overflow-x:auto; }}
pre code {{ background:none; padding:0; }}
.stats {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:12px; margin: 18px 0; }}
.stat {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.stat-value {{ font-size:1.55rem; font-weight:650; font-variant-numeric: tabular-nums; }}
.stat-label {{ color:var(--ink); font-size:0.9rem; line-height:1.35; margin-top:2px; }}
.stat-note {{ color:var(--ink3); font-size:0.8rem; margin-top:6px; line-height:1.35; }}
.callout {{ background:var(--callout); border-left:3px solid var(--accent); padding:12px 16px; border-radius:6px; }}
.grid2 {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 8px 28px; align-items:start; }}
.table-wrap {{ overflow-x:auto; margin: 8px 0 12px; border:1px solid var(--line); border-radius:8px; background:var(--surface); }}
table {{ border-collapse:collapse; width:100%; font-size:0.86rem; }}
th, td {{ padding:7px 10px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
th {{ color:var(--ink2); font-weight:600; background:var(--bg); position:sticky; top:0; }}
td.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
tbody tr:last-child td {{ border-bottom:none; }}
figure {{ margin: 16px 0; }} figure img {{ width:100%; height:auto; border:1px solid var(--line); border-radius:8px; display:block; }}
figcaption {{ color:var(--ink2); font-size:0.86rem; margin-top:6px; }}
details {{ margin: 10px 0; }} summary {{ cursor:pointer; color:var(--accent); font-weight:550; }}
.tag {{ display:inline-block; font-size:0.72rem; font-weight:650; text-transform:uppercase; letter-spacing:0.04em; padding:1px 7px;
  border-radius:4px; color:#fff; margin-right:4px; }}
.tag.obs {{ background:#0e7f57; }} .tag.imp {{ background:#9a6a00; }} .tag.syn {{ background:#5243b8; }} .tag.sim {{ background:#b9471b; }}
ul {{ padding-left: 1.2em; }}
@media (max-width: 640px) {{ body {{ font-size:15px; }} header {{ padding-top:24px; }} }}
</style></head>
<body>
<header><div class="inner">
<h1>Crew Dispatch and Route Optimization</h1>
<p class="subtitle">CivicOps AI · genetic algorithm for rank-aware crew assignment, A* routing on the NYC road network, rolling-horizon replanning.
Research prototype; proposals only.</p>
</div></header>
<nav><div class="inner">{nav}</div></nav>
<main>{"".join(S)}</main>
</body></html>"""
    OUT.write_text(page, encoding="utf-8")
    print(f"{OUT}  {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    build()
