"""Static facility and coverage maps (PNG). Presentation only - no business logic.

Outputs (outputs/maps/):
  facility_overview.png        all core facilities, colour + marker by category, borough outlines
  facility_small_multiples.png one panel per category (incl. exploratory transit)
  coverage_map.png             five boroughs, facility search buffer, road-area buffer, facilities outside NYC

Usage: python visualization/static_facility_map.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from visualization import map_common as mc  # noqa: E402
from visualization import style  # noqa: E402

CORE = ["HOSPITAL", "SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE"]


def _points(f):
    return f.geometry.representative_point()


def overview() -> None:
    b, f = mc.boroughs(), mc.facilities()
    area = mc.coverage("facility_search_area")
    fig, ax = plt.subplots(figsize=(10, 10.5))
    area.boundary.plot(ax=ax, color=style.AXIS, lw=0.8, linestyle="--", zorder=2)
    b.plot(ax=ax, facecolor="#f3f2ee", edgecolor=style.MUTED, lw=0.9, zorder=0)
    mc.draw_roads(ax, area.total_bounds)
    handles = []
    sizes = {"SCHOOL": 5, "CHILDCARE": 9, "HOSPITAL": 34, "SENIOR_CARE": 12, "FIRE_STATION": 12, "POLICE": 16}
    for cat in ["SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE", "HOSPITAL"]:
        g = f[f.category == cat]
        p = _points(g)
        ax.scatter(p.x, p.y, s=sizes[cat], c=style.CATEGORY_COLOR[cat], marker=style.CATEGORY_MARKER[cat],
                   linewidths=0.4, edgecolors=style.SURFACE, zorder=5 if cat != "HOSPITAL" else 7, alpha=0.9)
        handles.append(Line2D([], [], ls="", marker=style.CATEGORY_MARKER[cat], color=style.CATEGORY_COLOR[cat],
                              markeredgecolor=style.SURFACE, ms=8, label=f"{style.CATEGORY_LABEL[cat]} ({len(g):,})"))
    handles = sorted(handles, key=lambda h: CORE.index(next(c for c in CORE if style.CATEGORY_LABEL[c] in h.get_label())))
    handles.append(Line2D([], [], color=style.AXIS, ls="--", label="Facility search area (NYC + 2 km)"))
    mc.label_boroughs(ax, b)
    mc.finish(ax, area.total_bounds)
    ax.legend(handles=handles, loc="upper left", title="Core critical facilities (OSM, de-duplicated)",
              title_fontsize=9, fontsize=8.5, frameon=True, facecolor=style.SURFACE, edgecolor=style.GRID)
    mc.scale_bar(ax, 5000)
    mc.north_arrow(ax)
    mc.attribution(ax)
    ax.set_title("Critical facilities across the five NYC boroughs", loc="left", fontsize=14)
    style.save(fig, mc.ensure_dir() / "facility_overview.png")


def small_multiples() -> None:
    b, f = mc.boroughs(), mc.facilities()
    bounds = b.total_bounds
    cats = CORE + ["TRANSIT_STATION"]
    fig, axes = plt.subplots(2, 4, figsize=(14, 8))
    for ax, cat in zip(axes.flat, cats):
        b.plot(ax=ax, facecolor="#f3f2ee", edgecolor=style.MUTED, lw=0.5)
        g = f[f.category == cat]
        p = _points(g)
        ax.scatter(p.x, p.y, s=4 if cat == "SCHOOL" else 9, c=style.CATEGORY_COLOR[cat],
                   marker=style.CATEGORY_MARKER[cat], linewidths=0.6 if cat == "TRANSIT_STATION" else 0)
        inside = int((g.borough != "OUTSIDE_NYC").sum())
        ax.set_title(f"{style.CATEGORY_LABEL[cat]}\n{inside:,} in NYC, {len(g) - inside:,} in buffer", fontsize=9.5)
        mc.finish(ax, bounds)
    ax = axes.flat[-1]
    ax.set_axis_off()
    ax.text(0, 0.6, "One panel per facility category.\nPolygons drawn at their\nrepresentative point.\n\n"
            "Transit stations are exploratory\nand do not qualify in the\nselected ruleset.",
            fontsize=9, color=style.INK_2, va="top", transform=ax.transAxes)
    ax.text(0, 0.05, style.OSM_ATTRIBUTION, fontsize=7, color=style.MUTED, transform=ax.transAxes)
    fig.suptitle("Facility categories acquired from OpenStreetMap (de-duplicated)", x=0.01, ha="left",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    style.save(fig, mc.ensure_dir() / "facility_small_multiples.png")


def coverage_map() -> None:
    b, f = mc.boroughs(), mc.facilities()
    area = mc.coverage("facility_search_area")
    road_area = mc.coverage("road_area")
    fig, ax = plt.subplots(figsize=(10, 10.5))
    area.plot(ax=ax, facecolor=style.SEQ_BLUE[0], edgecolor="none", alpha=0.6, zorder=0)
    road_area.boundary.plot(ax=ax, color=style.SEQ_BLUE[3], lw=0.9, linestyle=":", zorder=2)
    for k, bb in enumerate(style.BOROUGH_ORDER):
        b[b.borough == bb].plot(ax=ax, facecolor=style.BOROUGH_COLOR[bb], edgecolor=style.SURFACE,
                                lw=1.5, alpha=0.30, zorder=1)
    b.boundary.plot(ax=ax, color=style.INK_2, lw=0.8, zorder=3)
    out = f[f.borough == "OUTSIDE_NYC"]
    p = _points(out)
    ax.scatter(p.x, p.y, s=10, c=style.INK, marker="o", zorder=6, label=f"Facilities outside NYC within the buffer ({len(out):,})")
    mc.label_boroughs(ax, b, fontsize=10)
    mc.finish(ax, area.total_bounds)
    handles = [Line2D([], [], color=style.SEQ_BLUE[0], lw=8, label="Facility search area: borough union + 2,000 m"),
               Line2D([], [], color=style.SEQ_BLUE[3], lw=1, ls=":", label="Road-network area: borough union + 1,000 m"),
               Line2D([], [], color=style.INK_2, lw=1, label="Borough boundary (OSM admin relation, includes water)"),
               Line2D([], [], ls="", marker="o", color=style.INK, ms=4, label=f"Facility outside NYC, inside buffer ({len(out):,})")]
    ax.legend(handles=handles, loc="upper left", fontsize=8.5, frameon=True, facecolor=style.SURFACE, edgecolor=style.GRID)
    mc.scale_bar(ax, 5000)
    mc.north_arrow(ax)
    mc.attribution(ax)
    ax.set_title("Five-borough coverage and geographic buffers", loc="left", fontsize=14)
    style.save(fig, mc.ensure_dir() / "coverage_map.png")


def main() -> None:
    style.apply()
    overview()
    small_multiples()
    coverage_map()
    print("facility maps written to", mc.MAPS)


if __name__ == "__main__":
    main()
