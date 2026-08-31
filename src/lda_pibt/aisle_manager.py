"""High-level aisle traffic controller.

Implements spec sections 10 (aisle states), 11-12 (directional demand),
16 (hysteresis), 17 (drain-before-reverse), 18 (entry reservations) and
27 (aisle-direction constraints on candidate moves).

Design principle (spec 42): *robots generate directional requests, but aisles
make directional decisions.*
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from .config import Params
from .congestion import OccupancyIndex
from .robot import Robot
from .types import (
    INF,
    AisleDirection,
    AisleState,
    Reservation,
    Vertex,
)
from .warehouse import Aisle, Warehouse


class AisleManager:
    """Owns aisle direction, queues and reservations."""

    def __init__(self, warehouse: Warehouse, index: OccupancyIndex, params: Params):
        self.warehouse = warehouse
        self.index = index
        self.params = params
        self.direction_switches = 0
        self.starvation_flips = 0
        self.requests: Dict[int, Tuple[int, AisleDirection]] = {}
        #: robot id -> [(aisle id, direction, weight)], populated only when
        #: `demand_spread` is on; see `update_aisle_queues`
        self.route_requests: Dict[int, List[Tuple[int, AisleDirection, float]]] = {}
        self._robot_lookup: Dict[int, Robot] = {}

    @property
    def enabled(self) -> bool:
        """True when aisles decide direction (as opposed to robots preferring one)."""
        return self.params.direction_control == "aisle"

    @property
    def active(self) -> bool:
        """True when the aisle layer does anything at all this run.

        Entry admission is a capacity mechanism and makes sense whatever decides
        direction, so `reservations` alone is enough to keep the layer running.
        Gating it on `enabled` is what made `reservations=True` a silent no-op
        under `direction_control="robot"`.
        """
        return self.enabled or self.params.reservations

    # ------------------------------------------------------ robot requests
    def requested_direction(self, robot: Robot, aisle: Aisle) -> AisleDirection:
        """Direction in which `robot` intends to traverse `aisle` (spec 12)."""
        route = robot.route or [robot.position]
        indices = [
            aisle.position_index(v)
            for v in route
            if aisle.position_index(v) is not None
        ]
        if len(indices) >= 2:
            return (
                AisleDirection.FORWARD
                if indices[-1] > indices[0]
                else AisleDirection.REVERSE
                if indices[-1] < indices[0]
                else AisleDirection.NONE
            )
        # Single touching vertex: use the entry side, then the exit side.
        first_inside = next(
            (v for v in route if aisle.position_index(v) is not None), None
        )
        if first_inside is None:
            return AisleDirection.NONE
        return aisle.entry_direction(first_inside)

    def route_direction_requests(
        self, robot: Robot
    ) -> List[Tuple[int, AisleDirection, float]]:
        """Every aisle the robot's route touches, with the direction it needs.

        Spec 12 aggregates demand over "the aisles along its route"; charging a
        robot to its next aisle alone hides two things an aisle needs to know:
        the robots already inside it that want to keep flowing, and the traffic
        heading for it from more than one aisle away.  Weight decays as
        1/(1 + steps to the entry) so imminent demand still dominates.
        """
        route = robot.route or [robot.position]
        spans: Dict[int, Tuple[int, int, int]] = {}
        for step, vertex in enumerate(route):
            aisle_id = self.warehouse.aisle_id(vertex)
            if aisle_id is None:
                continue
            index = self.warehouse.aisles[aisle_id].position_index(vertex)
            if index is None:
                continue
            # Keep the first index the route touches and the last, so a route
            # that leaves and re-enters the aisle is still one span.
            first, _last, entry_step = spans.get(aisle_id, (index, index, step))
            spans[aisle_id] = (first, index, entry_step)

        requests: List[Tuple[int, AisleDirection, float]] = []
        for aisle_id, (first, last, entry_step) in spans.items():
            aisle = self.warehouse.aisles[aisle_id]
            if last > first:
                direction = AisleDirection.FORWARD
            elif last < first:
                direction = AisleDirection.REVERSE
            else:
                direction = aisle.entry_direction(route[entry_step])
            if direction is AisleDirection.NONE:
                continue
            requests.append((aisle_id, direction, 1.0 / (1.0 + entry_step)))
        return requests

    def update_aisle_queues(self, robots: Iterable[Robot], timestep: int) -> None:
        """Refresh entrance queues and the per-robot direction request (30.6)."""
        robots = list(robots)
        for aisle in self.warehouse.aisles.values():
            aisle.forward_queue.clear()
            aisle.reverse_queue.clear()
        self.requests.clear()
        self.route_requests.clear()
        self._robot_lookup = {r.id: r for r in robots}

        for robot in robots:
            aisle_id = robot.next_aisle
            if aisle_id is None:
                aisle_id = robot.current_aisle
            aisle = self.warehouse.get_aisle(aisle_id)
            if aisle is None:
                robot.preferred_aisle_direction = AisleDirection.NONE
            else:
                direction = self.requested_direction(robot, aisle)
                robot.preferred_aisle_direction = direction
                if direction is not AisleDirection.NONE:
                    self.requests[robot.id] = (aisle.id, direction)

            if self.params.demand_spread:
                entries = self.route_direction_requests(robot)
            elif robot.id in self.requests:
                next_aisle_id, next_direction = self.requests[robot.id]
                entries = [(next_aisle_id, next_direction, 1.0)]
            else:
                entries = []
            self.route_requests[robot.id] = entries
            for entry_aisle_id, direction, _weight in entries:
                if robot.current_aisle == entry_aisle_id:
                    continue
                entry_aisle = self.warehouse.aisles[entry_aisle_id]
                queue = (
                    entry_aisle.forward_queue
                    if direction is AisleDirection.FORWARD
                    else entry_aisle.reverse_queue
                )
                queue.add(robot.id)

    # ---------------------------------------------------- directional demand
    def _robot_demand(self, robot: Robot, aisle: Aisle, timestep: int) -> float:
        r"""w_u U_i + w_w W_i + w_p P_i - w_l L_i - w_c C_i (spec 12)."""
        p = self.params
        urgency = robot.task.urgency(timestep) if robot.task else 0.0
        # A robot that shuffles in place still starves, so stalled route
        # progress counts as waiting (this is what lets an aisle flip).
        waiting = float(
            max(robot.waiting_time, robot.no_progress_steps) + robot.blocked_time
        )
        graph = self.warehouse.graph
        d_start = graph.route_distance(robot.position, aisle.start_vertex)
        d_end = graph.route_distance(robot.position, aisle.end_vertex)
        nearest = min(d_start, d_end)
        proximity = 1.0 / (1.0 + nearest) if nearest != INF else 0.0
        route_length = (
            robot.route_distance_to_waypoint
            if robot.route_distance_to_waypoint != INF
            else 0.0
        )
        congestion = self.index.aisle_load(aisle.id)
        return (
            p.w_urgency * urgency
            + p.w_waiting * waiting
            + p.w_proximity * proximity
            - p.w_route_length * route_length
            - p.w_congestion * congestion
        )

    def compute_directional_demand(
        self,
        aisle: Aisle,
        direction: AisleDirection,
        robots: Dict[int, Robot],
        timestep: int,
    ) -> float:
        """S_a^+ or S_a^- (spec section 12)."""
        total = 0.0
        for robot_id, entries in self.route_requests.items():
            robot = robots.get(robot_id)
            if robot is None:
                continue
            for aisle_id, requested, weight in entries:
                if aisle_id != aisle.id or requested is not direction:
                    continue
                total += weight * self._robot_demand(robot, aisle, timestep)
        return total

    # ------------------------------------------------------ direction update
    def update_aisle_direction(
        self,
        aisle: Aisle,
        forward_demand: float,
        reverse_demand: float,
        timestep: int,
    ) -> AisleDirection:
        """Spec section 16.1, with drain-before-reverse (17) and a maximum green.

        Hysteresis alone is only half a traffic signal.  It bounds how *soon* a
        committed direction may change; nothing bounds how long it may persist.
        An aisle whose two demands are near-balanced -- the normal case when
        pickups sit on one side of the map and deliveries on the other -- never
        leaves the dead band, so it holds one direction indefinitely and the
        traffic wanting the other way starves. `maximum_aisle_lock_time` closes
        that gap: past it, any opposing demand at all forces a drain and a flip.
        """
        imbalance = forward_demand - reverse_demand
        if self.params.parity_bias and (forward_demand or reverse_demand):
            imbalance += self.params.parity_bias * (1 if aisle.id % 2 == 0 else -1)
        threshold = (
            aisle.switch_threshold if self.params.hysteresis else 0.0
        )
        occupancy = self.index.aisle_occupancy.get(aisle.id, 0)
        aisle.occupancy = occupancy

        # Spec 16: an aisle may switch immediately when the current direction is
        # *infeasible*.  A DRAINING aisle that never empties is exactly that, so
        # reopen it rather than letting it block traffic forever.
        if aisle.state is AisleState.DRAINING:
            if aisle.draining_since is None:
                aisle.draining_since = timestep
            drained = occupancy == 0
            timed_out = timestep - aisle.draining_since > self.params.max_drain_time
            if not (drained or timed_out):
                return aisle.current_direction
            target = aisle.pending_direction
            aisle.draining_since = None
            aisle.pending_direction = AisleDirection.NONE
            if drained and target is not AisleDirection.NONE:
                # The drain was requested to reverse the flow; honour it now
                # rather than falling back into the imbalance test, which would
                # simply re-commit the direction we just drained.
                self._commit(
                    aisle,
                    target,
                    AisleState.FORWARD
                    if target is AisleDirection.FORWARD
                    else AisleState.REVERSE,
                    timestep,
                )
                return target
            aisle.state = AisleState.OPEN
            aisle.previous_direction = aisle.current_direction
            aisle.current_direction = AisleDirection.NONE
            aisle.lock_until = timestep + aisle.minimum_lock_time
            aisle.direction_since = timestep
            return AisleDirection.NONE
        aisle.draining_since = None

        # Robots are still inside: hold the direction, possibly start draining.
        if occupancy > 0 and aisle.current_direction is not AisleDirection.NONE:
            forward = aisle.current_direction is AisleDirection.FORWARD
            opposing_demand = reverse_demand if forward else forward_demand
            opposing_imbalance = -imbalance if forward else imbalance
            starved = (
                timestep - aisle.direction_since >= aisle.maximum_lock_time
                and opposing_demand > 0.0
            )
            if opposing_imbalance > threshold or starved:
                self._begin_drain(aisle, timestep, forced=starved)
            return aisle.current_direction

        # Aisle is empty (or has no committed direction).
        if self.params.hysteresis and timestep < aisle.lock_until:
            return aisle.current_direction

        if imbalance > threshold and (forward_demand > 0.0 or threshold == 0.0):
            self._commit(aisle, AisleDirection.FORWARD, AisleState.FORWARD, timestep)
            return AisleDirection.FORWARD
        if imbalance < -threshold and (reverse_demand > 0.0 or threshold == 0.0):
            self._commit(aisle, AisleDirection.REVERSE, AisleState.REVERSE, timestep)
            return AisleDirection.REVERSE

        aisle.state = AisleState.OPEN
        aisle.current_direction = AisleDirection.NONE
        aisle.direction_since = timestep
        return AisleDirection.NONE

    def _begin_drain(self, aisle: Aisle, timestep: int, forced: bool) -> None:
        """Enter DRAINING, remembering which way the aisle should flow next."""
        if aisle.state is AisleState.DRAINING:
            return
        aisle.state = AisleState.DRAINING
        aisle.draining_since = timestep
        aisle.pending_direction = (
            AisleDirection.REVERSE
            if aisle.current_direction is AisleDirection.FORWARD
            else AisleDirection.FORWARD
        )
        if forced:
            aisle.starvation_flips += 1
            self.starvation_flips += 1

    def _commit(
        self,
        aisle: Aisle,
        direction: AisleDirection,
        state: AisleState,
        timestep: int,
    ) -> None:
        if aisle.current_direction is not direction:
            aisle.previous_direction = aisle.current_direction
            if aisle.previous_direction is not AisleDirection.NONE:
                aisle.direction_switches += 1
                self.direction_switches += 1
            aisle.direction_since = timestep
        aisle.current_direction = direction
        aisle.state = state
        aisle.lock_until = timestep + aisle.minimum_lock_time

    def step_directions(self, robots: Dict[int, Robot], timestep: int) -> None:
        """Spec section 30, step 7."""
        if not self.enabled:
            return
        proposals: List[Tuple[float, int, Aisle, float, float]] = []
        for aisle in self.warehouse.aisles.values():
            if not aisle.manageable:
                aisle.state = AisleState.OPEN
                aisle.current_direction = AisleDirection.NONE
                continue
            forward = self.compute_directional_demand(
                aisle, AisleDirection.FORWARD, robots, timestep
            )
            reverse = self.compute_directional_demand(
                aisle, AisleDirection.REVERSE, robots, timestep
            )
            proposals.append((abs(forward - reverse), aisle.id, aisle, forward, reverse))

        if not self.params.coordinate_aisle_directions:
            for _, _, aisle, forward, reverse in proposals:
                self.update_aisle_direction(aisle, forward, reverse, timestep)
            return

        # Decide the directions as a *set*: strongest demand commits first, and
        # any commit that would leave some vertex unreachable from another is
        # rolled back, so the directed graph always stays strongly connected.
        proposals.sort(key=lambda entry: (-entry[0], entry[1]))
        assignment = {a.id: a.current_direction for a in self.warehouse.aisles.values()}
        for _, aisle_id, aisle, forward, reverse in proposals:
            before = assignment[aisle_id]
            snapshot = (
                aisle.current_direction,
                aisle.previous_direction,
                aisle.state,
                aisle.lock_until,
                aisle.draining_since,
                aisle.direction_since,
                aisle.pending_direction,
            )
            proposed = self.update_aisle_direction(aisle, forward, reverse, timestep)
            assignment[aisle_id] = proposed
            if proposed is before or proposed is AisleDirection.NONE:
                continue
            if self._strongly_connected(assignment):
                continue
            (
                aisle.current_direction,
                aisle.previous_direction,
                aisle.state,
                aisle.lock_until,
                aisle.draining_since,
                aisle.direction_since,
                aisle.pending_direction,
            ) = snapshot
            assignment[aisle_id] = before

    def _strongly_connected(self, assignment: Dict[int, AisleDirection]) -> bool:
        """Is every vertex still reachable from every other under `assignment`?"""
        graph = self.warehouse.graph
        vertices = graph.vertices
        if not vertices:
            return True

        def passable(source: Vertex, target: Vertex) -> bool:
            aisle_id = self.warehouse.aisle_id(target)
            if aisle_id is None:
                return True
            direction = assignment.get(aisle_id, AisleDirection.NONE)
            if direction is AisleDirection.NONE:
                return True
            return self.warehouse.traversal_direction(source, target) is direction

        def reachable(forward: bool) -> int:
            start = vertices[0]
            seen = {start}
            stack = [start]
            while stack:
                current = stack.pop()
                for neighbor in graph.neighbors(current):
                    ok = (
                        passable(current, neighbor)
                        if forward
                        else passable(neighbor, current)
                    )
                    if ok and neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            return len(seen)

        return reachable(True) == len(vertices) and reachable(False) == len(vertices)

    # -------------------------------------------------------- reservations
    def update_aisle_reservations(
        self, ordered_robots: List[Robot], timestep: int
    ) -> None:
        """Expire stale reservations, then grant new ones (spec 18).

        Gated on `reservations` alone.  Admission control caps how many robots
        may be inside a narrow aisle at once, which is a claim about capacity,
        not about direction -- gating it on `direction_control == "aisle"` made
        the flag a silent no-op in every robot-level configuration.
        """
        if not self.params.reservations:
            return
        for aisle in self.warehouse.aisles.values():
            expired = [
                rid
                for rid, res in aisle.reservations.items()
                if not res.is_valid(timestep)
            ]
            for rid in expired:
                aisle.reservations.pop(rid, None)

        for robot in ordered_robots:
            entry = self.requests.get(robot.id)
            if entry is None:
                continue
            aisle_id, direction = entry
            aisle = self.warehouse.get_aisle(aisle_id)
            if aisle is None or robot.current_aisle == aisle_id:
                continue
            if not aisle.manageable:
                continue
            if aisle.state in (AisleState.DRAINING,):
                continue
            if (
                aisle.current_direction is not AisleDirection.NONE
                and direction is not aisle.current_direction
            ):
                continue
            if robot.id in aisle.reservations:
                continue
            occupancy = self.index.aisle_occupancy.get(aisle.id, 0)
            pending = sum(
                1
                for rid in aisle.reservations
                if self._robot_outside(rid, aisle)
            )
            if occupancy + pending >= aisle.capacity:
                continue
            travel = float(aisle.length)
            if not self._at_entrance(robot, aisle):
                continue
            reservation = Reservation(
                robot_id=robot.id,
                aisle_id=aisle.id,
                direction=direction,
                entry_time=timestep,
                expected_exit_time=timestep + int(travel) + 1,
                expiry_time=timestep + self.params.reservation_ttl,
            )
            aisle.reservations[robot.id] = reservation
            robot.aisle_reservation = reservation

    def _robot_outside(self, robot_id: int, aisle: Aisle) -> bool:
        robot = self._robot_lookup.get(robot_id)
        return robot is None or robot.current_aisle != aisle.id

    def consume_reservations(self, robots: Iterable[Robot]) -> None:
        """Drop reservations once the robot is inside or past its target aisle."""
        for robot in robots:
            res = robot.aisle_reservation
            if res is None:
                continue
            aisle = self.warehouse.get_aisle(res.aisle_id)
            if aisle is None:
                robot.aisle_reservation = None
                continue
            if robot.current_aisle == aisle.id or robot.next_aisle != aisle.id:
                aisle.reservations.pop(robot.id, None)
                robot.aisle_reservation = None

    def _at_entrance(self, robot: Robot, aisle: Aisle, radius: int = 3) -> bool:
        """Only robots queued at an entrance may hold a reservation."""
        graph = self.warehouse.graph
        return min(
            graph.route_distance(robot.position, aisle.start_vertex),
            graph.route_distance(robot.position, aisle.end_vertex),
        ) <= radius

    def has_valid_reservation(
        self, robot: Robot, aisle: Aisle, timestep: int
    ) -> bool:
        res = aisle.reservations.get(robot.id)
        return res is not None and res.is_valid(timestep)

    def release_reservations(self, robot: Robot) -> None:
        for aisle in self.warehouse.aisles.values():
            aisle.reservations.pop(robot.id, None)
        robot.aisle_reservation = None

    def can_enter_aisle(
        self,
        robot: Robot,
        aisle: Aisle,
        direction: AisleDirection,
        timestep: int,
    ) -> bool:
        """Spec section 18.1.

        Two separate gates, because they answer different questions:

        * the **occupancy cap** applies to every managed aisle, OPEN included.
          Over-filling a single-file corridor is what builds a queue that
          cannot drain, and that is true whether or not the corridor currently
          has a direction.  This is the mechanism H3 is about.
        * the **ticket** is required only once the aisle is directional, per
          spec 18. Demanding one to enter an OPEN aisle would throttle every
          crossing on a map whose aisles are mostly short and mostly open.
        """
        if aisle.state is AisleState.DRAINING:
            return False
        if aisle.state is not AisleState.OPEN and direction is not aisle.current_direction:
            return False
        if self.index.aisle_occupancy.get(aisle.id, 0) >= aisle.capacity:
            return False
        if not self.params.reservations or aisle.state is AisleState.OPEN:
            return True
        return self.has_valid_reservation(robot, aisle, timestep)

    # ------------------------------------------------ movement legality (27)
    def violates_aisle_direction(
        self, robot: Robot, current: Vertex, candidate: Vertex, timestep: int
    ) -> bool:
        """Spec section 27: does this move run against the aisle's direction?

        Per spec 10.2/10.4 the directional state restricts *entry*: a robot
        already inside may continue in the aisle direction or leave.  An egress
        move toward the nearer endpoint is always kept available so that the
        DRAINING state can actually terminate.

        The name is historical: this is a *predicate*, not a veto.  Its answer
        is priced by `CandidateScorer.aisle_penalty` rather than used to drop
        the candidate -- see the note there for why deleting the move instead
        breaks PIBT's progress argument.  It becomes a rejection again only
        under `hard_direction_constraints`.
        """
        if not self.enabled:
            return False
        if timestep <= robot.ignore_direction_until:
            return False
        if candidate == current:
            return False
        candidate_aisle_id = self.warehouse.aisle_id(candidate)
        if candidate_aisle_id is None:
            return False  # intersections are governed by their own rules
        aisle = self.warehouse.aisles[candidate_aisle_id]
        if aisle.state is AisleState.OPEN:
            return False

        direction = self.warehouse.traversal_direction(current, candidate)
        inside = robot.current_aisle == aisle.id

        if inside:
            if direction is aisle.current_direction:
                return False
            return not self._is_egress_move(aisle, current, candidate)

        if aisle.state is AisleState.DRAINING:
            return True
        return direction is not aisle.current_direction

    @staticmethod
    def _is_egress_move(aisle: Aisle, current: Vertex, candidate: Vertex) -> bool:
        """True when the move takes the robot closer to the nearest aisle end."""
        ci = aisle.position_index(current)
        ni = aisle.position_index(candidate)
        if ci is None or ni is None:
            return True  # leaving the aisle entirely
        last = aisle.length - 1
        return min(ni, last - ni) < min(ci, last - ci)

    def violates_aisle_reservation(
        self, robot: Robot, current: Vertex, candidate: Vertex, timestep: int
    ) -> bool:
        """Entering a managed aisle needs a ticket and room (spec 18).

        Independent of `direction_control`: this is the capacity mechanism, and
        an over-filled corridor jams the same way whichever layer chose its
        direction.
        """
        if not self.params.reservations:
            return False
        if timestep <= robot.ignore_direction_until:
            return False
        candidate_aisle_id = self.warehouse.aisle_id(candidate)
        if candidate_aisle_id is None or candidate_aisle_id == robot.current_aisle:
            return False
        aisle = self.warehouse.aisles[candidate_aisle_id]
        if not aisle.manageable:
            return False
        direction = self.warehouse.traversal_direction(current, candidate)
        return not self.can_enter_aisle(robot, aisle, direction, timestep)

    # ----------------------------------------------------------- statistics
    def state_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for aisle in self.warehouse.aisles.values():
            counts[aisle.state.value] += 1
        return dict(counts)

    def record_aisle_exits(self, robots: Iterable[Robot]) -> None:
        """Count robots that left a managed aisle this step (H1's own metric).

        Global throughput cannot say whether direction control made the *aisles*
        flow better; the number of robots each managed aisle clears can.
        """
        for robot in robots:
            previous = self.warehouse.aisle_id(robot.previous_position)
            if previous is None or previous == robot.current_aisle:
                continue
            aisle = self.warehouse.aisles[previous]
            if aisle.manageable:
                aisle.exits += 1

    def count_head_on_conflicts(self, robots: Iterable[Robot]) -> int:
        """Pairs facing each other inside one single-file aisle (H3's metric).

        Two robots on adjacent cells of the same straight aisle, each of whose
        *routes* runs through the other's cell, cannot pass: one has to reverse
        out of the aisle.  This is the event entry admission exists to prevent.

        The test is on intended routes, not on the granted moves: PIBT rejects
        swap conflicts by construction, so counting robots that actually
        exchange cells would count an event that can never occur.
        """
        by_position = {robot.position: robot for robot in robots}
        conflicts = 0
        for robot in robots:
            route = robot.route
            if len(route) < 2:
                continue
            target = route[1]
            other = by_position.get(target)
            if other is None or other.id <= robot.id:
                continue  # count each pair once
            if len(other.route) < 2 or other.route[1] != robot.position:
                continue
            aisle_id = self.warehouse.aisle_id(robot.position)
            if aisle_id is None or aisle_id != self.warehouse.aisle_id(target):
                continue
            if self.warehouse.aisles[aisle_id].axis:
                conflicts += 1
        return conflicts

    def stats(self) -> Dict[str, float]:
        managed = [a for a in self.warehouse.aisles.values() if a.manageable]
        return {
            "direction_switches": self.direction_switches,
            "starvation_flips": self.starvation_flips,
            "aisle_exits": sum(a.exits for a in managed),
            "managed_aisles": len(managed),
        }


__all__ = ["AisleManager"]
