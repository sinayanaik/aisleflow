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
        self.requests: Dict[int, Tuple[int, AisleDirection]] = {}
        self._robot_lookup: Dict[int, Robot] = {}

    @property
    def enabled(self) -> bool:
        return self.params.direction_control == "aisle"

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

    def update_aisle_queues(self, robots: Iterable[Robot], timestep: int) -> None:
        """Refresh entrance queues and the per-robot direction request (30.6)."""
        robots = list(robots)
        for aisle in self.warehouse.aisles.values():
            aisle.forward_queue.clear()
            aisle.reverse_queue.clear()
        self.requests.clear()
        self._robot_lookup = {r.id: r for r in robots}

        for robot in robots:
            aisle_id = robot.next_aisle
            if aisle_id is None:
                aisle_id = robot.current_aisle
            aisle = self.warehouse.get_aisle(aisle_id)
            if aisle is None:
                robot.preferred_aisle_direction = AisleDirection.NONE
                continue
            direction = self.requested_direction(robot, aisle)
            robot.preferred_aisle_direction = direction
            if direction is AisleDirection.NONE:
                continue
            self.requests[robot.id] = (aisle.id, direction)
            inside = robot.current_aisle == aisle.id
            if not inside:
                if direction is AisleDirection.FORWARD:
                    aisle.forward_queue.add(robot.id)
                else:
                    aisle.reverse_queue.add(robot.id)

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
        for robot_id, (aisle_id, requested) in self.requests.items():
            if aisle_id != aisle.id or requested is not direction:
                continue
            robot = robots.get(robot_id)
            if robot is None:
                continue
            total += self._robot_demand(robot, aisle, timestep)
        return total

    # ------------------------------------------------------ direction update
    def update_aisle_direction(
        self,
        aisle: Aisle,
        forward_demand: float,
        reverse_demand: float,
        timestep: int,
    ) -> AisleDirection:
        """Spec section 16.1, with the drain-before-reverse protocol (17)."""
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
            elif timestep - aisle.draining_since > self.params.max_drain_time:
                aisle.state = AisleState.OPEN
                aisle.previous_direction = aisle.current_direction
                aisle.current_direction = AisleDirection.NONE
                aisle.lock_until = timestep + aisle.minimum_lock_time
                aisle.draining_since = None
                return AisleDirection.NONE
        else:
            aisle.draining_since = None

        # Robots are still inside: hold the direction, possibly start draining.
        if occupancy > 0 and aisle.current_direction is not AisleDirection.NONE:
            if aisle.current_direction is AisleDirection.FORWARD:
                if imbalance < -threshold:
                    aisle.state = AisleState.DRAINING
                return aisle.current_direction
            if aisle.current_direction is AisleDirection.REVERSE:
                if imbalance > threshold:
                    aisle.state = AisleState.DRAINING
                return aisle.current_direction

        # Aisle is empty (or has no committed direction).
        draining_and_empty = (
            aisle.state is AisleState.DRAINING and occupancy == 0
        )
        if (
            self.params.hysteresis
            and timestep < aisle.lock_until
            and not draining_and_empty
        ):
            return aisle.current_direction

        if imbalance > threshold and (forward_demand > 0.0 or threshold == 0.0):
            self._commit(aisle, AisleDirection.FORWARD, AisleState.FORWARD, timestep)
            return AisleDirection.FORWARD
        if imbalance < -threshold and (reverse_demand > 0.0 or threshold == 0.0):
            self._commit(aisle, AisleDirection.REVERSE, AisleState.REVERSE, timestep)
            return AisleDirection.REVERSE

        aisle.state = AisleState.OPEN
        aisle.current_direction = AisleDirection.NONE
        return AisleDirection.NONE

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
        aisle.current_direction = direction
        aisle.state = state
        aisle.lock_until = timestep + aisle.minimum_lock_time

    def step_directions(self, robots: Dict[int, Robot], timestep: int) -> None:
        """Spec section 30, step 7."""
        if not self.enabled:
            return
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
            self.update_aisle_direction(aisle, forward, reverse, timestep)

    # -------------------------------------------------------- reservations
    def update_aisle_reservations(
        self, ordered_robots: List[Robot], timestep: int
    ) -> None:
        """Expire stale reservations, then grant new ones (spec 18)."""
        if not (self.enabled and self.params.reservations):
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
        """Spec section 18.1."""
        if aisle.state is AisleState.DRAINING:
            return False
        if aisle.state is AisleState.OPEN:
            return True
        if direction is not aisle.current_direction:
            return False
        if self.index.aisle_occupancy.get(aisle.id, 0) >= aisle.capacity:
            return False
        if not self.params.reservations:
            return True
        return self.has_valid_reservation(robot, aisle, timestep)

    # ------------------------------------------------ movement legality (27)
    def violates_aisle_direction(
        self, robot: Robot, current: Vertex, candidate: Vertex, timestep: int
    ) -> bool:
        """Spec section 27.

        Per spec 10.2/10.4 the directional state restricts *entry*: a robot
        already inside may continue in the aisle direction or leave.  An egress
        move toward the nearer endpoint is always kept available so that the
        DRAINING state can actually terminate.
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
        """Entering a directional aisle needs a reservation (spec 18)."""
        if not (self.enabled and self.params.reservations):
            return False
        if timestep <= robot.ignore_direction_until:
            return False
        candidate_aisle_id = self.warehouse.aisle_id(candidate)
        if candidate_aisle_id is None or candidate_aisle_id == robot.current_aisle:
            return False
        aisle = self.warehouse.aisles[candidate_aisle_id]
        if aisle.state is AisleState.OPEN:
            return False
        direction = self.warehouse.traversal_direction(current, candidate)
        return not self.can_enter_aisle(robot, aisle, direction, timestep)

    # ----------------------------------------------------------- statistics
    def state_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for aisle in self.warehouse.aisles.values():
            counts[aisle.state.value] += 1
        return dict(counts)


__all__ = ["AisleManager"]
