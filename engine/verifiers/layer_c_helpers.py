"""Construction helpers shared by Layer C rule check functions.

Pure map-building/predicate helpers only — no pass/fail judgment logic
lives here (that's each rule's job). Keeping judgment out of this module
means every helper here is independently unit-testable and reusable
across rules without duplicating the same dict comprehension in each one.
"""

from __future__ import annotations

from collections import defaultdict

from engine.layout import Layout, Opening, Wall


def walls_by_id(layout: Layout) -> dict[str, Wall]:
    """Return a map from wall.id to Wall for every wall in the layout."""
    return {wall.id: wall for wall in layout.walls}


def openings_by_wall(layout: Layout) -> dict[str, list[Opening]]:
    """Return a map from wall_id to every Opening on that wall."""
    result: dict[str, list[Opening]] = defaultdict(list)
    for opening in layout.openings:
        result[opening.wall_id].append(opening)
    return result


def is_exterior_wall(wall: Wall, room_id: str) -> bool:
    """Return True if `wall` is an exterior wall of `room_id`.

    Exterior = exactly one room in bounds_rooms, and it's this room.
    """
    return len(wall.bounds_rooms) == 1 and wall.bounds_rooms[0] == room_id
