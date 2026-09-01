"""Rolling-Horizon Collision Resolution (Li, Tinka, Kiesel, Durham, Kumar &
Koenig, AAAI 2021: *Lifelong Multi-Agent Path Finding in Large-Scale
Warehouses*).

RHCR is two ideas, and both of them are about *bounding* something:

**Bounded horizon.** Collisions are resolved only within the next `w`
timesteps. Paths are not truncated to `w` and agents are not required to
reach their goals inside it -- the window bounds how far ahead the solver
argues about conflicts, nothing else. Li et al.'s central finding is that a
small `w` is not merely cheaper but often *better*, because resolving
collisions 60 steps ahead of a warehouse that will have changed by then buys
nothing.

**Rolling replanning.** All agents are replanned together every `h <= w`
timesteps and follow the committed plan in between. Replanning every
timestep is what RHCR exists not to do.

Agents are given a *sequence* of goals rather than one goal, which is how the
paper keeps a robot that finishes a task mid-window from idling until the
next replan: a robot on its way to a pickup is planned pickup-then-delivery
in a single search (`space_time_search.bounded_horizon_astar`).

**High-level solver.** The paper is explicit that RHCR is a framework that
takes any MAPF solver, and that PBS -- Priority-Based Search (Ma, Harabor,
Stuckey, Li & Koenig, AAAI 2019), already cited in the README -- is its
default and best-performing choice at warehouse scale. PBS is implemented
here: depth-first over a partial priority order, branching on the first
conflict inside the window, replanning only the agents below the new
ordering. When PBS exceeds its node budget the window degrades to plain
prioritized planning, which Li et al. also do rather than blocking.

Task assignment is *not* part of RHCR. The paper takes assignment as given
from a separate task assigner, so this planner uses the simulator's shared
`assignment.TaskAssigner` unchanged -- unlike Token Passing, where the
assignment rules are part of the published algorithm.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from ..config import Params
from ..congestion import OccupancyIndex
from ..robot import Robot
from ..scoring import CandidateScorer
from ..types import RobotState, Vertex
from ..warehouse import Warehouse
from .space_time_search import (
    ReservationTable,
    bounded_horizon_astar,
    prioritized_plan,
    resolve_residual_conflicts,
)

_counter = itertools.count()


@dataclass
class _PBSNode:
    """One node of the priority-based search: a strict partial order and a plan."""

    #: agent id -> agents that must yield to it (it has higher priority)
    lower: Dict[int, Set[int]]
    paths: Dict[int, List[Vertex]]
    cost: float = 0.0


class RHCRPlanner:
    """Drop-in low-level planner satisfying `Simulator`'s planner contract.

    `window` (w) and `replan_period` (h) come from `params.baseline_window`
    and `params.baseline_replan_period`; `scorer` is unused.
    """

    #: PBS high-level node budget for one window. Exceeding it degrades to
    #: prioritized planning for that window rather than blocking the run.
    pbs_node_cap: int = 200
    #: single-agent search budget inside one PBS node
    node_expansion_cap: int = 20_000

    def __init__(
        self,
        warehouse: Warehouse,
        index: OccupancyIndex,
        scorer: CandidateScorer,
        params: Params,
    ):
        self.warehouse = warehouse
        self.graph = warehouse.graph
        self.params = params
        self.window = max(1, params.baseline_window)
        self.replan_period = max(1, min(params.baseline_replan_period, self.window))
        self.paths: Dict[int, List[Vertex]] = {}
        #: the goal sequence each committed path was planned for, so a robot
        #: handed a new task mid-window can be spotted and repaired
        self._planned_for: Dict[int, List[Vertex]] = {}
        self._last_replan_time: Optional[int] = None

        self.replans = 0
        self.repairs = 0
        self.pbs_expansions = 0
        self.pbs_fallbacks = 0
        self.low_level_calls = 0
        self.forced_holds = 0

    # ---------------------------------------------------------------- goals
    def _goal_sequence(self, robot: Robot) -> List[Vertex]:
        """What this agent is trying to visit, in order.

        A robot on the way to a pickup is given both legs, so a window that
        happens to reach the pickup keeps going instead of parking there
        until the next replan. Everything else is a single goal.
        """
        if robot.task is not None and robot.state is RobotState.TO_PICKUP:
            return [robot.task.pickup, robot.task.delivery]
        if robot.waypoint is not None:
            return [robot.waypoint]
        return [robot.position]

    # ------------------------------------------------------------- planning
    def plan_step(self, ordered_robots: Sequence[Robot], timestep: int) -> None:
        for robot in ordered_robots:
            robot.reset_step_state()

        goals = {r.id: self._goal_sequence(r) for r in ordered_robots}
        due = (
            self._last_replan_time is None
            or timestep - self._last_replan_time >= self.replan_period
            or any(
                len(self.paths.get(r.id) or []) < 2 or self.paths[r.id][0] != r.position
                for r in ordered_robots
            )
        )
        if due:
            self.paths = self._plan_window(ordered_robots, goals, timestep)
            self._planned_for = {i: list(g) for i, g in goals.items()}
            self._last_replan_time = timestep
            self.replans += 1
        else:
            self._repair_reassigned(ordered_robots, goals, timestep)

        for robot in ordered_robots:
            path = self.paths.get(robot.id) or [robot.position]
            robot.next_position = path[1] if len(path) > 1 else path[0]

        # a no-op on a conflict-free window plan; counted, not hidden
        forced = resolve_residual_conflicts(ordered_robots)
        self.forced_holds += len(forced)

        for robot in ordered_robots:
            path = self.paths.get(robot.id) or [robot.position]
            if robot.id in forced:
                self.paths[robot.id] = [robot.position]
            else:
                self.paths[robot.id] = path[1:] if len(path) > 1 else path

    def _repair_reassigned(
        self,
        robots: Sequence[Robot],
        goals: Dict[int, List[Vertex]],
        timestep: int,
    ) -> None:
        """Re-plan, between periodic replans, any robot whose goal changed.

        RHCR's agents follow their committed window plan between replans, and
        in Li et al. that costs nothing because an agent is given its *next*
        goal before it reaches the current one. Here task assignment is online
        and greedy, so a robot that completes a delivery mid-window is handed
        a fresh task its committed plan knows nothing about -- and, since that
        plan ends by waiting at the goal it already reached, it stands still
        until the next periodic replan. Measured at 7% of robot-steps on
        `warehouse_medium`.

        This is the documented simplification: such a robot gets a
        single-agent repair against every other robot's *committed* remaining
        path, which is reserved first, so the repaired plan cannot collide
        with the window plan it is being spliced into.
        """
        stale = [
            robot for robot in robots
            if self._planned_for.get(robot.id) != goals[robot.id]
        ]
        if not stale:
            return
        stale_ids = {robot.id for robot in stale}
        table = ReservationTable()
        for robot in robots:
            if robot.id in stale_ids:
                continue
            path = self.paths.get(robot.id) or [robot.position]
            table.reserve_path(robot.id, path, timestep, rest_at_end=False)
        repaired = prioritized_plan(
            stale, goals, table, self.graph, timestep, self.window,
            node_expansion_cap=self.node_expansion_cap,
        )
        self.paths.update(repaired)
        for robot in stale:
            self._planned_for[robot.id] = list(goals[robot.id])
        self.repairs += len(stale)

    # ------------------------------------------------------------------ PBS
    def _plan_window(
        self,
        robots: Sequence[Robot],
        goals: Dict[int, List[Vertex]],
        timestep: int,
    ) -> Dict[int, List[Vertex]]:
        """One window of Priority-Based Search over `self.window` timesteps."""
        ids = [r.id for r in robots]
        by_id = {r.id: r for r in robots}

        root = _PBSNode(lower={i: set() for i in ids}, paths={})
        free = ReservationTable()
        for robot in robots:
            # against an empty table `_single` always succeeds -- waiting the
            # window out is unconstrained -- but the root of the search is not
            # the place to depend on that
            path = self._single(robot, goals[robot.id], free, timestep)
            root.paths[robot.id] = path or [robot.position] * (self.window + 1)
        root.cost = self._cost(root.paths, goals)

        stack: List[_PBSNode] = [root]
        expansions = 0
        while stack:
            node = stack.pop()
            expansions += 1
            self.pbs_expansions += 1
            if expansions > self.pbs_node_cap:
                self.pbs_fallbacks += 1
                return self._prioritized_fallback(robots, goals, timestep)

            conflict = self._first_conflict(node.paths)
            if conflict is None:
                return node.paths

            a, b = conflict
            children: List[_PBSNode] = []
            for high, low in ((a, b), (b, a)):
                child = _PBSNode(
                    lower={i: set(s) for i, s in node.lower.items()},
                    paths=dict(node.paths),
                )
                child.lower[high].add(low)
                if self._cycle(child.lower, high):
                    continue
                if not self._replan_below(child, low, by_id, goals, timestep):
                    continue
                child.cost = self._cost(child.paths, goals)
                children.append(child)
            # depth-first, cheaper child explored first: push it last
            for child in sorted(children, key=lambda c: -c.cost):
                stack.append(child)

        self.pbs_fallbacks += 1
        return self._prioritized_fallback(robots, goals, timestep)

    def _replan_below(
        self,
        node: _PBSNode,
        agent: int,
        by_id: Dict[int, Robot],
        goals: Dict[int, List[Vertex]],
        timestep: int,
    ) -> bool:
        """Replan `agent` and, transitively, everyone below it that it now hits.

        PBS's `update-plan`: an agent only avoids agents ranked above it, so
        adding an ordering can only invalidate the plans of agents below the
        newly-demoted one.
        """
        queue = [agent]
        seen: Set[int] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            table = ReservationTable()
            for other in self._above(node.lower, current):
                table.reserve_path(other, node.paths[other], timestep, rest_at_end=False)
            path = self._single(by_id[current], goals[current], table, timestep)
            if path is None:
                return False
            node.paths[current] = path
            for below in self._below(node.lower, current):
                if self._pair_conflict(node.paths[current], node.paths[below]) is not None:
                    queue.append(below)
        return True

    @staticmethod
    def _above(lower: Dict[int, Set[int]], agent: int) -> Set[int]:
        """Everyone `agent` must yield to (transitive closure upwards)."""
        result: Set[int] = set()
        stack = [
            higher for higher, below in lower.items() if agent in below
        ]
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(h for h, below in lower.items() if current in below)
        return result

    @staticmethod
    def _below(lower: Dict[int, Set[int]], agent: int) -> Set[int]:
        result: Set[int] = set()
        stack = list(lower[agent])
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(lower[current])
        return result

    @classmethod
    def _cycle(cls, lower: Dict[int, Set[int]], agent: int) -> bool:
        return agent in cls._below(lower, agent)

    def _single(
        self,
        robot: Robot,
        goals: Sequence[Vertex],
        table: ReservationTable,
        timestep: int,
    ) -> Optional[List[Vertex]]:
        self.low_level_calls += 1
        path = bounded_horizon_astar(
            self.graph, robot.position, goals, timestep, table, self.window,
            node_expansion_cap=self.node_expansion_cap, robot_id=robot.id,
        )
        if path is None or len(path) != self.window + 1:
            # waiting the window out is legal unless a higher-priority agent
            # has already claimed this cell, in which case the branch is
            # infeasible and PBS should drop it
            for offset in range(self.window + 1):
                if not table.is_free(robot.position, timestep + offset, robot_id=robot.id):
                    return None
            return [robot.position] * (self.window + 1)
        return path

    # -------------------------------------------------------- conflict tests
    def _first_conflict(self, paths: Dict[int, List[Vertex]]) -> Optional[Tuple[int, int]]:
        ids = sorted(paths)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if self._pair_conflict(paths[a], paths[b]) is not None:
                    return (a, b)
        return None

    @staticmethod
    def _pair_conflict(first: Sequence[Vertex], second: Sequence[Vertex]) -> Optional[int]:
        span = min(len(first), len(second))
        for t in range(span):
            if first[t] == second[t]:
                return t
            if t and first[t] == second[t - 1] and second[t] == first[t - 1]:
                return t
        return None

    def _cost(self, paths: Dict[int, List[Vertex]], goals: Dict[int, List[Vertex]]) -> float:
        """How much journey the whole plan still has left at the window's end.

        The natural PBS cost -- sum of path lengths -- is constant here:
        every bounded-horizon path is exactly `window + 1` long by
        construction, so it ranked every node identically and the
        cheapest-child-first ordering did nothing at all. What actually
        differs between two window plans is how far the agents got, so the
        cost is the remaining distance from where each one ends up.
        """
        total = 0.0
        for robot_id, path in paths.items():
            goal = goals[robot_id][-1] if goals.get(robot_id) else path[-1]
            distance = self.graph.route_distance(path[-1], goal)
            total += distance if distance != float("inf") else float(len(self.graph))
        return total

    def _prioritized_fallback(
        self,
        robots: Sequence[Robot],
        goals: Dict[int, List[Vertex]],
        timestep: int,
    ) -> Dict[int, List[Vertex]]:
        return prioritized_plan(
            robots, goals, ReservationTable(), self.graph, timestep, self.window,
            node_expansion_cap=self.node_expansion_cap,
        )

    # ---------------------------------------------------------- statistics
    def stats(self) -> Dict[str, Any]:
        return {
            "rhcr_replans": self.replans,
            "rhcr_mid_window_repairs": self.repairs,
            "rhcr_pbs_expansions": self.pbs_expansions,
            "rhcr_pbs_fallbacks": self.pbs_fallbacks,
            "rhcr_low_level_calls": self.low_level_calls,
            "rhcr_forced_holds": self.forced_holds,
            "rhcr_window": self.window,
            "rhcr_replan_period": self.replan_period,
        }


__all__ = ["RHCRPlanner"]
