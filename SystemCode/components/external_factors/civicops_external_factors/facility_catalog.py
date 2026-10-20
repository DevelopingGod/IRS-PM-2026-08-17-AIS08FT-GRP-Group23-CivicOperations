"""Facility-category definitions, OSM tag classification and facility normalisation.

Data preparation uses this module to turn raw OSM features into the cached facility table.
The tests use it too. It does not depend on OSMnx, so the runtime evaluator stays lightweight.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import yaml

# Tag columns copied into dedicated columns of the facility table (all tags are also kept in tags_json).
KEPT_TAG_COLUMNS = [
    "name", "official_name", "operator", "amenity", "healthcare", "social_facility",
    "social_facility:for", "railway", "public_transport", "station", "network",
    "addr:housenumber", "addr:street", "addr:postcode", "addr:city", "isced:level", "building",
]

FACILITY_COLUMNS = [
    "facility_id", "osm_type", "osm_id", "category", "category_status", "name",
    "geometry_type", "borough", "is_duplicate", "duplicate_of", "geometry_repaired",
    "tags_json", "acquired_at",
]


@dataclass(frozen=True)
class CategoryDef:
    code: str
    status: str
    label: str
    rationale: str
    query_tags: Mapping[str, list[str]]
    match: list[Mapping[str, Any]]


@dataclass
class CategoryCatalog:
    version: str
    precedence: list[str]
    categories: dict[str, CategoryDef]
    excluded: list[dict] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CategoryCatalog":
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        cats = {
            code: CategoryDef(
                code=code,
                status=spec["status"],
                label=spec.get("label", code),
                rationale=" ".join(str(spec.get("rationale", "")).split()),
                query_tags=spec["query_tags"],
                match=spec["match"],
            )
            for code, spec in raw["categories"].items()
        }
        precedence = raw.get("precedence", list(cats))
        missing = set(cats) - set(precedence)
        if missing:
            raise ValueError(f"Categories missing from precedence list: {sorted(missing)}")
        return cls(version=str(raw.get("version", "?")), precedence=precedence,
                   categories=cats, excluded=raw.get("excluded", []))

    def combined_query_tags(self) -> dict[str, list[str]]:
        """Union of all categories' query tags, in the dict format expected by OSMnx."""
        out: dict[str, set[str]] = {}
        for cat in self.categories.values():
            for key, values in cat.query_tags.items():
                out.setdefault(key, set()).update(values)
        return {k: sorted(v) for k, v in out.items()}

    def classify(self, tags: Mapping[str, Any]) -> str | None:
        """Return the first category (by precedence) whose match rules the tag dict satisfies."""
        for code in self.precedence:
            for rule in self.categories[code].match:
                if _rule_matches(rule, tags):
                    return code
        return None


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA


def _rule_matches(rule: Mapping[str, Any], tags: Mapping[str, Any]) -> bool:
    for key, expected in rule.items():
        value = tags.get(key)
        if _is_missing(value):
            return False
        # OSM multi-values are separated by ';'
        values = {v.strip() for v in str(value).split(";")}
        allowed = set(expected) if isinstance(expected, (list, tuple, set)) else {expected}
        if not values & {str(a) for a in allowed}:
            return False
    return True


def _clean_tags(row: Mapping[str, Any], tag_columns: Iterable[str]) -> dict[str, str]:
    out = {}
    for col in tag_columns:
        val = row.get(col)
        if _is_missing(val):
            continue
        out[col] = str(val)
    return out


def _display_name(tags: Mapping[str, str]) -> str | None:
    for key in ("name", "official_name", "operator"):
        if tags.get(key):
            return tags[key]
    return None


def normalise_osm_features(raw: gpd.GeoDataFrame, catalog: CategoryCatalog,
                           acquired_at: str) -> tuple[gpd.GeoDataFrame, dict]:
    """Convert an OSMnx features GeoDataFrame into the facility schema.

    Returns (facilities, report). Features that do not match any category, or whose geometry
    is missing or empty, are dropped and counted in the report. Invalid geometries are
    repaired with shapely.make_valid and flagged with geometry_repaired=True.
    """
    report = {"raw_features": int(len(raw)), "unclassified": 0, "missing_or_empty_geometry": 0,
              "repaired_geometry": 0, "unclassified_examples": []}
    if raw.crs is None:
        raise ValueError("Raw OSM features have no CRS")
    raw = raw.to_crs("EPSG:4326")
    df = raw.reset_index()
    if "element" not in df.columns:  # OSMnx < 2 used element_type / osmid
        df = df.rename(columns={"element_type": "element", "osmid": "id"})
    tag_columns = [c for c in df.columns if c not in ("element", "id", "geometry", "nodes", "ways")]

    records = []
    for _, row in df.iterrows():
        tags = _clean_tags(row, tag_columns)
        category = catalog.classify(tags)
        if category is None:
            report["unclassified"] += 1
            if len(report["unclassified_examples"]) < 10:
                report["unclassified_examples"].append(
                    {"id": f"{row['element']}/{row['id']}", **{k: tags[k] for k in list(tags)[:6]}})
            continue
        geom = row.geometry
        if geom is None or geom.is_empty:
            report["missing_or_empty_geometry"] += 1
            continue
        repaired = False
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
            repaired = True
            report["repaired_geometry"] += 1
        records.append({
            "facility_id": f"{row['element']}/{row['id']}",
            "osm_type": str(row["element"]),
            "osm_id": int(row["id"]),
            "category": category,
            "category_status": catalog.categories[category].status,
            "name": _display_name(tags),
            "geometry_type": geom.geom_type,
            "borough": None,
            "is_duplicate": False,
            "duplicate_of": None,
            "geometry_repaired": repaired,
            "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
            "acquired_at": acquired_at,
            **{f"tag_{k.replace(':', '_')}": tags.get(k) for k in KEPT_TAG_COLUMNS},
            "geometry": geom,
        })
    fac = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    report["facilities"] = int(len(fac))
    return fac, report


def _norm_name(name: Any) -> str:
    if _is_missing(name) or not name:
        return ""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def mark_duplicates(fac: gpd.GeoDataFrame, metric_crs: str, point_tolerance_m: float = 15.0,
                    containment_ratio: float = 0.95) -> tuple[gpd.GeoDataFrame, dict]:
    """Flag duplicate OSM representations of the same facility (same category only).

    Rules, applied within each category:
      D1  A point that lies within, or within ``point_tolerance_m`` of, a polygon of the same
          category is a duplicate of that polygon, if the names match or either name is
          missing. OSM often maps a facility both as a POI node and as a building/campus area.
      D2  A polygon at least ``containment_ratio`` covered by a larger same-category polygon is
          a duplicate of it, if the names match or either name is missing.
      D3  Two points of the same category within ``point_tolerance_m`` with the same normalised
          name: the later one (by facility_id) is a duplicate.
    Duplicates stay in the cache (is_duplicate=True, duplicate_of=<id>) for auditing. The
    runtime index excludes them by default. A duplicate never makes the nearest distance
    smaller, because the kept geometry contains or nearly coincides with it.
    """
    fac = fac.copy()
    m = fac.to_crs(metric_crs)
    names = fac["name"].map(_norm_name)
    is_dup = np.zeros(len(fac), dtype=bool)
    dup_of: list[Any] = [None] * len(fac)
    counts = {"D1_point_in_polygon": 0, "D2_polygon_in_polygon": 0, "D3_point_point": 0}
    geoms = m.geometry.values
    ids = fac["facility_id"].values
    is_area = np.array([g.geom_type in ("Polygon", "MultiPolygon") for g in geoms])

    def names_compatible(i: int, j: int) -> bool:
        return not names.iat[i] or not names.iat[j] or names.iat[i] == names.iat[j]

    for cat, idx in fac.groupby("category").indices.items():
        idx = np.asarray(idx)
        polys = idx[is_area[idx]]
        pts = idx[np.array([geoms[i].geom_type == "Point" for i in idx])]
        if len(polys):
            tree = shapely.STRtree(geoms[polys])
            # D1
            if len(pts):
                pairs = tree.query(geoms[pts], predicate="dwithin", distance=point_tolerance_m)
                for p_local, poly_local in zip(*pairs):
                    i, j = pts[p_local], polys[poly_local]
                    if not is_dup[i] and names_compatible(i, j):
                        is_dup[i], dup_of[i] = True, ids[j]
                        counts["D1_point_in_polygon"] += 1
            # D2
            areas = shapely.area(geoms[polys])
            pairs = tree.query(geoms[polys], predicate="intersects")
            for a_local, b_local in zip(*pairs):
                if a_local == b_local:
                    continue
                i, j = polys[a_local], polys[b_local]
                if is_dup[i] or areas[a_local] >= areas[b_local] or areas[a_local] == 0:
                    continue
                inter = shapely.area(shapely.intersection(geoms[i], geoms[j]))
                if inter / areas[a_local] >= containment_ratio and names_compatible(i, j):
                    is_dup[i], dup_of[i] = True, ids[j]
                    counts["D2_polygon_in_polygon"] += 1
        # D3
        live_pts = np.array([i for i in pts if not is_dup[i]], dtype=int)
        if len(live_pts) > 1:
            tree = shapely.STRtree(geoms[live_pts])
            pairs = tree.query(geoms[live_pts], predicate="dwithin", distance=point_tolerance_m)
            for a_local, b_local in zip(*pairs):
                i, j = live_pts[a_local], live_pts[b_local]
                if i == j or is_dup[i] or is_dup[j]:
                    continue
                if names.iat[i] and names.iat[i] == names.iat[j]:
                    later = i if ids[i] > ids[j] else j
                    keep = j if later == i else i
                    is_dup[later], dup_of[later] = True, ids[keep]
                    counts["D3_point_point"] += 1

    # Resolve chains (A dup of B, B dup of C) so duplicate_of always points to a kept facility.
    id_to_pos = {fid: k for k, fid in enumerate(ids)}
    for k in range(len(dup_of)):
        seen = set()
        while dup_of[k] is not None and is_dup[id_to_pos[dup_of[k]]] and dup_of[k] not in seen:
            seen.add(dup_of[k])
            dup_of[k] = dup_of[id_to_pos[dup_of[k]]]
    fac["is_duplicate"] = is_dup
    fac["duplicate_of"] = dup_of
    counts["total_duplicates"] = int(is_dup.sum())
    return fac, counts


def assign_boroughs(fac: gpd.GeoDataFrame, boroughs: gpd.GeoDataFrame, metric_crs: str) -> pd.Series:
    """Borough containing each facility's representative point; 'OUTSIDE_NYC' if none."""
    pts = fac.to_crs(metric_crs).geometry.representative_point()
    b = boroughs.to_crs(metric_crs)[["borough", "geometry"]]
    joined = gpd.sjoin(gpd.GeoDataFrame(geometry=pts, crs=metric_crs), b, how="left",
                       predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    return joined["borough"].fillna("OUTSIDE_NYC").reindex(fac.index)
