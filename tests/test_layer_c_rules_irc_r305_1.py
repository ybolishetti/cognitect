"""Tests for IRC 2021 §R305.1 — ceiling height
(engine/verifiers/rules/irc_r305_1.py)."""

from __future__ import annotations

from engine.verifiers import CodeCheckContext
from engine.verifiers.rules.irc_r305_1 import IRC_R305_1_MinCeilingHeight
from tests.test_layer_c import make_layout, make_rect_walls, make_room

RULE = IRC_R305_1_MinCeilingHeight()
CTX = CodeCheckContext()


def test_bedroom_with_8ft_ceiling_passes():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls], ceiling_height_ft=8.0)
    layout = make_layout([room], walls)
    assert RULE.check(layout, CTX) == []


def test_bedroom_with_6_9ft_ceiling_fails():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls], ceiling_height_ft=6.9)
    layout = make_layout([room], walls)
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["room_1"]


def test_bedroom_with_exact_7ft_ceiling_passes():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bedroom", [w.id for w in walls], ceiling_height_ft=7.0)
    layout = make_layout([room], walls)
    assert RULE.check(layout, CTX) == []


def test_closet_with_6ft_ceiling_passes():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "closet", [w.id for w in walls], ceiling_height_ft=6.0)
    layout = make_layout([room], walls)
    assert RULE.check(layout, CTX) == []


def test_multiple_rooms_one_below_exactly_one_failure():
    walls_1 = make_rect_walls("room_1", "r1", x0=0.0, y0=0.0, x1=10.0, y1=10.0)
    room_1 = make_room(
        "room_1", "bedroom", [w.id for w in walls_1], x0=0.0, y0=0.0, x1=10.0, y1=10.0, ceiling_height_ft=9.0
    )
    walls_2 = make_rect_walls("room_2", "r2", x0=50.0, y0=50.0, x1=60.0, y1=60.0)
    room_2 = make_room(
        "room_2", "bedroom", [w.id for w in walls_2], x0=50.0, y0=50.0, x1=60.0, y1=60.0, ceiling_height_ft=6.5
    )
    layout = make_layout([room_1, room_2], walls_1 + walls_2)
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["room_2"]
