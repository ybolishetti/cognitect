"""Tests for engine/layout — the typed Layout schema (Architecture C, DRAFT 1).

Schema-only coverage: type constraints, field validators, and Layout-level
cross-reference validators. No verifiers, no generators — those are later
DRAFTs and have their own test suites.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from engine.layout import (
    Exit,
    FloorPlanSpec,
    GenerationFailure,
    GridLine,
    Layout,
    LayoutAuditManifest,
    Opening,
    Room,
    RoomRequirement,
    SiteConstraints,
    StructuralGrid,
    VerifierResult,
    Wall,
)
from engine.layout.schemas import _shoelace_area

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _square_vertices(x: float = 0.0, y: float = 0.0, size: float = 10.0) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]


def _square_room(
    room_id: str = "room_1",
    wall_ids: list[str] | None = None,
    x: float = 0.0,
    y: float = 0.0,
    size: float = 10.0,
) -> Room:
    wall_ids = wall_ids or ["wall_s", "wall_e", "wall_n", "wall_w"]
    return Room(
        id=room_id,
        name="Test Room",
        room_type="bedroom",
        vertices=_square_vertices(x, y, size),
        area_sqft=size * size,
        boundary_wall_ids=wall_ids,
    )


def _square_walls(room_id: str = "room_1", prefix: str = "wall", x: float = 0.0, y: float = 0.0, size: float = 10.0) -> list[Wall]:
    return [
        Wall(id=f"{prefix}_s", start=(x, y), end=(x + size, y), bounds_rooms=[room_id]),
        Wall(id=f"{prefix}_e", start=(x + size, y), end=(x + size, y + size), bounds_rooms=[room_id]),
        Wall(id=f"{prefix}_n", start=(x + size, y + size), end=(x, y + size), bounds_rooms=[room_id]),
        Wall(id=f"{prefix}_w", start=(x, y + size), end=(x, y), bounds_rooms=[room_id]),
    ]


def _minimal_layout(**overrides) -> Layout:
    room = _square_room()
    walls = _square_walls()
    kwargs = dict(plan_id="plan_test1", rooms=[room], walls=walls, extent_x_ft=10.0, extent_y_ft=10.0)
    kwargs.update(overrides)
    return Layout(**kwargs)


# ── Room tests ────────────────────────────────────────────────────────────


def test_room_valid_rectangular_passes():
    room = _square_room()
    assert room.area_sqft == 100.0


def test_room_valid_l_shaped_passes():
    vertices = [(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10), (0, 0)]
    room = Room(
        id="room_lshape",
        name="L Room",
        room_type="living",
        vertices=vertices,
        area_sqft=75.0,
        boundary_wall_ids=["wall_a", "wall_b", "wall_c"],
    )
    assert room.area_sqft == 75.0


def test_room_non_closed_vertices_raises():
    vertices = [(0, 0), (10, 0), (10, 10), (0, 10)]  # last != first
    with pytest.raises(ValidationError, match="closed"):
        Room(
            id="room_1", name="Room", room_type="bedroom",
            vertices=vertices, area_sqft=100.0,
            boundary_wall_ids=["wall_a", "wall_b", "wall_c"],
        )


def test_room_cw_ordered_vertices_raises():
    vertices = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]  # clockwise
    with pytest.raises(ValidationError, match="counter-clockwise"):
        Room(
            id="room_1", name="Room", room_type="bedroom",
            vertices=vertices, area_sqft=100.0,
            boundary_wall_ids=["wall_a", "wall_b", "wall_c"],
        )


def test_room_area_mismatch_raises():
    with pytest.raises(ValidationError, match="does not match"):
        Room(
            id="room_1", name="Room", room_type="bedroom",
            vertices=_square_vertices(), area_sqft=200.0,
            boundary_wall_ids=["wall_a", "wall_b", "wall_c"],
        )


def test_room_empty_boundary_wall_ids_raises():
    with pytest.raises(ValidationError):
        Room(
            id="room_1", name="Room", room_type="bedroom",
            vertices=_square_vertices(), area_sqft=100.0,
            boundary_wall_ids=[],
        )


def test_room_duplicate_boundary_wall_ids_raises():
    with pytest.raises(ValidationError, match="duplicate"):
        Room(
            id="room_1", name="Room", room_type="bedroom",
            vertices=_square_vertices(), area_sqft=100.0,
            boundary_wall_ids=["wall_a", "wall_a", "wall_b"],
        )


def test_room_negative_vertex_coordinate_raises():
    vertices = [(-1, 0), (10, 0), (10, 10), (-1, 10), (-1, 0)]
    with pytest.raises(ValidationError, match="non-negative"):
        Room(
            id="room_1", name="Room", room_type="bedroom",
            vertices=vertices, area_sqft=110.0,
            boundary_wall_ids=["wall_a", "wall_b", "wall_c"],
        )


def test_room_invalid_id_pattern_raises():
    with pytest.raises(ValidationError):
        Room(
            id="Room1", name="Room", room_type="bedroom",
            vertices=_square_vertices(), area_sqft=100.0,
            boundary_wall_ids=["wall_a", "wall_b", "wall_c"],
        )


# ── Wall tests ────────────────────────────────────────────────────────────


def test_wall_valid_passes():
    wall = Wall(id="wall_1", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=["room_1"])
    assert wall.length_ft == 10.0


def test_wall_zero_length_raises():
    with pytest.raises(ValidationError, match="differ"):
        Wall(id="wall_1", start=(5.0, 5.0), end=(5.0, 5.0), bounds_rooms=[])


def test_wall_negative_coordinate_raises():
    with pytest.raises(ValidationError, match="non-negative"):
        Wall(id="wall_1", start=(-5.0, 0.0), end=(10.0, 0.0), bounds_rooms=[])


def test_wall_length_ft_computed_property():
    wall = Wall(id="wall_diag", start=(0.0, 0.0), end=(10.0, 10.0), bounds_rooms=[])
    assert math.isclose(wall.length_ft, math.sqrt(2) * 10, rel_tol=1e-4)


def test_wall_bounds_rooms_max_two_raises():
    with pytest.raises(ValidationError):
        Wall(
            id="wall_1", start=(0.0, 0.0), end=(10.0, 0.0),
            bounds_rooms=["room_1", "room_2", "room_3"],
        )


# ── Opening tests ─────────────────────────────────────────────────────────


def test_opening_valid_door_passes():
    opening = Opening(
        id="opening_door1", opening_type="door", wall_id="wall_1",
        offset_ft=1.0, width_ft=3.0,
    )
    assert opening.width_ft == 3.0


def test_opening_invalid_id_pattern_raises():
    with pytest.raises(ValidationError):
        Opening(
            id="Door1", opening_type="door", wall_id="wall_1",
            offset_ft=1.0, width_ft=3.0,
        )


def test_opening_negative_offset_raises():
    with pytest.raises(ValidationError):
        Opening(
            id="opening_door1", opening_type="door", wall_id="wall_1",
            offset_ft=-1.0, width_ft=3.0,
        )


def test_opening_zero_width_raises():
    with pytest.raises(ValidationError):
        Opening(
            id="opening_door1", opening_type="door", wall_id="wall_1",
            offset_ft=1.0, width_ft=0.0,
        )


# ── Layout-level cross-reference tests ───────────────────────────────────


def test_layout_room_references_nonexistent_wall_raises():
    room = _square_room(wall_ids=["wall_s", "wall_e", "wall_missing"])
    walls = _square_walls()
    with pytest.raises(ValidationError, match="non-existent wall"):
        Layout(plan_id="plan_test1", rooms=[room], walls=walls, extent_x_ft=10.0, extent_y_ft=10.0)


def test_layout_opening_references_nonexistent_wall_raises():
    room = _square_room()
    walls = _square_walls()
    opening = Opening(
        id="opening_door1", opening_type="door", wall_id="wall_missing",
        offset_ft=1.0, width_ft=3.0,
    )
    with pytest.raises(ValidationError, match="non-existent wall"):
        Layout(
            plan_id="plan_test1", rooms=[room], walls=walls, openings=[opening],
            extent_x_ft=10.0, extent_y_ft=10.0,
        )


def test_layout_exit_references_nonexistent_opening_raises():
    room = _square_room()
    walls = _square_walls()
    exit_ = Exit(id="exit_front", opening_id="opening_missing", exit_type="primary")
    with pytest.raises(ValidationError, match="non-existent opening|does not exist"):
        Layout(
            plan_id="plan_test1", rooms=[room], walls=walls, exits=[exit_],
            extent_x_ft=10.0, extent_y_ft=10.0,
        )


def test_layout_exit_references_window_opening_raises():
    room = _square_room()
    walls = _square_walls()
    window = Opening(
        id="opening_window1", opening_type="window", wall_id="wall_s",
        offset_ft=1.0, width_ft=3.0,
    )
    exit_ = Exit(id="exit_front", opening_id="opening_window1", exit_type="primary")
    with pytest.raises(ValidationError, match="not a door"):
        Layout(
            plan_id="plan_test1", rooms=[room], walls=walls,
            openings=[window], exits=[exit_],
            extent_x_ft=10.0, extent_y_ft=10.0,
        )


def test_layout_duplicate_room_ids_raises():
    room_a = _square_room(room_id="room_1", wall_ids=["wall_s", "wall_e", "wall_n"])
    room_b = _square_room(room_id="room_1", wall_ids=["wall_s", "wall_e", "wall_w"])
    walls = _square_walls()
    with pytest.raises(ValidationError, match="duplicate room"):
        Layout(plan_id="plan_test1", rooms=[room_a, room_b], walls=walls, extent_x_ft=10.0, extent_y_ft=10.0)


def test_layout_duplicate_wall_ids_raises():
    room = _square_room()
    walls = _square_walls() + [Wall(id="wall_s", start=(0.0, 0.0), end=(5.0, 0.0), bounds_rooms=[])]
    with pytest.raises(ValidationError, match="duplicate wall"):
        Layout(plan_id="plan_test1", rooms=[room], walls=walls, extent_x_ft=10.0, extent_y_ft=10.0)


def test_layout_extent_smaller_than_max_vertex_raises():
    with pytest.raises(ValidationError, match="extent_x_ft"):
        _minimal_layout(extent_x_ft=5.0)


# ── Layout-level positive tests ───────────────────────────────────────────


def test_layout_minimal_valid_passes():
    layout = _minimal_layout()
    assert len(layout.rooms) == 1
    assert len(layout.walls) == 4
    assert layout.openings == []


def test_layout_three_rooms_with_shared_walls_passes():
    room_1 = Room(
        id="room_1", name="Room 1", room_type="bedroom",
        vertices=_square_vertices(0, 0, 10), area_sqft=100.0,
        boundary_wall_ids=["wall_1_s", "wall_12", "wall_1_n", "wall_1_w"],
    )
    room_2 = Room(
        id="room_2", name="Room 2", room_type="bedroom",
        vertices=_square_vertices(10, 0, 10), area_sqft=100.0,
        boundary_wall_ids=["wall_2_s", "wall_23", "wall_2_n", "wall_12"],
    )
    room_3 = Room(
        id="room_3", name="Room 3", room_type="bedroom",
        vertices=_square_vertices(20, 0, 10), area_sqft=100.0,
        boundary_wall_ids=["wall_3_s", "wall_3_e", "wall_3_n", "wall_23"],
    )
    walls = [
        Wall(id="wall_1_w", start=(0, 10), end=(0, 0), bounds_rooms=["room_1"]),
        Wall(id="wall_1_s", start=(0, 0), end=(10, 0), bounds_rooms=["room_1"]),
        Wall(id="wall_12", start=(10, 0), end=(10, 10), bounds_rooms=["room_1", "room_2"]),
        Wall(id="wall_1_n", start=(10, 10), end=(0, 10), bounds_rooms=["room_1"]),
        Wall(id="wall_2_s", start=(10, 0), end=(20, 0), bounds_rooms=["room_2"]),
        Wall(id="wall_23", start=(20, 0), end=(20, 10), bounds_rooms=["room_2", "room_3"]),
        Wall(id="wall_2_n", start=(20, 10), end=(10, 10), bounds_rooms=["room_2"]),
        Wall(id="wall_3_s", start=(20, 0), end=(30, 0), bounds_rooms=["room_3"]),
        Wall(id="wall_3_e", start=(30, 0), end=(30, 10), bounds_rooms=["room_3"]),
        Wall(id="wall_3_n", start=(30, 10), end=(20, 10), bounds_rooms=["room_3"]),
    ]
    layout = Layout(
        plan_id="plan_rowhouse", rooms=[room_1, room_2, room_3], walls=walls,
        extent_x_ft=30.0, extent_y_ft=10.0,
    )
    assert len(layout.rooms) == 3
    assert len(layout.walls) == 10


def test_layout_roundtrip_model_dump():
    layout = _minimal_layout()
    dumped = layout.model_dump()
    recovered = Layout.model_validate(dumped)
    assert recovered == layout


def test_layout_roundtrip_json():
    layout = _minimal_layout()
    dumped_json = layout.model_dump_json()
    recovered = Layout.model_validate_json(dumped_json)
    assert recovered == layout


# ── FloorPlanSpec tests ────────────────────────────────────────────────────


def test_floorplanspec_valid_passes():
    req = RoomRequirement(name="Master Bedroom", room_type="bedroom")
    spec = FloorPlanSpec(
        spec_id="spec_test1", room_requirements=[req],
        original_nl="I want a master bedroom",
    )
    assert spec.n_candidates == 8


def test_floorplanspec_empty_room_requirements_raises():
    with pytest.raises(ValidationError):
        FloorPlanSpec(spec_id="spec_test1", room_requirements=[], original_nl="empty plan")


def test_floorplanspec_n_candidates_over_max_raises():
    req = RoomRequirement(name="Kitchen", room_type="kitchen")
    with pytest.raises(ValidationError):
        FloorPlanSpec(
            spec_id="spec_test1", room_requirements=[req],
            original_nl="a kitchen", n_candidates=33,
        )


def test_floorplanspec_default_jurisdiction_is_irc_2021():
    constraints = SiteConstraints()
    assert constraints.jurisdiction == "IRC-2021"


def test_floorplanspec_north_bearing_360_raises():
    with pytest.raises(ValidationError):
        SiteConstraints(north_bearing_deg=360.0)


# ── LayoutAuditManifest tests ───────────────────────────────────────────────


def _make_verifier_result(**overrides) -> VerifierResult:
    kwargs = dict(
        verifier_name="layer_a_geometry", passed=True,
        checks_run=["no_overlap"], elapsed_ms=12.5,
    )
    kwargs.update(overrides)
    return VerifierResult(**kwargs)


def test_auditmanifest_valid_passes():
    manifest = LayoutAuditManifest(
        generator="stub-v1", generator_version="2026-07-05",
        spec_hash="sha256:abc123", verifier_results=[_make_verifier_result()],
        generated_at=datetime.now(timezone.utc), selection_rank=0,
        total_candidates=8, survived_layer_a=8, survived_layer_c=6,
    )
    assert manifest.selection_rank == 0


def test_auditmanifest_attach_to_layout_roundtrips():
    manifest = LayoutAuditManifest(
        generator="stub-v1", generator_version="2026-07-05",
        spec_hash="sha256:abc123", verifier_results=[_make_verifier_result()],
        generated_at=datetime.now(timezone.utc), selection_rank=0,
        total_candidates=8, survived_layer_a=8, survived_layer_c=6,
    )
    layout = _minimal_layout(audit=manifest)
    recovered = Layout.model_validate(layout.model_dump())
    assert recovered.audit == manifest


def test_verifierresult_score_out_of_range_raises():
    with pytest.raises(ValidationError):
        _make_verifier_result(score=1.5)


# ── GenerationFailure tests ─────────────────────────────────────────────────


def test_generationfailure_message():
    err = GenerationFailure(
        spec_id="spec_test1", total_candidates=8,
        layer_a_failures=2, layer_c_failures=3, details=[],
    )
    assert "spec_test1" in str(err)
    assert "2/8 failed Layer A" in str(err)
    assert "3/8 failed Layer C" in str(err)


def test_generationfailure_attributes_accessible():
    details = [{"check": "no_overlap", "detail": "rooms overlap"}]
    err = GenerationFailure(
        spec_id="spec_test1", total_candidates=8,
        layer_a_failures=2, layer_c_failures=3, details=details,
    )
    assert err.spec_id == "spec_test1"
    assert err.total_candidates == 8
    assert err.layer_a_failures == 2
    assert err.layer_c_failures == 3
    assert err.details == details


# ── Helper tests ─────────────────────────────────────────────────────────


def test_shoelace_area_square():
    vertices = _square_vertices(0, 0, 10)
    assert math.isclose(_shoelace_area(vertices), 100.0)


def test_shoelace_area_sign_flips_for_cw_ordering():
    ccw = _square_vertices(0, 0, 10)
    cw = list(reversed(ccw))
    assert math.isclose(_shoelace_area(cw), -_shoelace_area(ccw))


def test_shoelace_area_triangle():
    vertices = [(0.0, 0.0), (4.0, 0.0), (0.0, 3.0), (0.0, 0.0)]
    assert math.isclose(_shoelace_area(vertices), 6.0)
