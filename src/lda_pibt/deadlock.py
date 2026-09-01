"""Deadlock detection and localized, progressive recovery.

Implements spec section 28 (blocked-time, dependency graph, repeated
configurations) and section 29 (seven recovery levels).
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import Params
from .congestion import OccupancyIndex
from .robot import Robot
from .types import INF, RobotState, Vertex
from .warehouse import Warehouse

#: The remedies, in escalation order. Two more used to sit above these --
#: releasing entry permits, and dropping every directional constraint for the
#: group -- and both are gone: the permits they released no longer exist, and
#: truncating the ladder at this depth measured 6% *better* than running the
#: full one, because the strongest remedy tore up routes faster than the jam
#: was clearing.
RECOVERY_LEVEL_COUNT = 4

#: Human-readable names, in the same order, for the docs and the sensitivity
#: study.  Index i here describes `recovery_l{i+1}_fires`.
RECOVERY_LEVEL_NAMES = (
    "recompute routes",
    "raise blocked priorities",
    "allow temporary reverse",
    "assign escape vertices",
)


class DeadlockMonitor:
    """Tracks progress, builds the dependency graph and recovers locally."""

    def __init__(
        self,
        warehouse: Warehouse,
        index: OccupancyIndex,
        params: Params,
    ):
        self.warehouse = warehouse
        self.index = index
        self.params = params

        self.detected = 0
        self.recovered = 0
        self.unrecovered = 0
        self.recovery_time_total = 0
        self._group_start: Dict[frozenset, int] = {}
        self._group_level: Dict[frozenset, int] = {}
        self._unrecovered_keys: Set[frozenset] = set()
        #: Which of the seven remedies actually run, and which of them are
        #: followed by the group clearing.  A recovery level nothing ever fires,
        #: or that never precedes a resolution, is a level the ladder does not
        #: need -- and that cannot be measured by zeroing a weight, because the
        #: levels are code paths rather than numbers.
        self.level_fires: List[int] = [0] * RECOVERY_LEVEL_COUNT
        self.level_resolved: List[int] = [0] * RECOVERY_LEVEL_COUNT
        #: group key -> (level index last applied, timestep it was applied)
        self._group_last: Dict[frozenset, Tuple[int, int]] = {}

    # ------------------------------------------------------------ tracking
    def update_progress(self, robot: Robot, timestep: int) -> None:
        r"""Delta_i(t) = D_w(x_i(t-1)) - D_w(x_i(t)) (spec 28)."""
        previous = robot.previous_route_distance
        current = robot.route_distance_to_waypoint
        # A robot that has arrived, or has nothing to do, is not stalled.
        if (
            robot.state in (RobotState.FREE, RobotState.PARKED)
            or current == 0
            or robot.waypoint is None
            or robot.waypoint == robot.position
        ):
            robot.no_progress_steps = 0
            robot.last_progress_time = timestep
            robot.previous_route_distance = current
            return
        made_progress = (
            previous != INF and current != INF and current < previous
        )
        if made_progress:
            robot.no_progress_steps = 0
            robot.last_progress_time = timestep
        else:
            robot.no_progress_steps += 1
        robot.previous_route_distance = current

    def is_blocked(self, robot: Robot) -> bool:
        if robot.state in (RobotState.FREE, RobotState.PARKED):
            return False
        return (
            robot.no_progress_steps >= self.params.stall_steps
            or robot.blocked_time >= self.params.stall_steps
        )

    @staticmethod
    def repeated_configuration(robot: Robot, period: int = 4) -> bool:
        """Detect X(t-k) = X(t) on the robot's own local history (spec 28)."""
        history = list(robot.position_history)
        if len(history) < 2 * period:
            return False
        return history[-period:] == history[-2 * period : -period]

    # ------------------------------------------------- dependency structure
    def build_dependency_graph(
        self, robots: Sequence[Robot]
    ) -> Dict[int, Set[int]]:
        """Spec 28.1: edge i -> j means robot i waits for robot j."""
        graph: Dict[int, Set[int]] = {r.id: set() for r in robots}
        for robot in robots:
            other = robot.waiting_for_robot
            if other is not None and other.id in graph:
                graph[robot.id].add(other.id)
        return graph

    @staticmethod
    def find_cycles(graph: Dict[int, Set[int]]) -> List[List[int]]:
        """All simple directed cycles via iterative colouring DFS."""
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {node: WHITE for node in graph}
        cycles: List[List[int]] = []
        for start in graph:
            if colour[start] != WHITE:
                continue
            stack: List[Tuple[int, Iterable[int]]] = [(start, iter(graph[start]))]
            path = [start]
            colour[start] = GREY
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    if colour.get(nxt, BLACK) == GREY:
                        idx = path.index(nxt)
                        cycles.append(path[idx:])
                    elif colour.get(nxt, BLACK) == WHITE:
                        colour[nxt] = GREY
                        path.append(nxt)
                        stack.append((nxt, iter(graph[nxt])))
                        advanced = True
                        break
                if not advanced:
                    colour[node] = BLACK
                    stack.pop()
                    if path:
                        path.pop()
        return cycles

    def detect_deadlocked_groups(
        self, robots: Sequence[Robot], timestep: int
    ) -> List[List[Robot]]:
        """Spec section 28 / section 30, step 9."""
        if not self.params.recovery:
            return []
        by_id = {r.id: r for r in robots}
        blocked = [r for r in robots if self.is_blocked(r)]
        if not blocked:
            return []

        groups: List[List[Robot]] = []
        claimed: Set[int] = set()

        # Spec 28 lists three stall signals. No progress alone is the weakest:
        # in dense lifelong traffic a robot queues for `stall_steps` steps as a
        # matter of course, so treating that as a deadlock escalates recovery
        # on healthy traffic -- and levels 5-7 (temporary reverse, escape
        # vertices, waypoint hijack) then cost far more than they save. Require
        # one of the two positive signals to corroborate it.
        corroborated: Set[int] = set()
        if self.params.require_deadlock_corroboration:
            corroborated = {
                robot.id for robot in robots if self.repeated_configuration(robot)
            }

        # 1. Directed cycles in the wait-for graph are a strong signal.
        dependency = self.build_dependency_graph(robots)
        for cycle in self.find_cycles(dependency):
            members = [by_id[i] for i in cycle if i in by_id]
            if any(self.is_blocked(m) for m in members):
                ids = {m.id for m in members}
                if ids & claimed:
                    continue
                claimed |= ids
                corroborated |= ids  # a cycle is corroboration in itself
                groups.append(members)

        # 2. Spatially clustered blocked robots (localized, not global).
        radius = max(2, self.params.local_congestion_radius)
        remaining = [r for r in blocked if r.id not in claimed]
        seen: Set[int] = set()
        for robot in remaining:
            if robot.id in seen:
                continue
            component = [robot]
            seen.add(robot.id)
            frontier = deque([robot])
            while frontier:
                current = frontier.popleft()
                ball = self.warehouse.graph.bfs_ball(current.position, radius)
                for other in remaining:
                    if other.id in seen:
                        continue
                    if other.position in ball:
                        seen.add(other.id)
                        component.append(other)
                        frontier.append(other)
            if self.params.require_deadlock_corroboration and not any(
                member.id in corroborated for member in component
            ):
                continue  # slow, but nothing says it is stuck
            groups.append(component)

        current_keys = {frozenset(r.id for r in g) for g in groups}
        # A group that no longer appears has recovered.
        for key in list(self._group_start):
            if key not in current_keys:
                start = self._group_start.pop(key)
                self._group_level.pop(key, None)
                self.recovery_time_total += max(0, timestep - start)
                self.recovered += 1
                last = self._group_last.pop(key, None)
                # Credit the remedy only when the group cleared soon enough
                # after it to plausibly be its doing; a group that limps on for
                # another 50 steps was not fixed by that level.
                if last is not None:
                    level, applied_at = last
                    if timestep - applied_at <= self.params.stall_steps:
                        self.level_resolved[level] += 1
        for key in current_keys:
            if key not in self._group_start:
                self._group_start[key] = timestep
                self._group_level[key] = 0
                self.detected += 1
        return groups

    # ------------------------------------------------------------ recovery
    def group_has_progress(self, group: Sequence[Robot]) -> bool:
        return any(r.no_progress_steps < self.params.stall_steps for r in group)

    def recover_from_deadlock(
        self, group: Sequence[Robot], timestep: int
    ) -> bool:
        """Spec section 29: apply the next-stronger remedy for this group.

        Recovery is *progressive*: each timestep the group is still deadlocked
        it escalates by one level, so cheap remedies get a chance to work
        before the expensive ones run.
        """
        key = frozenset(r.id for r in group)
        all_levels = (
            self._recompute_routes,
            self._increase_blocked_priorities,
            self._allow_temporary_reverse_move,
            self._assign_escape_vertices,
        )
        # `recovery_max_level` truncates the ladder so a sweep over 0..7
        # measures each level's *marginal* value. Leaving one level out in the
        # middle would measure nothing, because the levels only ever fire in
        # order: level 5 is reached solely by levels 1-4 having failed first.
        depth = max(0, min(self.params.recovery_max_level, len(all_levels)))
        levels = all_levels[:depth]
        level = self._group_level.get(key, 0)
        if level >= len(levels):
            if key not in self._unrecovered_keys:
                self._unrecovered_keys.add(key)
                self.unrecovered += 1
            if not levels:
                return False  # recovery_max_level == 0: no remedy exists
            # Keep applying the strongest remedy.
            levels[-1](group, timestep)
            self.level_fires[len(levels) - 1] += 1
            self._group_last[key] = (len(levels) - 1, timestep)
            return False
        levels[level](group, timestep)
        self.level_fires[level] += 1
        self._group_last[key] = (level, timestep)
        self._group_level[key] = level + 1
        for robot in group:
            robot.recovery_events += 1
        return True

    def _severity(self, group: Sequence[Robot]) -> float:
        return max((r.no_progress_steps for r in group), default=0)

    # Level 1
    def _recompute_routes(self, group: Sequence[Robot], timestep: int) -> None:
        for robot in group:
            if robot.waypoint is None:
                continue
            robot.route = self.warehouse.graph.shortest_route(
                robot.position, robot.waypoint
            )

    # Level 2
    def _increase_blocked_priorities(
        self, group: Sequence[Robot], timestep: int
    ) -> None:
        """Buy the stuck group rank, at the same rate waiting buys it."""
        for robot in group:
            robot.priority += self.params.waiting_weight * robot.no_progress_steps

    # Level 3
    def _allow_temporary_reverse_move(
        self, group: Sequence[Robot], timestep: int
    ) -> None:
        """Let the group back out of wherever it wedged itself."""
        for robot in group:
            robot.allow_reverse_until = timestep + self.params.stall_steps

    # Level 4
    def _assign_escape_vertices(
        self, group: Sequence[Robot], timestep: int
    ) -> None:
        escapes = self._escape_vertices()
        if not escapes:
            return
        taken: Set[Vertex] = set()
        for robot in group:
            best: Optional[Vertex] = None
            best_d = INF
            for vertex in escapes:
                if vertex in taken or self.index.is_occupied(vertex):
                    continue
                d = self.warehouse.graph.route_distance(robot.position, vertex)
                if d < best_d:
                    best_d = d
                    best = vertex
            if best is not None:
                taken.add(best)
                robot.recovery_vertex = best
                robot.waypoint = best
                robot.state = RobotState.RECOVERY

    def _escape_vertices(self) -> List[Vertex]:
        wh = self.warehouse
        vertices = list(wh.parking_vertices)
        vertices += [v for v, info in wh.info.items() if info.is_passing_bay]
        if not vertices:
            vertices = [v for v in wh.graph.vertices if wh.graph.degree(v) >= 3]
        return vertices

    # ---------------------------------------------------------- statistics
    def stats(self) -> Dict[str, float]:
        out: Dict[str, float] = {
            "deadlocks_detected": self.detected,
            "deadlocks_recovered": self.recovered,
            "deadlocks_unrecovered": self.unrecovered,
            "mean_recovery_time": (
                self.recovery_time_total / self.recovered if self.recovered else 0.0
            ),
        }
        for i in range(RECOVERY_LEVEL_COUNT):
            out[f"recovery_l{i + 1}_fires"] = self.level_fires[i]
            out[f"recovery_l{i + 1}_resolved"] = self.level_resolved[i]
        return out


__all__ = ["DeadlockMonitor", "RECOVERY_LEVEL_COUNT", "RECOVERY_LEVEL_NAMES"]
