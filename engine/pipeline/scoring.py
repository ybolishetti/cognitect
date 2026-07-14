"""Combine user-constraint fit + Layer B advisory into a single score in [0, 1].

The scorer is intentionally *shallow* — three sub-scores, mean-weighted, no
learned model. If we want a smarter selector later (weight training,
learning-to-rank), that ships as its own file. This one gets the pipeline
working end-to-end with defensible math.

Sub-scores (each in [0, 1], higher is better):
  1. area_fit           — how close each Room.area_sqft is to its preferred_area_sqft
  2. adjacency_fit      — fraction of RoomRequirement.adjacencies that were honored
  3. layer_b            — Layer B's advisory score (None → skipped, weight redistributed)

Combined = weighted mean of the sub-scores that are present. Missing
sub-scores are dropped, not zeroed (a bad Layer B shouldn't tank a spec
that has no user adjacencies to score).
"""

from __future__ import annotations

from typing import Optional

from engine.layout import FloorPlanSpec, Layout, Room, RoomRequirement

DEFAULT_WEIGHTS = {
    "area_fit": 0.5,
    "adjacency_fit": 0.3,
    "layer_b": 0.2,
}


def compute_layout_score(
    spec: FloorPlanSpec,
    layout: Layout,
    layer_b_score: Optional[float] = None,
) -> tuple[float, float]:
    """Return (combined_score, user_score).

    - combined_score: weighted mean of (area_fit, adjacency_fit, layer_b)
    - user_score:     weighted mean of (area_fit, adjacency_fit) — layer_b excluded
                      This is what the audit manifest records — it stays stable
                      across Layer B evolution.

    Both are floats in [0, 1].
    """
    area = _area_fit(spec, layout)
    adj = _adjacency_fit(spec, layout)
    user_score = _weighted_mean({"area_fit": area, "adjacency_fit": adj}, DEFAULT_WEIGHTS)
    parts: dict = {"area_fit": area, "adjacency_fit": adj}
    if layer_b_score is not None:
        parts["layer_b"] = layer_b_score
    combined = _weighted_mean(parts, DEFAULT_WEIGHTS)
    return combined, user_score


def _area_fit(spec: FloorPlanSpec, layout: Layout) -> float:
    """Return mean(1 - |emitted - preferred| / preferred) across rooms with a preferred_area_sqft.

    Rooms without a preferred_area are excluded from the mean. If no room has
    a preferred, returns 1.0 (there's nothing to fit -- don't penalize).
    """
    matched = _match_requirements_to_rooms(spec, layout)
    ratios: list[float] = []
    for req, room in matched:
        if req.preferred_area_sqft is None:
            continue
        pref = req.preferred_area_sqft
        deviation = abs(room.area_sqft - pref) / pref
        ratios.append(max(0.0, 1.0 - deviation))
    if not ratios:
        return 1.0
    return sum(ratios) / len(ratios)


def _adjacency_fit(spec: FloorPlanSpec, layout: Layout) -> float:
    """Fraction of requested adjacencies that were honored.

    Adjacency is honored iff the two rooms share at least one Wall (i.e.
    both room ids appear in some wall.bounds_rooms). If no adjacencies
    were requested, returns 1.0.
    """
    matched = _match_requirements_to_rooms(spec, layout)
    name_to_room_id = {req.name: room.id for req, room in matched}

    total = 0
    honored = 0
    for req, room in matched:
        for other_name in req.adjacencies:
            total += 1
            other_id = name_to_room_id.get(other_name)
            if other_id is None:
                continue
            if _rooms_share_wall(layout, room.id, other_id):
                honored += 1
    if total == 0:
        return 1.0
    return honored / total


def _match_requirements_to_rooms(spec: FloorPlanSpec, layout: Layout) -> list[tuple[RoomRequirement, Room]]:
    """Best-effort match: first by exact name, then by room_type + index.

    If a spec asks for [Living, Bedroom, Bedroom] and layout has rooms
    named [Living, Bedroom, Bedroom], names match directly. If layout
    renamed them, we fall back to room_type + positional index.
    """
    rooms_by_name: dict[str, Room] = {room.name: room for room in layout.rooms}
    rooms_by_type: dict[str, list[Room]] = {}
    for room in layout.rooms:
        rooms_by_type.setdefault(room.room_type, []).append(room)
    type_index: dict[str, int] = {}

    matched: list[tuple[RoomRequirement, Room]] = []
    for req in spec.room_requirements:
        room = rooms_by_name.get(req.name)
        if room is None:
            idx = type_index.get(req.room_type, 0)
            candidates = rooms_by_type.get(req.room_type, [])
            if idx < len(candidates):
                room = candidates[idx]
                type_index[req.room_type] = idx + 1
        if room is not None:
            matched.append((req, room))
    return matched


def _rooms_share_wall(layout: Layout, room_a: str, room_b: str) -> bool:
    for wall in layout.walls:
        if len(wall.bounds_rooms) == 2 and set(wall.bounds_rooms) == {room_a, room_b}:
            return True
    return False


def _weighted_mean(parts: dict, weights: dict) -> float:
    """Weighted mean over the sub-scores actually present in `parts`."""
    active_weights = {k: weights[k] for k in parts.keys() if k in weights}
    total_w = sum(active_weights.values())
    if total_w == 0:
        return 0.0
    return sum(parts[k] * active_weights[k] for k in active_weights) / total_w
