"""Occupancy bookkeeping and congestion estimates.

Implements spec section 23.1 (congestion terms) with the O(1) data structures
recommended in spec section 32: a vertex-occupancy hash map, aisle occupancy
counters, entrance-queue counters and a spatial hash for local robot density.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from .config import Params
from .robot import Robot
from .types import Vertex
from .warehouse import Warehouse


class OccupancyIndex:
    """Vertex / aisle / spatial-bucket occupancy maintained incrementally."""

    def __init__(self, warehouse: Warehouse, params: Params):
        self.warehouse = warehouse
        self.params = params
        self.cell = max(1, params.local_congestion_radius)
        self.vertex_occupancy: Dict[Vertex, Robot] = {}
        self.aisle_occupancy: Dict[int, int] = defaultdict(int)
        self.bucket_counts: Dict[Tuple[int, int], int] = defaultdict(int)

    # -------------------------------------------------------------- rebuild
    def rebuild(self, robots: Iterable[Robot]) -> None:
        self.vertex_occupancy.clear()
        self.aisle_occupancy.clear()
        self.bucket_counts.clear()
        for robot in robots:
            self.vertex_occupancy[robot.position] = robot
            aisle_id = self.warehouse.aisle_id(robot.position)
            if aisle_id is not None:
                self.aisle_occupancy[aisle_id] += 1
            self.bucket_counts[self._bucket(robot.position)] += 1
        for aisle_id, aisle in self.warehouse.aisles.items():
            aisle.occupancy = self.aisle_occupancy.get(aisle_id, 0)
            aisle.congestion_cost = aisle.occupancy / max(1, aisle.capacity)

    def _bucket(self, v: Vertex) -> Tuple[int, int]:
        return (v[0] // self.cell, v[1] // self.cell)

    # --------------------------------------------------------------- queries
    def robot_at(self, v: Vertex) -> Optional[Robot]:
        return self.vertex_occupancy.get(v)

    def is_occupied(self, v: Vertex) -> bool:
        return v in self.vertex_occupancy

    def aisle_load(self, aisle_id: Optional[int]) -> float:
        if aisle_id is None:
            return 0.0
        aisle = self.warehouse.get_aisle(aisle_id)
        if aisle is None:
            return 0.0
        return self.aisle_occupancy.get(aisle_id, 0) / max(1, aisle.capacity)

    def local_density(self, v: Vertex, exclude: Optional[Robot] = None) -> int:
        """Approximate |{j : d(v, x_j) <= R_local}| via the spatial hash."""
        radius = self.params.local_congestion_radius
        br, bc = self._bucket(v)
        total = 0
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                total += self.bucket_counts.get((br + dr, bc + dc), 0)
        if total == 0:
            return 0
        # Refine within the 3x3 bucket window using Manhattan distance.
        count = 0
        for dr in range(-radius, radius + 1):
            span = radius - abs(dr)
            for dc in range(-span, span + 1):
                u = (v[0] + dr, v[1] + dc)
                other = self.vertex_occupancy.get(u)
                if other is not None and (exclude is None or other.id != exclude.id):
                    count += 1
        return count

    def local_occupancy_ratio(
        self, v: Vertex, exclude: Optional[Robot] = None
    ) -> float:
        """`local_density` as a fraction of the passable cells in range.

        `C_aisle` and `C_downstream` are already ratios in [0, 1]; mixing a raw
        count with them makes `C_local` the whole mixture and lets `mu * C`
        reach the scale of `alpha * Delta`, which inverts the intended ordering
        of the score terms.  Dividing by the reachable cell count puts all
        three terms on one scale.
        """
        radius = self.params.local_congestion_radius
        cells = 0
        for dr in range(-radius, radius + 1):
            span = radius - abs(dr)
            for dc in range(-span, span + 1):
                if self.warehouse.graph.contains((v[0] + dr, v[1] + dc)):
                    cells += 1
        if cells == 0:
            return 0.0
        return self.local_density(v, exclude=exclude) / cells


class CongestionModel:
    """Computes C_i(v) = w1*C_local + w2*C_aisle + w3*C_downstream (spec 23.1)."""

    def __init__(self, warehouse: Warehouse, index: OccupancyIndex, params: Params):
        self.warehouse = warehouse
        self.index = index
        self.params = params
        self._downstream_cache: Dict[Tuple[Vertex, Vertex], float] = {}

    def begin_timestep(self) -> None:
        self._downstream_cache.clear()

    def downstream(self, v: Vertex, waypoint: Optional[Vertex]) -> float:
        """Mean occupancy of the next K route vertices beyond `v` (spec 23.1)."""
        if waypoint is None:
            return 0.0
        key = (v, waypoint)
        cached = self._downstream_cache.get(key)
        if cached is not None:
            return cached
        horizon = self.params.downstream_horizon
        route = self.warehouse.graph.shortest_route(v, waypoint, horizon=horizon)
        tail = route[1:]
        if not tail:
            value = 0.0
        else:
            value = sum(1.0 for u in tail if self.index.is_occupied(u)) / len(tail)
        self._downstream_cache[key] = value
        return value

    def congestion(self, robot: Robot, candidate: Vertex) -> float:
        r"""C_i(v) = w1 C_local + w2 C_aisle + w3 C_downstream (spec 23.1).

        With `congestion_normalisation` on, `C_local` is an occupancy ratio and
        the weights are normalised to sum to 1, so `C_i(v)` lies in [0, 1] and
        `mu * C` cannot outrank `alpha * Delta`.
        """
        if not self.params.congestion_scoring:
            return 0.0
        p = self.params
        aisle = self.index.aisle_load(self.warehouse.aisle_id(candidate))
        down = self.downstream(candidate, robot.waypoint)
        if not p.congestion_normalisation:
            local = float(self.index.local_density(candidate, exclude=robot))
            return (
                p.omega_local * local
                + p.omega_aisle * aisle
                + p.omega_downstream * down
            )
        local = self.index.local_occupancy_ratio(candidate, exclude=robot)
        weight = p.omega_local + p.omega_aisle + p.omega_downstream
        if weight <= 0.0:
            return 0.0
        return (
            p.omega_local * local
            + p.omega_aisle * aisle
            + p.omega_downstream * down
        ) / weight

    def route_congestion(self, source: Vertex, goal: Vertex) -> float:
        """Congestion estimate along a whole route, for task assignment (19).

        Both halves are per-cell / per-aisle means, so the result stays in
        roughly [0, 2] whatever the route length -- a long route through empty
        aisles must not look more congested than a short jammed one.
        """
        route = self.warehouse.graph.shortest_route(source, goal)
        if len(route) <= 1:
            return 0.0
        occupied = sum(1 for v in route if self.index.is_occupied(v))
        aisle_ids = {
            self.warehouse.aisle_id(v)
            for v in route
            if self.warehouse.aisle_id(v) is not None
        }
        aisle_load = sum(self.index.aisle_load(a) for a in aisle_ids)
        return occupied / len(route) + aisle_load / max(1, len(aisle_ids))


__all__ = ["OccupancyIndex", "CongestionModel"]
