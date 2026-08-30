"""Low-level planner: Priority Inheritance with Backtracking.

Implements spec section 22 (candidate generation and hard rejection rules),
section 25 (the PIBT recursion) and section 26 (conflict checks).  Direction,
congestion and turning cost only affect *candidate ordering* and *aisle access*;
they never replace the vertex/swap checks or the backtracking mechanism.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set

from .aisle_manager import AisleManager
from .config import Params
from .congestion import OccupancyIndex
from .robot import Robot
from .scoring import CandidateScorer
from .types import PIBTResult, Vertex, movement_direction
from .warehouse import Warehouse


class PIBTPlanner:
    """One-timestep collision-free move selection for all robots."""

    def __init__(
        self,
        warehouse: Warehouse,
        index: OccupancyIndex,
        scorer: CandidateScorer,
        aisle_manager: AisleManager,
        params: Params,
    ):
        self.warehouse = warehouse
        self.index = index
        self.scorer = scorer
        self.aisles = aisle_manager
        self.params = params

        self.reserved_vertices: Set[Vertex] = set()
        self.recursive_calls = 0
        self.backtracks = 0
        self.invalid_results = 0

    # ------------------------------------------------------------ per-step
    def plan_step(self, ordered_robots: Sequence[Robot], timestep: int) -> None:
        """Spec section 30, step 12."""
        self.reserved_vertices.clear()
        for robot in ordered_robots:
            robot.reset_step_state()
        for robot in ordered_robots:
            if robot.next_position is None:
                result = self.pibt(robot, None, timestep)
                if result is PIBTResult.INVALID:
                    robot.blocked_time += 1
                    self.invalid_results += 1
                else:
                    robot.blocked_time = 0

    # ------------------------------------------------- candidate generation
    def candidates(self, robot: Robot) -> List[Vertex]:
        r"""C_i = N(x_i) union {x_i} (spec 22)."""
        return list(self.warehouse.graph.neighbors(robot.position)) + [robot.position]

    def feasible_candidates(
        self, robot: Robot, parent: Optional[Robot], timestep: int
    ) -> List[Vertex]:
        """Apply the hard rejection rules of spec 22.1."""
        current = robot.position
        feasible: List[Vertex] = []
        for candidate in self.candidates(robot):
            if not self.warehouse.graph.contains(candidate):
                continue
            if self.violates_kinematics(robot, current, candidate):
                continue
            if self.aisles.violates_aisle_direction(
                robot, current, candidate, timestep
            ):
                continue
            if self.aisles.violates_aisle_reservation(
                robot, current, candidate, timestep
            ):
                continue
            if self.creates_vertex_conflict(robot, candidate):
                continue
            if parent is not None and self.creates_swap_conflict(
                robot, parent, candidate
            ):
                continue
            feasible.append(candidate)
        feasible.sort(key=lambda c: self.scorer.sort_key(robot, c))
        return feasible

    def explain_candidates(
        self, robot: Robot, timestep: int
    ) -> List[Dict[str, object]]:
        """Why each candidate move was accepted or rejected.

        Returns one entry per candidate with the rules that fired, in the same
        order `feasible_candidates` applies them, plus the score used for
        ordering.  This is the diagnostic behind the GUI's robot inspector: a
        stalled robot is almost always explained by one repeated reason.
        """
        current = robot.position
        rows: List[Dict[str, object]] = []
        for candidate in self.candidates(robot):
            reasons: List[str] = []
            if not self.warehouse.graph.contains(candidate):
                reasons.append("off-graph")
            if self.violates_kinematics(robot, current, candidate):
                reasons.append("kinematics")
            if self.aisles.violates_aisle_direction(
                robot, current, candidate, timestep
            ):
                reasons.append("aisle-direction")
            if self.aisles.violates_aisle_reservation(
                robot, current, candidate, timestep
            ):
                reasons.append("no-reservation")
            # A robot's own reservation is not a conflict with itself; without
            # this the cell it actually moved into looks blocked afterwards.
            if candidate != robot.next_position and self.creates_vertex_conflict(
                robot, candidate
            ):
                reasons.append("vertex-conflict")
            occupant = self.index.robot_at(candidate)
            rows.append(
                {
                    "vertex": list(candidate),
                    "reasons": reasons,
                    "legal": not reasons,
                    "score": round(self.scorer.score(robot, candidate), 4),
                    "occupied_by": None
                    if occupant is None or occupant.id == robot.id
                    else occupant.id,
                    "chosen": robot.next_position == candidate,
                }
            )
        rows.sort(key=lambda row: (not row["legal"], -float(row["score"])))
        return rows

    # ------------------------------------------------------- legality rules
    def violates_kinematics(
        self, robot: Robot, current: Vertex, candidate: Vertex
    ) -> bool:
        """Reverse motion is normally allowed but can be forbidden by recovery."""
        if candidate == current:
            return False
        if robot.allow_reverse_until >= 0:
            return False
        return False

    def creates_vertex_conflict(self, robot: Robot, candidate: Vertex) -> bool:
        """Spec 26.1 - implemented in O(1) via the reservation set."""
        if candidate in self.reserved_vertices:
            return True
        occupant = self.index.robot_at(candidate)
        if occupant is not None and occupant.id != robot.id:
            if occupant.next_position is not None and occupant.next_position == candidate:
                return True
        return False

    @staticmethod
    def creates_swap_conflict(
        robot: Robot, parent: Optional[Robot], candidate: Vertex
    ) -> bool:
        """Spec 26.2."""
        if parent is None:
            return False
        return candidate == parent.position and parent.next_position == robot.position

    def candidate_is_available(self, robot: Robot, candidate: Vertex) -> bool:
        """Spec 26.3."""
        occupant = self.index.robot_at(candidate)
        if occupant is None or occupant.id == robot.id:
            return True
        if occupant.next_position is None:
            return False
        return occupant.next_position != candidate

    # ------------------------------------------------------------ recursion
    def pibt(
        self, robot: Robot, parent: Optional[Robot], timestep: int
    ) -> PIBTResult:
        """Spec section 25."""
        self.recursive_calls += 1
        feasible = self.feasible_candidates(robot, parent, timestep)

        for candidate in feasible:
            if candidate in self.reserved_vertices:
                continue

            robot.next_position = candidate
            self.reserved_vertices.add(candidate)

            occupant = self.index.robot_at(candidate)

            if occupant is None or occupant.id == robot.id:
                return PIBTResult.VALID

            if occupant.next_position is not None:
                if occupant.next_position != candidate:
                    return PIBTResult.VALID
                self.reserved_vertices.discard(candidate)
                robot.next_position = None
                continue

            # Priority inheritance: ask the occupant to move.
            robot.waiting_for_robot = occupant
            result = self.pibt(occupant, robot, timestep)
            if result is PIBTResult.VALID:
                return PIBTResult.VALID

            # Backtracking.
            self.backtracks += 1
            self.reserved_vertices.discard(candidate)
            robot.next_position = None

        # No candidate succeeded: stay in place.
        robot.next_position = robot.position
        self.reserved_vertices.add(robot.position)
        return PIBTResult.INVALID

    # ---------------------------------------------------------- statistics
    def stats(self) -> Dict[str, int]:
        return {
            "pibt_recursive_calls": self.recursive_calls,
            "pibt_backtracks": self.backtracks,
            "pibt_invalid_results": self.invalid_results,
            "candidate_evaluations": self.scorer.evaluations,
        }


__all__ = ["PIBTPlanner"]
