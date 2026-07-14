"""Tests for IRC 2021 §R311.7 — hallway width
(engine/verifiers/rules/irc_r311_7.py)."""

from __future__ import annotations

from engine.layout import Room, Wall
from engine.verifiers import CodeCheckContext
from engine.verifiers.rules.irc_r311_7 import IRC_R311_7_HallwayWidth
from tests.test_layer_c import make_layout, make_rect_walls, make_room

# Horizontal leg (0,0)-(10,4) unioned with a narrow 2.5ft-wide vertical leg
# (0,4)-(2.5,9) sharing the corner at (0,4)-(2.5,4). Shortest edge is the
# 2.5ft cap at the end of the narrow leg.
_L_SHAPE_VERTS = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (2.5, 4.0), (2.5, 9.0), (0.0, 9.0), (0.0, 0.0)]

RULE = IRC_R311_7_HallwayWidth()
CTX = CodeCheckContext()


def test_3ft_by_20ft_hallway_passes():
    walls = make_rect_walls("room_1", "r1", x0=0.0, y0=0.0, x1=20.0, y1=3.0)
    room = make_room("room_1", "hallway", [w.id for w in walls], x0=0.0, y0=0.0, x1=20.0, y1=3.0)
    layout = make_layout([room], walls)
    assert RULE.check(layout, CTX) == []


def test_2_5ft_by_20ft_hallway_fails():
    walls = make_rect_walls("room_1", "r1", x0=0.0, y0=0.0, x1=20.0, y1=2.5)
    room = make_room("room_1", "hallway", [w.id for w in walls], x0=0.0, y0=0.0, x1=20.0, y1=2.5)
    layout = make_layout([room], walls)
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert "2.500ft" in failures[0]["detail"]


def test_non_hallway_2ft_by_2ft_room_passes():
    walls = make_rect_walls("room_1", "r1", x0=0.0, y0=0.0, x1=2.0, y1=2.0)
    room = make_room("room_1", "closet", [w.id for w in walls], x0=0.0, y0=0.0, x1=2.0, y1=2.0)
    layout = make_layout([room], walls)
    assert RULE.check(layout, CTX) == []


def test_l_shaped_hallway_with_narrow_leg_fails():
    verts = _L_SHAPE_VERTS
    walls = [Wall(id=f"wall_l{i}", start=verts[i], end=verts[i + 1], bounds_rooms=["room_1"]) for i in range(6)]
    room = Room(
        id="room_1",
        name="L Hallway",
        room_type="hallway",
        vertices=verts,
        area_sqft=52.5,
        boundary_wall_ids=[w.id for w in walls],
    )
    layout = make_layout([room], walls)
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["room_1"]


def test_two_hallways_one_narrow_one_wide_exactly_one_failure():
    wide_walls = make_rect_walls("room_wide", "rw", x0=0.0, y0=0.0, x1=20.0, y1=3.0)
    wide = make_room("room_wide", "hallway", [w.id for w in wide_walls], x0=0.0, y0=0.0, x1=20.0, y1=3.0)

    narrow_walls = make_rect_walls("room_narrow", "rn", x0=50.0, y0=50.0, x1=70.0, y1=52.5)
    narrow = make_room(
        "room_narrow", "hallway", [w.id for w in narrow_walls], x0=50.0, y0=50.0, x1=70.0, y1=52.5
    )

    layout = make_layout([wide, narrow], wide_walls + narrow_walls)
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["room_narrow"]
