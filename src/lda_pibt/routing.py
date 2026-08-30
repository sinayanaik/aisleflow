"""Direction- and congestion-aware route planning (spec sections 2, 4, 14, 15).

The low-level PIBT layer only ever looks one step ahead, so the *route* is what
carries directional information into candidate scoring.  A robot that plans
straight through an aisle currently flowing the other way will sit at the
entrance until the aisle flips; biasing the route around it is what the
proposal means by "dynamic route and waypoint selection".
"""

from __future__ import annotations

import heapq
from typing import Dict, List, Optional

from .config import Params
from .types import INF, AisleDirection, AisleState, Vertex
from .warehouse import Warehouse


class Router:
    """Shortest route with soft penalties for opposing aisle direction."""

    def __init__(self, warehouse: Warehouse, params: Params):
        self.warehouse = warehouse
        self.params = params

    @property
    def direction_aware(self) -> bool:
        return (
            self.params.direction_aware_routing
            and self.params.direction_control == "aisle"
        )

    def edge_penalty(self, u: Vertex, v: Vertex) -> float:
        """Extra cost of traversing u -> v given the current aisle states."""
        aisle = self.warehouse.aisle_of(v)
        if aisle is None or aisle.state is AisleState.OPEN:
            return 0.0
        direction = self.warehouse.traversal_direction(u, v)
        if aisle.state is AisleState.DRAINING:
            return float(self.params.route_direction_penalty)
        if direction is aisle.current_direction:
            return 0.0
        return float(self.params.route_direction_penalty)

    def route(self, source: Vertex, goal: Vertex) -> List[Vertex]:
        """A route from `source` to `goal`; falls back to the plain BFS route."""
        graph = self.warehouse.graph
        if source == goal:
            return [source]
        if not self.direction_aware:
            return graph.shortest_route(source, goal)
        if graph.route_distance(source, goal) == INF:
            return [source]

        heuristic = graph.distance_map(goal)
        dist: Dict[Vertex, float] = {source: 0.0}
        parent: Dict[Vertex, Optional[Vertex]] = {source: None}
        heap: List = [(heuristic.get(source, 0.0), 0.0, source)]
        visited = set()

        while heap:
            _, cost, current = heapq.heappop(heap)
            if current in visited:
                continue
            visited.add(current)
            if current == goal:
                break
            for neighbor in graph.neighbors(current):
                if heuristic.get(neighbor, INF) == INF:
                    continue
                step = 1.0 + self.edge_penalty(current, neighbor)
                new_cost = cost + step
                if new_cost < dist.get(neighbor, INF):
                    dist[neighbor] = new_cost
                    parent[neighbor] = current
                    heapq.heappush(
                        heap,
                        (new_cost + heuristic.get(neighbor, 0.0), new_cost, neighbor),
                    )

        if goal not in parent:
            return graph.shortest_route(source, goal)
        path: List[Vertex] = []
        node: Optional[Vertex] = goal
        while node is not None:
            path.append(node)
            node = parent[node]
        path.reverse()
        return path


__all__ = ["Router"]
