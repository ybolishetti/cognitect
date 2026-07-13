"""Architecture C — Layer A geometry verifier (Shapely-based hard gate).

See ARCHITECTURE_C.md and DRAFT_ARCH_C_2_LAYER_A.md. Layer A checks what
engine/layout/schemas.py explicitly does not: room-polygon overlap, wall
endpoint connectivity, room.vertices vs boundary_wall_ids agreement,
exterior envelope connectivity, and opening pairwise overlap.
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

__all__ = [
    "verify_layer_a",
    "check_rooms_do_not_overlap",
    "check_walls_form_closed_room_boundaries",
    "check_walls_meet_at_endpoints",
    "check_openings_lie_on_walls",
    "check_openings_do_not_overlap_on_same_wall",
    "check_no_negative_room_areas",
    "check_exterior_envelope_is_single_closed_polygon",
    "check_room_polygons_match_boundary_walls",
]
