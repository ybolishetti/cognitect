"""Architecture C verifiers — Layer A (geometry) and Layer C (code).

See ARCHITECTURE_C.md, DRAFT_ARCH_C_2_LAYER_A.md, and
DRAFT_ARCH_C_5_LAYER_C.md. Layer A checks what engine/layout/schemas.py
explicitly does not: room-polygon overlap, wall endpoint connectivity,
room.vertices vs boundary_wall_ids agreement, exterior envelope
connectivity, and opening pairwise overlap. Layer C checks residential
building code compliance (IRC-2021) via a registry of CodeRule instances.
"""

from engine.verifiers.layer_a import (
    verify_layer_a,
    check_rooms_do_not_overlap,
    check_walls_form_closed_room_boundaries,
    check_walls_meet_at_endpoints,
    check_openings_lie_on_walls,
    check_openings_do_not_overlap_on_same_wall,
    check_no_negative_room_areas,
    check_exterior_envelope_is_single_closed_polygon,
    check_room_polygons_match_boundary_walls,
)
from engine.verifiers.layer_c import verify_layer_c
from engine.verifiers.rules import (
    IRC_2021_RULES,
    CodeCheckContext,
    CodeRule,
    lookup_rule,
)

__all__ = [
    # Layer A
    "verify_layer_a",
    "check_rooms_do_not_overlap",
    "check_walls_form_closed_room_boundaries",
    "check_walls_meet_at_endpoints",
    "check_openings_lie_on_walls",
    "check_openings_do_not_overlap_on_same_wall",
    "check_no_negative_room_areas",
    "check_exterior_envelope_is_single_closed_polygon",
    "check_room_polygons_match_boundary_walls",
    # Layer C
    "verify_layer_c",
    "IRC_2021_RULES",
    "CodeCheckContext",
    "CodeRule",
    "lookup_rule",
]
