"""GUI backend tests: session, API payloads and the candidate explainer."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from lda_pibt.gui.server import Handler, SimulationSession

MAP = Path("maps/warehouse_small.map")


@pytest.fixture(scope="module")
def session() -> SimulationSession:
    return SimulationSession(MAP, n_robots=8, variant="full_lda_pibt", rate=0.6, seed=1)


@pytest.fixture(scope="module")
def base_url(session):
    handler = type("Bound", (Handler,), {"session": session})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(base, path):
    return json.loads(urllib.request.urlopen(base + path).read())


def post(base, path, body):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(request).read())


# ------------------------------------------------------------------ layout
def test_layout_matches_the_warehouse(session):
    layout = session.layout()
    warehouse = session.sim.warehouse
    assert layout["width"] == warehouse.width
    assert layout["height"] == warehouse.height
    assert len(layout["passable"]) == warehouse.height
    assert len(layout["aisles"]) == len(warehouse.aisles)


def test_state_is_json_serialisable(session):
    json.dumps(session.state())


def test_state_covers_every_robot_and_aisle(session):
    state = session.state()
    assert len(state["robots"]) == len(session.sim.robots)
    assert len(state["aisles"]) == len(session.sim.warehouse.aisles)


# ----------------------------------------------------------------- driving
def test_advance_moves_the_clock(session):
    before = session.sim.timestep
    session.advance(5)
    assert session.sim.timestep == before + 5


def test_reset_returns_to_zero(session):
    session.advance(3)
    session.reset()
    assert session.sim.timestep == 0
    assert session.state()["metrics"]["completed"] == 0


def test_reconfigure_rebuilds_with_new_settings(session):
    session.reconfigure({"n_robots": 5, "overrides": {"turn_penalty": 7.5}})
    assert len(session.sim.robots) == 5
    assert session.sim.params.turn_penalty == 7.5
    assert session.sim.timestep == 0
    session.reconfigure({"n_robots": 8, "overrides": {"turn_penalty": 0.5}})


# ---------------------------------------------------------------- explainer
def test_explainer_lists_every_candidate(session):
    session.reset()
    session.advance(20)
    robot = session.sim.robots[0]
    rows = session.sim.planner.explain_candidates(robot, session.sim.timestep - 1)
    assert len(rows) == len(session.sim.planner.candidates(robot))
    assert all("reasons" in row and "score" in row for row in rows)


def test_explainer_marks_exactly_one_chosen_move(session):
    session.reset()
    session.advance(20)
    for robot in session.sim.robots:
        rows = session.sim.planner.explain_candidates(robot, session.sim.timestep - 1)
        assert sum(1 for row in rows if row["chosen"]) == 1


def test_the_chosen_move_is_never_reported_as_self_conflicting(session):
    """A robot's own reservation must not look like a conflict afterwards."""
    session.reset()
    session.advance(30)
    for robot in session.sim.robots:
        for row in session.sim.planner.explain_candidates(
            robot, session.sim.timestep - 1
        ):
            if row["chosen"]:
                assert "vertex-conflict" not in row["reasons"]


def test_inspect_robot_handles_a_missing_id(session):
    assert "error" in session.inspect_robot(9999)


# ---------------------------------------------------------------- heatmaps
@pytest.mark.parametrize("kind", ["congestion", "stall", "priority", "none"])
def test_heatmaps_have_grid_shape(session, kind):
    grid = session.heatmap(kind)
    assert len(grid) == session.sim.warehouse.height
    assert all(len(row) == session.sim.warehouse.width for row in grid)


# ------------------------------------------------------------ http surface
def test_index_page_is_served(base_url):
    html = urllib.request.urlopen(base_url + "/").read().decode()
    assert "SPAR-PIBT" in html
    assert "<canvas" in html


def test_init_endpoint(base_url):
    payload = get(base_url, "/api/init")
    assert set(payload) == {"layout", "options", "state"}
    assert payload["options"]["maps"]
    assert payload["options"]["tunable"]


def test_step_endpoint_advances_and_stays_collision_free(base_url):
    state = post(base_url, "/api/step", {"n": 10})
    assert state["timestep"] >= 10
    assert state["metrics"]["collision_free"]


def test_robot_endpoint(base_url):
    state = get(base_url, "/api/state")
    robot_id = state["robots"][0]["id"]
    payload = get(base_url, f"/api/robot?id={robot_id}")
    assert payload["id"] == robot_id
    assert payload["candidates"]


def test_heatmap_endpoint(base_url):
    assert get(base_url, "/api/heatmap?kind=congestion")["grid"]


def test_unknown_route_is_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(base_url + "/api/nope")
    assert excinfo.value.code == 404
