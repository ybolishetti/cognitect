"""IRC 2021 §R311.2 — Egress door width.

At least one exterior door must have width_ft >= 3.0 (36"). We check
Opening.width_ft (rough opening width) as a proxy for the 32" clear width
required by code, since 36" nominal is the standard-of-practice minimum.

Failing entity: layout-level (empty entity_ids means "the plan itself").
"""

from __future__ import annotations

from engine.layout import Layout
from engine.verifiers.layer_c_helpers import walls_by_id
from engine.verifiers.rules.base import CodeCheckContext, CodeRule

MIN_EGRESS_DOOR_WIDTH_FT = 3.0  # 36" nominal rough opening -> ~32" clear


class IRC_R311_2_PrimaryExitDoorWidth(CodeRule):
    rule_id = "irc_r311_2"
    citation = "IRC 2021 §R311.2 — Egress door"

    def check(self, layout: Layout, ctx: CodeCheckContext) -> list[dict]:
        walls = walls_by_id(layout)
        exterior_doors = []
        for op in layout.openings:
            if op.opening_type != "door":
                continue
            wall = walls.get(op.wall_id)
            if wall is None:
                continue
            if len(wall.bounds_rooms) == 1:  # exterior wall
                exterior_doors.append(op)

        if not exterior_doors:
            return [{
                "check": self.rule_id,
                "citation": self.citation,
                "detail": "plan has no exterior doors",
                "entity_ids": [],
            }]

        qualifying = [op for op in exterior_doors if op.width_ft >= MIN_EGRESS_DOOR_WIDTH_FT]
        if not qualifying:
            widest = max(op.width_ft for op in exterior_doors)
            return [{
                "check": self.rule_id,
                "citation": self.citation,
                "detail": (
                    f"no exterior door meets the {MIN_EGRESS_DOOR_WIDTH_FT}ft minimum "
                    f"width — widest exterior door is {widest:.2f}ft"
                ),
                "entity_ids": sorted(op.id for op in exterior_doors),
            }]
        return []
