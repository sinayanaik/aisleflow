"""Lifelong-layer tests (spec sections 8, 13, 19-21, 23, 28, 29, 36)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lda_pibt import (
    CandidateScorer,
    CongestionModel,
    OccupancyIndex,
    Params,
    Robot,
    Task,
    TaskGenerator,
    TaskQueue,
    Warehouse,
    build_simulator,
    compute_priority,
    jain_fairness,
    order_by_priority,
    percentile,
)
from lda_pibt.assignment import TaskAssigner, update_task_state, update_waypoint
from lda_pibt.config import ablation
from lda_pibt.deadlock import DeadlockMonitor
from lda_pibt.scoring import (
    compute_aisle_bonus,
    compute_proximity_mode,
    turning_cost,
)
from lda_pibt.types import (
    Compass,
    ProximityMode,
    RobotState,
    TaskStatus,
)

GRID = "\n".join(["p......d"] + ["........"] * 5 + ["p......d"])


def warehouse(**overrides) -> Warehouse:
    return Warehouse.from_string(GRID, Params(**overrides))


# ------------------------------------------------------------------- tasks
def test_task_lifecycle_transitions():
    wh = warehouse()
    task = Task(id=0, pickup=(0, 0), delivery=(0, 7))
    robot = Robot(id=0, position=(0, 0), task=task, state=RobotState.TO_PICKUP)
    task.status = TaskStatus.TO_PICKUP
    update_task_state(robot, 1)
    assert robot.state is RobotState.TO_DELIVERY
    assert task.status is TaskStatus.TO_DELIVERY
    assert robot.waypoint == (0, 7)

    robot.position = (0, 7)
    completed = update_task_state(robot, 9)
    assert completed is task
    assert task.status is TaskStatus.COMPLETED
    assert task.completion_time == 9
    assert task.service_time == 9
    assert robot.state is RobotState.FREE
    assert robot.completed_tasks == 1


def test_waypoint_follows_robot_state():
    task = Task(id=0, pickup=(0, 0), delivery=(0, 7))
    robot = Robot(id=0, position=(3, 3), task=task, state=RobotState.TO_PICKUP)
    update_waypoint(robot)
    assert robot.waypoint == (0, 0)
    robot.state = RobotState.TO_DELIVERY
    update_waypoint(robot)
    assert robot.waypoint == (0, 7)


def test_batch_generator_respects_total():
    gen = TaskGenerator([(0, 0)], [(0, 7)], mode="batch", total=5, seed=0)
    assert len(gen.receive_new_tasks(0)) == 5
    assert gen.receive_new_tasks(1) == []


def test_periodic_generator_fires_on_period():
    gen = TaskGenerator([(0, 0)], [(0, 7)], mode="periodic", rate=2, period=3, seed=0)
    assert len(gen.receive_new_tasks(0)) == 2
    assert gen.receive_new_tasks(1) == []
    assert len(gen.receive_new_tasks(3)) == 2


def test_generator_is_reproducible():
    a = TaskGenerator([(0, 0)], [(0, 7)], rate=2.0, seed=11).receive_new_tasks(0)
    b = TaskGenerator([(0, 0)], [(0, 7)], rate=2.0, seed=11).receive_new_tasks(0)
    assert [t.pickup for t in a] == [t.pickup for t in b]


def test_queue_only_returns_released_unassigned_tasks():
    queue = TaskQueue([Task(id=0, pickup=(0, 0), delivery=(0, 7), release_time=5)])
    assert queue.available(1) == []
    assert len(queue.available(5)) == 1


# -------------------------------------------------------------- assignment
def test_greedy_assignment_prefers_the_nearer_robot():
    wh = warehouse()
    wh.precompute()
    params = Params()
    index = OccupancyIndex(wh, params)
    robots = [Robot(id=0, position=(0, 1)), Robot(id=1, position=(6, 7))]
    index.rebuild(robots)
    assigner = TaskAssigner(wh, CongestionModel(wh, index, params), params)
    queue = TaskQueue([Task(id=0, pickup=(0, 0), delivery=(0, 7))])
    assert assigner.assign_tasks_greedily(robots, queue, 0) == 1
    assert robots[0].task is not None
    assert robots[1].task is None


def test_assignment_sets_both_sides_of_the_link():
    wh = warehouse()
    wh.precompute()
    params = Params()
    index = OccupancyIndex(wh, params)
    robots = [Robot(id=0, position=(0, 1))]
    index.rebuild(robots)
    assigner = TaskAssigner(wh, CongestionModel(wh, index, params), params)
    task = Task(id=0, pickup=(0, 0), delivery=(0, 7))
    assigner.assign_tasks_greedily(robots, TaskQueue([task]), 0)
    assert task.assignment == 0
    assert robots[0].waypoint == task.pickup


# ---------------------------------------------------------------- priority
def test_loaded_robots_outrank_free_robots():
    params = Params()
    loaded = Robot(id=0, position=(0, 0), state=RobotState.TO_DELIVERY)
    loaded.task = Task(id=0, pickup=(0, 0), delivery=(0, 7))
    loaded.task.status = TaskStatus.TO_DELIVERY
    free = Robot(id=1, position=(1, 1))
    assert compute_priority(loaded, 0, params) > compute_priority(free, 0, params)


def test_waiting_eventually_overcomes_the_class_gap():
    """Spec 21.1 / 38.3: fairness."""
    params = Params()
    loaded = Robot(id=0, position=(0, 0), state=RobotState.TO_DELIVERY)
    loaded.task = Task(id=0, pickup=(0, 0), delivery=(0, 7))
    loaded.task.status = TaskStatus.TO_DELIVERY
    starved = Robot(id=1, position=(1, 1), state=RobotState.TO_PICKUP)
    starved.task = Task(id=1, pickup=(6, 0), delivery=(6, 7))
    starved.waiting_time = 500
    assert compute_priority(starved, 0, params) > compute_priority(loaded, 0, params)


def test_priority_ordering_is_deterministic():
    robots = [Robot(id=i, position=(0, i)) for i in range(5)]
    for r in robots:
        r.priority = 1.0
    assert [r.id for r in order_by_priority(robots)] == [0, 1, 2, 3, 4]


# ----------------------------------------------------------------- scoring
def test_proximity_modes():
    params = Params(r_near=2, r_far=8)
    assert compute_proximity_mode(20, params) is ProximityMode.TRANSIT
    assert compute_proximity_mode(5, params) is ProximityMode.APPROACH
    assert compute_proximity_mode(1, params) is ProximityMode.ARRIVAL


def test_aisle_weight_weakens_with_proximity():
    params = Params()
    transit = compute_aisle_bonus(ProximityMode.TRANSIT, params)
    arrival = compute_aisle_bonus(ProximityMode.ARRIVAL, params)
    assert transit > arrival


def test_turning_cost_penalises_reversal_most():
    params = Params()
    assert turning_cost(Compass.EAST, Compass.EAST, params) == 0.0
    assert turning_cost(Compass.EAST, Compass.NORTH, params) == 1.0
    assert turning_cost(Compass.EAST, Compass.WEST, params) == params.reverse_multiplier


def test_progress_dominates_the_score():
    """The score's tier structure: progress dominates every tie-break."""
    params = Params()
    wh = warehouse()
    wh.precompute()
    index = OccupancyIndex(wh, params)
    robot = Robot(id=0, position=(3, 3), waypoint=(0, 7))
    index.rebuild([robot])
    scorer = CandidateScorer(wh, CongestionModel(wh, index, params), params)
    robot.aisle_bonus = params.aisle_bonus
    robot.preferred_direction = Compass.SOUTH  # deliberately wrong way
    toward = scorer.score(robot, (2, 3))  # progress, wrong direction
    away = scorer.score(robot, (4, 3))  # no progress, preferred direction
    assert toward > away


# ----------------------------------------------------------------- metrics
def test_percentile_and_fairness():
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([], 95) == 0.0
    assert jain_fairness([5, 5, 5, 5]) == pytest.approx(1.0)
    assert jain_fairness([10, 0, 0, 0]) == pytest.approx(0.25)


def test_report_fields_are_consistent():
    params = ablation("full_lda_pibt", Params(seed=3))
    wh = Warehouse.from_file("maps/warehouse_small.map", params)
    sim = build_simulator(wh, 6, params)
    report = sim.run(max_timesteps=100)
    assert report.timesteps == 100
    assert report.completed_tasks == len(sim.metrics.completed)
    assert report.throughput == pytest.approx(report.completed_tasks / 100)
    assert report.total_travel_distance == sum(r.travel_distance for r in sim.robots)
    assert 0.0 < report.jain_fairness <= 1.0


# ---------------------------------------------------------------- deadlock
def test_dependency_cycle_is_found():
    params = Params()
    wh = warehouse()
    index = OccupancyIndex(wh, params)
    
    monitor = DeadlockMonitor(wh, index, params)
    a, b = Robot(id=0, position=(0, 0)), Robot(id=1, position=(0, 1))
    a.waiting_for_robot, b.waiting_for_robot = b, a
    cycles = monitor.find_cycles(monitor.build_dependency_graph([a, b]))
    assert any(set(c) == {0, 1} for c in cycles)


def test_arrived_robots_are_never_counted_as_stalled():
    params = Params()
    wh = warehouse()
    index = OccupancyIndex(wh, params)
    
    monitor = DeadlockMonitor(wh, index, params)
    robot = Robot(id=0, position=(0, 0), waypoint=(0, 0))
    robot.route_distance_to_waypoint = 0.0
    for t in range(50):
        monitor.update_progress(robot, t)
    assert robot.no_progress_steps == 0


def test_stalled_robot_accumulates_no_progress():
    params = Params()
    wh = warehouse()
    index = OccupancyIndex(wh, params)
    
    monitor = DeadlockMonitor(wh, index, params)
    robot = Robot(id=0, position=(3, 3), waypoint=(0, 7), state=RobotState.TO_PICKUP)
    robot.route_distance_to_waypoint = 7.0
    robot.previous_route_distance = 7.0
    for t in range(15):
        monitor.update_progress(robot, t)
    assert robot.no_progress_steps == 15
    assert monitor.is_blocked(robot)


def test_recovery_escalates_one_level_at_a_time():
    params = ablation("full_lda_pibt", Params(seed=1, stall_steps=1))
    wh = Warehouse.from_file("maps/warehouse_small.map", params)
    sim = build_simulator(wh, 4, params)
    sim.step()
    robots = sim.robots[:2]
    key = frozenset(r.id for r in robots)
    sim.deadlocks._group_start[key] = 0
    sim.deadlocks._group_level[key] = 0
    sim.deadlocks.recover_from_deadlock(robots, 1)
    assert sim.deadlocks._group_level[key] == 1
    sim.deadlocks.recover_from_deadlock(robots, 2)
    assert sim.deadlocks._group_level[key] == 2


# ---------------------------------------------------------------- lifelong
def test_lifelong_run_completes_tasks():
    params = ablation("lifelong_pibt", Params(seed=1))
    wh = Warehouse.from_file("maps/warehouse_small.map", params)
    gen = TaskGenerator(wh.pickup_vertices, wh.delivery_vertices, rate=0.5, seed=1)
    sim = build_simulator(wh, 8, params, task_generator=gen)
    report = sim.run(max_timesteps=200)
    assert report.completed_tasks > 0
    assert report.mean_service_time > 0


def test_runs_are_reproducible_for_a_fixed_seed():
    def run():
        params = ablation("full_lda_pibt", Params(seed=42))
        wh = Warehouse.from_file("maps/warehouse_small.map", params)
        gen = TaskGenerator(wh.pickup_vertices, wh.delivery_vertices, rate=0.5, seed=42)
        sim = build_simulator(wh, 8, params, task_generator=gen)
        result = sim.run(max_timesteps=120).to_dict()
        # Wall-clock timings are not part of the deterministic state.
        for key in ("mean_runtime_ms_per_step", "max_runtime_ms_per_step"):
            result.pop(key)
        return result

    assert run() == run()


def test_one_shot_mode_terminates_at_goals():
    params = Params(lifelong=False)
    wh = Warehouse.from_string("\n".join(["......"] * 4), params)
    from lda_pibt import Simulator

    robots = [Robot(id=0, position=(0, 0)), Robot(id=1, position=(3, 5))]
    sim = Simulator(wh, robots, params=params, static_goals={0: (3, 5), 1: (0, 0)})
    sim.run(max_timesteps=100)
    assert sim.robots[0].position == (3, 5)
    assert sim.robots[1].position == (0, 0)


def test_history_recording():
    params = ablation("full_lda_pibt", Params(seed=1))
    wh = Warehouse.from_file("maps/warehouse_small.map", params)
    sim = build_simulator(wh, 5, params, record_history=True)
    sim.run(max_timesteps=20)
    assert len(sim.history) == 20
    assert set(sim.history[0].positions) == {r.id for r in sim.robots}


# --------------------------------------------- corroborated deadlock detection
def test_recovery_needs_more_than_slow_progress():
    """Queueing is not a deadlock.

    `stall_steps` no-progress steps happen constantly in dense lifelong traffic.
    Treating that alone as a deadlock escalates recovery on healthy robots, and
    levels 5-7 (temporary reverse, escape vertices, waypoint hijack) then cost
    far more than they save.
    """
    from lda_pibt.deadlock import DeadlockMonitor

    params = Params(recovery=True, require_deadlock_corroboration=True, stall_steps=2)
    warehouse = Warehouse.from_string("\n".join(["." * 9 for _ in range(3)]), params)
    sim = build_simulator(warehouse, 3, params, task_generator=None)
    monitor: DeadlockMonitor = sim.deadlocks

    for robot in sim.robots:
        robot.state = RobotState.TO_PICKUP
        robot.no_progress_steps = 50
        robot.waypoint = (2, 8)
        assert monitor.is_blocked(robot)
        # No repeated configuration and no wait-for cycle: nothing corroborates.
        robot.position_history.clear()
        robot.waiting_for_robot = None

    assert monitor.detect_deadlocked_groups(sim.robots, 100) == []

    # A robot cycling between two cells *is* corroboration.
    stuck = sim.robots[0]
    for _ in range(4):
        stuck.position_history.append((0, 0))
        stuck.position_history.append((0, 1))
    assert monitor.repeated_configuration(stuck)
    assert monitor.detect_deadlocked_groups(sim.robots, 101)


def test_uncorroborated_mode_restores_the_old_trigger():
    from lda_pibt.deadlock import DeadlockMonitor

    params = Params(recovery=True, require_deadlock_corroboration=False, stall_steps=2)
    warehouse = Warehouse.from_string("\n".join(["." * 9 for _ in range(3)]), params)
    sim = build_simulator(warehouse, 3, params, task_generator=None)
    monitor: DeadlockMonitor = sim.deadlocks
    for robot in sim.robots:
        robot.state = RobotState.TO_PICKUP
        robot.no_progress_steps = 50
        robot.waypoint = (2, 8)
    assert monitor.detect_deadlocked_groups(sim.robots, 100)




def test_the_report_summary_prints_only_fields_that_exist():
    """`print(report)` is the CLI's whole output, and nothing covered it.

    `summary_lines` referenced three metrics that had been deleted with the
    aisle-direction layer, so every `main.py run` ended in an AttributeError
    while the test suite stayed green -- the suite only ever read fields off
    the report, never rendered it.
    """
    params = ablation("full_lda_pibt", Params(seed=0))
    wh = Warehouse.from_file("maps/warehouse_small.map", params)
    sim = build_simulator(wh, 4, params)
    report = sim.run(max_timesteps=30)

    text = str(report)
    assert "throughput" in text
    assert "collision free" in text
    # Every line must be "label : value" with a value that actually rendered.
    for line in report.summary_lines():
        assert ":" in line, line
        assert line.split(":", 1)[1].strip(), f"empty value in {line!r}"
