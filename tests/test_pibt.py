"""PIBT correctness tests (spec sections 22, 25, 26, 31)."""

from __future__ import annotations

import pytest

from lda_pibt import (
    Params,
    Robot,
    Simulator,
    Warehouse,
    build_simulator,
    no_swap_conflicts,
    no_vertex_conflicts,
)
from lda_pibt.config import ablation
from lda_pibt.task import TaskGenerator
from lda_pibt.types import PlanningError, RobotState
from lda_pibt.validate import (
    contains_swap_conflict,
    contains_vertex_conflict,
    execute_moves,
    validate_plan,
)

OPEN_GRID = "\n".join(["......"] * 6)
RING = """
.....
.@@@.
.@@@.
.@@@.
.....
""".strip()


def static_sim(text: str, starts, goals, **overrides) -> Simulator:
    params = Params(lifelong=False, **overrides)
    warehouse = Warehouse.from_string(text, params)
    robots = [Robot(id=i, position=v) for i, v in enumerate(starts)]
    return Simulator(
        warehouse,
        robots,
        params=params,
        static_goals={i: g for i, g in enumerate(goals)},
    )


# --------------------------------------------------------------- conflicts
def test_vertex_conflict_detected():
    a = Robot(id=0, position=(0, 0))
    b = Robot(id=1, position=(0, 2))
    a.next_position = b.next_position = (0, 1)
    assert contains_vertex_conflict([a, b]) is not None
    assert not no_vertex_conflicts([a, b])
    with pytest.raises(PlanningError):
        validate_plan([a, b])


def test_swap_conflict_detected():
    a = Robot(id=0, position=(0, 0))
    b = Robot(id=1, position=(0, 1))
    a.next_position, b.next_position = (0, 1), (0, 0)
    assert contains_swap_conflict([a, b]) is not None
    assert not no_swap_conflicts([a, b])


def test_following_moves_are_legal():
    """A -> B's cell while B vacates is a follow, not a swap."""
    a = Robot(id=0, position=(0, 0))
    b = Robot(id=1, position=(0, 1))
    a.next_position, b.next_position = (0, 1), (0, 2)
    validate_plan([a, b])


def test_execute_moves_updates_position_and_history():
    a = Robot(id=0, position=(0, 0))
    a.next_position = (0, 1)
    execute_moves([a])
    assert a.position == (0, 1)
    assert a.previous_position == (0, 0)
    assert a.travel_distance == 1


# ------------------------------------------------------------- basic PIBT
def test_two_robots_swap_ends_of_a_ring():
    """The classic PIBT case: opposing goals resolved by rotation."""
    sim = static_sim(RING, [(0, 0), (4, 4)], [(4, 4), (0, 0)])
    sim.run(max_timesteps=60)
    assert sim.robots[0].position == (4, 4)
    assert sim.robots[1].position == (0, 0)


def test_higher_priority_robot_displaces_a_stationary_one():
    """Priority inheritance must push an idle robot out of the way."""
    sim = static_sim(OPEN_GRID, [(0, 0), (0, 2)], [(0, 4), (0, 2)])
    sim.run(max_timesteps=40)
    assert sim.robots[0].position == (0, 4)


def test_corridor_head_on_resolves():
    sim = static_sim("......", [(0, 0), (0, 5)], [(0, 5), (0, 0)])
    sim.run(max_timesteps=200)
    # A 1-wide corridor with no passing bay has no solution; PIBT must at
    # least keep the plan collision free rather than crashing.
    assert no_vertex_conflicts(sim.robots)
    assert no_swap_conflicts(sim.robots)


def test_dense_static_instance_stays_collision_free():
    grid = "\n".join(["........"] * 8)
    starts = [(r, c) for r in range(8) for c in range(8)][:40]
    goals = list(reversed(starts))
    sim = static_sim(grid, starts, goals)
    sim.run(max_timesteps=200)
    assert no_vertex_conflicts(sim.robots)
    assert sim.collision_free


def test_pibt_always_assigns_a_next_position():
    sim = static_sim(OPEN_GRID, [(0, 0), (0, 1), (0, 2)], [(5, 5), (5, 4), (5, 3)])
    sim.step()
    assert all(r.next_position is not None for r in sim.robots)


def test_waiting_is_always_a_candidate():
    sim = static_sim(OPEN_GRID, [(0, 0)], [(0, 0)])
    candidates = sim.planner.candidates(sim.robots[0])
    assert (0, 0) in candidates


# ------------------------------------------------- collision freedom (§38.1)
@pytest.mark.parametrize(
    "variant",
    [
        "pibt_baseline",
        "lifelong_pibt",
        "directional_pibt",
        "hysteresis_pibt",
        "aisle_managed_pibt",
        "full_lda_pibt",
        "turning_cost_only",
        "direction_control_only",
        "aisle_direction_only",
        "reservations_only",
        "congestion_only",
        "recovery_only",
    ],
)
def test_every_variant_is_collision_free(variant):
    params = ablation(variant, Params(seed=5))
    warehouse = Warehouse.from_file("maps/warehouse_small.map", params)
    generator = TaskGenerator(
        warehouse.pickup_vertices, warehouse.delivery_vertices, rate=0.6, seed=5
    )
    sim = build_simulator(warehouse, 10, params, task_generator=generator)
    report = sim.run(max_timesteps=150)
    assert report.collision_free
    assert no_vertex_conflicts(sim.robots)
    assert no_swap_conflicts(sim.robots)


def test_robots_never_occupy_obstacles_or_leave_the_graph():
    params = ablation("full_lda_pibt", Params(seed=2))
    warehouse = Warehouse.from_file("maps/warehouse_medium.map", params)
    sim = build_simulator(warehouse, 25, params)
    for _ in range(120):
        sim.step()
        for robot in sim.robots:
            assert warehouse.graph.contains(robot.position)


def test_moves_are_to_a_neighbour_or_the_same_cell():
    params = ablation("full_lda_pibt", Params(seed=4))
    warehouse = Warehouse.from_file("maps/warehouse_small.map", params)
    sim = build_simulator(warehouse, 8, params)
    for _ in range(80):
        before = {r.id: r.position for r in sim.robots}
        sim.step()
        for robot in sim.robots:
            previous = before[robot.id]
            assert (
                robot.position == previous
                or robot.position in warehouse.graph.neighbors(previous)
            )


# ----------------------------------------- direction ranks, it does not reject
CORRIDOR_GRID = "\n".join([".........", ".@@@@@@@.", "........."])


def _directional_corridor(**overrides):
    """One 9-cell aisle locked FORWARD, one robot inside it at index 6."""
    params = Params(
        direction_control="aisle",
        hysteresis=True,
        reservations=False,
        directional_aisle_min_length=1,
        **overrides,
    )
    warehouse = Warehouse.from_string(CORRIDOR_GRID, params)
    sim = build_simulator(warehouse, 1, params, task_generator=None)
    aisle = max(warehouse.aisles.values(), key=lambda a: a.length)
    sim.index.rebuild(sim.robots)
    sim.aisles.update_aisle_direction(aisle, 20.0, 0.0, 0)

    robot = sim.robots[0]
    robot.position = aisle.vertices[6]
    robot.current_aisle = aisle.id
    robot.waypoint = aisle.vertices[0]
    sim.index.rebuild(sim.robots)
    # Moving to index 5 is neither with the flow nor an egress move.
    return sim, aisle, robot, aisle.vertices[5]


def test_aisle_direction_does_not_shrink_the_candidate_set():
    """The invariant the aisle layer used to break.

    Priority inheritance works because a robot can always be pushed into an
    adjacent cell. Deleting counterflow moves from the candidate set removes
    exactly that freedom, so inheritance chains dead-end. Direction is priced
    instead: the move survives the filter, carrying a penalty.
    """
    sim, aisle, robot, against = _directional_corridor()
    assert sim.aisles.violates_aisle_direction(robot, robot.position, against, 1)

    candidates = sim.planner.feasible_candidates(robot, None, 1)
    assert against in candidates, "a legal move was deleted, not deprioritised"
    assert sim.scorer.aisle_penalty(robot, against) == sim.params.zeta_counterflow

    with_flow = aisle.vertices[7]
    assert sim.scorer.aisle_penalty(robot, with_flow) == 0.0


def test_hard_constraints_flag_restores_the_old_rejection():
    sim, _aisle, robot, against = _directional_corridor(
        hard_direction_constraints=True
    )
    assert against not in sim.planner.feasible_candidates(robot, None, 1)
    # In that mode the planner rejects the move, so the score must not also
    # charge for it -- otherwise the penalty is counted twice.
    assert sim.scorer.aisle_penalty(robot, against) == 0.0


def test_progress_still_outranks_the_counterflow_penalty():
    """zeta must sit below alpha, or direction becomes a constraint again."""
    params = Params()
    assert params.zeta_counterflow < params.alpha_progress
    assert params.zeta_reservation < params.alpha_progress
    assert params.zeta_counterflow > params.beta_strong
    assert params.zeta_counterflow > params.gamma_strong
