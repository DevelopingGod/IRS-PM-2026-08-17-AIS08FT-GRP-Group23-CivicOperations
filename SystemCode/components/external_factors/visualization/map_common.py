"""Shared helpers for static maps: layers, scale bar, north arrow, attribution (EPSG:32118 metres)."""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visualization import style  # noqa: E402

OSM = ROOT / "data" / "osm"
METRIC = "EPSG:32118"
MAPS = ROOT / "outputs" / "maps"


def boroughs() -> gpd.GeoDataFrame:
    return gpd.read_file(OSM / "boundaries" / "nyc_boroughs.gpkg", layer="boroughs").to_crs(METRIC)


def coverage(layer: str) -> gpd.GeoDataFrame:
    return gpd.read_file(OSM / "boundaries" / "nyc_coverage.gpkg", layer=layer).to_crs(METRIC)


def facilities(include_duplicates: bool = False) -> gpd.GeoDataFrame:
    f = gpd.read_parquet(OSM / "facilities" / "critical_facilities.parquet")
    if not include_duplicates:
        f = f[~f["is_duplicate"]]
    return f.to_crs(METRIC)


def roads(bbox=None) -> gpd.GeoDataFrame | None:
    """Road edges for a light basemap (optional; returns None if the road bundle is absent)."""
    p = OSM / "roads" / "nyc_drive.gpkg"
    if not p.exists():
        return None
    kw = {}
    if bbox is not None:
        # bbox in metric CRS -> convert to the layer CRS (EPSG:4326)
        b = gpd.GeoSeries.from_xy([bbox[0], bbox[2]], [bbox[1], bbox[3]], crs=METRIC).to_crs("EPSG:4326")
        kw["bbox"] = (b.x.min(), b.y.min(), b.x.max(), b.y.max())
    e = gpd.read_file(p, layer="edges", columns=["highway"], **kw)
    return e.to_crs(METRIC)


def draw_roads(ax, bbox=None, lw=0.12, color="#d9d8d2") -> bool:
    e = roads(bbox)
    if e is None or e.empty:
        return False
    major = e["highway"].astype(str).str.contains("motorway|trunk|primary", regex=True)
    e[~major].plot(ax=ax, color=color, linewidth=lw, zorder=1)
    e[major].plot(ax=ax, color="#c8c7c0", linewidth=lw * 3, zorder=1)
    return True


def scale_bar(ax, length_m: float, label: str | None = None, loc=(0.05, 0.05)) -> None:
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    x = x0 + loc[0] * (x1 - x0)
    y = y0 + loc[1] * (y1 - y0)
    h = (y1 - y0) * 0.008
    ax.add_patch(Rectangle(
        (x, y), length_m / 2, h, facecolor=style.INK, edgecolor=style.INK, lw=0.6, zorder=20))
    ax.add_patch(Rectangle(
        (x + length_m / 2, y), length_m / 2, h, facecolor="white", edgecolor=style.INK, lw=0.6, zorder=20))
    ax.text(x, y + 2.2 * h, "0", fontsize=7.5, ha="center", color=style.INK_2, zorder=20)
    ax.text(x + length_m, y + 2.2 * h, label or (f"{length_m / 1000:g} km" if length_m >= 1000 else f"{length_m:g} m"),
            fontsize=7.5, ha="center", color=style.INK_2, zorder=20)


def north_arrow(ax, loc=(0.95, 0.90)) -> None:
    ax.annotate("N", xy=(loc[0], loc[1]), xytext=(loc[0], loc[1] - 0.08), xycoords="axes fraction",
                ha="center", va="center", fontsize=10, fontweight="bold", color=style.INK,
                arrowprops=dict(arrowstyle="-|>", color=style.INK, lw=1.2), zorder=20)


def attribution(ax, extra: str = "") -> None:
    txt = style.OSM_ATTRIBUTION + ". Projection EPSG:32118 (NAD83 / New York Long Island, metres)." + (" " + extra if extra else "")
    ax.text(1.0, -0.008, txt, transform=ax.transAxes, ha="right", va="top", fontsize=6.5, color=style.MUTED, zorder=20)


def finish(ax, bounds, pad=0.02) -> None:
    x0, y0, x1, y1 = bounds
    dx, dy = (x1 - x0) * pad, (y1 - y0) * pad
    ax.set_xlim(x0 - dx, x1 + dx)
    ax.set_ylim(y0 - dy, y1 + dy)
    ax.set_aspect("equal")
    ax.set_axis_off()


def label_boroughs(ax, b: gpd.GeoDataFrame, fontsize=9) -> None:
    offsets = {"MANHATTAN": (-2500, 1500), "STATEN ISLAND": (0, 0), "BRONX": (0, 0), "QUEENS": (2500, 0), "BROOKLYN": (0, -1500)}
    for r in b.itertuples():
        p = r.geometry.representative_point()
        dx, dy = offsets.get(r.borough, (0, 0))
        ax.text(p.x + dx, p.y + dy, r.borough.title(), fontsize=fontsize, color=style.INK_2, ha="center",
                va="center", fontweight="bold", alpha=0.85, zorder=15)


def ensure_dir() -> Path:
    MAPS.mkdir(parents=True, exist_ok=True)
    return MAPS

