"""Spatial proximity engine: projected straight-line distances with per-category STRtree indexes.

* Coordinates are projected from WGS84 to a metric CRS (default EPSG:32118, NAD83 / New York
  Long Island, metres). Metres are never computed by subtracting latitude/longitude values.
* The distance to a facility is the planar distance to its full geometry: point to point, or
  point to the nearest polygon boundary (0 when the request lies inside the polygon). Polygon
  centroids are never used.
* Each facility category has its own STRtree, so candidate search is O(log n) and categories
  can carry different thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import shapely
import shapely.ops
from pyproj import Geod, Transformer

_GEOD = Geod(ellps="WGS84")


@dataclass
class CategoryIndex:
    category: str
    rows: np.ndarray        # row positions into SpatialEngine.facilities
    geoms: np.ndarray       # metric geometries
    tree: shapely.STRtree


class SpatialEngine:
    def __init__(self, facilities: gpd.GeoDataFrame, boroughs: gpd.GeoDataFrame,
                 metric_crs: str = "EPSG:32118", categories: tuple | list | None = None,
                 include_duplicates: bool = False):
        if facilities.crs is None:
            raise ValueError("facilities GeoDataFrame must have a CRS")
        fac = facilities
        if not include_duplicates and "is_duplicate" in fac.columns:
            fac = fac[~fac["is_duplicate"].astype(bool)]
        if categories is not None:
            fac = fac[fac["category"].isin(list(categories))]
        self.metric_crs = metric_crs
        self.facilities = fac.reset_index(drop=True)
        self._metric = self.facilities.to_crs(metric_crs)
        self._to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
        self._to_wgs84 = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
        self.indexes: dict[str, CategoryIndex] = {}
        geoms_all = self._metric.geometry.values
        for cat, rows in self.facilities.groupby("category").indices.items():
            rows = np.asarray(rows)
            geoms = np.asarray(geoms_all[rows])
            self.indexes[cat] = CategoryIndex(cat, rows, geoms, shapely.STRtree(geoms))
        b = boroughs.to_crs(metric_crs).reset_index(drop=True)
        self._borough_names = b["borough"].astype(str).str.upper().to_numpy()
        self._borough_geoms = np.asarray(b.geometry.values)
        self._borough_tree = shapely.STRtree(self._borough_geoms)
        self._nyc_union = shapely.union_all(self._borough_geoms)
        shapely.prepare(self._nyc_union)

    # ------------------------------------------------------------------------------------------
    def project(self, lat, lon) -> np.ndarray:
        """WGS84 lat/lon arrays -> array of metric shapely Points."""
        lat = np.atleast_1d(np.asarray(lat, dtype=float))
        lon = np.atleast_1d(np.asarray(lon, dtype=float))
        x, y = self._to_metric.transform(lon, lat)
        return shapely.points(np.asarray(x), np.asarray(y))

    def to_wgs84(self, x, y):
        lon, lat = self._to_wgs84.transform(x, y)
        return lat, lon

    def in_coverage(self, points: np.ndarray, tolerance_m: float = 0.0) -> np.ndarray:
        if tolerance_m > 0:
            return shapely.dwithin(self._nyc_union, points, tolerance_m)
        return shapely.intersects(self._nyc_union, points)

    def borough_of(self, points: np.ndarray) -> list:
        out = [None] * len(points)
        p_idx, b_idx = self._borough_tree.query(points, predicate="within")
        for p, b in zip(p_idx, b_idx):
            if out[p] is None:
                out[p] = self._borough_names[b]
        return out

    # ------------------------------------------------------------------------------------------
    def within(self, points: np.ndarray, category: str, radius_m) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """All (point_index, facility_row, distance_m) pairs with distance <= radius_m.

        radius_m may be a scalar or an array broadcastable to ``points``.
        """
        idx = self.indexes.get(category)
        if idx is None or len(points) == 0:
            e = np.empty(0, dtype=int)
            return e, e, np.empty(0, dtype=float)
        radius = np.broadcast_to(np.asarray(radius_m, dtype=float), (len(points),))
        p_local, g_local = idx.tree.query(points, predicate="dwithin", distance=radius)
        d = shapely.distance(points[p_local], idx.geoms[g_local])
        return p_local.astype(int), idx.rows[g_local].astype(int), np.asarray(d, dtype=float)

    def nearest(self, points: np.ndarray, category: str) -> tuple[np.ndarray, np.ndarray]:
        """Nearest facility row and distance per point (row -1, distance inf if category empty)."""
        n = len(points)
        rows = np.full(n, -1, dtype=int)
        dist = np.full(n, np.inf, dtype=float)
        idx = self.indexes.get(category)
        if idx is None or n == 0:
            return rows, dist
        (p_local, g_local), d = idx.tree.query_nearest(points, return_distance=True, all_matches=False)
        rows[p_local] = idx.rows[g_local]
        dist[p_local] = d
        return rows, dist

    def nearest_distance_matrix(self, points: np.ndarray, categories) -> dict:
        """category -> nearest distance array (research helper)."""
        return {c: self.nearest(points, c)[1] for c in categories}

    # ------------------------------------------------------------------------------------------
    def brute_force_distances(self, point, category: str) -> np.ndarray:
        """Distances from one metric point to every facility in a category (for verification)."""
        idx = self.indexes.get(category)
        if idx is None:
            return np.empty(0)
        return np.asarray(shapely.distance(point, idx.geoms), dtype=float)

    def geodesic_distance_m(self, point, facility_row: int) -> float:
        """Ellipsoidal (WGS84) distance from a metric point to the nearest point of a facility.

        The nearest point is located in the projected plane. The distance between the two points
        is then measured on the WGS84 ellipsoid with pyproj.Geod. Used to verify planar distances.
        """
        geom = self._metric.geometry.iloc[facility_row]
        if shapely.intersects(geom, point):
            return 0.0
        a, b = shapely.ops.nearest_points(point, geom)
        lat1, lon1 = self.to_wgs84(a.x, a.y)
        lat2, lon2 = self.to_wgs84(b.x, b.y)
        _, _, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
        return float(dist)
