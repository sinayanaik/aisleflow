"""Token Passing and Token Passing with Task Swaps (Ma, Li, Kumar & Koenig,
AAMAS 2017: *Lifelong Multi-Agent Path Finding for Online Pickup and Delivery
Tasks*), implemented from the paper's Algorithms 1 and 2.

Both are **complete MAPD algorithms, not path planners**: the token holds the
task set *and* the assignment *and* every agent's path, and the three are
decided together. That is the point of the algorithm, and the reason this
class owns task assignment (`assign_tasks`, called by `Simulator` in place of
the shared greedy assigner) rather than accepting whatever assignment
something else made. Two rules in particular do not survive being separated
from the routing:

* a task may only be handed out if **no other agent's path already ends at
  its pickup or its delivery** (Algorithm 1, line 6). Handing several agents
  tasks that share a delivery cell is what a distance-greedy assigner does,
  and it is exactly the configuration Token Passing exists to avoid: every
  such agent needs the same cell as its resting place and all but one of
  them will fail to plan.
* an agent whose path has ended stays where it is *unless* it is sitting on
  the delivery cell of a task nobody has taken yet, in which case it moves
  aside (Path2, line 14). Without that rule an idle agent parks on an
  endpoint and quietly makes the instance unsolvable.

Algorithm 1 -- **TP** (`TokenPassingPlanner`)
    Each agent that has reached the end of its path receives the token, takes
    the reachable task whose pickup is nearest, and plans one path through
    pickup and on to delivery (`Path1`) against every other path in the
    token. It then follows that path to the end without replanning, which is
    what makes the algorithm cheap: one search per task, not one per robot
    per timestep.

Algorithm 2 -- **TPTS** (`TokenPassingTaskSwapsPlanner`)
    The same token, plus task swaps: an agent receiving the token may take a
    task already assigned to another agent that has not yet picked it up, if
    it can reach the pickup sooner. The robbed agent is freed and requests
    the token again in the same round. TPTS plans to the pickup and to the
    delivery as two separate searches, so a swapped-away task costs only the
    leg already travelled.

**Well-formedness.** Ma et al. prove TP is complete on *well-formed* MAPD
instances: every agent rests at an endpoint, and any two endpoints are
connected by a path that traverses no other endpoint. The maps in `maps/`
are not well-formed in that sense -- `warehouse_corridors` has no parking
bays at all, so a resting agent necessarily sits in someone's corridor -- and
that shows up in the results as genuinely lower throughput on the tightest
floors. That is a property of the algorithm meeting this warehouse, and is
reported as such; it is not simulated by handicapping the planner. Where the
paper's guarantee does not apply, a search that finds no path leaves the task
in the pool and the agent waits a timestep, which is the paper's own
behaviour for an agent that cannot be given a task.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from ..config import Params
from ..congestion import OccupancyIndex
from ..robot import Robot
from ..scoring import CandidateScorer
from ..task import Task, TaskQueue
from ..types import INF, RobotState, TaskStatus, Vertex
from ..warehouse import Warehouse
from .space_time_search import (
    Budget,
    ReservationTable,
    resolve_residual_conflicts,
    space_time_astar,
)


class TokenPassingPlanner:
    """Algorithm 1 of Ma et al. 2017.

    Satisfies `Simulator`'s planner contract (`plan_step`/`stats`) and, in
    addition, `assign_tasks` -- the optional hook a planner implements when
    it owns task assignment itself.
    """

    #: A* node expansions one agent may spend in one turn with the token,
    #: across however many candidate tasks that pays for. See
    #: `space_time_search.Budget` for why the cap is on work rather than on a
    #: fixed number of candidates. A search that succeeds costs a few hundred
    #: expansions, so this is dozens of candidates for an agent that has
    #: somewhere to go and one wasted timestep for one that does not.
    token_expansion_budget: int = 120_000

    #: Algorithm 2's task swaps. Off here, on in the subclass below.
    task_swaps: bool = False

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

        #: the token: every agent's remaining path, indexed from *now*, so
        #: `paths[i][0]` is where robot i stands at the current timestep
        self.paths: Dict[int, List[Vertex]] = {}

        #: Where Path2 may send an idle agent, keyed by how good a place it is
        #: to stand. Ma et al. say "some endpoint", which on a well-formed
        #: instance means a parking endpoint -- there are at least as many of
        #: those as agents, and resting on one never blocks a path between
        #: task endpoints. These maps have between 0 and 4 of them for up to
        #: 40 agents, so the set is extended with the next best things the map
        #: does offer. A documented deviation, and the alternative is an idle
        #: agent with nowhere legal to go, standing in the only corridor.
        self.rest_endpoints: Dict[Vertex, int] = self._rest_endpoints(warehouse)

        self.token_requests = 0
        self.astar_calls = 0
        self.path_not_found = 0
        self.tasks_assigned = 0
        self.task_swaps_made = 0
        self.move_aside_calls = 0
        self.forced_holds = 0

    # ------------------------------------------------------------ the token
    def _path_end(self, robot: Robot) -> Vertex:
        path = self.paths.get(robot.id)
        return path[-1] if path else robot.position

    def _reservations(
        self,
        robots: Sequence[Robot],
        mover: Robot,
        timestep: int,
        resting: Sequence[int] = (),
    ) -> ReservationTable:
        """The token as a reservation table, from every path but `mover`'s.

        Each path is reserved at absolute times and its last vertex is held
        from then on *forever*: that is where the agent rests until it next
        receives the token, which is the assumption the whole algorithm's
        collision-freedom rests on.

        `resting` names agents to reserve as standing still at their current
        cell instead of following their committed path. TPTS needs it: an
        agent about to have its task taken away will stop where it is, so a
        path planned around the journey it is no longer going to make would
        be planned around nothing.
        """
        stopped = set(resting)
        table = ReservationTable()
        for robot in robots:
            if robot.id == mover.id:
                continue
            path = (
                [robot.position]
                if robot.id in stopped
                else (self.paths.get(robot.id) or [robot.position])
            )
            table.reserve_path(robot.id, path, timestep, rest_at_end=True)
        return table

    def _reachable(self, robot: Robot, table: ReservationTable, timestep: int) -> Set[Vertex]:
        """Where this agent could still get to, given who has already stopped.

        A sound prune, not a heuristic one: an agent whose path has ended is
        resting on its cell from now until it next receives the token, so
        that cell is blocked at *every* future timestep and no space-time
        path can cross it. Anything outside the component this leaves is
        unreachable at any time, and a search for it is guaranteed to fail
        after exhausting the whole time-expanded space.

        Token Passing asks an idle agent to try again every timestep -- that
        is the algorithm, not a bug -- so on a floor where several agents are
        boxed in, this is the difference between a few hundred wasted A*
        expansions per timestep and a few hundred thousand.
        """
        blocked = {
            vertex
            for vertex, (holder, from_time) in table.terminal_holds.items()
            if holder != robot.id and from_time <= timestep
        }
        seen = {robot.position}
        stack = [robot.position]
        while stack:
            vertex = stack.pop()
            for neighbour in self.graph.neighbors(vertex):
                if neighbour in seen or neighbour in blocked:
                    continue
                seen.add(neighbour)
                stack.append(neighbour)
        return seen

    def _plan(
        self,
        robot: Robot,
        goals: Sequence[Vertex],
        table: ReservationTable,
        timestep: int,
        budget: Optional[Budget] = None,
    ) -> Optional[List[Vertex]]:
        """One path through `goals` in order, avoiding the token.

        Path1 is `[pickup, delivery]` and Path2 is `[somewhere out of the
        way]`; both are the same search run once per leg, with each leg's
        reservations committed before the next so the legs agree with each
        other.
        """
        working = ReservationTable(
            dict(table.vertex_reservations),
            dict(table.edge_reservations),
            dict(table.terminal_holds),
            table.horizon_end,
            {v: dict(c) for v, c in table.latest_claim.items()},
        )
        path: List[Vertex] = [robot.position]
        at = robot.position
        start = timestep
        for goal in goals:
            self.astar_calls += 1
            leg = space_time_astar(
                self.graph, at, goal, start, working,
                robot_id=robot.id, budget=budget,
            )
            if leg is None:
                self.path_not_found += 1
                return None
            working.reserve_path(robot.id, leg, start, rest_at_end=True)
            path.extend(leg[1:])
            start += len(leg) - 1
            at = goal
        return path

    # -------------------------------------------------------- assignment
    def assign_tasks(
        self, robots: Sequence[Robot], task_queue: TaskQueue, timestep: int
    ) -> None:
        """The `while` loop of Algorithm 1, lines 4-14.

        Called by `Simulator` instead of `assignment.TaskAssigner`. Every
        agent that has reached the end of its path receives the token in
        turn; an agent freed by a task swap re-enters the queue.
        """
        pending = [r for r in robots if len(self.paths.get(r.id) or []) <= 1]
        queued = list(pending)
        seen = 0
        # bounded: each pass either commits an agent or leaves it resting,
        # and a swap re-queues exactly the one agent it robbed
        while queued and seen < 4 * len(robots) + 8:
            robot = queued.pop(0)
            seen += 1
            self.token_requests += 1
            freed = self._receive_token(robot, robots, task_queue, timestep)
            if freed is not None:
                queued.append(freed)

    def _receive_token(
        self,
        robot: Robot,
        robots: Sequence[Robot],
        task_queue: TaskQueue,
        timestep: int,
    ) -> Optional[Robot]:
        """One agent's turn with the token. Returns an agent it robbed, if any."""
        table = self._reservations(robots, robot, timestep)
        reachable = self._reachable(robot, table, timestep)
        budget = Budget(self.token_expansion_budget)

        if robot.task is not None:
            # mid-task and out of path: TPTS plans pickup and delivery as two
            # separate searches, so this is the normal way it reaches its
            # delivery. For TP it only happens if a leg failed earlier.
            remaining = self._remaining_goals(robot)
            path = (
                self._plan(robot, remaining, table, timestep, budget)
                if all(goal in reachable for goal in remaining)
                else None
            )
            self.paths[robot.id] = path if path is not None else [robot.position]
            return None

        available = [
            task
            for task in self._assignable(robot, robots, task_queue, timestep)
            if task.pickup in reachable and task.delivery in reachable
        ]
        if available:
            robbed = self._take_best_task(
                robot, available, robots, table, task_queue, timestep, budget
            )
            if robbed is not None or robot.task is not None:
                return robbed

        # Algorithm 1, lines 11-14: rest here, unless resting here is what is
        # stopping somebody's task from ever being delivered.
        #
        # The paper's trigger is narrow -- move aside only if you are sitting
        # *on* the delivery cell of a pending task -- because on a well-formed
        # MAPD instance that is the only way a resting agent can block one.
        # These maps are not well-formed (see the module docstring), and there
        # the same agent blocks by sitting in the one corridor joining the
        # pickups to the deliveries. Path2 exists to get an idle agent out of
        # the way, so its trigger is widened to: *there is work to do and none
        # of it is reachable* -- reachable in the graph with every
        # already-resting agent removed, which is what `_reachable` computes.
        # That is a documented deviation from Algorithm 1, and without it
        # Token Passing deadlocks at t = 0 on `warehouse_bottleneck`, where 16
        # agents resting on 83 cells cut the floor into pieces before a single
        # task has been handed out.
        #
        # Deliberately *not* widened further, to "could not plan to any of
        # it". A search that fails while its goal is still reachable failed
        # because of an agent that is moving, and that agent will have moved
        # by the next timestep -- so the answer there is to wait and ask
        # again, which is the algorithm's own answer, rather than to walk off
        # somewhere. Widening it that far also made every idle agent on a
        # saturated floor run a Path2 search every timestep.
        unreachable_work = bool(task_queue.available(timestep)) and not available
        blocking = unreachable_work or any(
            task.delivery == robot.position
            for task in task_queue.tasks.values()
            if task.status is not TaskStatus.COMPLETED
        )
        if not blocking:
            self.paths[robot.id] = [robot.position]
            return None
        self.paths[robot.id] = self._move_aside(
            robot, robots, table, task_queue, timestep, reachable, budget
        )
        return None

    def _remaining_goals(self, robot: Robot) -> List[Vertex]:
        task = robot.task
        if task is None:
            return []
        if robot.state is RobotState.TO_DELIVERY:
            return [task.delivery]
        return [task.pickup, task.delivery]

    def _assignable(
        self,
        robot: Robot,
        robots: Sequence[Robot],
        task_queue: TaskQueue,
        timestep: int,
    ) -> List[Task]:
        """Algorithm 1, line 6: tasks whose endpoints nobody else is resting on."""
        occupied = {self._path_end(other) for other in robots if other.id != robot.id}
        return [
            task
            for task in task_queue.available(timestep)
            if task.pickup not in occupied and task.delivery not in occupied
        ]

    def _take_best_task(
        self,
        robot: Robot,
        available: Sequence[Task],
        robots: Sequence[Robot],
        table: ReservationTable,
        task_queue: TaskQueue,
        timestep: int,
        budget: Budget,
    ) -> Optional[Robot]:
        """Algorithm 1, lines 7-10: nearest reachable pickup, then plan it.

        Tries tasks in order of pickup distance and stops at the first one it
        can actually plan, or when this turn's search budget runs out. On a
        well-formed instance the first is always plannable and this is exactly
        the paper; on these maps it sometimes is not, and trying the
        next-nearest is strictly better than leaving an idle agent and an
        unclaimed task in the same room.
        """
        ordered = sorted(
            (
                (self.graph.route_distance(robot.position, task.pickup), task.id, task)
                for task in available
            ),
            key=lambda item: (item[0], item[1]),
        )
        for distance, _, task in ordered:
            if distance == INF or not budget:
                break
            path = self._plan(
                robot, [task.pickup, task.delivery], table, timestep, budget
            )
            if path is None:
                continue
            self._commit(robot, task, path)
            return None
        return None

    def _commit(self, robot: Robot, task: Task, path: List[Vertex]) -> None:
        robot.task = task
        robot.state = RobotState.TO_PICKUP
        robot.waypoint = task.pickup
        robot.parking_vertex = None
        task.assignment = robot.id
        task.status = TaskStatus.TO_PICKUP
        self.paths[robot.id] = path
        self.tasks_assigned += 1

    def _move_aside(
        self,
        robot: Robot,
        robots: Sequence[Robot],
        table: ReservationTable,
        task_queue: TaskQueue,
        timestep: int,
        reachable: Set[Vertex],
        budget: Budget,
    ) -> List[Vertex]:
        """Path2: go to an endpoint that is nobody's delivery and nobody's rest.

        Ma et al. leave the choice of endpoint open; the nearest qualifying
        one is taken here. If the instance offers none -- which is what a map
        with no parking bays and a task queue covering every delivery cell
        looks like -- the agent stays put and tries again next timestep.
        """
        self.move_aside_calls += 1
        wanted = {
            task.delivery
            for task in task_queue.tasks.values()
            if task.status is not TaskStatus.COMPLETED
        }
        resting = {self._path_end(other) for other in robots if other.id != robot.id}
        candidates = sorted(
            (tier, self.graph.route_distance(robot.position, endpoint), endpoint)
            for endpoint, tier in self._endpoints().items()
            if endpoint in reachable
            and endpoint not in wanted
            and endpoint not in resting
        )
        for _, distance, endpoint in candidates:
            if not budget:
                break
            if distance == INF or endpoint == robot.position:
                continue
            path = self._plan(robot, [endpoint], table, timestep, budget)
            if path is not None:
                return path
        return [robot.position]

    @staticmethod
    def _rest_endpoints(warehouse: Warehouse) -> Dict[Vertex, int]:
        """Every cell an idle agent may be sent to, keyed by how good it is.

        Tier 0 is a real parking endpoint, tier 1 a passing bay, tier 2 any
        remaining non-task cell whose occupant does not disconnect the floor.
        Path2 prefers the lowest tier it can reach, and the tiers exist
        because these maps do not supply enough of tier 0: `warehouse_medium`
        has four bays for forty agents and `warehouse_corridors` has none.
        """
        graph = warehouse.graph
        articulations = graph.articulation_points
        stations = set(warehouse.pickup_vertices) | set(warehouse.delivery_vertices)
        pools = (
            warehouse.parking_vertices,
            [v for v, info in warehouse.info.items() if info.is_passing_bay],
            [v for v in graph.vertices if v not in stations and v not in articulations],
        )
        tiers: Dict[Vertex, int] = {}
        for tier, pool in enumerate(pools):
            for vertex in pool:
                tiers.setdefault(vertex, tier)
        return tiers

    def _endpoints(self) -> Dict[Vertex, int]:
        return self.rest_endpoints

    # ------------------------------------------------------------ movement
    def plan_step(self, ordered_robots: Sequence[Robot], timestep: int) -> None:
        """Advance every agent one cell along its path in the token."""
        for robot in ordered_robots:
            robot.reset_step_state()

        for robot in ordered_robots:
            path = self.paths.get(robot.id) or [robot.position]
            if path[0] != robot.position:
                # the token and the world disagree: only reachable through
                # the residual-conflict net below, and repaired by dropping
                # the path so the agent requests the token again
                path = [robot.position]
                self.paths[robot.id] = path
            robot.next_position = path[1] if len(path) > 1 else path[0]

        forced = resolve_residual_conflicts(ordered_robots)
        self.forced_holds += len(forced)

        for robot in ordered_robots:
            path = self.paths.get(robot.id) or [robot.position]
            if robot.id in forced:
                self.paths[robot.id] = [robot.position]
            else:
                self.paths[robot.id] = path[1:] if len(path) > 1 else path

    def stats(self) -> Dict[str, Any]:
        return {
            "tp_token_requests": self.token_requests,
            "tp_astar_calls": self.astar_calls,
            "tp_path_not_found": self.path_not_found,
            "tp_tasks_assigned": self.tasks_assigned,
            "tp_task_swaps": self.task_swaps_made,
            "tp_move_aside_calls": self.move_aside_calls,
            "tp_forced_holds": self.forced_holds,
        }


class TokenPassingTaskSwapsPlanner(TokenPassingPlanner):
    """Algorithm 2 of Ma et al. 2017: Token Passing with Task Swaps (TPTS).

    Two differences from TP, both from the paper:

    1. An agent with the token may consider tasks **already assigned to
       another agent that has not yet picked them up**, and take one if it
       would reach the pickup sooner. The robbed agent is freed and requests
       the token again in the same round.
    2. Because a task can be taken away up to the moment it is picked up, an
       agent plans to the pickup and, on arriving, plans again to the
       delivery -- rather than committing to both legs at once as TP does.

    The endpoint restriction of Algorithm 1 line 6 is relaxed accordingly:
    the point of swapping is that a task's endpoints being spoken for does
    not have to mean the task waits.
    """

    task_swaps = True

    def _assignable(
        self,
        robot: Robot,
        robots: Sequence[Robot],
        task_queue: TaskQueue,
        timestep: int,
    ) -> List[Task]:
        """Unassigned tasks, plus assigned ones nobody has picked up yet."""
        occupied = {self._path_end(other) for other in robots if other.id != robot.id}
        free = [
            task
            for task in task_queue.available(timestep)
            if task.pickup not in occupied and task.delivery not in occupied
        ]
        swappable = [
            task
            for task in task_queue.tasks.values()
            if task.status is TaskStatus.TO_PICKUP
            and task.assignment is not None
            and task.assignment != robot.id
            and task.release_time <= timestep
        ]
        return free + swappable

    def _take_best_task(
        self,
        robot: Robot,
        available: Sequence[Task],
        robots: Sequence[Robot],
        table: ReservationTable,
        task_queue: TaskQueue,
        timestep: int,
        budget: Budget,
    ) -> Optional[Robot]:
        by_id = {r.id: r for r in robots}
        ordered = sorted(
            (
                (self.graph.route_distance(robot.position, task.pickup), task.id, task)
                for task in available
            ),
            key=lambda item: (item[0], item[1]),
        )
        for distance, _, task in ordered:
            if distance == INF or not budget:
                break
            holder = by_id.get(task.assignment) if task.assignment is not None else None
            if holder is not None and holder.id != robot.id:
                # a swap is only allowed if this agent really would get there
                # sooner -- otherwise the two agents trade the task forever
                holder_distance = self.graph.route_distance(holder.position, task.pickup)
                if distance >= holder_distance:
                    continue
                # the robbed agent stops where it stands until it receives the
                # token again, so plan around that rather than around the
                # journey it is about to abandon
                swap_table = self._reservations(robots, robot, timestep, resting=(holder.id,))
            else:
                swap_table = table
            # TPTS plans only to the pickup; the delivery leg is planned on
            # arrival, which is what makes a swap cheap
            path = self._plan(robot, [task.pickup], swap_table, timestep, budget)
            if path is None:
                continue
            if holder is not None and holder.id != robot.id:
                self._release(holder)
                self.task_swaps_made += 1
            self._commit(robot, task, path)
            return holder if holder is not None and holder.id != robot.id else None
        return None

    def _release(self, holder: Robot) -> None:
        holder.task = None
        holder.state = RobotState.FREE
        holder.waypoint = holder.position
        self.paths[holder.id] = [holder.position]


__all__ = ["TokenPassingPlanner", "TokenPassingTaskSwapsPlanner"]
