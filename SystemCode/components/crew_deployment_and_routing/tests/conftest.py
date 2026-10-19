import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crew_dispatch.catalog import load_catalog  # noqa: E402
from crew_dispatch.config import load_config  # noqa: E402
from crew_dispatch.fixtures import crew_record, grid_network, node_latlon  # noqa: E402
from crew_dispatch.roadnet.router import Router  # noqa: E402
from crew_dispatch.schemas import Crew  # noqa: E402

FAST_GA = {"population": 16, "generations": 30, "stall_generations": 10, "time_limit_s": 1e9, "polish_every": 5}


@pytest.fixture(scope="session")
def net():
    return grid_network()


@pytest.fixture(scope="session")
def router(net):
    r = Router(net, speed_factor=0.6, max_snap_m=500)
    r.build_landmarks(4)
    return r


@pytest.fixture()
def cfg():
    return load_config(overrides={"ga": FAST_GA, "dynamic": {"ga_generations_replan": 15}, "maps": {"enabled": False}})


@pytest.fixture(scope="session")
def catalog():
    return load_catalog(ROOT / "data" / "catalog" / "request_catalog.csv")


@pytest.fixture()
def crews(net):
    return [Crew.from_record(crew_record("C1", "DOT", ["ROAD_REPAIR"], *node_latlon(net, 0, 0), budget=240)),
            Crew.from_record(crew_record("C2", "DOT", ["ROAD_REPAIR", "SIDEWALK_REPAIR"], *node_latlon(net, 7, 7), budget=240)),
            Crew.from_record(crew_record("C3", "DEP", ["WATER_MAINS"], *node_latlon(net, 4, 4), budget=240))]
