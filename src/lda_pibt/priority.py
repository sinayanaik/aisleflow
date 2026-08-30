"""Task-aware, fairness-preserving priority function (spec section 21)."""

from __future__ import annotations

from typing import List

from .config import Params
from .robot import Robot
from .types import RobotState, TaskStatus


def compute_task_urgency(robot: Robot, timestep: int, params: Params) -> float:
    if robot.task is None:
        return 0.0
    return params.urgency_weight * robot.task.urgency(timestep)


def task_class_priority(robot: Robot, params: Params) -> float:
    r"""P_emergency > P_loaded > P_pickup > P_repositioning > P_free."""
    if robot.state is RobotState.RECOVERY:
        return params.priority_emergency
    if robot.task is None:
        if robot.state is RobotState.PARKED:
            return params.priority_repositioning
        return params.priority_free
    if robot.task.status is TaskStatus.TO_DELIVERY:
        return params.priority_loaded
    if robot.task.status is TaskStatus.TO_PICKUP:
        return params.priority_pickup
    return params.priority_free


def compute_priority(robot: Robot, timestep: int, params: Params) -> float:
    r"""p_i(t) = P_class + k_w W_i + k_b B_i + k_u U_i + k_e E_i + eps_i."""
    aisle_priority = (
        params.priority_inside_aisle if robot.current_aisle is not None else 0.0
    )
    return (
        task_class_priority(robot, params)
        + compute_task_urgency(robot, timestep, params)
        + aisle_priority
        + params.waiting_weight * robot.waiting_time
        + params.blocked_weight * robot.blocked_time
        + robot.tie_breaker
    )


def fairness_horizon(params: Params) -> float:
    r"""Waiting steps needed for k_w W_i to exceed P_max - P_min (spec 21.1)."""
    spread = params.priority_emergency - params.priority_free
    if params.waiting_weight <= 0:
        return float("inf")
    return spread / params.waiting_weight


def order_by_priority(robots: List[Robot]) -> List[Robot]:
    """Descending priority; the unique tie-breaker keeps this deterministic."""
    return sorted(robots, key=lambda r: (-r.priority, r.id))


__all__ = [
    "compute_priority",
    "compute_task_urgency",
    "task_class_priority",
    "order_by_priority",
    "fairness_horizon",
]
