"""Tests for IRC 2021 §R303.1 — wet-room opening
(engine/verifiers/rules/irc_r303_1.py)."""

from __future__ import annotations

from engine.layout import Opening, Wall
from engine.verifiers import CodeCheckContext
from engine.verifiers.rules.irc_r303_1 import IRC_R303_1_WetRoomOpening
from tests.test_layer_c import make_layout, make_rect_walls, make_room

RULE = IRC_R303_1_WetRoomOpening()
CTX = CodeCheckContext()


def test_bathroom_with_interior_door_passes():
    ext = [w for w in make_rect_walls("room_1", "r1") if w.id != "wall_r1_s"]
    shared = Wall(id="wall_shared", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=["room_1", "room_2"])
    room_1 = make_room("room_1", "bathroom", [w.id for w in ext] + [shared.id])
    room_2_walls = make_rect_walls("room_2", "r2", x0=50.0, y0=50.0, x1=60.0, y1=60.0)
    room_2 = make_room("room_2", "hallway", [w.id for w in room_2_walls], x0=50.0, y0=50.0, x1=60.0, y1=60.0)
    door = Opening(id="opening_door1", opening_type="door", wall_id="wall_shared", offset_ft=2.0, width_ft=3.0)
    layout = make_layout([room_1, room_2], ext + [shared] + room_2_walls, [door])
    assert RULE.check(layout, CTX) == []


def test_bathroom_with_no_openings_fails():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "bathroom", [w.id for w in walls])
    layout = make_layout([room], walls, [])
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["room_1"]


def test_kitchen_with_exterior_window_passes():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "kitchen", [w.id for w in walls])
    window = Opening(id="opening_win1", opening_type="window", wall_id="wall_r1_n", offset_ft=2.0, width_ft=3.0)
    layout = make_layout([room], walls, [window])
    assert RULE.check(layout, CTX) == []


def test_living_room_with_no_openings_passes():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "living", [w.id for w in walls])
    layout = make_layout([room], walls, [])
    assert RULE.check(layout, CTX) == []


def test_utility_with_no_openings_fails():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "utility", [w.id for w in walls])
    layout = make_layout([room], walls, [])
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["entity_ids"] == ["room_1"]
