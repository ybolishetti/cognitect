"""Tests for engine/verifiers/layer_b -- the structural sanity advisory
scorer (Architecture C, DRAFT 6).

Layer B never rejects: `passed` is always True, and issues surface only via
`warnings`. These tests exercise each `_check_*` sub-scorer in isolation,
then the `verify_layer_b` orchestrator end to end.
"""

from __future__ import annotations

import time

from engine.layout import Layout, Room, Wall
from engine.verifiers.layer_b import (
    _check_column_free_spans,
    _check_long_walls,
    _check_room_aspect_ratios,
    _check_wet_room_stacking,
    verify_layer_b,
)

# ── Fixture helpers (local to this file, per repo convention) ──────────────


def _rect_vertices(
    x0: float = 0.0, y0: float = 0.0, x1: float = 10.0, y1: float = 10.0
) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def make_room(
    room_id: str,
    room_type: str,
    wall_ids: list[str],
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 10.0,
    y1: float = 10.0,
    name: str | None = None,
) -> Room:
    return Room(
        id=room_id,
        name=name or room_id,
        room_type=room_type,
        vertices=_rect_vertices(x0, y0, x1, y1),
        area_sqft=(x1 - x0) * (y1 - y0),
        boundary_wall_ids=wall_ids,
    )


def make_rect_walls(
    room_id: str,
    prefix: str,
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 10.0,
    y1: float = 10.0,
) -> list[Wall]:
    """Four exterior walls (bounds_rooms=[room_id]) bounding a rectangle."""
    return [
        Wall(id=f"wall_{prefix}_s", start=(x0, y0), end=(x1, y0), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_e", start=(x1, y0), end=(x1, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_n", start=(x1, y1), end=(x0, y1), bounds_rooms=[room_id]),
        Wall(id=f"wall_{prefix}_w", start=(x0, y1), end=(x0, y0), bounds_rooms=[room_id]),
    ]


def shared_wall(wall_id: str, room_a: str, room_b: str, start=(0.0, 0.0), end=(1.0, 0.0)) -> Wall:
    return Wall(id=wall_id, start=start, end=end, bounds_rooms=[room_a, room_b])


def make_layout(
    rooms: list[Room],
    walls: list[Wall],
    extent_x_ft: float = 1000.0,
    extent_y_ft: float = 1000.0,
    plan_id: str = "plan_test1",
) -> Layout:
    return Layout(
        plan_id=plan_id,
        rooms=rooms,
        walls=walls,
        extent_x_ft=extent_x_ft,
        extent_y_ft=extent_y_ft,
    )


# ── _check_long_walls ───────────────────────────────────────────────────────


def test_no_long_walls_scores_one_with_no_warnings():
    walls = [Wall(id="wall_short", start=(0, 0), end=(10, 0), bounds_rooms=[])]
    layout = make_layout([], walls)
    score, warnings = _check_long_walls(layout)
    assert score == 1.0
    assert warnings == []


def test_unsupported_long_wall_is_flagged():
    walls = [Wall(id="wall_long", start=(0, 0), end=(40, 0), bounds_rooms=[])]
    layout = make_layout([], walls)
    score, warnings = _check_long_walls(layout)
    assert score < 1.0
    assert len(warnings) == 1
    assert warnings[0]["check"] == "long_walls"
    assert warnings[0]["entity_ids"] == ["wall_long"]


def test_long_wall_with_midspan_perpendicular_is_supported():
    walls = [
        Wall(id="wall_long", start=(0, 0), end=(40, 0), bounds_rooms=[]),
        Wall(id="wall_perp", start=(20, 0), end=(20, 10), bounds_rooms=[]),
    ]
    layout = make_layout([], walls)
    score, warnings = _check_long_walls(layout)
    assert score == 1.0
    assert warnings == []


# ── _check_room_aspect_ratios ───────────────────────────────────────────────


def test_square_room_scores_one():
    walls = make_rect_walls("room_1", "r1", x1=10, y1=10)
    room = make_room("room_1", "bedroom", [w.id for w in walls], x1=10, y1=10)
    layout = make_layout([room], walls)
    score, warnings = _check_room_aspect_ratios(layout)
    assert score == 1.0
    assert warnings == []


def test_elongated_room_flagged_as_outlier():
    walls = make_rect_walls("room_1", "r1", x1=20, y1=3)
    room = make_room("room_1", "bedroom", [w.id for w in walls], x1=20, y1=3)
    layout = make_layout([room], walls)
    score, warnings = _check_room_aspect_ratios(layout)
    assert score == 0.0
    assert len(warnings) == 1
    assert warnings[0]["entity_ids"] == ["room_1"]


def test_mixed_rooms_average_aspect_score():
    walls_a = make_rect_walls("room_1", "r1", x1=10, y1=10)
    room_a = make_room("room_1", "bedroom", [w.id for w in walls_a], x1=10, y1=10)
    walls_b = make_rect_walls("room_2", "r2", x0=0, y0=20, x1=20, y1=23)
    room_b = make_room("room_2", "bedroom", [w.id for w in walls_b], x0=0, y0=20, x1=20, y1=23)
    layout = make_layout([room_a, room_b], walls_a + walls_b)
    score, warnings = _check_room_aspect_ratios(layout)
    assert score == 0.5
    assert len(warnings) == 1


# ── _check_column_free_spans ────────────────────────────────────────────────


def test_no_eligible_rooms_scores_one():
    walls = make_rect_walls("room_1", "r1", x1=25, y1=25)
    room = make_room("room_1", "bedroom", [w.id for w in walls], x1=25, y1=25)
    layout = make_layout([room], walls)
    score, warnings = _check_column_free_spans(layout)
    assert score == 1.0
    assert warnings == []


def test_eligible_room_within_span_scores_one():
    walls = make_rect_walls("room_1", "r1", x1=15, y1=15)
    room = make_room("room_1", "living", [w.id for w in walls], x1=15, y1=15)
    layout = make_layout([room], walls)
    score, warnings = _check_column_free_spans(layout)
    assert score == 1.0
    assert warnings == []


def test_eligible_room_over_span_is_penalized():
    walls = make_rect_walls("room_1", "r1", x1=25, y1=30)
    room = make_room("room_1", "living", [w.id for w in walls], x1=25, y1=30)
    layout = make_layout([room], walls)
    score, warnings = _check_column_free_spans(layout)
    assert score == 0.0
    assert len(warnings) == 1
    assert warnings[0]["entity_ids"] == ["room_1"]


def test_multiple_offenders_averaged():
    walls_a = make_rect_walls("room_1", "r1", x1=15, y1=15)
    room_a = make_room("room_1", "living", [w.id for w in walls_a], x1=15, y1=15)
    walls_b = make_rect_walls("room_2", "r2", x0=0, y0=20, x1=25, y1=45)
    room_b = make_room("room_2", "dining", [w.id for w in walls_b], x0=0, y0=20, x1=25, y1=45)
    layout = make_layout([room_a, room_b], walls_a + walls_b)
    score, warnings = _check_column_free_spans(layout)
    assert score == 0.5
    assert len(warnings) == 1


# ── _check_wet_room_stacking ─────────────────────────────────────────────────


def test_no_wet_rooms_scores_one():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls])
    layout = make_layout([room], walls)
    score, warnings = _check_wet_room_stacking(layout)
    assert score == 1.0
    assert warnings == []


def test_two_wet_rooms_sharing_a_wall_scores_one():
    walls_a = make_rect_walls("room_1", "r1", x0=0, y0=0, x1=10, y1=10)
    room_a = make_room("room_1", "bathroom", [w.id for w in walls_a], x1=10, y1=10)
    walls_b = make_rect_walls("room_2", "r2", x0=10, y0=0, x1=20, y1=10)
    room_b = make_room("room_2", "kitchen", [w.id for w in walls_b], x0=10, y0=0, x1=20, y1=10)
    shared = shared_wall("wall_shared_ab", "room_1", "room_2", start=(10, 0), end=(10, 10))
    layout = make_layout([room_a, room_b], walls_a + walls_b + [shared])
    score, warnings = _check_wet_room_stacking(layout)
    assert score == 1.0
    assert warnings == []


def test_three_wet_rooms_none_stacking_scores_zero():
    walls_a = make_rect_walls("room_1", "r1", x0=0, y0=0, x1=10, y1=10)
    room_a = make_room("room_1", "bathroom", [w.id for w in walls_a], x1=10, y1=10)
    walls_b = make_rect_walls("room_2", "r2", x0=100, y0=100, x1=110, y1=110)
    room_b = make_room("room_2", "kitchen", [w.id for w in walls_b], x0=100, y0=100, x1=110, y1=110)
    walls_c = make_rect_walls("room_3", "r3", x0=200, y0=200, x1=210, y1=210)
    room_c = make_room("room_3", "utility", [w.id for w in walls_c], x0=200, y0=200, x1=210, y1=210)
    layout = make_layout([room_a, room_b, room_c], walls_a + walls_b + walls_c)
    score, warnings = _check_wet_room_stacking(layout)
    assert score == 0.0
    assert len(warnings) == 1


def test_three_wet_rooms_all_pairwise_adjacent_scores_one():
    walls_a = make_rect_walls("room_1", "r1", x0=0, y0=0, x1=10, y1=10)
    room_a = make_room("room_1", "bathroom", [w.id for w in walls_a], x1=10, y1=10)
    walls_b = make_rect_walls("room_2", "r2", x0=10, y0=0, x1=20, y1=10)
    room_b = make_room("room_2", "kitchen", [w.id for w in walls_b], x0=10, y0=0, x1=20, y1=10)
    walls_c = make_rect_walls("room_3", "r3", x0=20, y0=0, x1=30, y1=10)
    room_c = make_room("room_3", "utility", [w.id for w in walls_c], x0=20, y0=0, x1=30, y1=10)
    shared_ab = shared_wall("wall_shared_ab", "room_1", "room_2", start=(10, 0), end=(10, 10))
    shared_bc = shared_wall("wall_shared_bc", "room_2", "room_3", start=(20, 0), end=(20, 10))
    shared_ac = shared_wall("wall_shared_ac", "room_1", "room_3", start=(0, 20), end=(30, 20))
    layout = make_layout(
        [room_a, room_b, room_c],
        walls_a + walls_b + walls_c + [shared_ab, shared_bc, shared_ac],
    )
    score, warnings = _check_wet_room_stacking(layout)
    assert score == 1.0
    assert warnings == []


# ── verify_layer_b orchestrator ─────────────────────────────────────────────


def test_verifier_name_is_layer_b_structural():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls])
    layout = make_layout([room], walls)
    result = verify_layer_b(layout)
    assert result.verifier_name == "layer_b_structural"


def test_passed_is_always_true_even_with_all_subscores_zero():
    long_wall = Wall(id="wall_long", start=(0, 0), end=(40, 0), bounds_rooms=[])

    walls_living = make_rect_walls("room_1", "r1", x0=0, y0=100, x1=100, y1=121)
    room_living = make_room("room_1", "living", [w.id for w in walls_living], x0=0, y0=100, x1=100, y1=121)

    walls_bath = make_rect_walls("room_2", "r2", x0=200, y0=200, x1=240, y1=205)
    room_bath = make_room("room_2", "bathroom", [w.id for w in walls_bath], x0=200, y0=200, x1=240, y1=205)

    walls_kitchen = make_rect_walls("room_3", "r3", x0=300, y0=300, x1=340, y1=305)
    room_kitchen = make_room("room_3", "kitchen", [w.id for w in walls_kitchen], x0=300, y0=300, x1=340, y1=305)

    layout = make_layout(
        [room_living, room_bath, room_kitchen],
        [long_wall] + walls_living + walls_bath + walls_kitchen,
    )
    result = verify_layer_b(layout)
    assert result.passed is True
    assert result.score == 0.0


def test_score_is_valid_float_in_unit_interval():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls])
    layout = make_layout([room], walls)
    result = verify_layer_b(layout)
    assert isinstance(result.score, float)
    assert 0.0 <= result.score <= 1.0


# ── Integration ──────────────────────────────────────────────────────────────


def test_elapsed_ms_is_positive():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls])
    layout = make_layout([room], walls)
    result = verify_layer_b(layout)
    assert result.elapsed_ms >= 0


def test_warnings_are_structured_with_check_detail_entity_ids():
    walls = make_rect_walls("room_1", "r1", x1=20, y1=3)
    room = make_room("room_1", "bedroom", [w.id for w in walls], x1=20, y1=3)
    layout = make_layout([room], walls)
    result = verify_layer_b(layout)
    assert len(result.warnings) >= 1
    for warning in result.warnings:
        assert set(warning.keys()) == {"check", "detail", "entity_ids"}
        assert isinstance(warning["entity_ids"], list)


def test_trivial_layout_is_fast():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls])
    layout = make_layout([room], walls)
    start = time.perf_counter()
    result = verify_layer_b(layout)
    wall_clock_ms = (time.perf_counter() - start) * 1000.0
    assert result.elapsed_ms < 50.0
    assert wall_clock_ms < 50.0
