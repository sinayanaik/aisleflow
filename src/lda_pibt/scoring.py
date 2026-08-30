"""Candidate scoring: proximity modes, direction weights, turning cost.

Implements spec sections 13 (proximity), 14 (preferred route direction),
23 (proximity-dependent candidate score) and 24 (scoring pseudocode).
"""

from __future__ import annotations

from typing import Optional

from .config import Params
from .congestion import CongestionModel
from .robot import Robot
from .types import INF, Compass, ProximityMode, Vertex, movement_direction
from .warehouse import Warehouse


def compute_proximity_mode(route_distance: float, params: Params) -> ProximityMode:
    """Spec 13.1-13.3."""
    if route_distance > params.r_far:
        return ProximityMode.TRANSIT
    if route_distance > params.r_near:
        return ProximityMode.APPROACH
    return ProximityMode.ARRIVAL


def compute_direction_weight(route_distance: float, params: Params) -> float:
    r"""Smooth direction weight (spec 13.4).

    .. math::
        \beta_i = \beta_{\max}\min\left(1,
            \frac{\max(0, r_i - R_{near})}{\max(1, R_{far} - R_{near})}\right)
    """
    if params.direction_control == "none":
        return 0.0
    if route_distance == INF:
        return params.beta_strong
    numerator = max(0.0, route_distance - params.r_near)
    denominator = max(1.0, float(params.r_far - params.r_near))
    return params.beta_strong * min(1.0, numerator / denominator)


def compute_aisle_weight(mode: ProximityMode, params: Params) -> float:
    """Aisle-continuity weight gamma_i, weakened near the waypoint (spec 23)."""
    if params.direction_control == "none":
        return 0.0
    if mode is ProximityMode.TRANSIT:
        return params.gamma_strong
    if mode is ProximityMode.APPROACH:
        return 0.5 * (params.gamma_strong + params.gamma_weak)
    return params.gamma_weak


def turning_cost(
    previous: Compass, movement: Compass, params: Params
) -> float:
    r"""Spec 23.

    .. math::
        P^{turn}_i(v) = 0 \text{ (straight)}, 1 \text{ (turn)},
        \eta_{reverse} \text{ (reversal)}
    """
    if not params.turning_cost:
        return 0.0
    if movement is Compass.STAY or previous is Compass.STAY:
        return 0.0
    if movement is previous:
        return 0.0
    if movement is previous.opposite():
        return params.lambda_reverse
    return 1.0


def wait_penalty(robot: Robot, candidate: Vertex) -> float:
    """P^wait: waiting in place is only free when already at the waypoint."""
    if candidate != robot.position:
        return 0.0
    if robot.waypoint is not None and robot.position == robot.waypoint:
        return 0.0
    return 1.0


class CandidateScorer:
    """Ranks legal PIBT candidates (spec section 24)."""

    def __init__(
        self,
        warehouse: Warehouse,
        congestion: CongestionModel,
        params: Params,
    ):
        self.warehouse = warehouse
        self.congestion = congestion
        self.params = params
        self.evaluations = 0

    def progress(self, robot: Robot, candidate: Vertex) -> float:
        r"""Normalised route progress (spec 23)."""
        waypoint = robot.waypoint
        if waypoint is None:
            return 0.0
        graph = self.warehouse.graph
        current_distance = graph.route_distance(robot.position, waypoint)
        candidate_distance = graph.route_distance(candidate, waypoint)
        if current_distance == INF or candidate_distance == INF:
            return -1.0 if candidate_distance == INF else 0.0
        delta = current_distance - candidate_distance
        if self.params.progress_normalization == "route":
            # Spec 23 verbatim: normalised by the remaining route length.
            return delta / max(1.0, current_distance)
        # Default: per-step progress in {-1, 0, +1}, which is what makes the
        # recommended relation alpha > beta_strong > lambda_turn meaningful.
        return delta

    def score(self, robot: Robot, candidate: Vertex) -> float:
        """Complete candidate score S_i(v) (spec 23.2 / 24)."""
        self.evaluations += 1
        p = self.params
        wh = self.warehouse

        progress = self.progress(robot, candidate)

        movement = movement_direction(robot.position, candidate)
        direction_match = (
            1.0
            if (
                p.direction_control != "none"
                and movement is not Compass.STAY
                and movement is robot.preferred_direction
            )
            else 0.0
        )

        same_aisle = (
            1.0 if wh.aisle_id(candidate) == robot.current_aisle
            and robot.current_aisle is not None
            else 0.0
        )
        if wh.is_intersection(robot.position):
            same_aisle = 0.0

        turn_penalty = turning_cost(robot.previous_direction, movement, p)
        congestion = self.congestion.congestion(robot, candidate)
        wait = wait_penalty(robot, candidate)
        bottleneck = 1.0 if wh.is_bottleneck(candidate) else 0.0

        return (
            p.alpha_progress * progress
            + robot.direction_weight * direction_match
            + robot.aisle_weight * same_aisle
            - p.lambda_turn * turn_penalty
            - p.mu_congestion * congestion
            - p.nu_wait * wait
            - p.xi_bottleneck * bottleneck
        )

    def sort_key(self, robot: Robot, candidate: Vertex):
        """Deterministic ordering key: score desc, then vertex asc."""
        return (-self.score(robot, candidate), candidate)


def compute_route_direction(
    warehouse: Warehouse, robot: Robot
) -> Compass:
    """Preferred compass direction = orientation of the first route edge (14)."""
    route = robot.route
    if len(route) >= 2:
        return movement_direction(route[0], route[1])
    waypoint = robot.waypoint
    if waypoint is None or waypoint == robot.position:
        return Compass.STAY
    graph = warehouse.graph
    best: Optional[Vertex] = None
    best_delta = 0.0
    base = graph.route_distance(robot.position, waypoint)
    for n in graph.neighbors(robot.position):
        delta = base - graph.route_distance(n, waypoint)
        if best is None or delta > best_delta:
            best = n
            best_delta = delta
    if best is None:
        return Compass.STAY
    return movement_direction(robot.position, best)


def apply_direction_hysteresis(
    robot: Robot, proposed: Compass, timestep: int, params: Params
) -> Compass:
    """Keep the previous preference unless the robot is at a decision point.

    Spec 15/16: a robot retains its route request until its waypoint changes,
    the route becomes invalid, it reaches an intersection, or it is blocked.
    """
    if not params.hysteresis:
        return proposed
    previous = robot.preferred_direction
    if previous is Compass.STAY or proposed is previous:
        return proposed
    if robot.blocked_time >= params.t_blocked:
        return proposed
    if robot.mode is not ProximityMode.TRANSIT:
        return proposed
    # Only re-commit to a new direction at an intersection.
    if robot.current_aisle is None:
        return proposed
    return previous


__all__ = [
    "CandidateScorer",
    "compute_proximity_mode",
    "compute_direction_weight",
    "compute_aisle_weight",
    "turning_cost",
    "wait_penalty",
    "compute_route_direction",
    "apply_direction_hysteresis",
]
