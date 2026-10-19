"""Analysis of experiment CSVs -> tables (markdown), statistics and figures (``outputs/benchmarks/analysis``)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import wilcoxon  # noqa: E402

from ..config import COMPONENT_ROOT

B = COMPONENT_ROOT / "outputs" / "benchmarks"
A = B / "analysis"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
METHOD_LABEL = {"rank_first_greedy": "Rank-first greedy", "travel_aware_greedy": "Travel-aware greedy", "ga": "Genetic algorithm"}
METHOD_COLOR = {"rank_first_greedy": SERIES[0], "travel_aware_greedy": SERIES[1], "ga": SERIES[2]}
ROSTER_ORDER = ["cf0.6", "cf0.8", "cf1.0", "cf1.2", "legacy"]


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def bootstrap_ci(x, n=10_000, seed=0):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def holm(pvals: dict) -> dict:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[k] = running
    return out


def paired(df: pd.DataFrame, metric: str, a: str, b: str, key=("episode",)) -> dict:
    """Paired comparison a - b per episode (GA averaged over seeds)."""
    pa = df[df["method"] == a].groupby(list(key))[metric].mean()
    pb = df[df["method"] == b].groupby(list(key))[metric].mean()
    j = pd.concat([pa, pb], axis=1, keys=["a", "b"]).dropna()
    d = (j["a"] - j["b"]).values
    p = float(wilcoxon(d, zero_method="zsplit").pvalue) if len(d) >= 6 and np.any(d != 0) else 1.0
    lo, hi = bootstrap_ci(d)
    return {"n": int(len(d)), "mean_a": float(j["a"].mean()), "mean_b": float(j["b"].mean()), "mean_diff": float(d.mean()),
            "ci95": [lo, hi], "wins_a": int((d > 1e-9).sum()), "ties": int((np.abs(d) <= 1e-9).sum()),
            "wins_b": int((d < -1e-9).sum()), "p_wilcoxon": p}


def _md(df: pd.DataFrame, floatfmt="{:.2f}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append(floatfmt.format(v) if isinstance(v, (float, np.floating)) and np.isfinite(v) else str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------- tuning (validation)
def analyze_tuning() -> dict:
    df = pd.read_csv(B / "tuning_val.csv")
    if (B / "tuning_val_ext.csv").exists():      # protocol amendment A1 (validation only)
        df = pd.concat([df, pd.read_csv(B / "tuning_val_ext.csv")], ignore_index=True)
    df = df[df.get("error").isna()] if "error" in df else df
    df["inv_per_100"] = 100 * df["avoidable_inversion_pairs"] / df["served"].clip(lower=1)
    w = df[df["tag"] == "weights"].copy()
    w["config"] = w["p.transform"] + "(" + w["p.param"].astype(str) + "), λB=" + w["p.lambda_backlog"].map(lambda v: "lex" if v >= 1e6 else str(int(v)))
    g = w.groupby("config").agg(transform=("p.transform", "first"), param=("p.param", "first"),
                                lambda_backlog=("p.lambda_backlog", "first"), served=("served", "mean"),
                                inv_per_100=("inv_per_100", "mean"), travel_per_served=("travel_per_served_min", "mean"),
                                weighted_share=("weighted_served_share", "mean"), runs=("served", "size")).reset_index()
    ok = g[g["inv_per_100"] <= 2.0]
    rule = "constraint satisfied"
    if ok.empty:
        ok, rule = g.nsmallest(1, "inv_per_100"), "no configuration met the inversion constraint: lowest inversion rate"
        chosen = ok.iloc[0]
    else:
        best = ok["served"].max()
        tied = ok[ok["served"] >= 0.99 * best]
        chosen = tied.nsmallest(1, "travel_per_served").iloc[0]
    base = df[df["tag"] == "baseline"].groupby("method").agg(served=("served", "mean"), inv_per_100=("inv_per_100", "mean"),
                                                            travel_per_served=("travel_per_served_min", "mean")).reset_index()
    s = df[df["tag"] == "ga_size"]
    sz = s.groupby(["p.ga_size", "episode"])["fitness"].mean().unstack(0)
    rel = (sz.div(sz.min(axis=1), axis=0) - 1).mean() * 100
    rt = s.groupby("p.ga_size")["wall_s"].mean()
    best_mean = sz.mean().min()
    order = ["small", "default", "large"]
    ga_choice = next(n for n in order if n in sz and sz[n].mean() <= best_mean * 1.005)
    out = {"rule": rule, "chosen": {"transform": chosen["transform"], "param": float(chosen["param"]),
                                    "lambda_backlog": float(chosen["lambda_backlog"]), "served": float(chosen["served"]),
                                    "inv_per_100": float(chosen["inv_per_100"]), "travel_per_served": float(chosen["travel_per_served"])},
           "ga_size_choice": ga_choice, "ga_size_mean_fitness": sz.mean().to_dict(),
           "ga_size_mean_excess_over_best_pct": rel.to_dict(), "ga_size_mean_wall_s": rt.to_dict(),
           "baselines": base.to_dict("records"), "grid": g.sort_values("served", ascending=False).to_dict("records")}
    A.mkdir(parents=True, exist_ok=True)
    (A / "tuning_selection.json").write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    tab = g.sort_values(["inv_per_100"])[["config", "served", "inv_per_100", "travel_per_served", "weighted_share"]]
    (A / "tuning_table.md").write_text(_md(tab) + "\n\nBaselines (validation, cf0.8):\n\n" + _md(base) + "\n", encoding="utf-8")
    # figure: served vs inversion rate per configuration
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    _style(ax)
    for i, tr in enumerate(["reciprocal", "exponential", "linear", "log"]):
        sub = g[g["transform"] == tr]
        ax.scatter(sub["inv_per_100"], sub["served"], s=46, color=SERIES[i], edgecolor=SURFACE, lw=1.5, label=tr, zorder=3)
    for _, r in base.iterrows():
        ax.scatter([r["inv_per_100"]], [r["served"]], marker="D", s=50, color=INK2, zorder=3)
        ax.annotate(METHOD_LABEL[r["method"]], (r["inv_per_100"], r["served"]), xytext=(6, -10), textcoords="offset points", fontsize=8, color=INK2)
    ax.scatter([chosen["inv_per_100"]], [chosen["served"]], s=180, facecolors="none", edgecolors=INK, lw=1.4, zorder=4)
    ax.annotate("selected", (chosen["inv_per_100"], chosen["served"]), xytext=(8, 6), textcoords="offset points", fontsize=8.5, color=INK)
    ax.axvline(2.0, color=INK2, lw=1, ls=(0, (3, 3)))
    ax.text(2.05, ax.get_ylim()[0], " integrity limit", fontsize=8, color=INK2, va="bottom")
    ax.set_xlabel("Avoidable priority inversions per 100 served (validation mean)", fontsize=9.5, color=INK)
    ax.set_ylabel("Requests served per episode", fontsize=9.5, color=INK)
    ax.set_title("Validation: rank-weight transform × λB (GA, capacity factor 0.8)", loc="left", fontsize=11, color=INK)
    ax.legend(title="GA weight transform", fontsize=8, title_fontsize=8.5, frameon=False, loc="lower right")
    fig.savefig(A / "tuning_scatter.png", dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------- static (test)
STATIC_METRICS = [("served", "Requests served"), ("weighted_served_share", "Rank-weighted coverage"),
                  ("travel_min", "Travel minutes"), ("travel_per_served_min", "Travel per served (min)"),
                  ("avoidable_inversion_pairs", "Avoidable inversions"), ("bypassed_requests", "Bypassed higher-ranked requests"),
                  ("median_rank_served", "Median rank served")]


def analyze_static() -> dict:
    df = pd.read_csv(B / "static_test.csv")
    df = df[df.get("error").isna()] if "error" in df else df
    rows, tests = [], {}
    for ro in ROSTER_ORDER:
        sub = df[df["roster"] == ro]
        if sub.empty:
            continue
        for m in ("rank_first_greedy", "travel_aware_greedy", "ga"):
            s = sub[sub["method"] == m]
            per_ep = s.groupby("episode").mean(numeric_only=True)
            rows.append({"roster": ro, "method": METHOD_LABEL[m], "crews (mean)": per_ep["crews"].mean(),
                         "served": per_ep["served"].mean(), "served % of candidates": per_ep["served_pct_of_candidates"].mean(),
                         "rank-weighted coverage": per_ep["weighted_served_share"].mean(),
                         "travel min": per_ep["travel_min"].mean(), "travel/served": per_ep["travel_per_served_min"].mean(),
                         "avoidable inversions": per_ep["avoidable_inversion_pairs"].mean(),
                         "runtime s": per_ep["runtime_s_optimiser"].mean()})
        for b in ("rank_first_greedy", "travel_aware_greedy"):
            for metric, _ in STATIC_METRICS[:6]:
                tests[f"{ro}|ga-vs-{b}|{metric}"] = paired(sub, metric, "ga", b)
    adj = holm({k: v["p_wilcoxon"] for k, v in tests.items()})
    for k in tests:
        tests[k]["p_holm"] = adj[k]
    table = pd.DataFrame(rows)
    seed = df[df["method"] == "ga"].groupby(["roster", "episode"]).agg(sd_served=("served", "std"), sd_travel=("travel_min", "std"),
                                                                      sd_fit_rel=("fitness", lambda x: float(np.std(x) / np.mean(x))))
    seed_summary = seed.groupby("roster").mean().reindex(ROSTER_ORDER).dropna(how="all")
    reasons = df[[c for c in df.columns if c.startswith("reason.")] + ["roster", "method"]].fillna(0)
    reason_tab = reasons[reasons["method"] == "ga"].groupby("roster").mean(numeric_only=True).reindex(ROSTER_ORDER).dropna(how="all")
    A.mkdir(parents=True, exist_ok=True)
    tt = pd.DataFrame([{"comparison": k.split("|")[1], "roster": k.split("|")[0], "metric": k.split("|")[2],
                        "mean GA": v["mean_a"], "mean baseline": v["mean_b"], "mean diff": v["mean_diff"],
                        "95% CI": f"[{v['ci95'][0]:.2f}, {v['ci95'][1]:.2f}]", "GA better/tie/worse": None,
                        "p (Holm)": v["p_holm"], "n": v["n"], "_wins": (v["wins_a"], v["ties"], v["wins_b"])} for k, v in tests.items()])
    lower_better = {"travel_min", "travel_per_served_min", "avoidable_inversion_pairs", "bypassed_requests"}
    tt["GA better/tie/worse"] = [f"{w[2]}/{w[1]}/{w[0]}" if m in lower_better else f"{w[0]}/{w[1]}/{w[2]}"
                                 for m, w in zip(tt["metric"], tt["_wins"])]
    tt = tt.drop(columns="_wins")
    (A / "static_table.md").write_text(_md(table) + "\n", encoding="utf-8")
    (A / "static_tests.md").write_text(_md(tt, "{:.3g}") + "\n", encoding="utf-8")
    (A / "static_seed_variation.md").write_text(_md(seed_summary.reset_index(), "{:.3f}") + "\n", encoding="utf-8")
    (A / "static_reasons_ga.md").write_text(_md(reason_tab.reset_index(), "{:.2f}") + "\n", encoding="utf-8")
    (A / "static_tests.json").write_text(json.dumps(tests, indent=2), encoding="utf-8")
    _static_figure(df)
    return {"table": rows, "tests": tests}


def _static_figure(df):
    metrics = [("served_pct_of_candidates", "Served (% of eligible candidates)"), ("travel_per_served_min", "Travel minutes per served request"),
               ("avoidable_inversion_pairs", "Avoidable priority inversions"), ("weighted_served_share", "Rank-weighted coverage")]
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.2))
    rosters = [r for r in ROSTER_ORDER if r in set(df["roster"])]
    x = np.arange(len(rosters))
    for ax, (m, title) in zip(axes.flat, metrics):
        _style(ax)
        for j, meth in enumerate(("rank_first_greedy", "travel_aware_greedy", "ga")):
            vals, los, his = [], [], []
            for ro in rosters:
                per = df[(df["roster"] == ro) & (df["method"] == meth)].groupby("episode")[m].mean()
                vals.append(per.mean())
                lo, hi = bootstrap_ci(per.values, n=2000)
                los.append(per.mean() - lo)
                his.append(hi - per.mean())
            off = (j - 1) * 0.22
            ax.errorbar(x + off, vals, yerr=[los, his], fmt="o", ms=7, color=METHOD_COLOR[meth], ecolor=METHOD_COLOR[meth],
                        elinewidth=1.4, capsize=0, mec=SURFACE, mew=1.5, label=METHOD_LABEL[meth])
        ax.set_xticks(x)
        ax.set_xticklabels([r.replace("cf", "capacity ") if r != "legacy" else "legacy roster" for r in rosters], fontsize=8.5)
        ax.set_title(title, loc="left", fontsize=10, color=INK)
    axes[0, 0].legend(frameon=False, fontsize=8.5, loc="lower right")
    fig.suptitle("Held-out test episodes (n=50): mean per episode with 95% bootstrap CI", x=0.01, ha="left", fontsize=12, color=INK)
    fig.patch.set_facecolor(SURFACE)
    fig.savefig(A / "static_comparison.png", dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------- dynamic (test)
DYN_METRICS = ["completed_simulated", "completed_pct", "weighted_completed_share", "weighted_mean_wait_to_start_min",
               "actual_travel_min", "overtime_min", "plans_published", "total_changed_assignments",
               "mean_changed_assignments_per_publish", "optimiser_seconds", "locked_commitment_violations"]


def analyze_dynamic() -> dict:
    df = pd.read_csv(B / "dynamic_test.csv")
    df = df[df.get("error").isna()] if "error" in df else df
    per = df.groupby(["policy", "episode"])[DYN_METRICS].mean().reset_index()
    tab = per.groupby("policy")[DYN_METRICS].mean().reset_index()
    default = "GA hybrid 15 min (default)"
    tests = {}
    for pol in tab["policy"]:
        if pol == default:
            continue
        for m in ("completed_simulated", "weighted_completed_share", "weighted_mean_wait_to_start_min", "actual_travel_min",
                  "total_changed_assignments"):
            j = per[per["policy"].isin([default, pol])].pivot(index="episode", columns="policy", values=m).dropna()
            d = (j[default] - j[pol]).values
            p = float(wilcoxon(d, zero_method="zsplit").pvalue) if len(d) >= 6 and np.any(d != 0) else 1.0
            tests[f"{pol}|{m}"] = {"n": int(len(d)), "mean_default": float(j[default].mean()), "mean_policy": float(j[pol].mean()),
                                   "mean_diff_default_minus_policy": float(d.mean()), "ci95": bootstrap_ci(d), "p": p}
    adj = holm({k: v["p"] for k, v in tests.items()})
    for k in tests:
        tests[k]["p_holm"] = adj[k]
    A.mkdir(parents=True, exist_ok=True)
    show = tab.rename(columns={"completed_simulated": "completed", "completed_pct": "completed %",
                               "weighted_completed_share": "rank-wtd completed", "weighted_mean_wait_to_start_min": "rank-wtd wait (min)",
                               "actual_travel_min": "travel min", "overtime_min": "overtime min", "plans_published": "plans published",
                               "total_changed_assignments": "changed assignments", "mean_changed_assignments_per_publish": "changes/publish",
                               "optimiser_seconds": "optimiser s", "locked_commitment_violations": "locked violations"})
    (A / "dynamic_table.md").write_text(_md(show) + "\n", encoding="utf-8")
    tt = pd.DataFrame([{"policy": k.split("|")[0], "metric": k.split("|")[1], "default": v["mean_default"], "policy mean": v["mean_policy"],
                        "diff (default-policy)": v["mean_diff_default_minus_policy"], "95% CI": f"[{v['ci95'][0]:.2f}, {v['ci95'][1]:.2f}]",
                        "p (Holm)": v["p_holm"], "n": v["n"]} for k, v in tests.items()])
    (A / "dynamic_tests.md").write_text(_md(tt, "{:.3g}") + "\n", encoding="utf-8")
    (A / "dynamic_tests.json").write_text(json.dumps(tests, indent=2), encoding="utf-8")
    # figure: quality vs computation and churn
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    for ax in axes:
        _style(ax)
    for i, (_, r) in enumerate(tab.iterrows()):
        c = SERIES[i % len(SERIES)]
        axes[0].scatter(r["optimiser_seconds"], r["weighted_completed_share"], s=60, color=c, edgecolor=SURFACE, lw=1.5, zorder=3)
        axes[0].annotate(r["policy"], (r["optimiser_seconds"], r["weighted_completed_share"]), xytext=(6, 4),
                         textcoords="offset points", fontsize=7.8, color=INK)
        axes[1].scatter(r["total_changed_assignments"], r["weighted_completed_share"], s=60, color=c, edgecolor=SURFACE, lw=1.5, zorder=3)
        axes[1].annotate(r["policy"], (r["total_changed_assignments"], r["weighted_completed_share"]), xytext=(6, 4),
                         textcoords="offset points", fontsize=7.8, color=INK)
    axes[0].set_xlabel("Optimiser seconds per episode", fontsize=9.5)
    axes[1].set_xlabel("Changed assignments per episode (plan churn)", fontsize=9.5)
    for ax in axes:
        ax.set_ylabel("Rank-weighted completed share (simulated)", fontsize=9.5)
    fig.suptitle("Dynamic replay on held-out daytime test episodes (capacity factor 0.8; simulated execution)",
                 x=0.01, ha="left", fontsize=12, color=INK)
    fig.patch.set_facecolor(SURFACE)
    fig.savefig(A / "dynamic_policies.png", dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return {"table": tab.to_dict("records"), "tests": tests}


# ---------------------------------------------------------------------- sensitivity, exact gap, routing
def analyze_sensitivity() -> dict:
    out = {}
    for name, param in (("speed_sensitivity_test", "p.speed_factor"), ("budget_sensitivity_test", "p.budget")):
        f = B / f"{name}.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f)
        df = df[df.get("error").isna()] if "error" in df else df
        t = df.groupby([param, "method"]).agg(served=("served", "mean"), served_pct=("served_pct_of_candidates", "mean"),
                                              travel_min=("travel_min", "mean"), weighted=("weighted_served_share", "mean"),
                                              inversions=("avoidable_inversion_pairs", "mean")).reset_index()
        t["method"] = t["method"].map(METHOD_LABEL)
        (A / f"{name}.md").write_text(_md(t) + "\n", encoding="utf-8")
        out[name] = t.to_dict("records")
    f = B / "exact_gap_val.csv"
    if f.exists():
        df = pd.read_csv(f)
        g = {c: {"mean": float(df[c].mean()), "median": float(df[c].median()), "max": float(df[c].max()),
                 "share_optimal": float((df[c] < 1e-6).mean())} for c in ("gap_ga_pct", "gap_rank_first_pct", "gap_travel_aware_pct")}
        g["instances"] = int(len(df))
        (A / "exact_gap.json").write_text(json.dumps(g, indent=2), encoding="utf-8")
        out["exact_gap"] = g
    return out


def analyze_all():
    A.mkdir(parents=True, exist_ok=True)
    res = {}
    for name, fn in (("tuning", analyze_tuning), ("static", analyze_static), ("dynamic", analyze_dynamic), ("sensitivity", analyze_sensitivity)):
        try:
            res[name] = fn()
            print(f"analysed {name}")
        except FileNotFoundError as e:
            print(f"skip {name}: {e}")
    return res
