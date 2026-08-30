"""Rolling-Horizon Collision Resolution (Li, Tinka, Kiesel, Durham, Kumar &
Koenig, 2021) baseline planner.

Every `replan_period` steps, all robots are jointly replanned over a
`window`-length horizon with windowed Conflict-Based Search (CBS): find each
robot's own shortest space-time path, detect the first vertex/edge conflict
within the window, branch into two children that each forbid one of the two
robots from that (cell, time) or (edge, time), and recurse (Sharon et al.'s
CBS, restricted to the window). Between periodic replans, robots simply
consume their already-committed window path.

CBS is exponential in the worst case, so `cbs_node_cap` bounds the search and
falls back to prioritized planning when exceeded -- standard RHCR practice,
not a shortcut (Li et al. 2021 make the same tradeoff). The fallback rate is
reported via `stats()` rather than hidden, since a high rate at some robot
density is itself a finding.

A newly-assigned or path-exhausted robot between periodic replans gets an
immediate single-agent repair (via the same `prioritized_plan` helper used
for the CBS fallback) rather than waiting for the next window -- a documented
simplification so task (re)assignment, which happens every timestep
regardless of planner, does not stall a robot for up to `replan_period` steps.

Task assignment is untouched, matching `token_passing.py`: only the low-level
movement/collision-avoidance layer is replaced.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

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
    space_time_astar,
)

#: sentinel reservation holder for CBS constraints -- always distinct from any
#: real robot id (robots are ids 0..n-1), so `ReservationTable.is_free` blocks
#: exactly the constrained robot and nobody else.
_CONSTRAINT_SENTINEL = -1

#: a vertex constraint is (vertex, t); an edge constraint is (u, v, t)
Constraint = Tuple

_counter = itertools.count()


@dataclass
class _CBSNode:
    constraints: Dict[int, FrozenSet[Constraint]]
    paths: Dict[int, List[Vertex]]


class RHCRPlanner:
    """Drop-in low-level planner satisfying `Simulator`'s `plan_step`/`stats` contract.

    Accepts the same 5-argument constructor as `PIBTPlanner`; `scorer` and
    `aisle_manager` are unused. `window`/`replan_period` default from
    `params.baseline_window`/`params.baseline_replan_period`.
    """

    #: bounds worst-case CBS expansion; exceeding it degrades to prioritized
    #: planning for that window rather than blocking the simulation.
    node_expansion_cap: int = 500

    def __init__(
        self,
        warehouse: Warehouse,
        index: OccupancyIndex,
        scorer: CandidateScorer,
        aisle_manager: Any,
        params: Params,
    ):
        self.graph = warehouse.graph
        self.params = params
        self.window = params.baseline_window
        self.replan_period = params.baseline_replan_period
        self.paths: Dict[int, List[Vertex]] = {}
        self._last_replan_time: Optional[int] = None

        self.cbs_calls = 0
        self.cbs_expansions = 0
        self.cbs_fallbacks = 0
        self.forced_holds = 0

    # ------------------------------------------------------------- planning
    def plan_step(self, ordered_robots: Sequence[Robot], timestep: int) -> None:
        for robot in ordered_robots:
            robot.reset_step_state()

        goals = {
            robot.id: (robot.waypoint if robot.waypoint is not None else robot.position)
            for robot in ordered_robots
        }

        due_for_replan = (
            self._last_replan_time is None
            or timestep - self._last_replan_time >= self.replan_period
        )
        if due_for_replan:
            self.paths = self._plan_window(ordered_robots, goals, timestep)
            self._last_replan_time = timestep
        else:
            self._repair_stale_paths(ordered_robots, goals, timestep)

        for robot in ordered_robots:
            path = self.paths.get(robot.id) or [robot.position]
            robot.next_position = path[1] if len(path) > 1 else path[0]
            self.paths[robot.id] = path[1:] if len(path) > 1 else path

        forced = resolve_residual_conflicts(ordered_robots)
        self.forced_holds += len(forced)
        for robot in ordered_robots:
            if robot.id in forced:
                self.paths[robot.id] = [robot.position]

    def _repair_stale_paths(
        self, ordered_robots: Sequence[Robot], goals: Dict[int, Vertex], timestep: int
    ) -> None:
        table = ReservationTable()
        stale: List[Robot] = []
        for robot in ordered_robots:
            path = self.paths.get(robot.id)
            if path and len(path) > 1 and path[0] == robot.position and path[-1] == goals[robot.id]:
                reserve_path_with_hold(table, robot.id, path, timestep, self.window)
            else:
                stale.append(robot)
        if stale:
            repaired = prioritized_plan(
                stale, goals, table, self.graph, timestep, self.window,
                node_expansion_cap=self.node_expansion_cap, require_goal=False,
            )
            self.paths.update(repaired)

    # ------------------------------------------------------------------ CBS
    def _plan_window(
        self, robots: Sequence[Robot], goals: Dict[int, Vertex], timestep: int
    ) -> Dict[int, List[Vertex]]:
        self.cbs_calls += 1
        robots_by_id = {r.id: r for r in robots}
        empty: FrozenSet[Constraint] = frozenset()
        root_paths: Dict[int, List[Vertex]] = {}
        for robot in robots:
            path = self._low_level(robot, goals[robot.id], empty, timestep)
            root_paths[robot.id] = path if path is not None else [robot.position]
        root = _CBSNode(constraints={r.id: empty for r in robots}, paths=root_paths)

        open_heap: List[Tuple[int, int, _CBSNode]] = [
            (self._cost(root.paths), next(_counter), root)
        ]
        expansions = 0
        while open_heap:
            _, _, node = heapq.heappop(open_heap)
            expansions += 1
            self.cbs_expansions += 1
            if expansions > self.node_expansion_cap:
                self.cbs_fallbacks += 1
                return self._prioritized_fallback(robots, goals, timestep)

            conflict = self._first_conflict(node.paths, timestep)
            if conflict is None:
                return node.paths

            for robot_id, constraint in self._branch(conflict):
                child_constraints = dict(node.constraints)
                child_constraints[robot_id] = node.constraints[robot_id] | {constraint}
                new_path = self._low_level(
                    robots_by_id[robot_id], goals[robot_id], child_constraints[robot_id], timestep
                )
                if new_path is None:
                    continue  # over-constrained: this branch is infeasible, drop it
                child_paths = dict(node.paths)
                child_paths[robot_id] = new_path
                child = _CBSNode(child_constraints, child_paths)
                heapq.heappush(open_heap, (self._cost(child.paths), next(_counter), child))

        self.cbs_fallbacks += 1
        return self._prioritized_fallback(robots, goals, timestep)

    def _low_level(
        self, robot: Robot, goal: Vertex, constraints: FrozenSet[Constraint], timestep: int
    ) -> Optional[List[Vertex]]:
        table = ReservationTable()
        for constraint in constraints:
            if len(constraint) == 2:
                table.vertex_reservations[constraint] = _CONSTRAINT_SENTINEL
            else:
                table.edge_reservations[constraint] = _CONSTRAINT_SENTINEL
        return space_time_astar(
            self.graph, robot.position, goal, timestep, table, self.window,
            node_expansion_cap=self.node_expansion_cap, robot_id=robot.id,
            require_goal=False,
        )

    def _first_conflict(
        self, paths: Dict[int, List[Vertex]], start_time: int
    ) -> Optional[Tuple[int, int, str, Any, int]]:
        prev_positions: Optional[Dict[int, Vertex]] = None
        for offset in range(self.window + 1):
            positions: Dict[int, Vertex] = {}
            occupant_at: Dict[Vertex, int] = {}
            for robot_id, path in paths.items():
                v = path[min(offset, len(path) - 1)]
                positions[robot_id] = v
                if v in occupant_at:
                    return (occupant_at[v], robot_id, "vertex", v, start_time + offset)
                occupant_at[v] = robot_id

            if prev_positions is not None:
                seen_edges: Dict[Tuple[Vertex, Vertex], int] = {}
                for robot_id, v in positions.items():
                    u = prev_positions[robot_id]
                    if u == v:
                        continue
                    reverse = (v, u)
                    if reverse in seen_edges:
                        return (
                            seen_edges[reverse], robot_id, "edge", (u, v), start_time + offset,
                        )
                    seen_edges[(u, v)] = robot_id
            prev_positions = positions
        return None

    @staticmethod
    def _branch(conflict: Tuple[int, int, str, Any, int]) -> List[Tuple[int, Constraint]]:
        robot_a, robot_b, kind, payload, t = conflict
        if kind == "vertex":
            vertex = payload
            return [(robot_a, (vertex, t)), (robot_b, (vertex, t))]
        u, v = payload
        return [(robot_a, (u, v, t)), (robot_b, (v, u, t))]

    @staticmethod
    def _cost(paths: Dict[int, List[Vertex]]) -> int:
        return sum(len(p) for p in paths.values())

    def _prioritized_fallback(
        self, robots: Sequence[Robot], goals: Dict[int, Vertex], timestep: int
    ) -> Dict[int, List[Vertex]]:
        table = ReservationTable()
        return prioritized_plan(
            robots, goals, table, self.graph, timestep, self.window,
            node_expansion_cap=self.node_expansion_cap, require_goal=False,
        )

    # ---------------------------------------------------------- statistics
    def stats(self) -> Dict[str, Any]:
        return {
            "rhcr_cbs_calls": self.cbs_calls,
            "rhcr_cbs_expansions": self.cbs_expansions,
            "rhcr_cbs_fallbacks": self.cbs_fallbacks,
            "rhcr_forced_holds": self.forced_holds,
            "rhcr_window": self.window,
            "rhcr_replan_period": self.replan_period,
        }


__all__ = ["RHCRPlanner"]
