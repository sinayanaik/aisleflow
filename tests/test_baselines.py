"""Safety-net and unit tests for the external baseline planners.

Mirrors `test_pibt.py::test_every_variant_is_collision_free` for
`token_passing`/`token_passing_recovery`/`rhcr`, plus direct unit tests of
the shared space-time search primitive they're built on.
"""

from __future__ import annotations

import pytest

from lda_pibt.baselines.space_time_search import (
    ReservationTable,
    prioritized_plan,
    space_time_astar,
)
from lda_pibt.config import BASELINE_PARAMS_PRESET, Params
from lda_pibt.experiments import BASELINE_PLANNERS, run_once
from lda_pibt.graph import GridGraph
from lda_pibt.robot import Robot
from lda_pibt.simulator import build_simulator
from lda_pibt.task import TaskGenerator
from lda_pibt.validate import no_swap_conflicts, no_vertex_conflicts
from lda_pibt.warehouse import Warehouse

MAPS = [
    "corridor",
    "loop",
    "warehouse_small",
    "warehouse_narrow",
    "warehouse_bottleneck",
    "warehouse_corridors",
    "warehouse_medium",
]


def _grid(rows: int, cols: int) -> GridGraph:
    return GridGraph([[True] * cols for _ in range(rows)])


# ------------------------------------------------------------ ReservationTable
def test_reservation_table_blocks_vertex_reuse():
    table = ReservationTable()
    table.reserve_path(1, [(0, 0), (0, 1)], start_time=0)
    assert not table.is_free((0, 1), 1, robot_id=2)
    assert table.is_free((0, 1), 1, robot_id=1)  # a robot's own reservation is not a conflict


def test_reservation_table_blocks_swap():
    table = ReservationTable()
    # robot 1 moves (0,0)->(0,1) between t=0 and t=1
    table.reserve_path(1, [(0, 0), (0, 1)], start_time=0)
    # robot 2 attempting the reverse swap, (0,1)->(0,0), must be rejected
    assert not table.is_free((0, 0), 1, moving_from=(0, 1), robot_id=2)


def test_reserve_hold_blocks_every_timestep_in_range():
    table = ReservationTable()
    table.reserve_hold(1, (2, 2), from_time=5, until_time=8)
    for t in range(5, 9):
        assert not table.is_free((2, 2), t, robot_id=2)
    assert table.is_free((2, 2), 9, robot_id=2)


# -------------------------------------------------------------- space_time_astar
def test_space_time_astar_finds_a_direct_path_on_an_open_grid():
    graph = _grid(5, 5)
    table = ReservationTable()
    path = space_time_astar(graph, (0, 0), (0, 4), 0, table, horizon=10)
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (0, 4)
    assert len(path) - 1 == 4  # Manhattan-optimal on an open grid


def test_space_time_astar_routes_around_a_blocked_cell():
    graph = _grid(3, 3)
    table = ReservationTable()
    # Block the only direct route's midpoint at every relevant time.
    table.reserve_hold(99, (0, 1), from_time=0, until_time=10)
    path = space_time_astar(graph, (0, 0), (0, 2), 0, table, horizon=10)
    assert path is not None
    assert (0, 1) not in path


def test_space_time_astar_unreachable_goal_returns_none():
    # Two disconnected 1x1 "islands" -- GridGraph built from a passable mask
    # with a False gap in between.
    mask = [[True, False, True]]
    graph = GridGraph(mask)
    table = ReservationTable()
    assert space_time_astar(graph, (0, 0), (0, 2), 0, table, horizon=20) is None


def test_space_time_astar_require_goal_false_returns_best_effort_path():
    graph = _grid(1, 20)
    table = ReservationTable()
    # Goal is far beyond a short horizon: strict mode fails, relaxed mode
    # returns the closest reachable progress instead of None.
    assert space_time_astar(graph, (0, 0), (0, 19), 0, table, horizon=5) is None
    path = space_time_astar(
        graph, (0, 0), (0, 19), 0, table, horizon=5, require_goal=False
    )
    assert path is not None
    assert len(path) == 6  # start_time .. start_time+horizon inclusive
    assert path[-1] == (0, 5)  # made maximum progress toward the goal


def test_prioritized_plan_avoids_a_stationary_robot():
    graph = _grid(1, 5)
    mover = Robot(id=0, position=(0, 0))
    mover.waypoint = (0, 4)
    blocker = Robot(id=1, position=(0, 2))
    blocker.waypoint = (0, 2)  # already at its goal -- must not be walked through
    goals = {0: (0, 4), 1: (0, 2)}
    table = ReservationTable()
    paths = prioritized_plan([blocker, mover], goals, table, graph, 0, horizon=10)
    assert (0, 2) not in paths[0][1:]  # mover's path (after its own start) avoids blocker
    assert paths[1] == [(0, 2)]  # blocker legitimately stays put


# --------------------------------------------------------- simulator integration
@pytest.mark.parametrize("variant", sorted(BASELINE_PLANNERS))
@pytest.mark.parametrize("map_name", MAPS)
def test_baseline_is_collision_free(variant, map_name):
    params = Params(seed=3, max_timesteps=120, recovery=BASELINE_PLANNERS[variant][1],
                     **BASELINE_PARAMS_PRESET)
    warehouse = Warehouse.from_file(f"maps/{map_name}.map", params)
    generator = TaskGenerator(
        warehouse.pickup_vertices, warehouse.delivery_vertices, rate=0.6, seed=3
    )
    sim = build_simulator(
        warehouse, 6, params, task_generator=generator,
        planner_factory=BASELINE_PLANNERS[variant][0],
    )
    report = sim.run(max_timesteps=120)
    assert report.collision_free
    assert no_vertex_conflicts(sim.robots)
    assert no_swap_conflicts(sim.robots)


@pytest.mark.parametrize("variant", sorted(BASELINE_PLANNERS))
def test_baseline_completes_some_tasks(variant):
    # warehouse_small, not warehouse_bottleneck: the bottleneck's single
    # corridor is a known genuine gridlock case for planners without
    # priority inheritance (see README) -- this checks the planner *works*,
    # not that it matches PIBT's performance everywhere.
    result = run_once("maps/warehouse_small.map", variant, 10, 300, seed=1, rate=0.8)
    assert result["collision_free"]
    assert result["completed_tasks"] > 0


@pytest.mark.parametrize("variant", sorted(BASELINE_PLANNERS))
@pytest.mark.parametrize("map_name", MAPS)
def test_baseline_no_crash_all_maps(variant, map_name):
    result = run_once(f"maps/{map_name}.map", variant, 6, 50, seed=2, rate=0.6)
    assert result["collision_free"]
