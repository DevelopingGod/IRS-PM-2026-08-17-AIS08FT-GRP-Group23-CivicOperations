"""Static, offline route maps rendered from a plan document + its GeoJSON (no basemap tiles).

Everything drawn comes from the saved plan outputs: road-following legs (A* paths on the local OSM
graph), crew positions at the planning instant, ordered stops, locked jobs and backlog. Nothing is
re-optimised or invented at render time.

Colour: categorical slots in fixed order (validated palette, see research/decision_register.md);
at most 8 crews per panel - more crews are split across panels - and every crew is also identified
by a direct label and the legend, so identity never relies on colour alone.
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import patheffects as pe  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8986"
ROAD = "#d9d8d3"
ROAD_MAJOR = "#c4c3bd"
MAX_CREWS_PER_PANEL = 8
ROAD_WIDTH = {"motorway": 1.3, "trunk": 1.1, "primary": 0.9, "secondary": 0.7, "tertiary": 0.55,
              "motorway_link": 0.6, "trunk_link": 0.6, "primary_link": 0.5}
HALO = [pe.withStroke(linewidth=3.2, foreground=SURFACE)]


def _fmt_time(iso) -> str:
    try:
        d = iso if isinstance(iso, datetime) else datetime.fromisoformat(iso)
        return d.strftime("%a %d %b %Y %H:%M %Z").strip()
    except (TypeError, ValueError):
        return str(iso)


class BaseMap:
    """Road background from the local enriched network (cached per bounding box)."""

    def __init__(self, net):
        self.net = net
        lon_u, lat_u = net.node_lon[net.edge_u], net.node_lat[net.edge_u]
        self._edge_lon, self._edge_lat = lon_u, lat_u

    def draw(self, ax, bbox):
        x0, y0, x1, y1 = bbox
        sel = np.nonzero((self._edge_lon >= x0) & (self._edge_lon <= x1) & (self._edge_lat >= y0) & (self._edge_lat <= y1))[0]
        hw = self.net.edge_highway[sel]
        minor, major, widths = [], [], []
        for e, h in zip(sel, hw):
            g = self.net.edge_geometry(int(e))
            if h in ROAD_WIDTH:
                major.append(g)
                widths.append(ROAD_WIDTH[h])
            else:
                minor.append(g)
        ax.add_collection(LineCollection(minor, colors=ROAD, linewidths=0.35, zorder=1, capstyle="round"))
        ax.add_collection(LineCollection(major, colors=ROAD_MAJOR, linewidths=widths, zorder=1.1, capstyle="round"))


def _bbox(points, pad_frac=0.08, min_span=0.012):
    pts = np.asarray(points, float)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = max(x1 - x0, min_span) * (1 + 2 * pad_frac)
    h = max(y1 - y0, min_span * 0.76) * (1 + 2 * pad_frac)
    # aim for a near-square displayed extent (lon degrees are shorter by cos(lat))
    k = math.cos(math.radians(cy))
    if w * k < h:
        w = h / k
    else:
        h = w * k
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def _arrow_along(ax, xy, color, frac=0.55, zorder=5):
    """Arrowhead at a fraction of the polyline's length, pointing in travel direction."""
    if len(xy) < 2:
        return
    seg = np.diff(xy, axis=0)
    d = np.hypot(seg[:, 0], seg[:, 1])
    if d.sum() <= 0:
        return
    target = frac * d.sum()
    j = int(np.searchsorted(np.cumsum(d), target))
    j = min(j, len(seg) - 1)
    a, b = xy[j], xy[j + 1]
    ax.annotate("", xy=(b[0], b[1]), xytext=(a[0] + (b[0] - a[0]) * 0.01, a[1] + (b[1] - a[1]) * 0.01),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=0, mutation_scale=13), zorder=zorder)


def _scale_bar(ax, bbox):
    x0, y0, x1, y1 = bbox
    lat = (y0 + y1) / 2
    km_deg = 1 / (111.32 * math.cos(math.radians(lat)))
    span_km = (x1 - x0) / km_deg
    length = next(v for v in (0.25, 0.5, 1, 2, 5, 10, 20) if v >= span_km / 8)
    sx, sy = x0 + 0.04 * (x1 - x0), y0 + 0.035 * (y1 - y0)
    ax.plot([sx, sx + length * km_deg], [sy, sy], color=INK, lw=2.2, solid_capstyle="butt", zorder=9)
    ax.text(sx + length * km_deg / 2, sy + 0.012 * (y1 - y0), f"{length:g} km", ha="center", va="bottom",
            fontsize=8, color=INK, path_effects=HALO, zorder=9)


def render_plan_map(doc: dict, geojson: dict, net, out_path, *, agency: str | None = None, crew_ids=None,
                    title: str | None = None, show_backlog: bool = True, dpi: int = 170, base: BaseMap | None = None,
                    ax=None, legend: bool = True, bbox=None) -> Path | None:
    """Render one plan (optionally one agency / subset of crews). Returns the PNG path (None if ax given)."""
    base = base or BaseMap(net)
    crews = [c for c in doc["crews"] if (agency is None or c["agency"] == agency) and (crew_ids is None or c["crew_id"] in crew_ids)]
    active = [c for c in crews if c["planned_stops"] or c["locked_commitments"]]
    req = {r["request"]["request_id"]: r for r in doc["requests"]}
    shown = active[:MAX_CREWS_PER_PANEL]
    color = {c["crew_id"]: SERIES[i] for i, c in enumerate(shown)}

    pts = []
    for c in shown:
        pts.append((c["current_position"]["lon"], c["current_position"]["lat"]))
        for s in c["planned_stops"]:
            rr = req[s["request_id"]]["request"]
            pts.append((rr["longitude"], rr["latitude"]))
    backlog = []
    if show_backlog:
        for r in doc["requests"]:
            d = r["dispatch"]
            rr = r["request"]
            if d["crew_id"] is None and rr.get("latitude") is not None and (agency is None or rr.get("agency") == agency) \
                    and rr["request_id"] not in {j["request_id"] for c in doc["crews"] for j in c["locked_commitments"]} \
                    and "ALREADY_COMPLETED_OR_CANCELLED" not in d["reasons"]:
                backlog.append((rr["longitude"], rr["latitude"], d["c11_queue_status"]))
    if not pts:
        pts = [(b[0], b[1]) for b in backlog] or [(-73.95, 40.70)]
    bbox = bbox or _bbox(pts + [(b[0], b[1]) for b in backlog])

    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(14.0, 10.0))
    fig = ax.figure
    ax.set_facecolor(SURFACE)
    fig.patch.set_facecolor(SURFACE)
    base.draw(ax, bbox)

    feats = [f for f in geojson["features"] if f["properties"]["crew_id"] in color]
    for f in feats:
        cid = f["properties"]["crew_id"]
        xy = np.asarray(f["geometry"]["coordinates"])
        kind = f["properties"]["kind"]
        if kind == "leg":
            ax.plot(xy[:, 0], xy[:, 1], color=color[cid], lw=2.4, alpha=0.95, zorder=3, solid_capstyle="round",
                    path_effects=[pe.Stroke(linewidth=4.2, foreground=SURFACE), pe.Normal()])
            _arrow_along(ax, xy, color[cid])
        elif kind == "return_to_depot":
            ax.plot(xy[:, 0], xy[:, 1], color=color[cid], lw=1.3, ls=(0, (2, 2)), alpha=0.8, zorder=2.5)
        elif kind == "locked_leg":
            ax.plot(xy[:, 0], xy[:, 1], color=color[cid], lw=3.4, ls=(0, (1, 1.2)), alpha=1.0, zorder=3.2)
            _arrow_along(ax, xy, color[cid])

    # backlog / review / held
    style = {"provisional": ("x", INK_3, "Unserved eligible (backlog)"),
             "provisional_review": ("D", "#eb6834", "Pending human review"),
             "hold": ("X", "#d03b3b", "Held by C11")}
    seen_kinds = set()
    for x, y, st in backlog:
        m, col, _ = style.get(st, style["provisional"])
        if st == "provisional_review":
            ax.scatter([x], [y], marker=m, s=34, facecolors="none", edgecolors=col, linewidths=1.2, zorder=2.8)
        else:
            ax.scatter([x], [y], marker=m, s=26 if st == "provisional" else 40, c=col, linewidths=1.1, zorder=2.8)
        seen_kinds.add(st)

    # stops: true positions, numbers inside; same-crew stops closer than ~1.2% of extent share one label
    span = bbox[2] - bbox[0]
    for c in shown:
        cid = c["crew_id"]
        groups = []
        for s in c["planned_stops"]:
            rr = req[s["request_id"]]["request"]
            p = (rr["longitude"], rr["latitude"])
            for g in groups:
                if math.hypot(g["p"][0] - p[0], (g["p"][1] - p[1]) / math.cos(math.radians(p[1]))) < 0.012 * span:
                    g["n"].append(s["sequence"])
                    break
            else:
                groups.append({"p": p, "n": [s["sequence"]]})
        for g in groups:
            lab = ",".join(map(str, g["n"])) if len(g["n"]) <= 3 else f"{g['n'][0]}-{g['n'][-1]}"
            ax.scatter([g["p"][0]], [g["p"][1]], s=150 + 40 * (len(lab) - 1), c=color[cid], edgecolors=SURFACE, linewidths=1.6, zorder=6)
            ax.text(g["p"][0], g["p"][1], lab, ha="center", va="center", fontsize=6.8 if len(lab) > 2 else 7.5,
                    color="white", fontweight="bold", zorder=7)
        cp = c["current_position"]
        on_site = False
        for j in c["locked_commitments"]:
            here = abs(j["lat"] - cp["lat"]) < 1e-7 and abs(j["lon"] - cp["lon"]) < 1e-7
            on_site |= here and j["state"] == "IN_PROGRESS"
            ax.scatter([j["lon"]], [j["lat"]], marker="s", s=210 if here else 150, c=color[cid], edgecolors=INK,
                       linewidths=1.6, zorder=8.5 if here else 6.5)
            ax.text(j["lon"], j["lat"], "L" if j["state"] != "IN_PROGRESS" else "W", ha="center", va="center", fontsize=7.5,
                    color="white", fontweight="bold", zorder=8.6 if here else 7)
        if not on_site:   # a crew working on site is shown by its W square
            ax.scatter([cp["lon"]], [cp["lat"]], marker="^", s=190, c=color[cid], edgecolors=INK, linewidths=1.3, zorder=8)
        ax.annotate(cid, (cp["lon"], cp["lat"]), xytext=(7, 7), textcoords="offset points", fontsize=8, color=INK,
                    fontweight="bold", path_effects=HALO, zorder=9)
        dp = c["depot"]
        if (dp["lat"], dp["lon"]) != (cp["lat"], cp["lon"]):
            ax.scatter([dp["lon"]], [dp["lat"]], marker="p", s=70, facecolors="none", edgecolors=color[cid], linewidths=1.4, zorder=4)

    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect(1 / math.cos(math.radians((bbox[1] + bbox[3]) / 2)))
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color(ROAD_MAJOR)
    _scale_bar(ax, bbox)
    ax.text(0.995, 0.004, "Roads © OpenStreetMap contributors (ODbL). Routes: A* on local OSM graph.",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=6.5, color=INK_2)

    ev = doc["event"]
    scope = agency or "All agencies"
    head = title or f"{scope} — proposed plan v{doc['plan_version']}"
    ax.set_title(f"{head}\n{ev.get('type', 'event')} · {_fmt_time(doc['planning_time'])} · method {doc['algorithm']['method']}",
                 loc="left", fontsize=11, color=INK, pad=8)
    if legend:
        handles = []
        for c in shown:
            n = len(c["planned_stops"])
            handles.append(Line2D([0], [0], color=color[c["crew_id"]], lw=2.4,
                                  label=f"{c['crew_id']}  {n} stop{'s' if n != 1 else ''} · {c['planned_travel_min']:.0f} min travel"))
        handles += [Line2D([0], [0], marker="^", color="none", markerfacecolor=INK_3, markeredgecolor=INK, markersize=9, label="Crew position at planning time"),
                    Line2D([0], [0], marker="o", color="none", markerfacecolor=INK_3, markeredgecolor=SURFACE, markersize=10, label="Planned stop (number = order)"),
                    Line2D([0], [0], marker="s", color="none", markerfacecolor=INK_3, markeredgecolor=INK, markersize=9, label="Locked job (L en route, W on site)"),
                    Line2D([0], [0], color=INK_3, lw=3, ls=(0, (1, 1.2)), label="Locked leg (not re-optimised)"),
                    Line2D([0], [0], color=INK_3, lw=1.3, ls=(0, (2, 2)), label="Return to depot")]
        for st in ("provisional", "provisional_review", "hold"):
            if st in seen_kinds:
                m, col, lab = style[st]
                handles.append(Line2D([0], [0], marker=m, color="none", markerfacecolor=col if st != "provisional_review" else "none",
                                      markeredgecolor=col, markersize=7, label=lab))
        leg = ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=True,
                        framealpha=1.0, borderpad=0.8, title="Crews and symbols", title_fontsize=8.5, alignment="left")
        leg.get_frame().set_edgecolor(ROAD_MAJOR)
        if len(active) > len(shown):
            ax.text(0.01, 0.01, f"+{len(active) - len(shown)} more crews on other panels", transform=ax.transAxes,
                    fontsize=7.5, color=INK_2, va="bottom")
        st = _scope_stats(doc, agency)
        stats = (f"Assigned {st['assigned']} of {st['eligible']} eligible\n"
                 f"Travel {st['travel']:.0f} min · service {st['service']:.0f} min\n"
                 f"Rank-weighted coverage {st['coverage']}\n"
                 f"Pending review {st['review']} · held {st['held']} · locked {st['locked']}")
        ax.text(1.01, 0.0, stats, transform=ax.transAxes, va="bottom", ha="left", fontsize=8.5, color=INK, linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.6", fc=SURFACE, ec=ROAD_MAJOR), zorder=10)
    if own:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=dpi, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
        plt.close(fig)
        return out_path
    return None


def _scope_stats(doc: dict, agency: str | None) -> dict:
    """Map summary computed from the plan document itself, restricted to the shown agency."""
    reqs = [r for r in doc["requests"] if agency is None or r["request"].get("agency") == agency]
    eligible = [r for r in reqs if r["dispatch"]["overall_queue_position_used"] is not None
                and r["dispatch"]["execution_state"] not in ("APPROVED_NOT_STARTED", "EN_ROUTE", "IN_PROGRESS", "COMPLETED", "CANCELLED")]
    assigned = [r for r in eligible if r["dispatch"]["crew_id"]]
    crews = [c for c in doc["crews"] if agency is None or c["agency"] == agency]
    w_all = sum(r["dispatch"]["rank_weight"] or 0 for r in eligible)
    w_ok = sum(r["dispatch"]["rank_weight"] or 0 for r in assigned)
    return {"eligible": len(eligible), "assigned": len(assigned),
            "travel": sum(c["planned_travel_min"] for c in crews),
            "service": sum(s["service_min"] for c in crews for s in c["planned_stops"]),
            "coverage": f"{100 * w_ok / w_all:.0f}%" if w_all else "n/a",
            "review": sum(1 for r in reqs if r["dispatch"]["c11_queue_status"] == "provisional_review"),
            "held": sum(1 for r in reqs if r["dispatch"]["c11_queue_status"] == "hold"),
            "locked": sum(len(c["locked_commitments"]) for c in crews)}


def _doc_points(doc: dict, agency: str | None) -> list:
    pts = []
    for c in doc["crews"]:
        if agency is not None and c["agency"] != agency:
            continue
        if c["planned_stops"] or c["locked_commitments"]:
            pts.append((c["current_position"]["lon"], c["current_position"]["lat"]))
    for r in doc["requests"]:
        rr = r["request"]
        if (agency is None or rr.get("agency") == agency) and rr.get("latitude") is not None                 and "ALREADY_COMPLETED_OR_CANCELLED" not in r["dispatch"]["reasons"]:
            pts.append((rr["longitude"], rr["latitude"]))
    return pts


def render_comparison(prev_doc, prev_geo, doc, geo, net, out_path, *, agency=None, dpi=150, base=None) -> Path:
    """Before/after panels on one shared extent; the header counts only the shown agency's changes."""
    base = base or BaseMap(net)
    pts = _doc_points(prev_doc, agency) + _doc_points(doc, agency)
    bbox = _bbox(pts) if pts else None
    fig, axes = plt.subplots(1, 2, figsize=(26, 10))
    render_plan_map(prev_doc, prev_geo, net, None, agency=agency, ax=axes[0], base=base, bbox=bbox,
                    title=f"BEFORE — plan v{prev_doc['plan_version']}")
    render_plan_map(doc, geo, net, None, agency=agency, ax=axes[1], base=base, bbox=bbox,
                    title=f"AFTER — plan v{doc['plan_version']}")
    ch = doc["changes_from_previous"]
    ag = {r["request"]["request_id"]: r["request"].get("agency") for r in doc["requests"] + prev_doc["requests"]}
    crew_ag = {c["crew_id"]: c["agency"] for c in doc["crews"]}
    keep = (lambda rid: agency is None or ag.get(rid) == agency)
    n = {k: sum(1 for r in ch[k] if keep(r)) for k in ("newly_assigned", "moved_to_other_crew", "no_longer_assigned", "moved_into_execution")}
    reseq = sum(1 for c in ch["resequenced_crews"] if agency is None or crew_ag.get(c) == agency)
    fig.suptitle(f"Replanning comparison ({agency or 'all agencies'}): {n['newly_assigned']} newly assigned, "
                 f"{n['moved_to_other_crew']} moved to another crew, {n['no_longer_assigned']} dropped, "
                 f"{n['moved_into_execution']} moved into execution, {reseq} crews resequenced",
                 x=0.01, ha="left", fontsize=12, color=INK)
    fig.patch.set_facecolor(SURFACE)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return out_path


def render_crew_panels(doc, geo, net, out_path, *, agency, dpi=150, base=None) -> Path | None:
    """Small multiples: one panel per active crew of an agency (individual crew route maps)."""
    base = base or BaseMap(net)
    active = [c for c in doc["crews"] if c["agency"] == agency and (c["planned_stops"] or c["locked_commitments"])]
    if not active:
        return None
    n = len(active)
    cols = min(3, n)
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(6.2 * cols, 6.2 * rows), squeeze=False)
    for ax, c in zip(axes.flat, active):
        render_plan_map(doc, geo, net, None, agency=agency, crew_ids={c["crew_id"]}, ax=ax, base=base, show_backlog=False,
                        title=f"{c['crew_id']} — {len(c['planned_stops'])} stops, {c['planned_work_min']:.0f}/"
                              f"{c['capacity_for_new_work_min']:.0f} min", legend=False)
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    fig.patch.set_facecolor(SURFACE)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return out_path
