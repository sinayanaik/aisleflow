"""Joint-plan validation and synchronized execution (spec sections 26, 31)."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .robot import Robot
from .types import PlanningError, Vertex, movement_direction


def contains_vertex_conflict(robots: Sequence[Robot]) -> Optional[Tuple[int, int]]:
    """Return the ids of two robots sharing a next vertex, if any."""
    seen: Dict[Vertex, int] = {}
    for robot in robots:
        target = robot.next_position
        if target is None:
            continue
        if target in seen:
            return (seen[target], robot.id)
        seen[target] = robot.id
    return None


def contains_swap_conflict(robots: Sequence[Robot]) -> Optional[Tuple[int, int]]:
    """Return the ids of two robots exchanging positions, if any."""
    by_position: Dict[Vertex, Robot] = {r.position: r for r in robots}
    for robot in robots:
        target = robot.next_position
        if target is None or target == robot.position:
            continue
        other = by_position.get(target)
        if other is not None and other.next_position == robot.position:
            return (robot.id, other.id)
    return None


def no_vertex_conflicts(robots: Sequence[Robot]) -> bool:
    return contains_vertex_conflict(robots) is None


def no_swap_conflicts(robots: Sequence[Robot]) -> bool:
    return contains_swap_conflict(robots) is None


def validate_plan(robots: Sequence[Robot]) -> None:
    """Raise `PlanningError` when the joint move is not collision free."""
    clash = contains_vertex_conflict(robots)
    if clash is not None:
        raise PlanningError(f"Vertex conflict detected between robots {clash}")
    swap = contains_swap_conflict(robots)
    if swap is not None:
        raise PlanningError(f"Swap conflict detected between robots {swap}")


def execute_moves(robots: Sequence[Robot], validate: bool = True) -> int:
    """Apply the planned moves atomically (spec 31). Returns steps travelled."""
    if validate:
        validate_plan(robots)
    moved = 0
    for robot in robots:
        target = robot.next_position if robot.next_position is not None else robot.position
        if target != robot.position:
            robot.previous_direction = movement_direction(robot.position, target)
            robot.orientation = robot.previous_direction
            robot.travel_distance += 1
            moved += 1
        robot.previous_position = robot.position
        robot.position = target
        robot.position_history.append(target)
    return moved


__all__ = [
    "contains_vertex_conflict",
    "contains_swap_conflict",
    "no_vertex_conflicts",
    "no_swap_conflicts",
    "validate_plan",
    "execute_moves",
]
