"""Candidate scoring: how a robot ranks the cells it could move into next.

Every timestep, each robot has at most five options -- its four neighbours and
staying put -- and this module puts a single number on each one.  The planner
(`pibt.py`) then tries them in descending order.  Scoring never decides
safety; it only decides preference.

The score has four terms::

    S(v) = progress_reward * progress          # did we get closer?
         + aisle_bonus     * same_aisle        # keep flowing down one aisle
         - turn_penalty    * turn              # corners are slow
         - crowding_penalty * crowding         # avoid the jam

`progress` is -1, 0 or +1, and `progress_reward` is 10, so candidates fall
into three tiers ten points apart and the other three terms only ever reorder
*within* a tier.  That is the whole design: getting closer is what matters,
and everything else is a tie-break.  It is also why this file is much shorter
than it used to be -- a term worth less than a tie-break is worth nothing, and
the sensitivity study (`experiments/run_sensitivity.py`,
`docs/data/sensitivity.json`) showed five of the old nine terms were exactly
that.  See `docs/04-parameters.md` for the measured cost of each.
"""

from __future__ import annotations

from typing import Optional

from .config import Params
from .congestion import CongestionModel
from .robot import Robot
from .types import INF, Compass, ProximityMode, Vertex, movement_direction
from .warehouse import Warehouse


def compute_proximity_mode(route_distance: float, params: Params) -> ProximityMode:
    """How close the robot is to its waypoint: TRANSIT, APPROACH or ARRIVAL.

    Only `compute_aisle_bonus` reads this. A robot far from its target should
    stay in its lane; one that has nearly arrived needs to be free to turn off.
    """
    if route_distance > params.r_far:
        return ProximityMode.TRANSIT
    if route_distance > params.r_near:
        return ProximityMode.APPROACH
    return ProximityMode.ARRIVAL


def compute_aisle_bonus(mode: ProximityMode, params: Params) -> float:
    """Reward for staying in the aisle the robot is already in.

    Full strength in TRANSIT, half at APPROACH, weakest at ARRIVAL, so a robot
    commits to a lane while travelling and is free to leave it near its target.
    """
    if params.direction_control == "none":
        return 0.0
    if mode is ProximityMode.TRANSIT:
        return params.aisle_bonus
    if mode is ProximityMode.APPROACH:
        return 0.5 * (params.aisle_bonus + params.aisle_bonus_near)
    return params.aisle_bonus_near


def turning_cost(previous: Compass, movement: Compass, params: Params) -> float:
    """0 to carry straight on, 1 to turn a corner, `reverse_multiplier` to reverse."""
    if not params.turning_cost:
        return 0.0
    if movement is Compass.STAY or previous is Compass.STAY:
        return 0.0
    if movement is previous:
        return 0.0
    if movement is previous.opposite():
        return params.reverse_multiplier
    return 1.0


class CandidateScorer:
    """Ranks the cells a robot could move into this timestep."""

    def __init__(
        self,
        warehouse: Warehouse,
        congestion: CongestionModel,
        params: Params,
    ):
        self.warehouse = warehouse
        self.congestion = congestion
        self.params = params
        self.timestep = 0
        self.evaluations = 0

    def progress(self, robot: Robot, candidate: Vertex) -> float:
        """-1, 0 or +1: does this move take the robot closer to its waypoint?

        Measured on route distance, not straight-line distance, so a wall
        between the robot and its target counts as the detour it really is.
        """
        waypoint = robot.waypoint
        if waypoint is None:
            return 0.0
        graph = self.warehouse.graph
        current_distance = graph.route_distance(robot.position, waypoint)
        candidate_distance = graph.route_distance(candidate, waypoint)
        if current_distance == INF or candidate_distance == INF:
            return -1.0 if candidate_distance == INF else 0.0
        return current_distance - candidate_distance

    def score(self, robot: Robot, candidate: Vertex) -> float:
        """The complete candidate score `S(v)`; higher is better."""
        self.evaluations += 1
        p = self.params
        wh = self.warehouse

        progress = self.progress(robot, candidate)

        same_aisle = (
            1.0 if wh.aisle_id(candidate) == robot.current_aisle
            and robot.current_aisle is not None
            else 0.0
        )
        # At an intersection there is no aisle to continue along, so the bonus
        # would just favour an arbitrary one of the branches.
        if wh.is_intersection(robot.position):
            same_aisle = 0.0

        movement = movement_direction(robot.position, candidate)
        turn_penalty = turning_cost(robot.previous_direction, movement, p)
        crowding = self.congestion.crowding(robot, candidate)

        return (
            p.progress_reward * progress
            + robot.aisle_bonus * same_aisle
            - p.turn_penalty * turn_penalty
            - p.crowding_penalty * crowding
        )

    def sort_key(self, robot: Robot, candidate: Vertex):
        """Deterministic ordering key: score descending, then vertex ascending."""
        return (-self.score(robot, candidate), candidate)


def compute_route_direction(warehouse: Warehouse, robot: Robot) -> Compass:
    """Which way the robot's route leaves its current cell.

    Read by the aisle layer to work out which direction a robot is asking an
    aisle to flow; it no longer feeds the movement score.
    """
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
    """Keep the previous heading unless the robot is at a real decision point.

    Without this a robot equidistant by two routes flips between them every
    step and never commits to either.
    """
    if not params.hysteresis:
        return proposed
    previous = robot.preferred_direction
    if previous is Compass.STAY or proposed is previous:
        return proposed
    if robot.blocked_time >= params.stall_steps:
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
    "compute_aisle_bonus",
    "turning_cost",
    "compute_route_direction",
    "apply_direction_hysteresis",
]
