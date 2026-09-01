"""SPAR-PIBT simulator.

This module implements the complete lifelong algorithm of spec section 30 and
the move-execution contract of spec section 31.
"""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence

from .assignment import TaskAssigner, update_task_state, update_waypoint
from .config import Params
from .congestion import CongestionModel, OccupancyIndex
from .deadlock import DeadlockMonitor
from .metrics import MetricsCollector, MetricsReport, TimestepRecord
from .pibt import PIBTPlanner
from .priority import compute_priority, order_by_priority
from .robot import Robot
from .scoring import (
    CandidateScorer,
    compute_aisle_bonus,
    compute_proximity_mode,
)
from .task import Task, TaskGenerator, TaskQueue
from .types import INF, Compass, PlanningError, RobotState, Vertex
from .validate import execute_moves, validate_plan
from .warehouse import Warehouse


class Planner(Protocol):
    """What `Simulator` requires of a low-level movement planner.

    `PIBTPlanner` is the default; the baseline planners in `lda_pibt.baselines`
    (Token Passing, RHCR) satisfy this same protocol so they can be swapped in
    via `planner_factory` without touching the simulation loop.
    """

    def plan_step(self, ordered_robots: Sequence[Robot], timestep: int) -> None: ...

    def stats(self) -> Dict[str, Any]: ...

    #: Optional. A planner that defines `assign_tasks` owns task assignment
    #: and `Simulator` calls it instead of `assignment.TaskAssigner` (and
    #: skips idle-robot parking, which is then the planner's business too).
    #: Token Passing needs this: in Ma et al. 2017 the token holds the task
    #: set, the assignment and the paths, and its assignment rule -- never
    #: hand out a task whose endpoints another agent is resting on -- is what
    #: keeps its own path planning solvable. Assignment made by something
    #: else, to different rules, is not Token Passing with a different
    #: router; it is a different algorithm that cannot plan.


#: Builds a planner from the same five components `PIBTPlanner` takes.
#: Baseline planners accept and ignore `scorer` so every
#: planner can be constructed through this one uniform call site.
PlannerFactory = Callable[
    [Warehouse, OccupancyIndex, CandidateScorer, Params], Planner
]


@dataclass
class StepSnapshot:
    """Lightweight per-timestep snapshot used by the visualiser."""

    timestep: int
    positions: Dict[int, Vertex]
    states: Dict[int, str]
    completed_tasks: int


class Simulator:
    """Runs the two-layer planner over a warehouse for `max_timesteps`."""

    def __init__(
        self,
        warehouse: Warehouse,
        robots: Sequence[Robot],
        task_generator: Optional[TaskGenerator] = None,
        params: Optional[Params] = None,
        static_goals: Optional[Dict[int, Vertex]] = None,
        record_history: bool = False,
        planner_factory: Optional[PlannerFactory] = None,
    ):
        self.params = params or warehouse.params or Params()
        self.warehouse = warehouse
        self.robots: List[Robot] = list(robots)
        self.robots_by_id: Dict[int, Robot] = {r.id: r for r in self.robots}
        self.task_generator = task_generator
        self.task_queue = TaskQueue()
        self.static_goals = static_goals or {}
        self.record_history = record_history
        self.history: List[StepSnapshot] = []
        self.rng = random.Random(self.params.seed)

        self.warehouse.precompute()
        self.index = OccupancyIndex(warehouse, self.params)
        self.congestion = CongestionModel(warehouse, self.index, self.params)
        self.scorer = CandidateScorer(warehouse, self.congestion, self.params)
        self.assigner = TaskAssigner(warehouse, self.congestion, self.params)
        self.planner: Planner = (planner_factory or PIBTPlanner)(
            warehouse, self.index, self.scorer, self.params
        )
        #: whether the planner replaces `TaskAssigner` (see `Planner`)
        self.planner_assigns_tasks = hasattr(self.planner, "assign_tasks")
        self.deadlocks = DeadlockMonitor(warehouse, self.index, self.params)
        self.metrics = MetricsCollector()

        self.timestep = 0
        self.collision_free = True
        self.head_on_conflicts = 0
        self._assign_parking_slots()
        self._raise_recursion_limit()

    # ------------------------------------------------------------- helpers
    def _raise_recursion_limit(self) -> None:
        needed = 100 + 12 * max(1, len(self.robots))
        if sys.getrecursionlimit() < needed:
            sys.setrecursionlimit(needed)

    def _assign_parking_slots(self) -> None:
        free_slots = list(self.warehouse.parking_vertices)
        for robot in self.robots:
            robot.parking_vertex = free_slots.pop(0) if free_slots else None

    # ------------------------------------------------------------ one step
    def step(self) -> None:
        """One iteration of the lifelong loop (spec section 30)."""
        t = self.timestep
        started = time.perf_counter()

        self.index.rebuild(self.robots)
        self.congestion.begin_timestep()

        # 1. receive new tasks -------------------------------------------------
        if self.params.lifelong and self.task_generator is not None:
            self.task_queue.add_all(self.task_generator.receive_new_tasks(t))

        # 2. update task and robot states -------------------------------------
        if self.params.lifelong:
            for robot in self.robots:
                completed = update_task_state(robot, t)
                if completed is not None:
                    self.metrics.record_completion(completed)

        # 3. assign tasks to free robots --------------------------------------
        if self.params.lifelong:
            if self.planner_assigns_tasks:
                self.planner.assign_tasks(self.robots, self.task_queue, t)
            else:
                self.assigner.assign_tasks_greedily(self.robots, self.task_queue, t)
                self._park_idle_robots()

        # 4. update waypoints, route distance and proximity mode ---------------
        for robot in self.robots:
            if self.params.lifelong:
                update_waypoint(robot)
            else:
                robot.waypoint = self.static_goals.get(robot.id, robot.position)
            robot.route_distance_to_waypoint = self.warehouse.graph.route_distance(
                robot.position, robot.waypoint
            )
            robot.mode = compute_proximity_mode(
                robot.route_distance_to_waypoint, self.params
            )
            robot.aisle_bonus = compute_aisle_bonus(robot.mode, self.params)
            robot.current_aisle = self.warehouse.aisle_id(robot.position)

        # 5. compute routes and directional requests ---------------------------
        for robot in self.robots:
            robot.route = self.warehouse.graph.shortest_route(
                robot.position, robot.waypoint
            )


        # 9. detect and recover from deadlocks (using last step's wait graph) ---
        if self.params.recovery:
            for group in self.deadlocks.detect_deadlocked_groups(self.robots, t):
                self.deadlocks.recover_from_deadlock(group, t)

        # 10. priorities --------------------------------------------------------
        for robot in self.robots:
            robot.priority = compute_priority(robot, t, self.params)
        ordered = order_by_priority(self.robots)


        # 11-12. clear step state and run PIBT ---------------------------------
        self.planner.plan_step(ordered, t)

        # 12b. hypothesis-level counters, read before execution clears the
        # plan: head-on encounters are a property of the map and the routes,
        # so every variant is comparable on them.

        # 13. validate the joint plan ------------------------------------------
        if self.params.validate_every_step:
            try:
                validate_plan(self.robots)
            except PlanningError:
                self.collision_free = False
                raise

        # 14. synchronized execution -------------------------------------------
        execute_moves(self.robots, validate=False)

        # 15. statistics --------------------------------------------------------
        for robot in self.robots:
            at_waypoint = robot.waypoint is None or robot.position == robot.waypoint
            if robot.position == robot.previous_position and not at_waypoint:
                robot.waiting_time += 1
            elif at_waypoint or robot.position != robot.previous_position:
                robot.waiting_time = 0
            robot.current_aisle = self.warehouse.aisle_id(robot.position)
            robot.route_distance_to_waypoint = self.warehouse.graph.route_distance(
                robot.position, robot.waypoint
            )
            self.deadlocks.update_progress(robot, t)


        self.index.rebuild(self.robots)

        # 16. log metrics -------------------------------------------------------
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.metrics.log_timestep(
            TimestepRecord(
                timestep=t,
                completed_tasks=len(self.metrics.completed),
                pending_tasks=len(self.task_queue.pending),
                moving_robots=sum(
                    1 for r in self.robots if r.position != r.previous_position
                ),
                idle_robots=sum(1 for r in self.robots if r.is_idle),
                blocked_robots=sum(1 for r in self.robots if self.deadlocks.is_blocked(r)),
                runtime_ms=elapsed_ms,
            )
        )
        if self.record_history:
            self.history.append(
                StepSnapshot(
                    timestep=t,
                    positions={r.id: r.position for r in self.robots},
                    states={r.id: r.state.value for r in self.robots},
                    completed_tasks=len(self.metrics.completed),
                )
            )
        self.timestep += 1

    # ------------------------------------------------------------ sub-steps
    def _find_next_critical_aisle(self, robot: Robot) -> Optional[int]:
        """Spec section 15."""
        for vertex in robot.route:
            aisle_id = self.warehouse.aisle_id(vertex)
            if aisle_id is not None and aisle_id != robot.current_aisle:
                return aisle_id
        return None

    def _park_idle_robots(self) -> None:
        """Move task-less robots out of aisles so aisles can drain (spec 8)."""
        if not self.params.park_when_idle:
            return
        claimed = {
            r.parking_vertex for r in self.robots if r.parking_vertex is not None
        }
        for robot in self.robots:
            if robot.task is not None or robot.state not in (
                RobotState.FREE,
                RobotState.PARKED,
            ):
                continue
            if robot.parking_vertex is None:
                robot.parking_vertex = self._choose_parking_vertex(robot, claimed)
                if robot.parking_vertex is not None:
                    claimed.add(robot.parking_vertex)
            robot.state = RobotState.PARKED

    def _choose_parking_vertex(
        self, robot: Robot, claimed: set
    ) -> Optional[Vertex]:
        """Nearest free parking bay or passing bay, else stay where you are.

        Idle robots are deliberately *not* sent to intersections: an occupied
        intersection throttles every route through it.  A robot with no bay
        simply holds its position at the lowest priority class, and PIBT's
        priority inheritance displaces it when a busy robot needs the cell.
        """
        wh = self.warehouse
        graph = wh.graph
        pools = (
            wh.parking_vertices,
            [v for v, info in wh.info.items() if info.is_passing_bay],
        )
        for pool in pools:
            best: Optional[Vertex] = None
            best_d = INF
            for vertex in pool:
                if vertex in claimed:
                    continue
                d = graph.route_distance(robot.position, vertex)
                if d < best_d:
                    best_d = d
                    best = vertex
            if best is not None:
                return best
        return None

    # ---------------------------------------------------------------- runs
    def run(
        self,
        max_timesteps: Optional[int] = None,
        until_tasks: Optional[int] = None,
        progress: bool = False,
    ) -> MetricsReport:
        horizon = max_timesteps or self.params.max_timesteps
        for _ in range(horizon):
            if until_tasks is not None and len(self.metrics.completed) >= until_tasks:
                break
            if not self.params.lifelong and self._all_at_goals():
                break
            self.step()
            if progress and self.timestep % 50 == 0:  # pragma: no cover
                print(
                    f"  t={self.timestep:5d} "
                    f"completed={len(self.metrics.completed)}",
                    flush=True,
                )
        return self.report()

    def _all_at_goals(self) -> bool:
        return all(
            robot.position == self.static_goals.get(robot.id, robot.position)
            for robot in self.robots
        )

    def report(self) -> MetricsReport:
        extra: Dict[str, object] = {}
        extra.update(self.planner.stats())
        extra.update(self.deadlocks.stats())
        extra["head_on_conflicts"] = self.head_on_conflicts
        extra["collision_free"] = self.collision_free
        return self.metrics.build(
            self.robots, self.task_queue, self.timestep, extra
        )

    # ------------------------------------------------------------- display
    def render(self) -> str:
        """ASCII snapshot of the warehouse (robot ids modulo 36)."""
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
        canvas = [list(row) for row in self.warehouse.grid]
        for robot in self.robots:
            r, c = robot.position
            canvas[r][c] = alphabet[robot.id % len(alphabet)]
        header = (
            f"t={self.timestep}  completed={len(self.metrics.completed)}  "
            f"pending={len(self.task_queue.pending)}"
        )
        return header + "\n" + "\n".join("".join(row) for row in canvas)


def build_simulator(
    warehouse: Warehouse,
    n_robots: int,
    params: Optional[Params] = None,
    task_generator: Optional[TaskGenerator] = None,
    start_vertices: Optional[Sequence[Vertex]] = None,
    record_history: bool = False,
    planner_factory: Optional[PlannerFactory] = None,
) -> Simulator:
    """Convenience constructor: place `n_robots` on free, distinct vertices."""
    params = params or Params()
    rng = random.Random(params.seed)

    if start_vertices is None:
        candidates = [
            v
            for v in warehouse.graph.vertices
            if not warehouse.info[v].is_pickup_area
            and not warehouse.info[v].is_delivery_area
        ]
        if len(candidates) < n_robots:
            candidates = list(warehouse.graph.vertices)
        if len(candidates) < n_robots:
            raise ValueError(
                f"warehouse has {len(candidates)} usable cells but "
                f"{n_robots} robots were requested"
            )
        starts = rng.sample(candidates, n_robots)
    else:
        starts = list(start_vertices)[:n_robots]
        if len(starts) < n_robots:
            raise ValueError("not enough start vertices supplied")

    robots = [Robot(id=i, position=v) for i, v in enumerate(starts)]

    if task_generator is None and params.lifelong:
        task_generator = TaskGenerator(
            pickups=warehouse.pickup_vertices or warehouse.graph.vertices[:1],
            deliveries=warehouse.delivery_vertices or warehouse.graph.vertices[-1:],
            mode="poisson",
            rate=max(0.2, n_robots / 20.0),
            seed=params.seed,
        )

    return Simulator(
        warehouse,
        robots,
        task_generator=task_generator,
        params=params,
        record_history=record_history,
        planner_factory=planner_factory,
    )


__all__ = ["Simulator", "build_simulator", "StepSnapshot", "Planner", "PlannerFactory"]
