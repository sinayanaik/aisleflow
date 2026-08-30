"""Warehouse graph: 4-connected grid, distance maps, structural features.

Implements spec sections 9.1 (distance maps) and 9.3 (graph features).
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .types import INF, Vertex

_DELTAS = ((-1, 0), (1, 0), (0, -1), (0, 1))


class GridGraph:
    """4-connected grid graph over the passable cells of a warehouse map."""

    def __init__(self, passable: Sequence[Sequence[bool]]):
        self.height = len(passable)
        self.width = len(passable[0]) if self.height else 0
        self.passable = [list(row) for row in passable]

        self.vertices: List[Vertex] = [
            (r, c)
            for r in range(self.height)
            for c in range(self.width)
            if self.passable[r][c]
        ]
        self.vertex_set: Set[Vertex] = set(self.vertices)
        self._neighbors: Dict[Vertex, Tuple[Vertex, ...]] = {
            v: tuple(self._compute_neighbors(v)) for v in self.vertices
        }
        self._distance_maps: Dict[Vertex, Dict[Vertex, float]] = {}

        # Structural features (spec 9.3), computed lazily on first access.
        self._articulation: Optional[Set[Vertex]] = None
        self._bridges: Optional[Set[Tuple[Vertex, Vertex]]] = None

    # ------------------------------------------------------------------ basic
    def _compute_neighbors(self, v: Vertex) -> Iterable[Vertex]:
        r, c = v
        for dr, dc in _DELTAS:
            n = (r + dr, c + dc)
            if n in self.vertex_set or (
                0 <= n[0] < self.height
                and 0 <= n[1] < self.width
                and self.passable[n[0]][n[1]]
            ):
                yield n

    def neighbors(self, v: Vertex) -> Tuple[Vertex, ...]:
        return self._neighbors.get(v, ())

    def reverse_neighbors(self, v: Vertex) -> Tuple[Vertex, ...]:
        # The grid is undirected, so in/out neighbourhoods coincide.
        return self.neighbors(v)

    def degree(self, v: Vertex) -> int:
        return len(self._neighbors.get(v, ()))

    def contains(self, v: Vertex) -> bool:
        return v in self.vertex_set

    def __len__(self) -> int:
        return len(self.vertices)

    @property
    def max_degree(self) -> int:
        return max((len(n) for n in self._neighbors.values()), default=0)

    # --------------------------------------------------------- distance maps
    def compute_bfs_distance_map(self, goal: Vertex) -> Dict[Vertex, float]:
        """BFS distance map D_g(v) = d_route(v, g) (spec 9.1)."""
        if goal not in self.vertex_set:
            raise ValueError(f"goal {goal} is not a passable vertex")
        distance: Dict[Vertex, float] = {v: INF for v in self.vertices}
        distance[goal] = 0.0
        queue: deque[Vertex] = deque([goal])
        while queue:
            current = queue.popleft()
            for neighbor in self.reverse_neighbors(current):
                if distance[neighbor] == INF:
                    distance[neighbor] = distance[current] + 1.0
                    queue.append(neighbor)
        return distance

    def distance_map(self, goal: Vertex) -> Dict[Vertex, float]:
        """Cached distance map. Computed on demand, reused afterwards."""
        cached = self._distance_maps.get(goal)
        if cached is None:
            cached = self.compute_bfs_distance_map(goal)
            self._distance_maps[goal] = cached
        return cached

    def precompute_distance_maps(self, goals: Iterable[Vertex]) -> None:
        for goal in goals:
            self.distance_map(goal)

    def route_distance(self, source: Vertex, goal: Vertex) -> float:
        return self.distance_map(goal).get(source, INF)

    def shortest_route(
        self, source: Vertex, goal: Vertex, horizon: Optional[int] = None
    ) -> List[Vertex]:
        """Greedy descent on the distance map -> one shortest path.

        Deterministic: ties broken by the fixed neighbour ordering.
        """
        dmap = self.distance_map(goal)
        if dmap.get(source, INF) == INF:
            return [source]
        route = [source]
        current = source
        steps = 0
        while current != goal:
            if horizon is not None and steps >= horizon:
                break
            best = None
            best_d = dmap[current]
            for n in self.neighbors(current):
                d = dmap.get(n, INF)
                if d < best_d:
                    best_d = d
                    best = n
            if best is None:
                break
            route.append(best)
            current = best
            steps += 1
        return route

    # ---------------------------------------------------- structural features
    def _compute_articulation_and_bridges(self) -> None:
        """Iterative Hopcroft-Tarjan lowlink on the undirected grid."""
        articulation: Set[Vertex] = set()
        bridges: Set[Tuple[Vertex, Vertex]] = set()
        disc: Dict[Vertex, int] = {}
        low: Dict[Vertex, int] = {}
        parent: Dict[Vertex, Optional[Vertex]] = {}
        timer = 0

        for root in self.vertices:
            if root in disc:
                continue
            parent[root] = None
            stack: List[Tuple[Vertex, int]] = [(root, 0)]
            disc[root] = low[root] = timer
            timer += 1
            root_children = 0
            while stack:
                node, index = stack[-1]
                nbrs = self.neighbors(node)
                if index < len(nbrs):
                    stack[-1] = (node, index + 1)
                    nxt = nbrs[index]
                    if nxt == parent.get(node):
                        continue
                    if nxt in disc:
                        low[node] = min(low[node], disc[nxt])
                    else:
                        parent[nxt] = node
                        disc[nxt] = low[nxt] = timer
                        timer += 1
                        stack.append((nxt, 0))
                        if node == root:
                            root_children += 1
                else:
                    stack.pop()
                    par = parent.get(node)
                    if par is not None:
                        low[par] = min(low[par], low[node])
                        if low[node] > disc[par]:
                            bridges.add(tuple(sorted((par, node))))  # type: ignore[arg-type]
                        if par != root and low[node] >= disc[par]:
                            articulation.add(par)
            if root_children > 1:
                articulation.add(root)

        self._articulation = articulation
        self._bridges = bridges

    @property
    def articulation_points(self) -> Set[Vertex]:
        if self._articulation is None:
            self._compute_articulation_and_bridges()
        assert self._articulation is not None
        return self._articulation

    @property
    def bridges(self) -> Set[Tuple[Vertex, Vertex]]:
        if self._bridges is None:
            self._compute_articulation_and_bridges()
        assert self._bridges is not None
        return self._bridges

    @property
    def dead_ends(self) -> Set[Vertex]:
        return {v for v in self.vertices if self.degree(v) <= 1}

    @property
    def intersections(self) -> Set[Vertex]:
        return {v for v in self.vertices if self.degree(v) >= 3}

    def bfs_ball(self, center: Vertex, radius: int) -> Set[Vertex]:
        """All vertices within `radius` graph steps of `center`."""
        seen = {center}
        frontier = [center]
        for _ in range(radius):
            nxt: List[Vertex] = []
            for v in frontier:
                for n in self.neighbors(v):
                    if n not in seen:
                        seen.add(n)
                        nxt.append(n)
            frontier = nxt
            if not frontier:
                break
        return seen

    def satisfies_pibt_reachability(self) -> bool:
        """Rough check of PIBT's sufficient condition (spec 9.3).

        PIBT's reachability guarantee assumes every pair of adjacent vertices
        lies on a simple cycle; a graph with bridges or dead ends violates it.
        """
        return not self.dead_ends and not self.bridges


__all__ = ["GridGraph"]
