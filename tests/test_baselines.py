"""Safety-net and unit tests for the published baseline planners.

Mirrors `test_pibt.py::test_every_variant_is_collision_free` for
`token_passing`/`token_passing_task_swaps`/`rhcr`, plus direct unit tests of
the two space-time searches they are built on -- which are different searches
on purpose, and were one search before, which is how both baselines came to
report near-zero throughput on every map.
"""

from __future__ import annotations

import pytest

from lda_pibt.baselines.space_time_search import (
    ReservationTable,
    bounded_horizon_astar,
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

#: what the default run covers. These planners re-solve a space-time search
#: per agent per task (Token Passing) or per window (RHCR), so they cost two
#: orders of magnitude what a PIBT variant costs: one head-on map, one general
#: map and one known-gridlock map keep the distinct failure modes, and
#: `-m slow` runs the rest.
CORE_MAPS = ["corridor", "warehouse_small", "warehouse_bottleneck"]

#: the default horizon. Long enough for the corridor to fill and for a planner
#: that is going to deadlock to have deadlocked.
CORE_STEPS = 60

#: runs already computed, keyed by their arguments. Several assertions want the
#: same simulation, and that is the expensive part, not the assertion.
_RUNS: dict = {}


def _grid(rows: int, cols: int) -> GridGraph:
    return GridGraph([[True] * cols for _ in range(rows)])


def baseline_run(variant: str, map_name: str, steps: int = CORE_STEPS):
    """A seeded lifelong run of one baseline planner, computed at most once."""
    key = (variant, map_name, steps)
    if key not in _RUNS:
        params = Params(seed=3, max_timesteps=steps, **BASELINE_PARAMS_PRESET)
        warehouse = Warehouse.from_file(f"maps/{map_name}.map", params)
        generator = TaskGenerator(
            warehouse.pickup_vertices, warehouse.delivery_vertices,
            rate=0.6, seed=3,
        )
        sim = build_simulator(
            warehouse, 6, params, task_generator=generator,
            planner_factory=BASELINE_PLANNERS[variant],
        )
        _RUNS[key] = (sim, sim.run(max_timesteps=steps))
    return _RUNS[key]


# ------------------------------------------------------------ ReservationTable
def test_reservation_table_blocks_vertex_reuse():
    table = ReservationTable()
    table.reserve_path(1, [(0, 0), (0, 1)], start_time=0, rest_at_end=False)
    assert not table.is_free((0, 1), 1, robot_id=2)
    assert table.is_free((0, 1), 1, robot_id=1)  # a robot's own reservation is not a conflict


def test_reservation_table_blocks_swap():
    table = ReservationTable()
    # robot 1 moves (0,0)->(0,1) between t=0 and t=1
    table.reserve_path(1, [(0, 0), (0, 1)], start_time=0, rest_at_end=False)
    # robot 2 attempting the reverse swap, (0,1)->(0,0), must be rejected
    assert not table.is_free((0, 0), 1, moving_from=(0, 1), robot_id=2)


def test_a_finished_path_holds_its_last_cell_forever():
    """The property a fixed-length hold cannot express, and the reason for it.

    A Token Passing agent rests at the end of its path until it next receives
    the token, which may be hundreds of timesteps later. Reserving a finite
    hold instead lets a later robot plan straight through the cell once the
    hold lapses -- a real collision, not a theoretical one.
    """
    table = ReservationTable()
    table.reserve_path(1, [(0, 0), (0, 1)], start_time=0)
    for t in (1, 50, 5_000):
        assert not table.is_free((0, 1), t, robot_id=2)
    assert not table.rest_is_clear((0, 1), 1, robot_id=2)
    assert table.rest_is_clear((0, 1), 1, robot_id=1)


# ------------------------------------------- space_time_astar (Token Passing)
def test_space_time_astar_finds_a_direct_path_on_an_open_grid():
    graph = _grid(5, 5)
    path = space_time_astar(graph, (0, 0), (0, 4), 0, ReservationTable())
    assert path is not None
    assert path[0] == (0, 0) and path[-1] == (0, 4)
    assert len(path) - 1 == 4  # Manhattan-optimal on an open grid


def test_space_time_astar_routes_around_a_blocked_cell():
    graph = _grid(3, 3)
    table = ReservationTable()
    table.reserve_path(99, [(0, 1)], start_time=0)  # someone resting there
    path = space_time_astar(graph, (0, 0), (0, 2), 0, table, robot_id=1)
    assert path is not None
    assert (0, 1) not in path


def test_space_time_astar_reaches_a_goal_far_beyond_any_fixed_horizon():
    """The bug this file exists to prevent recurring.

    The search used to take a `horizon` and fail if the goal was further away
    than it. Every robot on a map wider than the horizon then failed on the
    same timestep, waited, and the planner reported a deadlock that was
    entirely its caller's arithmetic.
    """
    graph = _grid(1, 200)
    path = space_time_astar(graph, (0, 0), (0, 199), 0, ReservationTable())
    assert path is not None
    assert path[-1] == (0, 199)
    assert len(path) - 1 == 199


def test_space_time_astar_unreachable_goal_returns_none():
    graph = GridGraph([[True, False, True]])
    assert space_time_astar(graph, (0, 0), (0, 2), 0, ReservationTable()) is None


# --------------------------------------------------- bounded_horizon_astar (RHCR)
def test_bounded_horizon_astar_makes_progress_toward_a_distant_goal():
    """RHCR's window bounds collision resolution, not the journey.

    A goal 19 steps away and a 5-step window is the *normal* case in a
    lifelong warehouse, and it has to return five steps of progress rather
    than "no path".
    """
    graph = _grid(1, 20)
    path = bounded_horizon_astar(graph, (0, 0), [(0, 19)], 0, ReservationTable(), window=5)
    assert path is not None
    assert len(path) == 6  # start_time .. start_time + window inclusive
    assert path[-1] == (0, 5)


def test_bounded_horizon_astar_visits_its_goals_in_order():
    """A robot on its way to a pickup is planned pickup-then-delivery."""
    graph = _grid(1, 12)
    path = bounded_horizon_astar(
        graph, (0, 4), [(0, 2), (0, 6)], 0, ReservationTable(), window=8
    )
    assert path is not None
    assert path.index((0, 2)) < path.index((0, 6))


def test_bounded_horizon_astar_pads_to_the_window_when_it_arrives_early():
    graph = _grid(1, 6)
    path = bounded_horizon_astar(graph, (0, 0), [(0, 2)], 0, ReservationTable(), window=7)
    assert path is not None
    assert len(path) == 8
    assert path[-1] == (0, 2)


def test_prioritized_plan_avoids_a_stationary_robot():
    graph = _grid(1, 5)
    mover = Robot(id=0, position=(0, 0))
    blocker = Robot(id=1, position=(0, 2))
    goals = {0: [(0, 4)], 1: [(0, 2)]}
    paths = prioritized_plan(
        [blocker, mover], goals, ReservationTable(), graph, 0, window=10
    )
    assert paths[1] == [(0, 2)] * 11  # blocker legitimately stays put
    assert (0, 2) not in paths[0][1:]  # and the mover routes around it


# --------------------------------------------------------- simulator integration
@pytest.mark.parametrize("variant", sorted(BASELINE_PLANNERS))
@pytest.mark.parametrize("map_name", CORE_MAPS)
def test_baseline_is_collision_free(variant, map_name):
    sim, report = baseline_run(variant, map_name)
    assert report.collision_free
    assert no_vertex_conflicts(sim.robots)
    assert no_swap_conflicts(sim.robots)


@pytest.mark.parametrize("variant", sorted(BASELINE_PLANNERS))
@pytest.mark.parametrize("map_name", CORE_MAPS)
def test_baseline_robots_stay_on_the_graph(variant, map_name):
    """The other half of "did not crash": every position is a real vertex."""
    sim, _ = baseline_run(variant, map_name)
    for robot in sim.robots:
        assert robot.position in sim.warehouse.graph.vertex_set


@pytest.mark.slow
@pytest.mark.parametrize("variant", sorted(BASELINE_PLANNERS))
@pytest.mark.parametrize("map_name", [m for m in MAPS if m not in CORE_MAPS])
def test_baseline_is_collision_free_on_every_other_map(variant, map_name):
    """The wide sweep: the four maps the default run leaves out, at 120 steps."""
    sim, report = baseline_run(variant, map_name, steps=120)
    assert report.collision_free
    assert no_vertex_conflicts(sim.robots)
    assert no_swap_conflicts(sim.robots)


@pytest.mark.parametrize("variant", sorted(BASELINE_PLANNERS))
def test_baseline_delivers_at_a_credible_rate(variant):
    """Not "> 0": a baseline can clear that bar while being broken.

    Every one of these was passing a `completed_tasks > 0` check while
    delivering three tasks out of six hundred, which is what a planner looks
    like when its search has been given the wrong question rather than when it
    is genuinely beaten. On an uncrowded floor -- 8 robots on
    `warehouse_medium`, well inside every one of these algorithms' design
    envelope -- all three should be in the same league as the PIBT variants,
    which deliver about 24 tasks in 150 steps here.
    """
    result = run_once("maps/warehouse_medium.map", variant, 8, 150, seed=1, rate=1.5)
    assert result["collision_free"]
    assert result["completed_tasks"] >= 12, (
        f"{variant} delivered {result['completed_tasks']} tasks on an "
        f"uncrowded floor; a published lifelong planner does not do that "
        f"unless something is asking it the wrong question"
    )


def test_token_passing_owns_its_own_task_assignment():
    """The rule that makes Token Passing plannable is an *assignment* rule.

    Algorithm 1 line 6 will not hand out a task whose pickup or delivery
    another agent is resting on. A distance-greedy assigner happily gives
    eight robots tasks that share a delivery cell, and then seven of them
    cannot plan. Running Token Passing on top of somebody else's assignment
    is not Token Passing with a different router.
    """
    params = Params(seed=0, max_timesteps=20, **BASELINE_PARAMS_PRESET)
    warehouse = Warehouse.from_file("maps/warehouse_small.map", params)
    generator = TaskGenerator(
        warehouse.pickup_vertices, warehouse.delivery_vertices, rate=2.0, seed=0
    )
    sim = build_simulator(
        warehouse, 6, params, task_generator=generator,
        planner_factory=BASELINE_PLANNERS["token_passing"],
    )
    assert sim.planner_assigns_tasks
    sim.run(max_timesteps=20)
    ends = [
        sim.planner.paths[robot.id][-1]
        for robot in sim.robots
        if sim.planner.paths.get(robot.id)
    ]
    assert len(set(ends)) == len(ends), "two agents committed to the same rest cell"
