"""Shared static-chart styling (light surface, validated categorical order, recessive chrome)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# Validated categorical order (dataviz reference palette, light mode). Fixed order, never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

CATEGORY_ORDER = ["HOSPITAL", "SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE", "TRANSIT_STATION"]
CATEGORY_COLOR = dict(zip(CATEGORY_ORDER, SERIES))
# Secondary encoding for maps with > 3 categories (colour never carries identity alone).
CATEGORY_MARKER = {"HOSPITAL": "P", "SCHOOL": "o", "CHILDCARE": "^", "SENIOR_CARE": "s",
                   "FIRE_STATION": "D", "POLICE": "v", "TRANSIT_STATION": "x"}
CATEGORY_LABEL = {"HOSPITAL": "Hospital", "SCHOOL": "School (K-12)", "CHILDCARE": "Childcare / kindergarten",
                  "SENIOR_CARE": "Nursing home / senior care", "FIRE_STATION": "Fire station",
                  "POLICE": "Police station", "TRANSIT_STATION": "Rail / subway station (exploratory)"}

BOROUGH_ORDER = ["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"]
BOROUGH_COLOR = dict(zip(BOROUGH_ORDER, SERIES))
SPLIT_ORDER = ["train", "val", "test"]
SPLIT_COLOR = dict(zip(SPLIT_ORDER, SERIES))

FLAGGED = SERIES[0]
NOT_FLAGGED = "#b9b8b2"

OSM_ATTRIBUTION = "Map data (c) OpenStreetMap contributors, ODbL"


def apply() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "Arial", "DejaVu Sans"], "font.size": 10,
        "text.color": INK, "axes.labelcolor": INK_2, "axes.edgecolor": AXIS,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.titlecolor": INK, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
        "lines.linewidth": 2.0, "lines.markersize": 5, "legend.frameon": False,
        "legend.fontsize": 9, "figure.dpi": 110, "savefig.dpi": 150, "savefig.bbox": "tight",
    })


def save(fig, path) -> None:
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
