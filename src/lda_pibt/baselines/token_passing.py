"""Token Passing (Ma, Kumar, Koenig & Ayanian, 2017) baseline planner.

A shared reservation table ("the token") is rebuilt every planning call from
robots that already hold a valid future path; robots without one -- a new
task, an exhausted path, or the first step -- request a fresh space-time-A*
path against it, in the simulator's existing priority order. Unlike PIBT,
there is no priority-inheritance or backtracking: if a robot's search fails
it simply waits in place and retries next timestep. That is a faithful
property of the published algorithm, not a bug, and is the source of the
starvation risk on single-file maps documented in the README.

Task assignment is untouched -- this planner only replaces PIBT's low-level
movement/collision-avoidance layer, reusing whatever `robot.task`/`waypoint`
the existing `assignment.TaskAssigner` already set.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ..config import Params
from ..congestion import OccupancyIndex
from ..robot import Robot
from ..scoring import CandidateScorer
from ..types import Vertex
from ..warehouse import Warehouse
from .space_time_search import (
    ReservationTable,
    prioritized_plan,
    reserve_path_with_hold,
    resolve_residual_conflicts,
)


class TokenPassingPlanner:
    """Drop-in low-level planner satisfying `Simulator`'s `plan_step`/`stats` contract.

    Accepts the same 5-argument constructor as `PIBTPlanner` so both can be
    built through the same `planner_factory` call site; `scorer` is unused,
    since Token Passing neither scores candidates
    nor manages aisle direction.
    """

    #: how many timesteps ahead a single-agent search is allowed to look
    horizon: int = 40
    #: bounds worst-case A* cost on pathological instances
    node_expansion_cap: int = 20_000

    def __init__(
        self,
        warehouse: Warehouse,
        index: OccupancyIndex,
        scorer: CandidateScorer,
        params: Params,
    ):
        self.graph = warehouse.graph
        self.params = params
        self.paths: Dict[int, List[Vertex]] = {}

        self.replans = 0
        self.astar_calls = 0
        self.path_not_found = 0
        self.forced_holds = 0

    def plan_step(self, ordered_robots: Sequence[Robot], timestep: int) -> None:
        for robot in ordered_robots:
            robot.reset_step_state()

        goals = {
            robot.id: (robot.waypoint if robot.waypoint is not None else robot.position)
            for robot in ordered_robots
        }

        table = ReservationTable()
        settled: List[Robot] = []
        needing_replan: List[Robot] = []
        for robot in ordered_robots:
            path = self.paths.get(robot.id)
            stale = (
                path is None
                or len(path) < 2
                or path[0] != robot.position
                or path[-1] != goals[robot.id]
            )
            (needing_replan if stale else settled).append(robot)

        for robot in settled:
            reserve_path_with_hold(table, robot.id, self.paths[robot.id], timestep, self.horizon)

        self.astar_calls += len(needing_replan)
        new_paths = prioritized_plan(
            needing_replan,
            goals,
            table,
            self.graph,
            timestep,
            self.horizon,
            node_expansion_cap=self.node_expansion_cap,
        )
        self.replans += len(needing_replan)
        for robot in needing_replan:
            path = new_paths[robot.id]
            if len(path) < 2:
                self.path_not_found += 1
            self.paths[robot.id] = path

        for robot in ordered_robots:
            path = self.paths[robot.id]
            robot.next_position = path[1] if len(path) > 1 else path[0]
            self.paths[robot.id] = path[1:] if len(path) > 1 else path

        forced = resolve_residual_conflicts(ordered_robots)
        self.forced_holds += len(forced)
        for robot in ordered_robots:
            if robot.id in forced:
                # The committed path assumed a move that didn't happen --
                # invalidate it so the next call replans from where the
                # robot actually is, instead of skipping ahead.
                self.paths[robot.id] = [robot.position]

    def stats(self) -> Dict[str, Any]:
        return {
            "tp_replans": self.replans,
            "tp_astar_calls": self.astar_calls,
            "tp_path_not_found": self.path_not_found,
            "tp_forced_holds": self.forced_holds,
        }


__all__ = ["TokenPassingPlanner"]
