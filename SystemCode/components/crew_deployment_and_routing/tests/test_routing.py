import math

import numpy as np
import pytest

from crew_dispatch.fixtures import LAT0, LON0, grid_network, node_latlon
from crew_dispatch.roadnet import speeds as spd
from crew_dispatch.roadnet.network import RoadNetwork, haversine_m
from crew_dispatch.roadnet.router import Router


def test_astar_equals_dijkstra_all_pairs(router):
    nodes = router.scc_nodes
    for s in nodes[::5]:
        for t in nodes[::7]:
            a = router.astar(int(s), int(t))
            alt = router.astar(int(s), int(t), heuristic="alt")
            d = router.dijkstra(int(s), int(t))
            assert a.cost_s == pytest.approx(d.cost_s, rel=1e-12, abs=1e-9)
            assert alt.cost_s == pytest.approx(d.cost_s, rel=1e-12, abs=1e-9)
            assert a.expanded <= d.expanded


def test_heuristic_bound_holds(router):
    rep = router.check_heuristic_bound()
    assert rep["violations"] == 0


def test_heuristic_never_overestimates(router):
    nodes = router.scc_nodes
    rng = np.random.default_rng(0)
    for s, t in rng.choice(nodes, size=(60, 2)):
        d = router.dijkstra(int(s), int(t)).cost_s
        assert router.heuristic(int(s), int(t)) <= d + 1e-9
        assert router.heuristic_alt(int(s), int(t)) <= d + 1e-9


def test_one_way_row_is_respected(router):
    # row 0 is one-way eastbound: west-bound travel along it must detour via row 1
    east = router.astar(0, 7).cost_s
    west = router.astar(7, 0).cost_s
    assert west > east
    path = router.astar(7, 0).nodes
    assert any(n >= 8 for n in path), "westbound path must leave row 0"


def test_disconnected_island_and_snapping(router, net):
    island = net.n_nodes - 2
    assert not router.in_main_scc[island]
    snap = router.snap([LAT0 - 0.02, float("nan"), node_latlon(net, 3, 3)[0]], [LON0 - 0.02, LON0, node_latlon(net, 3, 3)[1]])
    assert snap.reason == ["snap_distance_exceeded", "missing_coordinates", None]
    assert list(snap.ok) == [False, False, True]
    assert snap.node[2] == 3 * 8 + 3


def test_snap_never_returns_non_scc_node():
    net = grid_network(island=True)
    r = Router(net, max_snap_m=10_000)
    s = r.snap([LAT0 - 0.02], [LON0 - 0.02])     # right on the island
    assert s.ok[0] and r.in_main_scc[s.node[0]]


def test_matrix_backends_agree(router):
    nodes = list(router.scc_nodes[:12])
    a = router.matrix_minutes(nodes, nodes, backend="astar")
    d = router.matrix_minutes(nodes, nodes, backend="dijkstra")
    assert np.allclose(a, d, rtol=1e-6, atol=1e-6)
    assert np.allclose(np.diag(d), 0)


def test_path_geometry_is_continuous(router):
    p = router.astar(0, 63)
    xy = router.path_geometry(p.edges)
    assert len(xy) == len(p.edges) + 1
    assert router.path_length_m(p.edges) >= haversine_m(router.net.node_lat[0], router.net.node_lon[0],
                                                         router.net.node_lat[63], router.net.node_lon[63])


def test_unreachable_returns_inf():
    net = grid_network(island=True)
    r = Router(net)
    island = net.n_nodes - 2
    assert math.isinf(r.astar(0, island).cost_s)


@pytest.mark.parametrize("raw,mph,status", [
    ("25 mph", 25.0, "single"), ("['15 mph', '25 mph']", 20.0, "list"), ("2 mph", None, "invalid"),
    ("50", 50 / 1.609344, "single"), (None, None, "missing"), ("signals", None, "unparseable"),
    ("['4 mph', '25 mph']", 25.0, "list")])
def test_parse_maxspeed(raw, mph, status):
    v, st = spd.parse_maxspeed(raw)
    assert st == status
    assert (v is None and mph is None) or v == pytest.approx(mph)


def test_speed_imputation_hierarchy():
    hws = ["residential", "motorway", "motorway", "residential"] + ["motorway"] * 25
    maxs = [None, None, None, "30 mph"] + ["50 mph"] * 25
    inside = [True, True, False, True] + [True] * 25
    mph, src, rep = spd.impute_speeds(hws, maxs, inside)
    assert (mph[0], src[0]) == (25.0, "nyc_default_25mph")
    assert (mph[1], src[1]) == (50.0, "highway_class_median")   # NYC default not applied to motorways
    assert src[2] == "highway_class_median"
    assert (mph[3], src[3]) == (30.0, "osm_maxspeed")


def test_speed_factor_scales_travel_time():
    net = grid_network()
    a = Router(net, speed_factor=1.0).astar(0, 63).cost_s
    b = Router(net, speed_factor=0.5).astar(0, 63).cost_s
    assert b == pytest.approx(2 * a)
