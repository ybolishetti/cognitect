"""IRC 2021 §R305.1 — Ceiling height.

Habitable rooms must have ceiling_height_ft >= 7.0. Non-habitable rooms
(closet, garage) are exempt.
"""

from __future__ import annotations

from engine.layout import Layout
from engine.verifiers.rules.base import CodeCheckContext, CodeRule

MIN_CEILING_HEIGHT_FT = 7.0
HABITABLE_ROOM_TYPES = frozenset({
    "bedroom", "bathroom", "kitchen", "living", "dining",
    "hallway", "office", "utility",
})
# Explicitly NON-habitable (exempt): "closet", "garage", "other".


class IRC_R305_1_MinCeilingHeight(CodeRule):
    rule_id = "irc_r305_1"
    citation = "IRC 2021 §R305.1 — Ceiling height"

    def check(self, layout: Layout, ctx: CodeCheckContext) -> list[dict]:
        failures = []
        for room in layout.rooms:
            if room.room_type not in HABITABLE_ROOM_TYPES:
                continue
            if room.ceiling_height_ft < MIN_CEILING_HEIGHT_FT:
                failures.append({
                    "check": self.rule_id,
                    "citation": self.citation,
                    "detail": (
                        f"{room.room_type} {room.id} has ceiling_height_ft="
                        f"{room.ceiling_height_ft:.2f}, below the "
                        f"{MIN_CEILING_HEIGHT_FT}ft minimum"
                    ),
                    "entity_ids": [room.id],
                })
        return failures
