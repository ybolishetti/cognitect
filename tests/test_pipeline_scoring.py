"""Tests for engine/pipeline/scoring -- user-constraint + Layer B score
aggregation. Architecture C, DRAFT 6.
"""

from __future__ import annotations

from engine.layout import FloorPlanSpec, Layout, Room, RoomRequirement, Wall
from engine.pipeline.scoring import (
    DEFAULT_WEIGHTS,
    _adjacency_fit,
    _area_fit,
    _match_requirements_to_rooms,
    _weighted_mean,
    compute_layout_score,
)

# ── Fixture helpers (local to this file) ────────────────────────────────────


def _rect_walls(room_id: str, prefix: str, x0=0.0, y0=0.0, x1=10.0, y1=10.0) -> list[Wall]:
    return [
        Wall(id=f"wall_{prefix}_s", start=(x0, y0), end=(x1, y0), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_e", start=(x1, y0), end=(x1, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_n", start=(x1, y1), end=(x0, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_w", start=(x0, y1), end=(x0, y0), bounds_rooms=[room_id]),
    ]


def _rect_room(room_id: str, room_type: str, wall_ids: list[str], x0=0.0, y0=0.0, x1=10.0, y1=10.0, name=None) -> Room:
    return Room(
        id=room_id,
        name=name or room_id,
        room_type=room_type,
        vertices=[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
        area_sqft=(x1 - x0) * (y1 - y0),
        boundary_wall_ids=wall_ids,
    )


def _shared_wall(wall_id: str, room_a: str, room_b: str, start=(0.0, 0.0), end=(1.0, 0.0)) -> Wall:
    return Wall(id=wall_id, start=start, end=end, bounds_rooms=[room_a, room_b])


def _make_layout(rooms: list[Room], walls: list[Wall], plan_id="plan_test1") -> Layout:
    return Layout(plan_id=plan_id, rooms=rooms, walls=walls, extent_x_ft=1000.0, extent_y_ft=1000.0)


def _make_spec(room_requirements: list[RoomRequirement], spec_id="spec_test_scoring") -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id=spec_id,
        original_nl="a test plan",
        room_requirements=room_requirements,
    )


# ── _area_fit ────────────────────────────────────────────────────────────────


def test_area_fit_returns_one_when_no_room_has_a_preferred_area():
    req = RoomRequirement(name="Living", room_type="living")
    walls = _rect_walls("room_1", "r1")
    room = _rect_room("room_1", "living", [w.id for w in walls])
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    assert _area_fit(spec, layout) == 1.0


def test_area_fit_returns_one_on_exact_match():
    req = RoomRequirement(name="Living", room_type="living", preferred_area_sqft=100.0)
    walls = _rect_walls("room_1", "r1", x1=10, y1=10)
    room = _rect_room("room_1", "living", [w.id for w in walls], x1=10, y1=10)
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    assert _area_fit(spec, layout) == 1.0


def test_area_fit_returns_half_when_fifty_percent_over():
    req = RoomRequirement(name="Living", room_type="living", preferred_area_sqft=100.0)
    walls = _rect_walls("room_1", "r1", x1=15, y1=10)  # area = 150 = 1.5x preferred
    room = _rect_room("room_1", "living", [w.id for w in walls], x1=15, y1=10)
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    assert _area_fit(spec, layout) == 0.5


def test_area_fit_averages_across_multiple_rooms():
    req_a = RoomRequirement(name="Living", room_type="living", preferred_area_sqft=100.0)
    req_b = RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=100.0)
    walls_a = _rect_walls("room_1", "r1", x1=10, y1=10)  # exact match -> 1.0
    room_a = _rect_room("room_1", "living", [w.id for w in walls_a], x1=10, y1=10, name="Living")
    walls_b = _rect_walls("room_2", "r2", x0=0, y0=20, x1=15, y1=30)  # 150 = 1.5x -> 0.5
    room_b = _rect_room("room_2", "bedroom", [w.id for w in walls_b], x0=0, y0=20, x1=15, y1=30, name="Bedroom")
    spec = _make_spec([req_a, req_b])
    layout = _make_layout([room_a, room_b], walls_a + walls_b)
    assert _area_fit(spec, layout) == 0.75


# ── _adjacency_fit ───────────────────────────────────────────────────────────


def test_adjacency_fit_returns_one_when_no_adjacencies_requested():
    req = RoomRequirement(name="Living", room_type="living")
    walls = _rect_walls("room_1", "r1")
    room = _rect_room("room_1", "living", [w.id for w in walls], name="Living")
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    assert _adjacency_fit(spec, layout) == 1.0


def test_adjacency_fit_returns_one_when_all_honored():
    req_a = RoomRequirement(name="Living", room_type="living", adjacencies=["Kitchen"])
    req_b = RoomRequirement(name="Kitchen", room_type="kitchen")
    walls_a = _rect_walls("room_1", "r1", x0=0, y0=0, x1=10, y1=10)
    room_a = _rect_room("room_1", "living", [w.id for w in walls_a], x1=10, y1=10, name="Living")
    walls_b = _rect_walls("room_2", "r2", x0=10, y0=0, x1=20, y1=10)
    room_b = _rect_room("room_2", "kitchen", [w.id for w in walls_b], x0=10, y0=0, x1=20, y1=10, name="Kitchen")
    shared = _shared_wall("wall_shared_ab", "room_1", "room_2", start=(10, 0), end=(10, 10))
    spec = _make_spec([req_a, req_b])
    layout = _make_layout([room_a, room_b], walls_a + walls_b + [shared])
    assert _adjacency_fit(spec, layout) == 1.0


def test_adjacency_fit_returns_half_when_half_honored():
    req_a = RoomRequirement(name="Living", room_type="living", adjacencies=["Kitchen", "Bedroom"])
    req_b = RoomRequirement(name="Kitchen", room_type="kitchen")
    req_c = RoomRequirement(name="Bedroom", room_type="bedroom")
    walls_a = _rect_walls("room_1", "r1", x0=0, y0=0, x1=10, y1=10)
    room_a = _rect_room("room_1", "living", [w.id for w in walls_a], x1=10, y1=10, name="Living")
    walls_b = _rect_walls("room_2", "r2", x0=10, y0=0, x1=20, y1=10)
    room_b = _rect_room("room_2", "kitchen", [w.id for w in walls_b], x0=10, y0=0, x1=20, y1=10, name="Kitchen")
    walls_c = _rect_walls("room_3", "r3", x0=100, y0=100, x1=110, y1=110)
    room_c = _rect_room("room_3", "bedroom", [w.id for w in walls_c], x0=100, y0=100, x1=110, y1=110, name="Bedroom")
    shared = _shared_wall("wall_shared_ab", "room_1", "room_2", start=(10, 0), end=(10, 10))
    spec = _make_spec([req_a, req_b, req_c])
    layout = _make_layout([room_a, room_b, room_c], walls_a + walls_b + walls_c + [shared])
    assert _adjacency_fit(spec, layout) == 0.5


def test_adjacency_fit_counts_nonexistent_room_as_not_honored():
    req = RoomRequirement(name="Living", room_type="living", adjacencies=["Nonexistent"])
    walls = _rect_walls("room_1", "r1")
    room = _rect_room("room_1", "living", [w.id for w in walls], name="Living")
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    assert _adjacency_fit(spec, layout) == 0.0


# ── _weighted_mean ───────────────────────────────────────────────────────────


def test_weighted_mean_single_subscore_returns_that_value():
    assert _weighted_mean({"area_fit": 0.7}, DEFAULT_WEIGHTS) == 0.7


def test_weighted_mean_redistributes_over_missing_keys():
    # area_fit=0.5 (weight 0.5), adjacency_fit=1.0 (weight 0.3); layer_b missing.
    # Weighted mean over the two present keys: (0.5*0.5 + 1.0*0.3) / (0.5+0.3)
    result = _weighted_mean({"area_fit": 0.5, "adjacency_fit": 1.0}, DEFAULT_WEIGHTS)
    expected = (0.5 * 0.5 + 1.0 * 0.3) / (0.5 + 0.3)
    assert abs(result - expected) < 1e-9


def test_weighted_mean_all_keys_correctly_weighted():
    parts = {"area_fit": 1.0, "adjacency_fit": 0.5, "layer_b": 0.0}
    result = _weighted_mean(parts, DEFAULT_WEIGHTS)
    expected = (1.0 * 0.5 + 0.5 * 0.3 + 0.0 * 0.2) / (0.5 + 0.3 + 0.2)
    assert abs(result - expected) < 1e-9


# ── compute_layout_score ─────────────────────────────────────────────────────


def test_compute_layout_score_with_no_layer_b_combined_equals_user_score():
    req = RoomRequirement(name="Living", room_type="living", preferred_area_sqft=100.0)
    walls = _rect_walls("room_1", "r1", x1=10, y1=10)
    room = _rect_room("room_1", "living", [w.id for w in walls], x1=10, y1=10, name="Living")
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    combined, user_score = compute_layout_score(spec, layout, layer_b_score=None)
    assert combined == user_score


def test_compute_layout_score_with_layer_b_reflects_weighted_average():
    req = RoomRequirement(name="Living", room_type="living", preferred_area_sqft=100.0)
    walls = _rect_walls("room_1", "r1", x1=10, y1=10)
    room = _rect_room("room_1", "living", [w.id for w in walls], x1=10, y1=10, name="Living")
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    combined, user_score = compute_layout_score(spec, layout, layer_b_score=0.5)
    # area_fit=1.0, adjacency_fit=1.0 (no adjacencies requested) -> user_score=1.0
    assert user_score == 1.0
    expected_combined = (1.0 * 0.5 + 1.0 * 0.3 + 0.5 * 0.2) / (0.5 + 0.3 + 0.2)
    assert abs(combined - expected_combined) < 1e-9
    assert combined != user_score


# ── _match_requirements_to_rooms ────────────────────────────────────────────


def test_match_requirements_to_rooms_name_match_happy_path():
    req = RoomRequirement(name="Living", room_type="living")
    walls = _rect_walls("room_1", "r1")
    room = _rect_room("room_1", "living", [w.id for w in walls], name="Living")
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    matched = _match_requirements_to_rooms(spec, layout)
    assert matched == [(req, room)]


def test_match_requirements_to_rooms_falls_back_to_room_type_and_index():
    req = RoomRequirement(name="Primary Bedroom", room_type="bedroom")
    walls = _rect_walls("room_1", "r1")
    # Layout renamed the room, so exact name match fails and it must fall
    # back to room_type + positional index.
    room = _rect_room("room_1", "bedroom", [w.id for w in walls], name="Renamed Bedroom")
    spec = _make_spec([req])
    layout = _make_layout([room], walls)
    matched = _match_requirements_to_rooms(spec, layout)
    assert matched == [(req, room)]
