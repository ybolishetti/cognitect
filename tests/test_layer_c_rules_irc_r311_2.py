"""Tests for IRC 2021 §R311.2 — egress door width
(engine/verifiers/rules/irc_r311_2.py)."""

from __future__ import annotations

from engine.layout import Opening, Wall
from engine.verifiers import CodeCheckContext
from engine.verifiers.rules.irc_r311_2 import IRC_R311_2_PrimaryExitDoorWidth
from tests.test_layer_c import make_layout, make_rect_walls, make_room

RULE = IRC_R311_2_PrimaryExitDoorWidth()
CTX = CodeCheckContext()


def test_36_inch_exterior_door_passes():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "living", [w.id for w in walls])
    door = Opening(id="opening_door1", opening_type="door", wall_id="wall_r1_s", offset_ft=2.0, width_ft=3.0)
    layout = make_layout([room], walls, [door])
    assert RULE.check(layout, CTX) == []


def test_only_30_inch_exterior_door_fails():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "living", [w.id for w in walls])
    door = Opening(id="opening_door1", opening_type="door", wall_id="wall_r1_s", offset_ft=2.0, width_ft=2.5)
    layout = make_layout([room], walls, [door])
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert "widest exterior door is 2.50ft" in failures[0]["detail"]


def test_two_exterior_doors_one_qualifying_passes():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "living", [w.id for w in walls])
    door_narrow = Opening(
        id="opening_door1", opening_type="door", wall_id="wall_r1_s", offset_ft=0.0, width_ft=28.0 / 12.0
    )
    door_wide = Opening(id="opening_door2", opening_type="door", wall_id="wall_r1_e", offset_ft=2.0, width_ft=3.0)
    layout = make_layout([room], walls, [door_narrow, door_wide])
    assert RULE.check(layout, CTX) == []


def test_zero_exterior_doors_fails():
    walls = make_rect_walls("room_1", "r1")
    room = make_room("room_1", "living", [w.id for w in walls])
    layout = make_layout([room], walls, [])
    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["detail"] == "plan has no exterior doors"
    assert failures[0]["entity_ids"] == []


def test_36_inch_door_on_interior_wall_fails():
    ext = [w for w in make_rect_walls("room_1", "r1") if w.id != "wall_r1_s"]
    shared = Wall(id="wall_shared", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=["room_1", "room_2"])
    room_1 = make_room("room_1", "living", [w.id for w in ext] + [shared.id])
    room_2_walls = make_rect_walls("room_2", "r2", x0=50.0, y0=50.0, x1=60.0, y1=60.0)
    room_2 = make_room("room_2", "hallway", [w.id for w in room_2_walls], x0=50.0, y0=50.0, x1=60.0, y1=60.0)
    door = Opening(id="opening_door1", opening_type="door", wall_id="wall_shared", offset_ft=2.0, width_ft=3.0)
    layout = make_layout([room_1, room_2], ext + [shared] + room_2_walls, [door])

    failures = RULE.check(layout, CTX)
    assert len(failures) == 1
    assert failures[0]["detail"] == "plan has no exterior doors"
