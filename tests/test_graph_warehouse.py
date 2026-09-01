"""Graph, distance-map and warehouse-metadata tests (spec sections 9, 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lda_pibt import GridGraph, Params, Warehouse
from lda_pibt.types import INF

MAPS = Path(__file__).resolve().parents[1] / "maps"

OPEN_GRID = """
.....
.....
.....
""".strip()

CORRIDOR = "p...d"

TWO_ROOMS = """
...@...
...@...
.......
...@...
...@...
""".strip()


def make(text: str, **overrides) -> Warehouse:
    return Warehouse.from_string(text, Params(**overrides))


def test_grid_neighbours_and_degree():
    wh = make(OPEN_GRID)
    graph = wh.graph
    assert len(graph) == 15
    assert set(graph.neighbors((0, 0))) == {(0, 1), (1, 0)}
    assert graph.degree((1, 2)) == 4
    assert graph.max_degree == 4


def test_obstacles_are_not_vertices():
    wh = make(TWO_ROOMS)
    assert not wh.graph.contains((0, 3))
    assert wh.graph.contains((2, 3))


def test_bfs_distance_map_matches_manhattan_on_open_grid():
    wh = make(OPEN_GRID)
    dmap = wh.graph.compute_bfs_distance_map((0, 0))
    for (r, c), d in dmap.items():
        assert d == r + c


def test_distance_map_is_cached():
    wh = make(OPEN_GRID)
    first = wh.graph.distance_map((0, 0))
    assert wh.graph.distance_map((0, 0)) is first


def test_unreachable_vertices_have_infinite_distance():
    wh = make("..@..")
    dmap = wh.graph.distance_map((0, 0))
    assert dmap[(0, 1)] == 1
    assert dmap[(0, 3)] == INF


def test_shortest_route_is_a_real_path():
    wh = make(OPEN_GRID)
    route = wh.graph.shortest_route((0, 0), (2, 4))
    assert route[0] == (0, 0) and route[-1] == (2, 4)
    assert len(route) == 7
    for a, b in zip(route, route[1:]):
        assert b in wh.graph.neighbors(a)


def test_dead_ends_and_bridges_detected():
    wh = make(TWO_ROOMS)
    assert (2, 3) in wh.graph.articulation_points
    assert not wh.graph.satisfies_pibt_reachability()


def test_open_grid_satisfies_pibt_reachability():
    wh = make(OPEN_GRID)
    assert wh.graph.satisfies_pibt_reachability()


def test_special_cells_annotated():
    wh = make("p..d\n....\nk..b")
    assert wh.pickup_vertices == [(0, 0)]
    assert wh.delivery_vertices == [(0, 3)]
    assert wh.parking_vertices == [(2, 0)]
    assert wh.info[(2, 3)].is_bottleneck


def test_corridor_is_one_aisle_with_no_intersection():
    wh = make(CORRIDOR)
    assert len(wh.aisles) == 1
    aisle = wh.aisles[0]
    assert aisle.length == 5
    assert {aisle.start_vertex, aisle.end_vertex} == {(0, 0), (0, 4)}


def test_aisle_vertices_are_contiguous():
    wh = Warehouse.from_file("maps/warehouse_small.map", Params())
    for aisle in wh.aisles.values():
        for a, b in zip(aisle.vertices, aisle.vertices[1:]):
            assert b in wh.graph.neighbors(a)


def test_every_non_intersection_cell_belongs_to_exactly_one_aisle():
    wh = Warehouse.from_file("maps/warehouse_small.map", Params())
    owners = {}
    for aisle in wh.aisles.values():
        for v in aisle.vertices:
            assert v not in owners, f"{v} claimed twice"
            owners[v] = aisle.id
    for v in wh.graph.vertices:
        assert wh.is_intersection(v) or v in owners


def test_capacity_is_bounded_by_what_can_drain():
    """A corridor holds no more robots than it can empty in `max_drain_time`.

    The last robot in must still cross the whole aisle to leave, and every
    robot ahead of it adds a step, so the cap is `max_drain_time - length`.
    """
    assert make(CORRIDOR, max_drain_time=30).aisles[0].capacity == 5
    assert make(CORRIDOR, max_drain_time=8).aisles[0].capacity == 3


def test_summary_keys():
    wh = make(OPEN_GRID)
    assert wh.summary()["vertices"] == 15


# --------------------------------------------------- straight-run segmentation
def test_no_bundled_map_produces_a_bent_aisle():
    """An aisle direction only means something on a single compass axis.

    Segmenting by connected component alone produced L, U and T shaped
    "aisles" -- `warehouse_corridors` had two 25-cell U shapes spanning a whole
    corridor plus both vertical links. Giving a bend a direction makes the
    corner one-way in both senses and cuts the map in half.
    """
    for name in sorted(p.stem for p in MAPS.glob("*.map")):
        warehouse = Warehouse.from_file(MAPS / f"{name}.map", Params())
        bent = [a.id for a in warehouse.aisles.values() if a.length > 1 and not a.axis]
        assert not bent, f"{name} has bent aisles {bent}"


def test_every_aisle_is_a_contiguous_path():
    warehouse = Warehouse.from_file(MAPS / "warehouse_medium.map", Params())
    for aisle in warehouse.aisles.values():
        for u, v in zip(aisle.vertices, aisle.vertices[1:]):
            assert abs(u[0] - v[0]) + abs(u[1] - v[1]) == 1


def test_aisles_partition_the_non_intersection_cells():
    warehouse = Warehouse.from_file(MAPS / "warehouse_corridors.map", Params())
    owned = [v for aisle in warehouse.aisles.values() for v in aisle.vertices]
    assert len(owned) == len(set(owned)), "a cell belongs to two aisles"
    expected = {
        v for v in warehouse.graph.vertices if v not in warehouse.graph.intersections
    }
    assert set(owned) == expected


