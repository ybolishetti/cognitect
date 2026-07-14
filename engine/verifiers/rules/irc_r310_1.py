"""IRC 2021 §R310.1 — Emergency escape and rescue openings.

Every bedroom must have at least one exterior-wall opening (door OR
window). This is the weakened form of R310.1 — sill height (<44"),
minimum openable area (5.7 sqft), and clear opening dimensions
(20"W x 24"H) are deferred until Opening metadata carries operability.

Failing entity: the bedroom room_id. Fix hint: add a window on any wall
whose bounds_rooms is [<this room>].
"""

from __future__ import annotations

from engine.layout import Layout
from engine.verifiers.layer_c_helpers import is_exterior_wall, openings_by_wall, walls_by_id
from engine.verifiers.rules.base import CodeCheckContext, CodeRule


class IRC_R310_1_BedroomEgressWindow(CodeRule):
    rule_id = "irc_r310_1"
    citation = "IRC 2021 §R310.1 — Emergency escape and rescue openings"

    def check(self, layout: Layout, ctx: CodeCheckContext) -> list[dict]:
        failures = []
        walls = walls_by_id(layout)
        openings = openings_by_wall(layout)

        for room in layout.rooms:
            if room.room_type != "bedroom":
                continue

            has_egress = False
            for wall_id in room.boundary_wall_ids:
                wall = walls.get(wall_id)
                if wall is None:
                    # Cross-reference integrity is Layer A's problem; skip silently.
                    continue
                if not is_exterior_wall(wall, room.id):
                    continue
                for op in openings.get(wall_id, []):
                    if op.opening_type in ("door", "window"):
                        has_egress = True
                        break
                if has_egress:
                    break

            if not has_egress:
                failures.append({
                    "check": self.rule_id,
                    "citation": self.citation,
                    "detail": (
                        f"bedroom {room.id} ({room.name!r}) has no egress opening "
                        f"(no door or window on any exterior wall)"
                    ),
                    "entity_ids": [room.id],
                })
        return failures
