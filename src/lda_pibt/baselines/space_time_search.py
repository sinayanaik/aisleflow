"""Space-time (reservation-table) pathfinding shared by the baseline planners.

Nothing else in the codebase provides this: `routing.Router` and
`graph.GridGraph.shortest_route` are static, single-agent shortest paths with
no notion of *when* a cell is occupied, which is exactly what Token Passing
and RHCR need in order to avoid other robots without PIBT's
candidate-scoring/priority-inheritance machinery.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from ..graph import GridGraph
from ..types import Vertex

if TYPE_CHECKING:
    from ..robot import Robot

_counter = itertools.count()


@dataclass
class ReservationTable:
    """Vertex-time and edge-time occupancy used to keep planned paths conflict-free.

    Always built fresh for a single planning call and discarded afterwards --
    it never accumulates reservations across simulation timesteps, so its size
    is bounded by (number of robots x path/window length), not by run length.
    """

    vertex_reservations: Dict[Tuple[Vertex, int], int] = field(default_factory=dict)
    edge_reservations: Dict[Tuple[Vertex, Vertex, int], int] = field(default_factory=dict)

    def is_free(
        self,
        vertex: Vertex,
        t: int,
        moving_from: Optional[Vertex] = None,
        robot_id: Optional[int] = None,
    ) -> bool:
        holder = self.vertex_reservations.get((vertex, t))
        if holder is not None and holder != robot_id:
            return False
        if moving_from is not None:
            # Someone else moving the opposite way across the same edge this
            # timestep is a swap conflict, not just a vertex conflict.
            swap_holder = self.edge_reservations.get((vertex, moving_from, t))
            if swap_holder is not None and swap_holder != robot_id:
                return False
        return True

    def reserve_path(self, robot_id: int, path: Sequence[Vertex], start_time: int) -> None:
        for offset, vertex in enumerate(path):
            t = start_time + offset
            self.vertex_reservations[(vertex, t)] = robot_id
            if offset > 0:
                self.edge_reservations[(path[offset - 1], vertex, t)] = robot_id

    def reserve_hold(self, robot_id: int, vertex: Vertex, from_time: int, until_time: int) -> None:
        """Reserve `vertex` for every timestep in [from_time, until_time] -- used
        for a robot that could not find a path and is waiting in place, so
        other robots plan around it rather than through it."""
        for t in range(from_time, until_time + 1):
            self.vertex_reservations[(vertex, t)] = robot_id

    def goal_is_clear(
        self, vertex: Vertex, from_time: int, until_time: int, robot_id: Optional[int] = None
    ) -> bool:
        """Whether `vertex` is free of *other* reservations for the rest of the
        search horizon -- the usual cooperative-A* goal condition: a path may
        only stop and hold at its goal once nobody else needs the cell later."""
        for t in range(from_time, until_time + 1):
            holder = self.vertex_reservations.get((vertex, t))
            if holder is not None and holder != robot_id:
                return False
        return True


def reserve_path_with_hold(
    reservations: ReservationTable,
    robot_id: int,
    path: Sequence[Vertex],
    start_time: int,
    horizon: int,
) -> None:
    """Reserve `path` and hold its last vertex for the rest of `horizon`.

    A path shorter than `horizon` means the robot reaches (or already sits
    at) its goal and stops there; without the trailing hold, a robot planned
    later in the same call -- or in a later call, before this robot replans
    -- could path straight through that cell once the explicit path
    reservation ends, producing a real (not merely theoretical) collision.
    """
    reservations.reserve_path(robot_id, path, start_time)
    last_time = start_time + len(path) - 1
    if last_time < start_time + horizon:
        reservations.reserve_hold(robot_id, path[-1], last_time + 1, start_time + horizon)


def space_time_astar(
    graph: GridGraph,
    start: Vertex,
    goal: Vertex,
    start_time: int,
    reservations: ReservationTable,
    horizon: int,
    heuristic: Optional[Dict[Vertex, float]] = None,
    node_expansion_cap: Optional[int] = None,
    robot_id: Optional[int] = None,
    require_goal: bool = True,
) -> Optional[List[Vertex]]:
    """Time-expanded A* from `start` at `start_time` toward `goal`, avoiding `reservations`.

    Returns the sequence of vertices occupied at start_time, start_time+1, ...
    (inclusive of `start`). Waiting in place is always a legal action. Pure
    function: never mutates `reservations`; callers reserve the result.

    When `require_goal` is True (Token Passing's use, with a generous
    `horizon`), a path is only returned if it actually reaches `goal` and can
    hold there for the rest of `horizon`; otherwise `None`. When False
    (RHCR's windowed use, where `horizon` is a short planning window
    decoupled from how far away the goal actually is -- reaching it within
    one window would be the exception, not the rule), the search instead
    returns the best-effort full-length path: if goal is never reached, the
    path to whichever state at exactly `start_time + horizon` has the
    smallest remaining heuristic distance to `goal` (ties broken by A*'s
    exploration order). Waiting the entire horizon is always a legal
    fallback, so this practically always returns a path rather than `None`.
    """
    h = heuristic if heuristic is not None else graph.distance_map(goal)
    if h.get(start, float("inf")) == float("inf"):
        return None

    end_time = start_time + horizon
    start_state = (start, start_time)
    g_score: Dict[Tuple[Vertex, int], int] = {start_state: 0}
    came_from: Dict[Tuple[Vertex, int], Tuple[Vertex, int]] = {}
    open_heap: List[Tuple[float, int, Tuple[Vertex, int]]] = [
        (h.get(start, 0.0), next(_counter), start_state)
    ]
    expansions = 0
    best_boundary: Optional[Tuple[float, Tuple[Vertex, int]]] = None

    def _reconstruct(state: Tuple[Vertex, int]) -> List[Vertex]:
        path = [state[0]]
        while state in came_from:
            state = came_from[state]
            path.append(state[0])
        path.reverse()
        return path

    while open_heap:
        _, _, (vertex, t) = heapq.heappop(open_heap)
        expansions += 1
        if node_expansion_cap is not None and expansions > node_expansion_cap:
            break

        if vertex == goal and reservations.goal_is_clear(goal, t, end_time, robot_id):
            return _reconstruct((vertex, t))

        if t >= end_time:
            if not require_goal:
                h_val = h.get(vertex, float("inf"))
                if best_boundary is None or h_val < best_boundary[0]:
                    best_boundary = (h_val, (vertex, t))
            continue

        for nxt in (*graph.neighbors(vertex), vertex):
            nt = t + 1
            if not reservations.is_free(nxt, nt, moving_from=vertex, robot_id=robot_id):
                continue
            tentative_g = g_score[(vertex, t)] + 1
            state = (nxt, nt)
            if tentative_g < g_score.get(state, float("inf")):
                g_score[state] = tentative_g
                came_from[state] = (vertex, t)
                f = tentative_g + h.get(nxt, float("inf"))
                heapq.heappush(open_heap, (f, next(_counter), state))

    if not require_goal and best_boundary is not None:
        return _reconstruct(best_boundary[1])
    return None


def prioritized_plan(
    robots: Sequence["Robot"],
    goals: Dict[int, Vertex],
    reservations: ReservationTable,
    graph: GridGraph,
    start_time: int,
    horizon: int,
    node_expansion_cap: Optional[int] = None,
    require_goal: bool = True,
) -> Dict[int, List[Vertex]]:
    """Plan `robots` one at a time, in the given order, against a shared table.

    Each robot's resulting path (or a one-cell "wait" fallback reserved for
    the whole horizon if none is found) is committed into `reservations`
    before the next robot is planned, so later robots route around earlier
    ones. This is the "give up on joint optimality, just get a legal plan"
    building block shared by Token Passing's normal operation, its
    replanning-trigger pass, and RHCR's degrade-on-node-cap-exceeded fallback
    -- one implementation instead of near-duplicates.

    Before planning anyone, every robot in `robots` conservatively holds its
    *current* cell for one extra timestep (`start_time + 1`). Without this, an
    earlier robot in this same pass has no way to know a later, not-yet-planned
    robot is physically sitting there right now, and can path straight into
    it. A robot's own placeholder never blocks its own search (`is_free`
    treats a cell held by its own id as free), so this only prevents others
    from stepping into an occupied cell one step before its occupant has had
    a chance to move -- it does not stop the occupant itself from moving away.

    `require_goal` is forwarded to `space_time_astar` -- pass False when
    `horizon` is a short rolling window decoupled from actual goal distance
    (RHCR), so a robot too far from its goal to arrive within the window
    still gets a legal best-effort path instead of a "no path found" wait.
    """
    for robot in robots:
        reservations.vertex_reservations.setdefault((robot.position, start_time + 1), robot.id)

    paths: Dict[int, List[Vertex]] = {}
    for robot in robots:
        goal = goals[robot.id]
        path = space_time_astar(
            graph,
            robot.position,
            goal,
            start_time,
            reservations,
            horizon,
            node_expansion_cap=node_expansion_cap,
            robot_id=robot.id,
            require_goal=require_goal,
        )
        if path is None:
            path = [robot.position]
            reservations.reserve_hold(robot.id, robot.position, start_time, start_time + horizon)
        else:
            reserve_path_with_hold(reservations, robot.id, path, start_time, horizon)
        paths[robot.id] = path
    return paths


def resolve_residual_conflicts(ordered_robots: Sequence["Robot"]) -> List[int]:
    """Force lower-priority robots to hold until no conflicts remain.

    Both baseline planners build every `next_position` from a shared
    reservation table, which should already make this a no-op in the common
    case -- but a robot that becomes unable to find any path (see
    `token_passing.TokenPassingPlanner`'s module docstring) can contend with
    an already-committed higher-priority robot's move that was reserved
    *before* the contention existed. Token Passing has no priority-inheritance
    mechanism to resolve that the way PIBT would, so rather than let it reach
    `validate.execute_moves` as an actual collision, this downgrades whichever
    robot is later in `ordered_robots` (the simulator's existing priority
    order) to stay at its current position instead, and repeats until stable.

    Returns the ids of every robot forced to hold. A nonzero result is a real
    finding about contention under a baseline without priority inheritance,
    not a bug to hide -- callers should count it via their `stats()`.
    """
    from ..validate import contains_swap_conflict, contains_vertex_conflict

    forced: List[int] = []
    by_id = {r.id: r for r in ordered_robots}
    order_index = {r.id: i for i, r in enumerate(ordered_robots)}

    for _ in range(len(ordered_robots) + 1):
        vertex_clash = contains_vertex_conflict(ordered_robots)
        swap_clash = contains_swap_conflict(ordered_robots)
        if vertex_clash is None and swap_clash is None:
            break
        if vertex_clash is not None:
            a_id, b_id = vertex_clash
            robot_a, robot_b = by_id[a_id], by_id[b_id]
            if robot_a.next_position == robot_a.position:
                # a isn't moving -- downgrading it would be a no-op; the
                # conflict is b trying to move into a's occupied cell.
                loser_id = b_id
            elif robot_b.next_position == robot_b.position:
                loser_id = a_id
            else:
                loser_id = max(vertex_clash, key=lambda rid: order_index[rid])
            by_id[loser_id].next_position = by_id[loser_id].position
            forced.append(loser_id)
        elif swap_clash is not None:
            for rid in swap_clash:
                by_id[rid].next_position = by_id[rid].position
                forced.append(rid)
    return forced


__all__ = [
    "ReservationTable",
    "space_time_astar",
    "prioritized_plan",
    "reserve_path_with_hold",
    "resolve_residual_conflicts",
]
