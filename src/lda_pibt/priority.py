"""Who moves first: the priority function.

PIBT resolves conflicts by rank -- when two robots want the same cell, the
higher-priority one takes it and the other is pushed aside.  This module
decides that rank.

    priority = class_rank * priority_class_spread   # what the robot is doing
             + priority_inside_aisle (if inside)    # clear the narrow parts
             + waiting_weight * waiting_time        # fairness
             + tie_breaker                          # determinism

The classes used to be five hand-set constants (400, 300, 200, 100, 0). Only
their *order* ever mattered, so they are one rank times one spread now. The
urgency and blocked-time terms are gone: the sensitivity study found every run
bit-identical without them -- urgency because tasks in this simulator carry no
deadline, so `Task.urgency` is always 0, and blocked time because it is reset
the moment a robot moves and never grew enough to reorder anything.
"""

from __future__ import annotations

from typing import List

from .config import Params
from .robot import Robot
from .types import RobotState, TaskStatus


#: Rank per task class, highest first. Multiplied by `priority_class_spread`.
#: A robot in recovery outranks everything; a loaded robot outranks one still
#: on its way to a pickup, because dropping a carried task wastes the trip
#: already made.
CLASS_RANK = {
    "recovery": 4,
    "loaded": 3,
    "pickup": 2,
    "repositioning": 1,
    "free": 0,
}


def task_class(robot: Robot) -> str:
    """Which of the five classes this robot is in right now."""
    if robot.state is RobotState.RECOVERY:
        return "recovery"
    if robot.task is None:
        return "repositioning" if robot.state is RobotState.PARKED else "free"
    if robot.task.status is TaskStatus.TO_DELIVERY:
        return "loaded"
    if robot.task.status is TaskStatus.TO_PICKUP:
        return "pickup"
    return "free"


def task_class_priority(robot: Robot, params: Params) -> float:
    """The class term on its own."""
    return CLASS_RANK[task_class(robot)] * params.priority_class_spread


def compute_priority(robot: Robot, timestep: int, params: Params) -> float:
    """The robot's rank this timestep; higher goes first."""
    aisle_priority = (
        params.priority_inside_aisle if robot.current_aisle is not None else 0.0
    )
    return (
        task_class_priority(robot, params)
        + aisle_priority
        + params.waiting_weight * robot.waiting_time
        + robot.tie_breaker
    )


def fairness_horizon(params: Params) -> float:
    """How long a robot must wait before it outranks any class above it.

    This is the guarantee that nothing starves: waiting buys rank at a fixed
    rate, so a free robot that has been stuck this many steps outranks a robot
    in recovery that just arrived.
    """
    spread = CLASS_RANK["recovery"] * params.priority_class_spread
    if params.waiting_weight <= 0:
        return float("inf")
    return spread / params.waiting_weight


def order_by_priority(robots: List[Robot]) -> List[Robot]:
    """Descending priority; the unique tie-breaker keeps this deterministic."""
    return sorted(robots, key=lambda r: (-r.priority, r.id))


__all__ = [
    "CLASS_RANK",
    "compute_priority",
    "task_class",
    "task_class_priority",
    "order_by_priority",
    "fairness_horizon",
]
