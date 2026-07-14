"""IRC 2021 §R311.7 — Hallways.

Every room of type "hallway" must have a minimum polygon side length
of at least 3.0 ft. Approximates the "hallway width" requirement — a
proper minimum-width-along-medial-axis check is deferred. For
rectangular hallways this is exact; for L-shaped hallways it's
conservative (correctly flags a too-narrow segment).

Failing entity: the hallway room_id.
"""

from __future__ import annotations

import math

from engine.layout import Layout, Room
from engine.verifiers.rules.base import CodeCheckContext, CodeRule

MIN_HALLWAY_WIDTH_FT = 3.0


def _min_polygon_side_length(room: Room) -> float:
    """Return the length of the shortest polygon edge (feet)."""
    verts = room.vertices
    if len(verts) < 4:  # closed polygon has >=4 with wrap
        return 0.0
    min_len = math.inf
    for i in range(len(verts) - 1):
        x1, y1 = verts[i]
        x2, y2 = verts[i + 1]
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length < min_len:
            min_len = length
    return 0.0 if math.isinf(min_len) else min_len


class IRC_R311_7_HallwayWidth(CodeRule):
    rule_id = "irc_r311_7"
    citation = "IRC 2021 §R311.7 — Hallways"

    def check(self, layout: Layout, ctx: CodeCheckContext) -> list[dict]:
        failures = []
        for room in layout.rooms:
            if room.room_type != "hallway":
                continue
            min_side = _min_polygon_side_length(room)
            if min_side < MIN_HALLWAY_WIDTH_FT:
                failures.append({
                    "check": self.rule_id,
                    "citation": self.citation,
                    "detail": (
                        f"hallway {room.id} ({room.name!r}) has minimum polygon side "
                        f"length {min_side:.3f}ft, below the {MIN_HALLWAY_WIDTH_FT}ft "
                        f"minimum required by IRC §R311.7"
                    ),
                    "entity_ids": [room.id],
                })
        return failures
