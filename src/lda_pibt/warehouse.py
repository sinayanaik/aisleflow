"""Warehouse metadata: map parsing, vertex annotation, aisle segmentation.

Implements spec section 9.2 (warehouse metadata) and section 10 (aisle state).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .config import Params
from .graph import GridGraph
from .types import (
    AisleDirection,
    AisleState,
    Vertex,
)

# Map legend ---------------------------------------------------------------
OBSTACLE_CHARS = {"@", "#", "T"}
PICKUP_CHARS = {"p", "P"}
DELIVERY_CHARS = {"d", "D"}
PARKING_CHARS = {"k", "K"}
BOTTLENECK_CHARS = {"b", "B"}
PASSING_BAY_CHARS = {"y", "Y"}
FREE_CHARS = {".", "-", "o"} | PICKUP_CHARS | DELIVERY_CHARS | PARKING_CHARS
FREE_CHARS |= BOTTLENECK_CHARS | PASSING_BAY_CHARS


@dataclass
class VertexInfo:
    """Per-vertex warehouse metadata (spec 9.2)."""

    vertex: Vertex
    aisle_id: Optional[int] = None
    index_in_aisle: Optional[int] = None
    is_intersection: bool = False
    is_pickup_area: bool = False
    is_delivery_area: bool = False
    is_parking_area: bool = False
    is_bottleneck: bool = False
    is_passing_bay: bool = False


@dataclass
class Aisle:
    """An aisle: a maximal corridor segment between intersections (spec 9.2).

    `vertices` is ordered from `start_vertex` to `end_vertex`.  A movement that
    increases the index is FORWARD; a movement that decreases it is REVERSE.
    """

    id: int
    vertices: List[Vertex]
    capacity: int
    # runtime state
    current_direction: AisleDirection = AisleDirection.NONE
    previous_direction: AisleDirection = AisleDirection.NONE
    state: AisleState = AisleState.OPEN
    occupancy: int = 0
    forward_queue: Set[int] = field(default_factory=set)
    reverse_queue: Set[int] = field(default_factory=set)
    lock_until: int = 0
    congestion_cost: float = 0.0
    direction_switches: int = 0
    draining_since: Optional[int] = None
    minimum_lock_time: int = 20
    maximum_lock_time: int = 40
    switch_threshold: float = 5.0
    #: False -> this aisle is always OPEN (too short, or a cut edge)
    manageable: bool = True
    #: timestep the current direction was committed, for the maximum-green rule
    direction_since: int = 0
    #: direction to adopt once the aisle finishes draining
    pending_direction: AisleDirection = AisleDirection.NONE
    #: how many flips were forced by the maximum-green rule rather than by a
    #: demand imbalance -- the signal that an aisle is being starved
    starvation_flips: int = 0
    #: robots that have left the aisle, for per-aisle throughput
    exits: int = 0

    @property
    def axis(self) -> str:
        """'row' for a horizontal run, 'col' for a vertical one, '' otherwise.

        Segmentation splits at every turn, so a multi-cell aisle always has an
        axis; '' means a single cell (no direction) or a component that is not
        a simple path.
        """
        if len(self.vertices) < 2:
            return ""
        rows = {v[0] for v in self.vertices}
        cols = {v[1] for v in self.vertices}
        if len(rows) == 1:
            return "row"
        if len(cols) == 1:
            return "col"
        return ""

    @property
    def start_vertex(self) -> Vertex:
        return self.vertices[0]

    @property
    def end_vertex(self) -> Vertex:
        return self.vertices[-1]

    @property
    def length(self) -> int:
        return len(self.vertices)

    def index_of(self, v: Vertex) -> Optional[int]:
        try:
            return self.vertices.index(v)
        except ValueError:
            return None

    def contains(self, v: Vertex) -> bool:
        return v in self._member_set

    def __post_init__(self) -> None:
        self._member_set: Set[Vertex] = set(self.vertices)
        self._index: Dict[Vertex, int] = {v: i for i, v in enumerate(self.vertices)}

    def position_index(self, v: Vertex) -> Optional[int]:
        return self._index.get(v)

    def is_exit(self, v: Vertex, direction: AisleDirection) -> bool:
        """True when `v` is the endpoint a robot leaves through in `direction`."""
        if direction is AisleDirection.FORWARD:
            return v == self.end_vertex
        if direction is AisleDirection.REVERSE:
            return v == self.start_vertex
        return v in (self.start_vertex, self.end_vertex)

    def entry_direction(self, entry_vertex: Vertex) -> AisleDirection:
        """Direction implied by entering the aisle at `entry_vertex`."""
        idx = self._index.get(entry_vertex)
        if idx is None:
            return AisleDirection.NONE
        if idx <= (self.length - 1) / 2:
            return AisleDirection.FORWARD
        return AisleDirection.REVERSE


class Warehouse:
    """Graph plus warehouse-level metadata."""

    def __init__(self, grid: Sequence[str], params: Optional[Params] = None):
        self.params = params or Params()
        self.grid = [row.rstrip("\n") for row in grid if row.strip("\n") != ""]
        width = max(len(row) for row in self.grid)
        self.grid = [row.ljust(width) for row in self.grid]
        self.height = len(self.grid)
        self.width = width

        passable = [
            [ch not in OBSTACLE_CHARS and ch != " " for ch in row] for row in self.grid
        ]
        self.graph = GridGraph(passable)

        self.info: Dict[Vertex, VertexInfo] = {
            v: VertexInfo(vertex=v) for v in self.graph.vertices
        }
        self.pickup_vertices: List[Vertex] = []
        self.delivery_vertices: List[Vertex] = []
        self.parking_vertices: List[Vertex] = []
        self._annotate_special_cells()

        self.aisles: Dict[int, Aisle] = {}
        self._segment_aisles()
        self._annotate_structure()
        self._mark_manageable_aisles()

    # ------------------------------------------------------------- factories
    @classmethod
    def from_file(cls, path: str | Path, params: Optional[Params] = None) -> "Warehouse":
        text = Path(path).read_text()
        rows = [line for line in text.splitlines() if not line.startswith("//")]
        return cls(rows, params)

    @classmethod
    def from_string(cls, text: str, params: Optional[Params] = None) -> "Warehouse":
        return cls(text.splitlines(), params)

    # ------------------------------------------------------------ annotation
    def _annotate_special_cells(self) -> None:
        for v in self.graph.vertices:
            ch = self.grid[v[0]][v[1]]
            info = self.info[v]
            if ch in PICKUP_CHARS:
                info.is_pickup_area = True
                self.pickup_vertices.append(v)
            elif ch in DELIVERY_CHARS:
                info.is_delivery_area = True
                self.delivery_vertices.append(v)
            elif ch in PARKING_CHARS:
                info.is_parking_area = True
                self.parking_vertices.append(v)
            if ch in BOTTLENECK_CHARS:
                info.is_bottleneck = True
            if ch in PASSING_BAY_CHARS:
                info.is_passing_bay = True

    def _segment_aisles(self) -> None:
        """Aisles = connected components of non-intersection passable cells."""
        intersections = self.graph.intersections
        for v in intersections:
            self.info[v].is_intersection = True

        corridor = [v for v in self.graph.vertices if v not in intersections]
        corridor_set = set(corridor)
        visited: Set[Vertex] = set()
        aisle_id = 0

        for start in corridor:
            if start in visited:
                continue
            # collect the connected component
            component: List[Vertex] = []
            stack = [start]
            visited.add(start)
            while stack:
                v = stack.pop()
                component.append(v)
                for n in self.graph.neighbors(v):
                    if n in corridor_set and n not in visited:
                        visited.add(n)
                        stack.append(n)
            ordered = self._order_component(component, corridor_set)
            for run in self._split_straight_runs(ordered):
                aisle = self._build_aisle(aisle_id, run)
                self.aisles[aisle_id] = aisle
                for idx, v in enumerate(run):
                    self.info[v].aisle_id = aisle_id
                    self.info[v].index_in_aisle = idx
                aisle_id += 1

    def _build_aisle(self, aisle_id: int, vertices: List[Vertex]) -> Aisle:
        p = self.params
        minimum_lock = max(2, min(p.minimum_aisle_lock_time, 2 * len(vertices)))
        return Aisle(
            id=aisle_id,
            vertices=vertices,
            capacity=self._aisle_capacity(len(vertices)),
            minimum_lock_time=minimum_lock,
            # A maximum green below the minimum green would be contradictory:
            # the aisle would be due to flip before it is allowed to.
            maximum_lock_time=max(minimum_lock, p.maximum_aisle_lock_time),
            switch_threshold=p.direction_switch_threshold,
        )

    @staticmethod
    def _split_straight_runs(ordered: List[Vertex]) -> List[List[Vertex]]:
        """Cut a corridor component into maximal straight runs.

        A component of non-intersection cells is not necessarily a straight
        corridor: an L, U or T shape is one component too.  `AisleDirection`
        means "index increasing" and only carries a physical meaning -- one
        compass axis -- on a straight run, so giving a bend a traffic direction
        makes the corner one-way in *both* senses and cuts the map in half.
        Splitting at every turn keeps each aisle on a single axis.

        The turn cell closes the run it is already part of, and the next run
        begins after it, so the runs partition the component and each stays on
        one axis.  Runs of length 1 (a lone elbow) are kept: they fall below
        `directional_aisle_min_length`, so they stay bidirectional and behave
        like the intersections they sit between.
        """
        if len(ordered) <= 1:
            return [list(ordered)] if ordered else []

        def axis(u: Vertex, v: Vertex) -> Optional[int]:
            if abs(u[0] - v[0]) + abs(u[1] - v[1]) != 1:
                return None  # not adjacent: a non-path component
            return 0 if u[0] != v[0] else 1

        runs: List[List[Vertex]] = []
        run: List[Vertex] = [ordered[0]]
        run_axis: Optional[int] = None
        for previous, current in zip(ordered, ordered[1:]):
            step_axis = axis(previous, current)
            if step_axis is None or (run_axis is not None and step_axis != run_axis):
                runs.append(run)
                run, run_axis = [current], None
                continue
            run.append(current)
            run_axis = step_axis
        runs.append(run)
        return [r for r in runs if r]

    def _aisle_capacity(self, length: int) -> int:
        """How many robots may be inside an aisle of this many cells.

        A single-file corridor is throttled by the robot at its exit, so
        packing it full just builds a queue that cannot drain. The last robot
        in must still traverse `length` cells to leave and every robot ahead of
        it adds a step, so a queue of k needs roughly `length + k` steps to
        empty: capping k at `max_drain_time - length` keeps the aisle drainable
        within its own timeout.

        There used to be two other formulas behind an `aisle_capacity_model`
        switch. Neither changed throughput measurably and the code comments
        already called both wrong, so this is the only one now.
        """
        p = self.params
        raw = min(length, p.max_drain_time - length)
        return max(1, min(p.aisle_capacity, int(raw)))

    def _order_component(
        self, component: List[Vertex], corridor_set: Set[Vertex]
    ) -> List[Vertex]:
        """Order a corridor component from one endpoint to the other."""
        if len(component) == 1:
            return list(component)
        comp_set = set(component)

        def local_degree(v: Vertex) -> int:
            return sum(1 for n in self.graph.neighbors(v) if n in comp_set)

        endpoints = sorted(v for v in component if local_degree(v) <= 1)
        start = endpoints[0] if endpoints else min(component)

        ordered = [start]
        seen = {start}
        current = start
        while True:
            nxt = None
            for n in sorted(self.graph.neighbors(current)):
                if n in comp_set and n not in seen:
                    nxt = n
                    break
            if nxt is None:
                break
            ordered.append(nxt)
            seen.add(nxt)
            current = nxt
        # non-path components (rare): append the rest deterministically
        for v in sorted(component):
            if v not in seen:
                ordered.append(v)
        return ordered

    def _annotate_structure(self) -> None:
        """Mark bottlenecks from articulation points, bridges and dead ends."""
        for v in self.graph.articulation_points:
            self.info[v].is_bottleneck = True
        for u, v in self.graph.bridges:
            self.info[u].is_bottleneck = True
            self.info[v].is_bottleneck = True
        self.dead_ends = self.graph.dead_ends
        for v in self.dead_ends:
            self.info[v].is_bottleneck = True

    def _mark_manageable_aisles(self) -> None:
        """Decide which aisles may be given a traffic direction.

        An aisle is left permanently OPEN when it is too short to be worth a
        one-way rule, or when it carries a bridge edge: making a cut edge
        one-way disconnects the graph and destroys reachability (spec 9.3, 38.2).
        """
        bridges = self.graph.bridges
        min_length = self.params.directional_aisle_min_length
        for aisle in self.aisles.values():
            if aisle.length < min_length:
                aisle.manageable = False
                continue
            edges = [
                tuple(sorted((aisle.vertices[i], aisle.vertices[i + 1])))
                for i in range(aisle.length - 1)
            ]
            boundary = [
                tuple(sorted((endpoint, neighbor)))
                for endpoint in (aisle.start_vertex, aisle.end_vertex)
                for neighbor in self.graph.neighbors(endpoint)
                if self.aisle_id(neighbor) != aisle.id
            ]
            aisle.manageable = not any(e in bridges for e in edges + boundary)

    # -------------------------------------------------------------- queries
    def aisle_id(self, v: Vertex) -> Optional[int]:
        info = self.info.get(v)
        return info.aisle_id if info else None

    def get_aisle(self, aisle_id: Optional[int]) -> Optional[Aisle]:
        if aisle_id is None:
            return None
        return self.aisles.get(aisle_id)

    def aisle_of(self, v: Vertex) -> Optional[Aisle]:
        return self.get_aisle(self.aisle_id(v))

    def is_intersection(self, v: Vertex) -> bool:
        info = self.info.get(v)
        return bool(info and info.is_intersection)

    def is_bottleneck(self, v: Vertex) -> bool:
        info = self.info.get(v)
        return bool(info and info.is_bottleneck)

    def traversal_direction(self, u: Vertex, v: Vertex) -> AisleDirection:
        """Direction of the move u -> v relative to the aisle containing `v`."""
        aisle = self.aisle_of(v)
        if aisle is None:
            return AisleDirection.NONE
        vi = aisle.position_index(v)
        ui = aisle.position_index(u)
        if vi is None:
            return AisleDirection.NONE
        if ui is None:
            # entering from an intersection: direction implied by entry point
            return aisle.entry_direction(v)
        if vi > ui:
            return AisleDirection.FORWARD
        if vi < ui:
            return AisleDirection.REVERSE
        return AisleDirection.NONE

    @property
    def waypoint_vertices(self) -> List[Vertex]:
        return list(
            dict.fromkeys(
                self.pickup_vertices + self.delivery_vertices + self.parking_vertices
            )
        )

    def precompute(self) -> None:
        """Precompute distance maps for every station (spec 9.1)."""
        self.graph.precompute_distance_maps(self.waypoint_vertices)

    def reset_runtime_state(self) -> None:
        for aisle in self.aisles.values():
            aisle.current_direction = AisleDirection.NONE
            aisle.previous_direction = AisleDirection.NONE
            aisle.state = AisleState.OPEN
            aisle.occupancy = 0
            aisle.forward_queue.clear()
            aisle.reverse_queue.clear()
            aisle.lock_until = 0
            aisle.congestion_cost = 0.0
            aisle.direction_switches = 0
            aisle.draining_since = None
            aisle.direction_since = 0
            aisle.pending_direction = AisleDirection.NONE
            aisle.starvation_flips = 0
            aisle.exits = 0

    def summary(self) -> Dict[str, object]:
        return {
            "size": f"{self.height}x{self.width}",
            "vertices": len(self.graph),
            "aisles": len(self.aisles),
            "intersections": len(self.graph.intersections),
            "pickups": len(self.pickup_vertices),
            "deliveries": len(self.delivery_vertices),
            "parking": len(self.parking_vertices),
            "dead_ends": len(self.graph.dead_ends),
            "bridges": len(self.graph.bridges),
            "articulation_points": len(self.graph.articulation_points),
            "satisfies_pibt_reachability": self.graph.satisfies_pibt_reachability(),
        }


__all__ = ["Warehouse", "Aisle", "VertexInfo"]
