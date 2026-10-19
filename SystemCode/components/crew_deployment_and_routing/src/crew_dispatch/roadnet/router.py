"""Snapping, A* search, Dijkstra reference, travel-time rows/matrices and route geometry.

Edge cost  c(e) = length_e / (posted_mph_e * 0.44704 * speed_factor)            [seconds]
Heuristic  h(n) = haversine(n, target) / v_max * (1 - 1e-9)                       [seconds]

Admissibility: every edge satisfies length_e >= haversine(u, v) (checked over the whole graph by
``check_heuristic_bound``) and speed_e <= v_max, so c(e) >= haversine(u, v) / v_max. By the triangle
inequality of great-circle distance, h is also consistent: h(u) <= c(u, v) + h(v). With a consistent
heuristic A* returns optimal paths and never re-expands a closed node.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra as sp_dijkstra
from scipy.spatial import cKDTree

from .network import EARTH_RADIUS_M, RoadNetwork, haversine_m
from .speeds import MPH_TO_MPS


@dataclass
class SnapResult:
    node: np.ndarray        # int64, -1 when unroutable
    distance_m: np.ndarray  # float64 distance to the snapped node (nan when no coordinates)
    ok: np.ndarray          # bool
    reason: list            # per point: None | "missing_coordinates" | "snap_distance_exceeded"


@dataclass
class PathResult:
    cost_s: float
    nodes: list
    edges: list
    expanded: int


class Router:
    def __init__(self, network: RoadNetwork, speed_factor: float = 1.0, max_snap_m: float = 500.0):
        if not (0 < speed_factor <= 2):
            raise ValueError("speed_factor must be in (0, 2]")
        self.net = network
        self.speed_factor = float(speed_factor)
        self.max_snap_m = float(max_snap_m)
        n = network.n_nodes
        # Floor at 1 ms: scipy treats stored zeros ambiguously, and a zero-cost edge is not physical.
        travel_s = np.maximum(network.edge_length_m / (network.edge_speed_mph * MPH_TO_MPS * self.speed_factor), 1e-3)

        # Collapse parallel edges to the fastest one and drop self-loops.
        u, v = network.edge_u.astype(np.int64), network.edge_v.astype(np.int64)
        keep = u != v
        eid = np.nonzero(keep)[0]
        order = np.lexsort((travel_s[eid], v[eid], u[eid]))
        eid = eid[order]
        first = np.ones(len(eid), bool)
        first[1:] = (u[eid][1:] != u[eid][:-1]) | (v[eid][1:] != v[eid][:-1])
        eid = eid[first]
        self.edge_ids = eid                                    # CSR position -> original edge id
        self.csr = csr_matrix((travel_s[eid], (u[eid], v[eid])), shape=(n, n))
        self.csr.sort_indices()
        # csr_matrix sorts by (row, col) exactly like our lexsort, so positions align with edge_ids.
        assert np.array_equal(self.csr.indices, v[eid].astype(self.csr.indices.dtype))
        self.travel_s_by_edge = travel_s
        speeds_mps = network.edge_length_m[eid] / travel_s[eid]
        self.v_max_mps = float(speeds_mps.max()) if len(eid) else 1.0

        _, labels = connected_components(self.csr, directed=True, connection="strong")
        largest = np.bincount(labels).argmax()
        self.in_main_scc = labels == largest
        self.scc_nodes = np.nonzero(self.in_main_scc)[0]
        lat0 = math.radians(float(np.mean(network.node_lat)))
        self._kx = math.cos(lat0) * EARTH_RADIUS_M
        self._tree = cKDTree(self._project(network.node_lat[self.scc_nodes], network.node_lon[self.scc_nodes]))

        # Python lists for the pure-Python searches (much faster element access than numpy).
        self._indptr = self.csr.indptr.tolist()
        self._indices = self.csr.indices.tolist()
        self._weights = self.csr.data.tolist()
        self._lat_r = np.radians(network.node_lat).tolist()
        self._lon_r = np.radians(network.node_lon).tolist()
        self._cos_lat = [math.cos(x) for x in self._lat_r]
        self._rows: dict[int, np.ndarray] = {}
        self._rev_csr = None

    # ------------------------------------------------------------------ snapping
    def _project(self, lat, lon):
        lat = np.radians(np.asarray(lat, float))
        lon = np.radians(np.asarray(lon, float))
        return np.column_stack([lon * self._kx, lat * EARTH_RADIUS_M])

    def snap(self, lats, lons) -> SnapResult:
        """Nearest node of the largest strongly connected component (so every pair is routable)."""
        lats = np.asarray(lats, float)
        lons = np.asarray(lons, float)
        node = np.full(len(lats), -1, np.int64)
        dist = np.full(len(lats), np.nan)
        reason = [None] * len(lats)
        have = np.isfinite(lats) & np.isfinite(lons)
        if have.any():
            _, k = self._tree.query(self._project(lats[have], lons[have]))
            cand = self.scc_nodes[k]
            d = haversine_m(lats[have], lons[have], self.net.node_lat[cand], self.net.node_lon[cand])
            node[have], dist[have] = cand, d
        ok = have & (dist <= self.max_snap_m)
        for i in range(len(lats)):
            if not have[i]:
                reason[i] = "missing_coordinates"
            elif not ok[i]:
                reason[i] = "snap_distance_exceeded"
        node[~ok] = -1
        return SnapResult(node=node, distance_m=dist, ok=ok, reason=reason)

    # ------------------------------------------------------------------ heuristic
    def heuristic(self, a: int, b: int) -> float:
        la, lb = self._lat_r[a], self._lat_r[b]
        s = math.sin((lb - la) / 2) ** 2 + self._cos_lat[a] * self._cos_lat[b] * math.sin((self._lon_r[b] - self._lon_r[a]) / 2) ** 2
        return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(s))) / self.v_max_mps * (1 - 1e-9)

    def build_landmarks(self, k: int = 8, seed: int = 0) -> None:
        """ALT preprocessing: farthest-point landmarks with forward and backward distances.

        h_L(n, t) = max_L max(d(L, t) - d(L, n), d(n, L) - d(t, L)) is a lower bound on d(n, t) by
        the triangle inequality, so it is admissible (and consistent) for the same edge costs.
        """
        rng = np.random.default_rng(seed)
        rev = self.csr.T.tocsr()
        chosen = [int(rng.choice(self.scc_nodes))]
        fwd, bwd = [], []
        min_d = None
        for _ in range(k):
            f = sp_dijkstra(self.csr, directed=True, indices=chosen[-1])
            b = sp_dijkstra(rev, directed=True, indices=chosen[-1])
            fwd.append(f)
            bwd.append(b)
            d = np.where(self.in_main_scc, f + b, -1)
            min_d = d if min_d is None else np.minimum(min_d, d)
            if len(chosen) < k:
                chosen.append(int(np.argmax(np.where(self.in_main_scc, min_d, -1))))
        # Unreachable entries (outside the main SCC) contribute nothing to the bound.
        F = np.where(np.isfinite(np.vstack(fwd)), np.vstack(fwd), np.nan)
        B = np.where(np.isfinite(np.vstack(bwd)), np.vstack(bwd), np.nan)
        self.landmarks = chosen
        self._lm_fwd = [tuple(col) for col in F.T.tolist()]   # per node: d(L_i, n)
        self._lm_bwd = [tuple(col) for col in B.T.tolist()]   # per node: d(n, L_i)

    def heuristic_alt(self, a: int, b: int) -> float:
        fa, fb, ba, bb = self._lm_fwd[a], self._lm_fwd[b], self._lm_bwd[a], self._lm_bwd[b]
        best = 0.0
        for i in range(len(fa)):
            x = fb[i] - fa[i]
            y = ba[i] - bb[i]
            if x > best:
                best = x
            if y > best:
                best = y
        best = best if best == best else 0.0  # nan-safe
        return max(best * (1 - 1e-9), self.heuristic(a, b))

    def check_heuristic_bound(self) -> dict:
        """Verify c(e) >= h-bound for every CSR edge, i.e. the condition behind admissibility."""
        rows = np.repeat(np.arange(self.net.n_nodes), np.diff(self.csr.indptr))
        cols = self.csr.indices
        hav = haversine_m(self.net.node_lat[rows], self.net.node_lon[rows], self.net.node_lat[cols], self.net.node_lon[cols])
        lower = hav / self.v_max_mps * (1 - 1e-9)
        slack = self.csr.data - lower
        return {"edges_checked": int(len(slack)), "violations": int((slack < 0).sum()),
                "min_slack_s": float(slack.min()) if len(slack) else 0.0, "v_max_mps": self.v_max_mps}

    # ------------------------------------------------------------------ point-to-point searches
    def astar(self, source: int, target: int, use_heuristic: bool = True, heuristic: str = "haversine") -> PathResult:
        """A* (or Dijkstra with use_heuristic=False) from source to target on travel time.

        heuristic: 'haversine' (straight line / v_max) or 'alt' (landmarks, requires build_landmarks).
        """
        if source == target:
            return PathResult(0.0, [source], [], 0)
        indptr, indices, weights = self._indptr, self._indices, self._weights
        h = self.heuristic_alt if heuristic == "alt" else self.heuristic
        g = {source: 0.0}
        parent = {source: (-1, -1)}
        closed = set()
        heap = [(h(source, target) if use_heuristic else 0.0, 0.0, source)]
        expanded = 0
        while heap:
            _, gu, u = heapq.heappop(heap)
            if u in closed:
                continue
            closed.add(u)
            expanded += 1
            if u == target:
                break
            for pos in range(indptr[u], indptr[u + 1]):
                w = indices[pos]
                if w in closed:
                    continue
                gw = gu + weights[pos]
                if gw < g.get(w, math.inf):
                    g[w] = gw
                    parent[w] = (u, pos)
                    heapq.heappush(heap, (gw + (h(w, target) if use_heuristic else 0.0), gw, w))
        if target not in closed:
            return PathResult(math.inf, [], [], expanded)
        nodes, edges = [target], []
        while nodes[-1] != source:
            p, pos = parent[nodes[-1]]
            edges.append(int(self.edge_ids[pos]))
            nodes.append(p)
        return PathResult(g[target], nodes[::-1], edges[::-1], expanded)

    def dijkstra(self, source: int, target: int) -> PathResult:
        return self.astar(source, target, use_heuristic=False)

    # ------------------------------------------------------------------ one-to-many rows (matrix backend)
    def rows(self, sources) -> dict:
        """Shortest travel time (s) from each source to every node; cached per source node."""
        need = sorted({int(s) for s in sources if int(s) not in self._rows})
        for k in range(0, len(need), 64):
            chunk = need[k:k + 64]
            dist = sp_dijkstra(self.csr, directed=True, indices=chunk)
            for s, row in zip(chunk, np.atleast_2d(dist)):
                self._rows[s] = row.astype(np.float32)
        return {int(s): self._rows[int(s)] for s in sources}

    def matrix_minutes(self, origins, destinations, backend: str = "dijkstra") -> np.ndarray:
        """Travel-time matrix in minutes. backend: 'dijkstra' (one-to-many) or 'astar' (pairwise)."""
        origins = [int(o) for o in origins]
        destinations = [int(d) for d in destinations]
        if backend == "astar":
            out = np.empty((len(origins), len(destinations)))
            for i, o in enumerate(origins):
                for j, d in enumerate(destinations):
                    out[i, j] = self.astar(o, d).cost_s
            return out / 60.0
        r = self.rows(origins)
        return np.vstack([r[o][destinations] for o in origins]).astype(float) / 60.0 if origins else np.zeros((0, len(destinations)))

    def clear_cache(self):
        self._rows.clear()

    # ------------------------------------------------------------------ geometry
    def path_geometry(self, edges) -> np.ndarray:
        """Concatenate edge geometries (lon, lat) along a path."""
        if not len(edges):
            return np.zeros((0, 2))
        parts = [self.net.edge_geometry(e) for e in edges]
        out = [parts[0]] + [p[1:] for p in parts[1:]]
        return np.concatenate(out)

    def path_length_m(self, edges) -> float:
        return float(self.net.edge_length_m[list(edges)].sum()) if len(edges) else 0.0
