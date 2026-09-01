"""Space-time (reservation-table) pathfinding shared by the baseline planners.

Nothing else in the codebase provides this: `graph.GridGraph.shortest_route`
is a static, single-agent shortest path with no notion of *when* a cell is
occupied, which is exactly what Token Passing and RHCR need in order to avoid
other robots without PIBT's candidate-scoring/priority-inheritance machinery.

Two searches live here, because the two baselines need genuinely different
things and collapsing them into one was how the previous version of this file
made both of them fail:

`space_time_astar`
    Token Passing's search (Ma et al. 2017, Path1/Path2). Plans **all the way
    to the goal**, however far that is, and requires that the agent can then
    *stay* there -- a Token Passing path ends at an endpoint the agent rests
    at until it next receives the token, so the goal has to be free from
    arrival onwards, not merely for some fixed number of steps.

`bounded_horizon_astar`
    RHCR's search (Li et al. 2021). Resolves collisions only within the first
    `window` timesteps and ignores reservations beyond it, which is precisely
    what "bounded horizon" means in that paper -- the path is *not* required
    to reach the goal inside the window, and a robot whose goal is 60 steps
    away still gets a legal, progress-making path out of a 10-step window.
    It plans through a *sequence* of goals (pickup then delivery), because
    RHCR assigns each agent a sequence and a robot that would otherwise reach
    its pickup mid-window would sit there until the next replan.

The distinction matters because the failure mode is asymmetric: a windowed
search that is asked to reach a far goal returns "no path" for every robot at
once, and a full-goal search that is given a fixed short horizon does the
same. Either way every robot waits, and the baseline appears to deadlock when
what actually happened is that its planner was asked the wrong question.
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

#: Floor on how far past the last reservation a Token Passing search may
#: plan, when the graph is smaller than this. See `space_time_astar`.
MIN_TIME_SLACK = 32


@dataclass
class ReservationTable:
    """Vertex-time, edge-time and *terminal* occupancy of the committed paths.

    The first two are the usual cooperative-A* reservations. The third is
    what Token Passing actually needs and what a fixed-horizon table cannot
    express: an agent whose path ends at vertex ``v`` at time ``T`` stays
    there indefinitely -- until it next receives the token, which may be
    hundreds of timesteps later -- so ``v`` is blocked for *every* ``t >= T``,
    not for ``T..T+horizon``. Reserving a finite hold instead is a silent
    correctness hole (a later robot plans through the cell once the hold
    lapses) and reserving a very long one is a silent throughput hole (the
    table grows by horizon entries per idle robot per planning call).
    """

    vertex_reservations: Dict[Tuple[Vertex, int], int] = field(default_factory=dict)
    edge_reservations: Dict[Tuple[Vertex, Vertex, int], int] = field(default_factory=dict)
    #: vertex -> (robot id, first timestep it is held from, forever)
    terminal_holds: Dict[Vertex, Tuple[int, int]] = field(default_factory=dict)
    #: latest timestep any explicit reservation covers, so a goal test knows
    #: how far into the future it has to look
    horizon_end: int = 0
    #: vertex -> {robot id: last timestep that robot claims it}. Redundant
    #: with `vertex_reservations`, and kept because `rest_is_clear` is called
    #: once per goal-expansion and scanning the whole remaining horizon there
    #: made a failed Token Passing search quadratic in the horizon.
    latest_claim: Dict[Vertex, Dict[int, int]] = field(default_factory=dict)

    # ------------------------------------------------------------- queries
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
        terminal = self.terminal_holds.get(vertex)
        if terminal is not None and terminal[0] != robot_id and t >= terminal[1]:
            return False
        if moving_from is not None:
            # Someone else moving the opposite way across the same edge this
            # timestep is a swap conflict, not just a vertex conflict.
            swap_holder = self.edge_reservations.get((vertex, moving_from, t))
            if swap_holder is not None and swap_holder != robot_id:
                return False
        return True

    def rest_is_clear(self, vertex: Vertex, from_time: int, robot_id: Optional[int] = None) -> bool:
        """Whether `vertex` is free of *other* claims for all time from `from_time`.

        Token Passing's goal test. A path may only end at a vertex the agent
        can then occupy indefinitely, so this checks every remaining explicit
        reservation as well as the terminal holds.
        """
        terminal = self.terminal_holds.get(vertex)
        if terminal is not None and terminal[0] != robot_id:
            return False
        for holder, last in self.latest_claim.get(vertex, {}).items():
            if holder != robot_id and last >= from_time:
                return False
        return True

    # ------------------------------------------------------------ mutation
    def reserve_path(
        self,
        robot_id: int,
        path: Sequence[Vertex],
        start_time: int,
        rest_at_end: bool = True,
    ) -> None:
        """Commit `path` to the table, holding its last vertex forever after.

        `rest_at_end=False` is the windowed (RHCR) case, where a path is a
        slice of a longer journey and the agent keeps moving after it.
        """
        for offset, vertex in enumerate(path):
            t = start_time + offset
            self.vertex_reservations[(vertex, t)] = robot_id
            claims = self.latest_claim.setdefault(vertex, {})
            if claims.get(robot_id, -1) < t:
                claims[robot_id] = t
            if offset > 0:
                self.edge_reservations[(path[offset - 1], vertex, t)] = robot_id
        last_time = start_time + len(path) - 1
        self.horizon_end = max(self.horizon_end, last_time)
        if rest_at_end:
            self.terminal_holds[path[-1]] = (robot_id, last_time)

    def reserve_hold(self, robot_id: int, vertex: Vertex, from_time: int, until_time: int) -> None:
        """Reserve `vertex` for every timestep in [from_time, until_time]."""
        for t in range(from_time, until_time + 1):
            self.vertex_reservations[(vertex, t)] = robot_id
        claims = self.latest_claim.setdefault(vertex, {})
        if claims.get(robot_id, -1) < until_time:
            claims[robot_id] = until_time
        self.horizon_end = max(self.horizon_end, until_time)


# --------------------------------------------------------------------------
# Token Passing's search: reach the goal, however far, and be able to stay
# --------------------------------------------------------------------------
def space_time_astar(
    graph: GridGraph,
    start: Vertex,
    goal: Vertex,
    start_time: int,
    reservations: ReservationTable,
    heuristic: Optional[Dict[Vertex, float]] = None,
    node_expansion_cap: Optional[int] = None,
    robot_id: Optional[int] = None,
    time_slack: Optional[int] = None,
) -> Optional[List[Vertex]]:
    """Time-expanded A* from `start` at `start_time` to `goal`, avoiding `reservations`.

    Returns the vertices occupied at start_time, start_time+1, ... inclusive of
    `start`, ending at `goal` at a time from which the agent may rest there
    indefinitely; `None` if no such path exists. Waiting in place is always a
    legal action. Pure: never mutates `reservations`.

    The search is bounded in *time* by the last reservation plus `time_slack`
    rather than by a caller-chosen horizon, because Token Passing's paths are
    as long as the journey is: a fixed horizon shorter than the goal distance
    makes every call fail at once, which is a planner-shaped hole, not a
    property of the algorithm.

    `time_slack` defaults to the number of vertices in the graph, which makes
    the bound *complete* rather than arbitrary: past the last reservation the
    other agents are all resting, so the graph is static, and any path that
    exists reaches the goal within `|V| - 1` further steps. Cutting the
    search there therefore only ever rejects instances that had no answer --
    and it is what keeps a failed search from expanding the whole
    time-expanded space out to some round number of timesteps.
    """
    h = heuristic if heuristic is not None else graph.distance_map(goal)
    if h.get(start, float("inf")) == float("inf"):
        return None

    if time_slack is None:
        time_slack = max(MIN_TIME_SLACK, len(graph))
    end_time = max(reservations.horizon_end, start_time) + time_slack
    start_state = (start, start_time)
    g_score: Dict[Tuple[Vertex, int], int] = {start_state: 0}
    came_from: Dict[Tuple[Vertex, int], Tuple[Vertex, int]] = {}
    open_heap: List[Tuple[float, int, Tuple[Vertex, int]]] = [
        (h.get(start, 0.0), next(_counter), start_state)
    ]
    expansions = 0
    vertex_res = reservations.vertex_reservations
    edge_res = reservations.edge_reservations
    terminal_holds = reservations.terminal_holds

    while open_heap:
        _, _, state = heapq.heappop(open_heap)
        vertex, t = state
        expansions += 1
        if node_expansion_cap is not None and expansions > node_expansion_cap:
            return None

        if vertex == goal and reservations.rest_is_clear(goal, t, robot_id):
            return _reconstruct(came_from, state)

        if t >= end_time:
            continue

        nt = t + 1
        tentative_g = g_score[state] + 1
        for nxt in (*graph.neighbors(vertex), vertex):
            # `ReservationTable.is_free` inlined: this is the innermost loop
            # of the whole baseline suite and the call plus its four attribute
            # lookups cost more than the test itself
            holder = vertex_res.get((nxt, nt))
            if holder is not None and holder != robot_id:
                continue
            terminal = terminal_holds.get(nxt)
            if terminal is not None and terminal[0] != robot_id and nt >= terminal[1]:
                continue
            swap = edge_res.get((nxt, vertex, nt))
            if swap is not None and swap != robot_id:
                continue
            nxt_state = (nxt, nt)
            if tentative_g < g_score.get(nxt_state, float("inf")):
                g_score[nxt_state] = tentative_g
                came_from[nxt_state] = state
                heapq.heappush(
                    open_heap,
                    (tentative_g + h.get(nxt, float("inf")), next(_counter), nxt_state),
                )
    return None


# --------------------------------------------------------------------------
# RHCR's search: resolve collisions inside the window, ignore them outside
# --------------------------------------------------------------------------
def bounded_horizon_astar(
    graph: GridGraph,
    start: Vertex,
    goals: Sequence[Vertex],
    start_time: int,
    reservations: ReservationTable,
    window: int,
    heuristics: Optional[Sequence[Dict[Vertex, float]]] = None,
    node_expansion_cap: Optional[int] = None,
    robot_id: Optional[int] = None,
) -> Optional[List[Vertex]]:
    """Li et al. 2021's bounded-horizon single-agent search.

    Plans through the agent's *sequence* of goals -- pickup then delivery --
    and returns exactly `window + 1` vertices (start_time .. start_time+window),
    padding with waits at the final goal if every goal is reached early.

    "Bounded horizon" bounds *collision resolution*, not the journey: the path
    is never required to reach a goal within the window, and the search
    minimises f = (steps so far) + (remaining distance through the remaining
    goals), so a robot 60 steps from its goal still spends its 10-step window
    making 10 steps of progress rather than being told no path exists.

    Returns `None` only if the goal sequence is unreachable in the static
    graph; waiting in place for the whole window is otherwise always legal,
    provided the start cell itself is not reserved out from under the agent.
    """
    goals = list(goals) or [start]
    if heuristics is None:
        heuristics = [graph.distance_map(g) for g in goals]

    #: distance still to travel after finishing goal `i`, so the heuristic
    #: stays admissible across the whole sequence rather than only the leg
    tail = [0.0] * len(goals)
    for i in range(len(goals) - 2, -1, -1):
        leg = graph.route_distance(goals[i], goals[i + 1])
        if leg == float("inf"):
            return None
        tail[i] = tail[i + 1] + leg

    def h_of(vertex: Vertex, gi: int) -> float:
        if gi >= len(goals):
            return 0.0
        return heuristics[gi].get(vertex, float("inf")) + tail[gi]

    def advance(vertex: Vertex, gi: int) -> int:
        while gi < len(goals) and vertex == goals[gi]:
            gi += 1
        return gi

    if h_of(start, 0) == float("inf"):
        return None

    end_time = start_time + window
    start_state = (start, start_time, advance(start, 0))
    g_score: Dict[Tuple[Vertex, int, int], int] = {start_state: 0}
    came_from: Dict[Tuple[Vertex, int, int], Tuple[Vertex, int, int]] = {}
    open_heap = [(h_of(start, start_state[2]), next(_counter), start_state)]
    expansions = 0
    #: the best state seen at the window boundary, in case no goal sequence
    #: completes inside it -- the usual outcome, not the exception
    best: Optional[Tuple[Tuple[float, int], Tuple[Vertex, int, int]]] = None

    while open_heap:
        _, _, state = heapq.heappop(open_heap)
        vertex, t, gi = state
        expansions += 1
        if node_expansion_cap is not None and expansions > node_expansion_cap:
            break

        if gi >= len(goals):
            # every goal visited: the agent rests here for the rest of the
            # window, which is what RHCR does until its next replan
            if reservations.is_free(vertex, t, robot_id=robot_id) and all(
                reservations.is_free(vertex, u, moving_from=vertex, robot_id=robot_id)
                for u in range(t + 1, end_time + 1)
            ):
                path = _reconstruct(came_from, state)
                return path + [vertex] * (end_time - t)
            # cannot rest here -- keep searching, waiting is still an option
        if t >= end_time:
            key = (h_of(vertex, gi), g_score[state])
            if best is None or key < best[0]:
                best = (key, state)
            continue

        for nxt in (*graph.neighbors(vertex), vertex):
            nt = t + 1
            if not reservations.is_free(nxt, nt, moving_from=vertex, robot_id=robot_id):
                continue
            tentative_g = g_score[state] + 1
            nxt_state = (nxt, nt, advance(nxt, gi))
            if tentative_g < g_score.get(nxt_state, float("inf")):
                g_score[nxt_state] = tentative_g
                came_from[nxt_state] = state
                heapq.heappush(
                    open_heap,
                    (tentative_g + h_of(nxt, nxt_state[2]), next(_counter), nxt_state),
                )

    if best is not None:
        return _reconstruct(came_from, best[1])
    return None


def _reconstruct(came_from: Dict, state) -> List[Vertex]:
    path = [state[0]]
    while state in came_from:
        state = came_from[state]
        path.append(state[0])
    path.reverse()
    return path


# --------------------------------------------------------------------------
# prioritized planning over a window -- RHCR's fallback when PBS gives up
# --------------------------------------------------------------------------
def prioritized_plan(
    robots: Sequence["Robot"],
    goal_sequences: Dict[int, Sequence[Vertex]],
    reservations: ReservationTable,
    graph: GridGraph,
    start_time: int,
    window: int,
    node_expansion_cap: Optional[int] = None,
    order: Optional[Sequence[int]] = None,
) -> Dict[int, List[Vertex]]:
    """Plan `robots` one at a time against a shared table, in `order`.

    Each robot's path is committed into `reservations` before the next is
    planned, so later robots route around earlier ones. This is PBS's leaf
    operation (plan one agent under a fixed priority order) and RHCR's
    degrade-gracefully fallback when the high-level search hits its node cap
    -- Li et al. 2021 make the same tradeoff.

    Every path is exactly `window + 1` long, so the table describes the whole
    window for every robot and a caller may execute any prefix of it.
    """
    by_id = {r.id: r for r in robots}
    sequence = list(order) if order is not None else [r.id for r in robots]
    paths: Dict[int, List[Vertex]] = {}
    for robot_id in sequence:
        robot = by_id[robot_id]
        path = bounded_horizon_astar(
            graph, robot.position, goal_sequences[robot_id], start_time,
            reservations, window, node_expansion_cap=node_expansion_cap,
            robot_id=robot_id,
        )
        if path is None or len(path) != window + 1:
            path = [robot.position] * (window + 1)
        reservations.reserve_path(robot_id, path, start_time, rest_at_end=False)
        paths[robot_id] = path
    return paths


def resolve_residual_conflicts(ordered_robots: Sequence["Robot"]) -> List[int]:
    """Force lower-priority robots to hold until no conflicts remain.

    A safety net, not a mechanism: both baselines derive every
    `next_position` from a conflict-free set of committed paths, so this
    should find nothing. It exists because a silent collision reaching
    `validate.execute_moves` would abort the run, and because a nonzero count
    is a real finding about the planner rather than something to hide --
    both planners report it through `stats()`.
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
    "bounded_horizon_astar",
    "prioritized_plan",
    "resolve_residual_conflicts",
    "space_time_astar",
]
