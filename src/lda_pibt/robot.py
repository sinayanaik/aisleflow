"""Robot state (spec section 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Deque, List, Optional

from collections import deque

from .task import Task
from .types import (
    AisleDirection,
    Compass,
    ProximityMode,
    RobotState,
    Vertex,
    INF,
)


@dataclass
class Robot:
    """One warehouse robot. Field names follow spec section 7."""

    id: int
    position: Vertex
    previous_position: Optional[Vertex] = None
    orientation: Compass = Compass.EAST
    previous_direction: Compass = Compass.STAY

    state: RobotState = RobotState.FREE
    task: Optional[Task] = None
    waypoint: Optional[Vertex] = None

    current_aisle: Optional[int] = None
    next_aisle: Optional[int] = None
    preferred_direction: Compass = Compass.STAY
    preferred_aisle_direction: AisleDirection = AisleDirection.NONE

    waiting_time: int = 0
    blocked_time: int = 0
    last_progress_time: int = 0

    priority: float = 0.0
    tie_breaker: float = 0.0
    next_position: Optional[Vertex] = None
    reserved_vertex: Optional[Vertex] = None

    # derived per-timestep state
    route: List[Vertex] = field(default_factory=list)
    #: The route the robot *would* take if no aisle had a direction. The aisle
    #: layer votes with this rather than with `route`, because direction-aware
    #: routing sends robots around aisles flowing the wrong way -- and an aisle
    #: that never sees the traffic it is turning away has no demand to flip
    #: for, so it would hold one direction forever.
    demand_route: List[Vertex] = field(default_factory=list)
    route_distance_to_waypoint: float = INF
    previous_route_distance: float = INF
    mode: ProximityMode = ProximityMode.TRANSIT
    direction_weight: float = 0.0
    aisle_bonus: float = 0.0
    waiting_for_robot: Optional["Robot"] = None
    parking_vertex: Optional[Vertex] = None
    recovery_vertex: Optional[Vertex] = None
    allow_reverse_until: int = -1
    ignore_direction_until: int = -1

    # statistics
    travel_distance: int = 0
    completed_tasks: int = 0
    recovery_events: int = 0
    no_progress_steps: int = 0
    position_history: Deque[Vertex] = field(default_factory=lambda: deque(maxlen=16))

    # ---------------------------------------------------------------- helpers
    def __post_init__(self) -> None:
        if self.previous_position is None:
            self.previous_position = self.position
        if self.waypoint is None:
            self.waypoint = self.position
        if self.tie_breaker == 0.0:
            # deterministic, unique, and small enough not to reorder classes
            self.tie_breaker = 1e-4 * self.id

    @property
    def is_idle(self) -> bool:
        return self.state in (RobotState.FREE, RobotState.PARKED)

    @property
    def is_loaded(self) -> bool:
        return self.state is RobotState.TO_DELIVERY

    def reset_step_state(self) -> None:
        """Clear one-step planning state (spec section 30, step 11)."""
        self.next_position = None
        self.reserved_vertex = None
        self.waiting_for_robot = None

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Robot) and other.id == self.id

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"Robot(id={self.id}, pos={self.position}, state={self.state.value}, "
            f"wp={self.waypoint}, aisle={self.current_aisle})"
        )


__all__ = ["Robot"]
