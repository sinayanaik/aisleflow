"""Core enumerations and small value types (spec sections 7, 10, 14)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional, Tuple

Vertex = Tuple[int, int]  # (row, col) grid cell
INF = float("inf")


class Compass(Enum):
    """Grid movement direction, used for turning cost (spec 14, 23)."""

    NORTH = (-1, 0)
    SOUTH = (1, 0)
    EAST = (0, 1)
    WEST = (0, -1)
    STAY = (0, 0)

    @property
    def delta(self) -> Tuple[int, int]:
        return self.value

    def opposite(self) -> "Compass":
        dr, dc = self.value
        return Compass((-dr, -dc))


def movement_direction(u: Vertex, v: Vertex) -> Compass:
    """Compass direction of the movement u -> v. STAY when u == v."""
    return Compass((v[0] - u[0], v[1] - u[1]))


class RobotState(Enum):
    """Spec section 7.1."""

    FREE = "FREE"
    TO_PICKUP = "TO_PICKUP"
    TO_DELIVERY = "TO_DELIVERY"
    PARKED = "PARKED"
    RECOVERY = "RECOVERY"


class TaskStatus(Enum):
    """Spec section 7.2."""

    UNASSIGNED = "UNASSIGNED"
    TO_PICKUP = "TO_PICKUP"
    TO_DELIVERY = "TO_DELIVERY"
    COMPLETED = "COMPLETED"


class ProximityMode(Enum):
    """Spec section 13."""

    TRANSIT = "TRANSIT"
    APPROACH = "APPROACH"
    ARRIVAL = "ARRIVAL"


class PIBTResult(Enum):
    VALID = "VALID"
    INVALID = "INVALID"


class PlanningError(RuntimeError):
    """Raised when the executed joint move is not collision free (spec 31)."""


__all__ = [
    "Vertex",
    "INF",
    "Compass",
    "movement_direction",
    "RobotState",
    "TaskStatus",
    "ProximityMode",
    "PIBTResult",
    "PlanningError",
    "Optional",
]
