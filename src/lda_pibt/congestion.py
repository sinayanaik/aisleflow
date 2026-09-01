"""Occupancy bookkeeping and congestion estimates.

Implements spec section 23.1 (congestion terms) with the O(1) data structures
recommended in spec section 32: a vertex-occupancy hash map, aisle occupancy
counters, entrance-queue counters and a spatial hash for local robot density.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, Optional, Tuple

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

        Aisle load is already a ratio; a raw robot count mixed with it would
        dominate the mean and let crowding reach the scale of the progress
        reward, inverting the intended ordering of the score terms.
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
    """How crowded a cell is, as one number in [0, 1].

    Two signals, averaged:

    * **local** -- what fraction of the cells within `local_congestion_radius`
      hold a robot;
    * **aisle** -- how full the candidate's aisle is, against its capacity.

    Both are already fractions, so the mean is one too, and `crowding_penalty`
    can be read directly as "how many points of score a completely jammed cell
    costs". There used to be a third signal (mean occupancy of the next few
    route cells) and three weights to mix them: the sensitivity study found
    that changing the third signal's horizon -- including switching it off
    entirely -- left every run bit-identical, because at a tenth of the
    progress reward it never once broke a tie. See `docs/04-parameters.md`.
    """

    def __init__(self, warehouse: Warehouse, index: OccupancyIndex, params: Params):
        self.warehouse = warehouse
        self.index = index
        self.params = params

    def begin_timestep(self) -> None:
        """Kept for the simulator's call order; nothing is cached per step now."""

    def crowding(self, robot: Robot, candidate: Vertex) -> float:
        """Mean of the local and aisle crowding fractions, in [0, 1]."""
        if not self.params.congestion_scoring:
            return 0.0
        local = self.index.local_occupancy_ratio(candidate, exclude=robot)
        aisle = self.index.aisle_load(self.warehouse.aisle_id(candidate))
        return 0.5 * (local + aisle)

    def route_congestion(self, source: Vertex, goal: Vertex) -> float:
        """Crowding along a whole route, for task assignment.

        Both halves are means -- per cell, and per aisle -- so a long route
        through empty aisles does not look busier than a short jammed one
        simply for being long.
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
