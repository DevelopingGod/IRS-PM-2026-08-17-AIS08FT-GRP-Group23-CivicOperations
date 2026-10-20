"""Static maps of historical requests and the selected proximity buffers (PNG).

Reads outputs/tables/historical_decisions.parquet (written by research/holdout_evaluation.py)
and the request coordinates. Presentation only: decisions come from the evaluator.

Outputs (outputs/maps/):
  requests_flagged_citywide.png   every geolocated historical request: flagged / not flagged
  zoom_<area>.png                 neighbourhood close-ups with facility geometries and buffers

Usage: python visualization/static_request_map.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import geopandas as gpd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402
import yaml  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from visualization import map_common as mc  # noqa: E402
from visualization import style  # noqa: E402

CORE = ["HOSPITAL", "SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE"]
# Close-up windows (centre lat, lon; half-width m). Chosen to show contrasting densities.
ZOOMS = {
    "east_harlem_manhattan": (40.7920, -73.9470, 900, "East Harlem, Manhattan (dense grid)"),
    "flatbush_brooklyn": (40.6420, -73.9590, 900, "Flatbush, Brooklyn"),
    "new_dorp_staten_island": (40.5730, -74.1170, 900, "New Dorp, Staten Island (low density)"),
}


def load_points() -> gpd.GeoDataFrame:
    from common import load_requests
    dec = pd.read_parquet(ROOT / "outputs" / "tables" / "historical_decisions.parquet")
    req = load_requests()[["request_id", "latitude", "longitude"]]
    d = dec.merge(req, on="request_id")
    d = d[d.latitude.notna()]
    return gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.longitude, d.latitude), crs="EPSG:4326").to_crs(mc.METRIC)


def citywide(pts: gpd.GeoDataFrame, threshold: float) -> None:
    b = mc.boroughs()
    fig, ax = plt.subplots(figsize=(10, 10.5))
    b.plot(ax=ax, facecolor="#f3f2ee", edgecolor=style.MUTED, lw=0.9, zorder=0)
    mc.draw_roads(ax, b.total_bounds, lw=0.08)
    neg = pts[pts.external_factor == 0]
    pos = pts[pts.external_factor == 1]
    ind = pts[pts.external_factor.isna()]
    ax.scatter(neg.geometry.x, neg.geometry.y, s=1.6, c=style.NOT_FLAGGED, lw=0, zorder=3)
    ax.scatter(pos.geometry.x, pos.geometry.y, s=1.8, c=style.FLAGGED, lw=0, zorder=4)
    ax.scatter(ind.geometry.x, ind.geometry.y, s=14, c=style.SERIES[1], marker="x", lw=0.8, zorder=5)
    n = len(pts)
    handles = [Line2D([], [], ls="", marker="o", color=style.FLAGGED, ms=6,
                      label=f"1: qualifying facility within {threshold:g} m ({len(pos):,}, {100 * len(pos) / n:.1f}%)"),
               Line2D([], [], ls="", marker="o", color=style.NOT_FLAGGED, ms=6,
                      label=f"0: none within threshold ({len(neg):,}, {100 * len(neg) / n:.1f}%)"),
               Line2D([], [], ls="", marker="x", color=style.SERIES[1], ms=6,
                      label=f"null: uncertainty spans threshold ({len(ind):,})")]
    mc.label_boroughs(ax, b)
    mc.finish(ax, b.total_bounds)
    ax.legend(handles=handles, loc="upper left", title=f"Historical requests with coordinates (n = {n:,}, all splits)",
              title_fontsize=9, fontsize=8.5, frameon=True, facecolor=style.SURFACE, edgecolor=style.GRID)
    mc.scale_bar(ax, 5000)
    mc.north_arrow(ax)
    mc.attribution(ax, "Requests: supplied NYC311 sample (borough-stratified).")
    ax.set_title("Critical-facility proximity factor on historical NYC311 requests", loc="left", fontsize=14)
    style.save(fig, mc.ensure_dir() / "requests_flagged_citywide.png")


def zoom(name: str, lat: float, lon: float, half: float, title: str, pts, fac, threshold: float) -> None:
    c = gpd.GeoSeries.from_xy([lon], [lat], crs="EPSG:4326").to_crs(mc.METRIC).iloc[0]
    bounds = (c.x - half, c.y - half, c.x + half, c.y + half)
    win = shapely.box(*bounds)
    sel = fac[fac.intersects(win.buffer(threshold))]
    q = sel[sel.category.isin(CORE)]
    p = pts[pts.within(win)]
    fig, ax = plt.subplots(figsize=(12.5, 9.2))
    mc.draw_roads(ax, (bounds[0] - 200, bounds[1] - 200, bounds[2] + 200, bounds[3] + 200), lw=0.9, color="#e3e2dc")
    buf = gpd.GeoSeries([q.geometry.buffer(threshold).union_all()], crs=mc.METRIC)
    buf.plot(ax=ax, facecolor=style.SEQ_BLUE[0], edgecolor=style.SEQ_BLUE[3], lw=0.8, alpha=0.55, zorder=2)
    polys = sel[sel.geom_type.isin(["Polygon", "MultiPolygon"])]
    for cat, g in polys.groupby("category"):
        g.plot(ax=ax, facecolor=style.CATEGORY_COLOR[cat], edgecolor=style.CATEGORY_COLOR[cat], alpha=0.45, lw=0.8, zorder=3)
    for cat, g in sel.groupby("category"):
        rp = g.geometry.representative_point()
        filled = cat != "TRANSIT_STATION"
        ax.scatter(rp.x, rp.y, s=55, c=style.CATEGORY_COLOR[cat], marker=style.CATEGORY_MARKER[cat],
                   edgecolors=style.SURFACE if filled else None, linewidths=0.8 if filled else 1.6, zorder=6)
    neg, pos, ind = p[p.external_factor == 0], p[p.external_factor == 1], p[p.external_factor.isna()]
    ax.scatter(neg.geometry.x, neg.geometry.y, s=16, c=style.NOT_FLAGGED, edgecolors=style.SURFACE, linewidths=0.4, zorder=4)
    ax.scatter(pos.geometry.x, pos.geometry.y, s=18, c=style.FLAGGED, edgecolors=style.SURFACE, linewidths=0.4, zorder=5)
    if len(ind):
        ax.scatter(ind.geometry.x, ind.geometry.y, s=30, c=style.SERIES[1], marker="x", lw=1, zorder=5)
    mc.finish(ax, bounds, pad=0)
    present = [c for c in CORE + ["TRANSIT_STATION"] if c in set(sel.category)]
    handles = [Patch(facecolor=style.SEQ_BLUE[0], edgecolor=style.SEQ_BLUE[3], label=f"{threshold:g} m around qualifying facility geometry")]
    handles += [Line2D([], [], ls="", marker=style.CATEGORY_MARKER[c], color=style.CATEGORY_COLOR[c], ms=8,
                       label=style.CATEGORY_LABEL[c]) for c in present]
    handles += [Line2D([], [], ls="", marker="o", color=style.FLAGGED, ms=6, label=f"Request flagged 1 ({len(pos)})"),
                Line2D([], [], ls="", marker="o", color=style.NOT_FLAGGED, ms=6, label=f"Request 0 ({len(neg)})")]
    if len(ind):
        handles.append(Line2D([], [], ls="", marker="x", color=style.SERIES[1], ms=6, label=f"Request null ({len(ind)})"))
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8.5, frameon=False)
    mc.scale_bar(ax, 500)
    mc.north_arrow(ax)
    mc.attribution(ax, "Requests: supplied NYC311 sample.")
    ax.set_title(f"{title}: proximity buffers and request decisions", loc="left", fontsize=13)
    style.save(fig, mc.ensure_dir() / f"zoom_{name}.png")


def main() -> None:
    style.apply()
    cfg = yaml.safe_load(open(ROOT / "config" / "proximity_rules.yaml", encoding="utf-8"))
    threshold = float(cfg["threshold_selection"]["selected_threshold_m"])
    pts = load_points()
    fac = mc.facilities()
    citywide(pts, threshold)
    for name, (lat, lon, half, title) in ZOOMS.items():
        zoom(name, lat, lon, half, title, pts, fac, threshold)
    print("request maps written to", mc.MAPS)


if __name__ == "__main__":
    main()
