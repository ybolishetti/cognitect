"""Tests for engine/verifiers/layer_a — the geometry hard gate (Architecture C, DRAFT 2).

Covers layer_a_helpers.py (pure Shapely construction/graph helpers), each of
the 8 layer_a.py check functions in isolation (one hand-crafted invalid
Layout per check, plus a positive case), and the verify_layer_a orchestrator
(aggregation, determinism, VerifierResult shape).
"""

from __future__ import annotations

import pytest
from shapely.geometry.polygon import LinearRing

from engine.layout import Layout, Room, StructuralGrid, VerifierResult, Wall, Opening
from engine.verifiers import (
    check_exterior_envelope_is_single_closed_polygon,
    check_no_negative_room_areas,
    check_openings_do_not_overlap_on_same_wall,
    check_openings_lie_on_walls,
    check_room_polygons_match_boundary_walls,
    check_rooms_do_not_overlap,
    check_walls_form_closed_room_boundaries,
    check_walls_meet_at_endpoints,
    verify_layer_a,
)
from engine.verifiers.layer_a_helpers import (
    order_wall_ids_into_ring,
    room_polygon,
    wall_endpoint_graph,
    wall_linestring,
)

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


def _square_walls(
    room_id: str = "room_1", prefix: str = "wall", x: float = 0.0, y: float = 0.0, size: float = 10.0
) -> list[Wall]:
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


def _rect_vertices(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def _rect_room(
    room_id: str = "room_1",
    prefix: str = "wall",
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 10.0,
    y1: float = 10.0,
) -> Room:
    return Room(
        id=room_id,
        name="Rect Room",
        room_type="bedroom",
        vertices=_rect_vertices(x0, y0, x1, y1),
        area_sqft=(x1 - x0) * (y1 - y0),
        boundary_wall_ids=[f"wall_{prefix}_s", f"wall_{prefix}_e", f"wall_{prefix}_n", f"wall_{prefix}_w"],
    )


def _rect_walls(
    room_id: str = "room_1",
    prefix: str = "wall",
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 10.0,
    y1: float = 10.0,
) -> list[Wall]:
    return [
        Wall(id=f"wall_{prefix}_s", start=(x0, y0), end=(x1, y0), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_e", start=(x1, y0), end=(x1, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_n", start=(x1, y1), end=(x0, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_w", start=(x0, y1), end=(x0, y0), bounds_rooms=[room_id]),
    ]


def _bowtie_room_and_walls(room_id: str = "room_bowtie") -> tuple[Room, list[Wall]]:
    # Self-intersecting pentagon: shoelace area is +62 (schema-valid, CCW,
    # closed) but edges (10,10)->(0,4) and (4,10)->(0,0) cross, so this
    # slips past Room's own hand-rolled shoelace validation while Shapely's
    # real GEOS validity check correctly flags it.
    verts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 4.0), (4.0, 10.0), (0.0, 0.0)]
    wall_ids = ["wall_bt_1", "wall_bt_2", "wall_bt_3", "wall_bt_4", "wall_bt_5"]
    room = Room(
        id=room_id, name="Bowtie", room_type="other",
        vertices=verts, area_sqft=62.0, boundary_wall_ids=wall_ids,
    )
    walls = [
        Wall(id=wid, start=verts[i], end=verts[i + 1], bounds_rooms=[room_id])
        for i, wid in enumerate(wall_ids)
    ]
    return room, walls


def _multi_violation_layout() -> Layout:
    """Two independent Layer A violations from two different, non-adjacent
    checks: room_1/room_2 overlap in area; room_3 has an unrelated dangling
    stub wall. Used to prove verify_layer_a doesn't short-circuit."""
    room_1 = _rect_room(room_id="room_1", prefix="w1", x0=0, y0=0, x1=10, y1=10)
    room_2 = _rect_room(room_id="room_2", prefix="w2", x0=5, y0=5, x1=15, y1=15)
    room_3 = _rect_room(room_id="room_3", prefix="w3", x0=100, y0=100, x1=110, y1=110)
    walls = (
        _rect_walls(room_id="room_1", prefix="w1", x0=0, y0=0, x1=10, y1=10)
        + _rect_walls(room_id="room_2", prefix="w2", x0=5, y0=5, x1=15, y1=15)
        + _rect_walls(room_id="room_3", prefix="w3", x0=100, y0=100, x1=110, y1=110)
        + [Wall(id="wall_stub", start=(110.0, 110.0), end=(115.0, 115.0), bounds_rooms=["room_3"])]
    )
    return Layout(
        plan_id="plan_multi_violation", rooms=[room_1, room_2, room_3], walls=walls,
        extent_x_ft=110.0, extent_y_ft=110.0,
    )


# ── Helper-level tests (layer_a_helpers.py) ─────────────────────────────────


def test_room_polygon_area_matches_room_area_sqft():
    room = _square_room()
    assert room_polygon(room).area == pytest.approx(100.0)


def test_wall_linestring_length_matches_wall_length_ft():
    wall = Wall(id="wall_1", start=(0.0, 0.0), end=(3.0, 4.0), bounds_rooms=[])
    assert wall_linestring(wall).length == pytest.approx(wall.length_ft)


def test_wall_endpoint_graph_groups_exact_duplicates():
    wall_a = Wall(id="wall_a", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=[])
    wall_b = Wall(id="wall_b", start=(10.0, 0.0), end=(10.0, 10.0), bounds_rooms=[])
    graph = wall_endpoint_graph([wall_a, wall_b], round_decimals=4)
    assert len(graph[(10.0, 0.0)]) == 2


def test_wall_endpoint_graph_does_not_merge_points_beyond_rounding_tolerance():
    wall_a = Wall(id="wall_a", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=[])
    wall_b = Wall(id="wall_b", start=(10.005, 0.0), end=(20.0, 0.0), bounds_rooms=[])
    graph = wall_endpoint_graph([wall_a, wall_b], round_decimals=4)
    assert len(graph[(10.0, 0.0)]) == 1
    assert len(graph[(10.005, 0.0)]) == 1


def test_order_wall_ids_into_ring_square_returns_ccw_ring():
    walls_by_id = {w.id: w for w in _square_walls()}
    ring, reason = order_wall_ids_into_ring(
        ["wall_s", "wall_e", "wall_n", "wall_w"], walls_by_id, round_decimals=4
    )
    assert reason is None
    assert ring is not None
    assert ring[0] == ring[-1]
    assert LinearRing(ring).is_ccw
    assert set(ring[:-1]) == {(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)}


def test_order_wall_ids_into_ring_open_chain_returns_not_simple_cycle():
    walls_by_id = {w.id: w for w in _square_walls()}
    ring, reason = order_wall_ids_into_ring(["wall_s", "wall_e", "wall_n"], walls_by_id, round_decimals=4)
    assert ring is None
    assert reason == "not_simple_cycle"


def test_order_wall_ids_into_ring_missing_wall_id_returns_missing_wall():
    walls_by_id = {w.id: w for w in _square_walls()}
    ring, reason = order_wall_ids_into_ring(["wall_s", "wall_ghost"], walls_by_id, round_decimals=4)
    assert ring is None
    assert reason == "missing_wall"


def test_order_wall_ids_into_ring_two_disjoint_triangles_returns_multiple_cycles():
    triangle_1 = [
        Wall(id="wall_1a", start=(0.0, 0.0), end=(4.0, 0.0), bounds_rooms=[]),
        Wall(id="wall_1b", start=(4.0, 0.0), end=(0.0, 3.0), bounds_rooms=[]),
        Wall(id="wall_1c", start=(0.0, 3.0), end=(0.0, 0.0), bounds_rooms=[]),
    ]
    triangle_2 = [
        Wall(id="wall_2a", start=(20.0, 20.0), end=(24.0, 20.0), bounds_rooms=[]),
        Wall(id="wall_2b", start=(24.0, 20.0), end=(20.0, 23.0), bounds_rooms=[]),
        Wall(id="wall_2c", start=(20.0, 23.0), end=(20.0, 20.0), bounds_rooms=[]),
    ]
    all_walls = triangle_1 + triangle_2
    walls_by_id = {w.id: w for w in all_walls}
    ring, reason = order_wall_ids_into_ring([w.id for w in all_walls], walls_by_id, round_decimals=4)
    assert ring is None
    assert reason == "multiple_cycles"


# ── check_no_negative_room_areas ─────────────────────────────────────────────


def test_no_negative_room_areas_passes_for_valid_square():
    layout = _minimal_layout()
    assert check_no_negative_room_areas(layout) == []


def test_no_negative_room_areas_fails_for_self_intersecting_bowtie():
    room, walls = _bowtie_room_and_walls()
    layout = Layout(plan_id="plan_bowtie", rooms=[room], walls=walls, extent_x_ft=10.0, extent_y_ft=10.0)
    failures = check_no_negative_room_areas(layout)
    assert len(failures) == 1
    assert failures[0]["check"] == "no_negative_room_areas"
    assert failures[0]["entity_ids"] == ["room_bowtie"]


# ── check_rooms_do_not_overlap ────────────────────────────────────────────────


def test_rooms_do_not_overlap_passes_for_adjacent_shared_edge_rooms():
    room_1 = _rect_room(room_id="room_1", prefix="w1", x0=0, y0=0, x1=10, y1=10)
    room_2 = _rect_room(room_id="room_2", prefix="w2", x0=10, y0=0, x1=20, y1=10)
    walls = _rect_walls("room_1", "w1", 0, 0, 10, 10) + _rect_walls("room_2", "w2", 10, 0, 20, 10)
    layout = Layout(plan_id="plan_adjacent", rooms=[room_1, room_2], walls=walls, extent_x_ft=20.0, extent_y_ft=10.0)
    assert check_rooms_do_not_overlap(layout) == []


def test_rooms_do_not_overlap_fails_for_true_area_overlap():
    room_1 = _rect_room(room_id="room_1", prefix="w1", x0=0, y0=0, x1=10, y1=10)
    room_2 = _rect_room(room_id="room_2", prefix="w2", x0=5, y0=5, x1=15, y1=15)
    walls = _rect_walls("room_1", "w1", 0, 0, 10, 10) + _rect_walls("room_2", "w2", 5, 5, 15, 15)
    layout = Layout(plan_id="plan_overlap", rooms=[room_1, room_2], walls=walls, extent_x_ft=15.0, extent_y_ft=15.0)
    failures = check_rooms_do_not_overlap(layout)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["room_1", "room_2"]


# ── check_openings_lie_on_walls ───────────────────────────────────────────────


def test_openings_lie_on_walls_passes_for_valid_door():
    opening = Opening(id="opening_door1", opening_type="door", wall_id="wall_s", offset_ft=2.0, width_ft=3.0)
    layout = _minimal_layout(openings=[opening])
    assert check_openings_lie_on_walls(layout) == []


def test_openings_lie_on_walls_fails_when_true_length_exceeded():
    wall = Wall(id="wall_1", start=(0.0, 0.0), end=(5.0, 0.0), bounds_rooms=[])
    opening = Opening(id="opening_door1", opening_type="door", wall_id="wall_1", offset_ft=4.0, width_ft=1.05)
    # offset_ft + width_ft (5.05) exceeds wall_1's length (5.0) by more than
    # Layer A's tolerance, but is small enough that normal Layout(...)
    # construction would reject it at the schema level before Layer A ever
    # runs — model_construct bypasses that validator so we can exercise
    # Layer A's own (defense-in-depth) check in isolation.
    layout = Layout.model_construct(
        plan_id="plan_bypass",
        schema_version="1.0",
        rooms=[],
        walls=[wall],
        openings=[opening],
        structural_grid=StructuralGrid(),
        exits=[],
        extent_x_ft=5.0,
        extent_y_ft=5.0,
        audit=None,
        metadata={},
    )
    failures = check_openings_lie_on_walls(layout)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["opening_door1", "wall_1"]


# ── check_openings_do_not_overlap_on_same_wall ────────────────────────────────


def test_openings_do_not_overlap_on_same_wall_passes_for_non_overlapping_openings():
    wall = Wall(id="wall_1", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=[])
    opening_a = Opening(id="opening_a", opening_type="door", wall_id="wall_1", offset_ft=1.0, width_ft=2.0)
    opening_b = Opening(id="opening_b", opening_type="door", wall_id="wall_1", offset_ft=4.0, width_ft=2.0)
    layout = Layout(
        plan_id="plan_openings", rooms=[], walls=[wall], openings=[opening_a, opening_b],
        extent_x_ft=10.0, extent_y_ft=10.0,
    )
    assert check_openings_do_not_overlap_on_same_wall(layout) == []


def test_openings_do_not_overlap_on_same_wall_fails_for_overlapping_offsets():
    wall = Wall(id="wall_1", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=[])
    opening_a = Opening(id="opening_a", opening_type="door", wall_id="wall_1", offset_ft=2.0, width_ft=3.0)
    opening_b = Opening(id="opening_b", opening_type="door", wall_id="wall_1", offset_ft=4.0, width_ft=3.0)
    layout = Layout(
        plan_id="plan_openings_overlap", rooms=[], walls=[wall], openings=[opening_a, opening_b],
        extent_x_ft=10.0, extent_y_ft=10.0,
    )
    failures = check_openings_do_not_overlap_on_same_wall(layout)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["opening_a", "opening_b"]


# ── check_walls_meet_at_endpoints ─────────────────────────────────────────────


def test_walls_meet_at_endpoints_passes_for_closed_square_room():
    layout = _minimal_layout()
    assert check_walls_meet_at_endpoints(layout) == []


def test_walls_meet_at_endpoints_allows_freestanding_dangling_wall():
    wall_free = Wall(id="wall_free", start=(20.0, 20.0), end=(25.0, 20.0), bounds_rooms=[])
    layout = _minimal_layout(walls=_square_walls() + [wall_free])
    assert check_walls_meet_at_endpoints(layout) == []


def test_walls_meet_at_endpoints_fails_for_dangling_exterior_wall():
    wall_stub = Wall(id="wall_stub", start=(10.0, 10.0), end=(15.0, 15.0), bounds_rooms=["room_1"])
    layout = _minimal_layout(walls=_square_walls() + [wall_stub])
    failures = check_walls_meet_at_endpoints(layout)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["wall_stub"]


# ── check_walls_form_closed_room_boundaries ───────────────────────────────────


def test_walls_form_closed_room_boundaries_passes_for_square():
    layout = _minimal_layout()
    assert check_walls_form_closed_room_boundaries(layout) == []


def test_walls_form_closed_room_boundaries_fails_for_open_chain():
    room = _square_room(wall_ids=["wall_s", "wall_e", "wall_n"])
    layout = Layout(plan_id="plan_test1", rooms=[room], walls=_square_walls(), extent_x_ft=10.0, extent_y_ft=10.0)
    failures = check_walls_form_closed_room_boundaries(layout)
    assert len(failures) == 1
    assert "not_simple_cycle" in failures[0]["detail"]


# ── check_room_polygons_match_boundary_walls ──────────────────────────────────


def test_room_polygons_match_boundary_walls_passes_for_square():
    layout = _minimal_layout()
    assert check_room_polygons_match_boundary_walls(layout) == []


def test_room_polygons_match_boundary_walls_fails_for_shape_mismatch():
    room = _square_room()  # 10x10 square, area 100
    # Boundary walls instead trace a 10x5 rectangle sharing the same corner.
    walls = [
        Wall(id="wall_s", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=["room_1"]),
        Wall(id="wall_e", start=(10.0, 0.0), end=(10.0, 5.0), bounds_rooms=["room_1"]),
        Wall(id="wall_n", start=(10.0, 5.0), end=(0.0, 5.0), bounds_rooms=["room_1"]),
        Wall(id="wall_w", start=(0.0, 5.0), end=(0.0, 0.0), bounds_rooms=["room_1"]),
    ]
    layout = Layout(plan_id="plan_mismatch", rooms=[room], walls=walls, extent_x_ft=10.0, extent_y_ft=10.0)
    failures = check_room_polygons_match_boundary_walls(layout)
    assert len(failures) == 1
    assert failures[0]["check"] == "room_polygons_match_boundary_walls"


# ── check_exterior_envelope_is_single_closed_polygon ─────────────────────────


def test_exterior_envelope_passes_for_single_room():
    layout = _minimal_layout()
    assert check_exterior_envelope_is_single_closed_polygon(layout) == []


def test_exterior_envelope_fails_for_disconnected_islands():
    room_1 = _rect_room(room_id="room_1", prefix="w1", x0=0, y0=0, x1=10, y1=10)
    room_2 = _rect_room(room_id="room_2", prefix="w2", x0=50, y0=50, x1=60, y1=60)
    walls = _rect_walls("room_1", "w1", 0, 0, 10, 10) + _rect_walls("room_2", "w2", 50, 50, 60, 60)
    layout = Layout(plan_id="plan_islands", rooms=[room_1, room_2], walls=walls, extent_x_ft=60.0, extent_y_ft=60.0)
    failures = check_exterior_envelope_is_single_closed_polygon(layout)
    assert len(failures) == 1
    assert "2" in failures[0]["detail"]
    assert failures[0]["entity_ids"] == ["room_1", "room_2"]


def test_exterior_envelope_fails_for_enclosed_hole():
    room_bottom = _rect_room(room_id="room_bottom", prefix="wb", x0=0, y0=0, x1=30, y1=10)
    room_top = _rect_room(room_id="room_top", prefix="wt", x0=0, y0=20, x1=30, y1=30)
    room_left = _rect_room(room_id="room_left", prefix="wl", x0=0, y0=10, x1=10, y1=20)
    room_right = _rect_room(room_id="room_right", prefix="wr", x0=20, y0=10, x1=30, y1=20)
    walls = (
        _rect_walls("room_bottom", "wb", 0, 0, 30, 10)
        + _rect_walls("room_top", "wt", 0, 20, 30, 30)
        + _rect_walls("room_left", "wl", 0, 10, 10, 20)
        + _rect_walls("room_right", "wr", 20, 10, 30, 20)
    )
    layout = Layout(
        plan_id="plan_hole", rooms=[room_bottom, room_top, room_left, room_right], walls=walls,
        extent_x_ft=30.0, extent_y_ft=30.0,
    )
    failures = check_exterior_envelope_is_single_closed_polygon(layout)
    assert len(failures) == 1
    assert "hole" in failures[0]["detail"]
    assert failures[0]["entity_ids"] == ["room_bottom", "room_left", "room_right", "room_top"]


def test_exterior_envelope_passes_when_hole_is_filled():
    room_bottom = _rect_room(room_id="room_bottom", prefix="wb", x0=0, y0=0, x1=30, y1=10)
    room_top = _rect_room(room_id="room_top", prefix="wt", x0=0, y0=20, x1=30, y1=30)
    room_left = _rect_room(room_id="room_left", prefix="wl", x0=0, y0=10, x1=10, y1=20)
    room_right = _rect_room(room_id="room_right", prefix="wr", x0=20, y0=10, x1=30, y1=20)
    room_center = _rect_room(room_id="room_center", prefix="wc", x0=10, y0=10, x1=20, y1=20)
    walls = (
        _rect_walls("room_bottom", "wb", 0, 0, 30, 10)
        + _rect_walls("room_top", "wt", 0, 20, 30, 30)
        + _rect_walls("room_left", "wl", 0, 10, 10, 20)
        + _rect_walls("room_right", "wr", 20, 10, 30, 20)
        + _rect_walls("room_center", "wc", 10, 10, 20, 20)
    )
    layout = Layout(
        plan_id="plan_hole_filled",
        rooms=[room_bottom, room_top, room_left, room_right, room_center],
        walls=walls, extent_x_ft=30.0, extent_y_ft=30.0,
    )
    assert check_exterior_envelope_is_single_closed_polygon(layout) == []


# ── verify_layer_a orchestrator / cross-cutting ──────────────────────────────


def test_verify_layer_a_passes_for_minimal_valid_layout():
    result = verify_layer_a(_minimal_layout())
    assert result.passed is True
    assert result.failures == []
    assert result.checks_run == [
        "no_negative_room_areas",
        "rooms_do_not_overlap",
        "openings_lie_on_walls",
        "openings_do_not_overlap_on_same_wall",
        "walls_meet_at_endpoints",
        "walls_form_closed_room_boundaries",
        "room_polygons_match_boundary_walls",
        "exterior_envelope_is_single_closed_polygon",
    ]
    assert result.warnings == []
    assert result.score is None
    assert result.elapsed_ms >= 0


def test_verify_layer_a_runs_all_checks_regardless_of_earlier_failures():
    result = verify_layer_a(_multi_violation_layout())
    assert result.passed is False
    check_names = {f["check"] for f in result.failures}
    assert "rooms_do_not_overlap" in check_names
    assert "walls_meet_at_endpoints" in check_names


def test_verify_layer_a_failures_are_sorted_deterministically():
    layout = _multi_violation_layout()
    result_1 = verify_layer_a(layout)
    result_2 = verify_layer_a(layout)
    assert result_1.failures == result_2.failures


def test_verify_layer_a_failure_ordering_uses_detail_as_tiebreaker():
    wall_dangle = Wall(id="wall_dangle_both", start=(50.0, 50.0), end=(55.0, 55.0), bounds_rooms=["room_1"])
    layout = _minimal_layout(walls=_square_walls() + [wall_dangle])
    result = verify_layer_a(layout)
    relevant = [f for f in result.failures if f["check"] == "walls_meet_at_endpoints"]
    assert len(relevant) == 2
    assert all(f["entity_ids"] == ["wall_dangle_both"] for f in relevant)
    assert [f["detail"] for f in relevant] == sorted(f["detail"] for f in relevant)


def test_verify_layer_a_result_is_a_valid_verifierresult_instance():
    result = verify_layer_a(_minimal_layout())
    assert isinstance(result, VerifierResult)
    recovered = VerifierResult.model_validate(result.model_dump())
    assert recovered == result
