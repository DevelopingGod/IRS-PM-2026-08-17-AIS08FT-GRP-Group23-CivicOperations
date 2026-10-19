"""Checks on the real enriched NYC network (skipped if the derived file is absent)."""
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
NPZ = ROOT / "data" / "road_network" / "derived" / "nyc_drive_enriched.npz"
pytestmark = pytest.mark.skipif(not NPZ.exists(), reason="enriched network not built")


@pytest.fixture(scope="module")
def real_router():
    from crew_dispatch.roadnet.network import RoadNetwork
    from crew_dispatch.roadnet.router import Router
    r = Router(RoadNetwork.load(NPZ), speed_factor=0.6)
    r.build_landmarks(8)
    return r


def test_real_heuristic_admissible_bound(real_router):
    rep = real_router.check_heuristic_bound()
    assert rep["violations"] == 0 and rep["edges_checked"] > 140_000


def test_real_astar_matches_dijkstra(real_router):
    rng = np.random.default_rng(42)
    for s, t in rng.choice(real_router.scc_nodes, size=(25, 2)):
        d = real_router.dijkstra(int(s), int(t)).cost_s
        assert real_router.astar(int(s), int(t)).cost_s == pytest.approx(d, rel=1e-12)
        assert real_router.astar(int(s), int(t), heuristic="alt").cost_s == pytest.approx(d, rel=1e-12)


def test_speed_provenance_recorded(real_router):
    net = real_router.net
    labels = set(net.speed_source_labels())
    assert {"osm_maxspeed", "nyc_default_25mph", "highway_class_median"} <= labels
    assert np.all(net.edge_speed_mph >= 5) and np.all(net.edge_speed_mph <= 70)


def test_real_one_way_edges_present(real_router):
    net = real_router.net
    pairs = set(zip(net.edge_u.tolist(), net.edge_v.tolist()))
    one_way = sum((v, u) not in pairs for u, v in pairs)
    assert one_way > 10_000
