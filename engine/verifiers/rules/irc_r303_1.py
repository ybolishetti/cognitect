"""IRC 2021 §R303.1 — Habitable room ventilation.

Bathrooms, kitchens, and utility (laundry) rooms must have at least one
opening (door OR window) somewhere on their walls. Approximates the
natural-ventilation-OR-mechanical requirement — we can't verify HVAC,
but a room with zero openings can't have either, so it fails outright.

Failing entity: the wet-room room_id.
"""

from __future__ import annotations

from engine.layout import Layout
from engine.verifiers.layer_c_helpers import openings_by_wall
from engine.verifiers.rules.base import CodeCheckContext, CodeRule

WET_ROOM_TYPES = frozenset({"bathroom", "kitchen", "utility"})


class IRC_R303_1_WetRoomOpening(CodeRule):
    rule_id = "irc_r303_1"
    citation = "IRC 2021 §R303.1 — Habitable rooms (ventilation)"

    def check(self, layout: Layout, ctx: CodeCheckContext) -> list[dict]:
        failures = []
        walls_by_room: dict[str, set[str]] = {}
        for wall in layout.walls:
            for room_id in wall.bounds_rooms:
                walls_by_room.setdefault(room_id, set()).add(wall.id)
        openings = openings_by_wall(layout)

        for room in layout.rooms:
            if room.room_type not in WET_ROOM_TYPES:
                continue
            wall_ids = walls_by_room.get(room.id, set())
            has_opening = any(openings.get(wid) for wid in wall_ids)
            if not has_opening:
                failures.append({
                    "check": self.rule_id,
                    "citation": self.citation,
                    "detail": (
                        f"{room.room_type} {room.id} ({room.name!r}) has no openings "
                        f"on any of its bounding walls (unventilated / inaccessible)"
                    ),
                    "entity_ids": [room.id],
                })
        return failures
