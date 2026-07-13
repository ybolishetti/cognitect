"""Shapely construction helpers shared by Layer A check functions.

Pure geometry construction/graph-building only — no pass/fail judgment logic
lives here (that's layer_a.py's job). Keeping judgment out of this module
means every helper here is independently unit-testable against plain
coordinate data, without needing a full Layout.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import LinearRing

from engine.layout import Layout, Room, Wall


def room_polygon(room: Room) -> Polygon:
    """Return a Shapely Polygon from a Room's vertices."""
    return Polygon(room.vertices)


def wall_linestring(wall: Wall) -> LineString:
    """Return a Shapely LineString from a Wall's start/end.

    Uses full float precision — NOT the rounded Wall.length_ft computed_field.
    """
    return LineString([wall.start, wall.end])


def all_room_polygons(layout: Layout) -> list[tuple[str, Polygon]]:
    """Return [(room_id, polygon), ...] for every room in the layout."""
    return [(room.id, room_polygon(room)) for room in layout.rooms]


def _node_key(pt: tuple[float, float], round_decimals: int) -> tuple[float, float]:
    return (round(pt[0], round_decimals), round(pt[1], round_decimals))


def wall_endpoint_graph(
    walls: list[Wall], round_decimals: int
) -> dict[tuple[float, float], list[Wall]]:
    """Build a map from rounded (x, y) endpoint to every Wall touching it.

    `len(graph[node])` is that node's degree. Snapping tolerance is applied
    only here (via round_decimals) — two walls whose endpoints round to the
    same key are treated as meeting at that point.
    """
    graph: dict[tuple[float, float], list[Wall]] = defaultdict(list)
    for wall in walls:
        graph[_node_key(wall.start, round_decimals)].append(wall)
        graph[_node_key(wall.end, round_decimals)].append(wall)
    return graph


def order_wall_ids_into_ring(
    wall_ids: list[str],
    walls_by_id: dict[str, Wall],
    round_decimals: int,
) -> tuple[Optional[list[tuple[float, float]]], Optional[str]]:
    """Order a set of wall IDs into a single closed polygon ring.

    Shared by check_walls_form_closed_room_boundaries (topology + point-
    sequence comparison) and check_room_polygons_match_boundary_walls
    (Shapely area comparison) — the cycle-finding logic lives exactly once
    here.

    Returns (ring, None) on success: `ring` is a closed list of (x, y)
    tuples (ring[0] == ring[-1]) in CCW order (orientation is normalized via
    LinearRing.is_ccw so callers never need to handle direction themselves).

    Returns (None, reason) on failure. `reason` is one of:
      "missing_wall"     - a wall_id has no matching Wall in walls_by_id.
      "not_simple_cycle" - some snapped endpoint node has degree != 2, i.e.
                            the wall set is an open chain, has a dangling
                            branch, or has a node where 3+ walls meet.
      "multiple_cycles"  - every node has degree exactly 2, but the wall set
                            decomposes into 2+ disjoint simple cycles instead
                            of one single loop covering every wall_id.
    """
    walls: list[Wall] = []
    for wall_id in wall_ids:
        wall = walls_by_id.get(wall_id)
        if wall is None:
            return None, "missing_wall"
        walls.append(wall)

    canonical_point: dict[tuple[float, float], tuple[float, float]] = {}
    adjacency: dict[tuple[float, float], list[tuple[str, tuple[float, float]]]] = defaultdict(list)

    for wall in walls:
        a_key = _node_key(wall.start, round_decimals)
        b_key = _node_key(wall.end, round_decimals)
        canonical_point.setdefault(a_key, wall.start)
        canonical_point.setdefault(b_key, wall.end)
        adjacency[a_key].append((wall.id, b_key))
        adjacency[b_key].append((wall.id, a_key))

    if len(adjacency) != len(walls):
        return None, "not_simple_cycle"
    if any(len(edges) != 2 for edges in adjacency.values()):
        return None, "not_simple_cycle"

    start_key = next(iter(adjacency))
    current_key = start_key
    visited_walls: set[str] = set()
    ring_keys = [start_key]

    wall_id, next_key = adjacency[current_key][0]
    visited_walls.add(wall_id)
    ring_keys.append(next_key)
    current_key = next_key

    while current_key != start_key:
        edge0, edge1 = adjacency[current_key]
        wall_id, next_key = edge0 if edge0[0] not in visited_walls else edge1
        if wall_id in visited_walls:
            return None, "multiple_cycles"
        visited_walls.add(wall_id)
        ring_keys.append(next_key)
        current_key = next_key

    if len(visited_walls) != len(walls):
        return None, "multiple_cycles"

    ring = [canonical_point[key] for key in ring_keys]

    if not LinearRing(ring).is_ccw:
        ring = list(reversed(ring))

    return ring, None
