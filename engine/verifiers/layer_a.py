"""Layer A — the geometry hard gate (Architecture C).

Verifies that a Layout is geometrically sane using Shapely: rooms don't
overlap, walls connect at their endpoints, room polygons agree with the
polygon implied by their boundary walls, and the exterior envelope is a
single connected region with no islands or unmodeled holes.

Layer A checks are deterministic, geometric, and non-negotiable. Anything
the schema-level validators in engine/layout/schemas.py already enforce
(polygon closure, CCW ordering, area-matches-shoelace, cross-reference
integrity) is NOT re-checked here — see that module's docstring for the
line between schema-only and Layer A concerns.
"""

from __future__ import annotations

import time
from collections import defaultdict

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity

from engine.layout import Layout, VerifierResult
from engine.verifiers.layer_a_helpers import (
    all_room_polygons,
    order_wall_ids_into_ring,
    room_polygon,
    wall_endpoint_graph,
    wall_linestring,
)

# Point coincidence / 1-D interval tolerance: an authoring-scale allowance
# (~1/8 inch) for comparing independently-authored coordinates or opening
# offsets. Deliberately coarser than ENDPOINT_ROUND_DECIMALS below, which
# exists for a different purpose — the two are not meant to be reconciled
# into a single number.
COORDINATE_TOLERANCE_FT = 0.01

# Symmetric-difference-area tolerance for comparing a room's vertices
# polygon against the polygon implied by its boundary walls, expressed as a
# per-foot-of-perimeter allowance (see check_room_polygons_match_boundary_walls):
# "the boundary may be off by up to this many feet, uniformly around the
# whole perimeter" (~0.6 inch). Scales with room size, unlike a flat area
# threshold.
POLYGON_MATCH_TOLERANCE_FT = 0.05

# Decimal places used to snap wall endpoint coordinates into graph node keys
# (wall_endpoint_graph, order_wall_ids_into_ring). This exists purely to
# dedupe floating-point representation noise between two walls whose
# authors intended the exact same point — it is intentionally much finer
# grained (0.0001ft) than COORDINATE_TOLERANCE_FT's 0.01ft. Two walls whose
# corners are genuinely ~0.005ft apart (a real, if small, authoring gap)
# will correctly NOT be merged, and will surface as a dangling-endpoint
# failure instead.
ENDPOINT_ROUND_DECIMALS = 4


def check_no_negative_room_areas(layout: Layout) -> list[dict]:
    """Every room's Shapely polygon must be valid with positive area.

    Re-validates with real Shapely/GEOS geometry, independent of the
    schema's own hand-rolled shoelace math: a self-intersecting polygon
    (e.g. a "bowtie" quadrilateral) can have a positive net shoelace sum
    and pass schema validation, while Shapely's is_valid correctly flags it.
    """
    failures = []
    for room in layout.rooms:
        poly = room_polygon(room)
        if not poly.is_valid:
            failures.append({
                "check": "no_negative_room_areas",
                "detail": f"room {room.id} polygon is invalid: {explain_validity(poly)}",
                "entity_ids": [room.id],
            })
        elif poly.area <= 0:
            failures.append({
                "check": "no_negative_room_areas",
                "detail": f"room {room.id} polygon has non-positive area ({poly.area})",
                "entity_ids": [room.id],
            })
    return failures


def check_rooms_do_not_overlap(layout: Layout) -> list[dict]:
    """No two rooms may have overlapping interiors (shared edges/walls OK).

    Uses Shapely's overlaps(), NOT intersects(): overlaps() is true only
    when two geometries share some but not all interior points (real area
    overlap); two rooms that only share a boundary edge (the normal
    adjacent-room case) return False from overlaps().

    Rooms whose own polygon is invalid are skipped — their invalidity is
    already reported by check_no_negative_room_areas.
    """
    failures = []
    candidates = [(room, room_polygon(room)) for room in layout.rooms]
    candidates = [(room, poly) for room, poly in candidates if poly.is_valid]

    for i in range(len(candidates)):
        room_a, poly_a = candidates[i]
        for j in range(i + 1, len(candidates)):
            room_b, poly_b = candidates[j]
            if poly_a.overlaps(poly_b):
                failures.append({
                    "check": "rooms_do_not_overlap",
                    "detail": (
                        f"room {room_a.id} and room {room_b.id} polygons overlap "
                        f"(area overlap, not just a shared edge)"
                    ),
                    "entity_ids": sorted([room_a.id, room_b.id]),
                })
    return failures


def check_openings_lie_on_walls(layout: Layout) -> list[dict]:
    """Every opening's [offset_ft, offset_ft + width_ft] range must fit
    within its parent wall's true geometric length.

    Re-derives the wall's length from its raw Shapely LineString rather
    than trusting Wall.length_ft (rounded to 4 decimals) — defense-in-depth
    against schema evolution, as noted in the DRAFT spec.
    """
    failures = []
    walls_by_id = {wall.id: wall for wall in layout.walls}

    for opening in layout.openings:
        wall = walls_by_id.get(opening.wall_id)
        if wall is None:
            failures.append({
                "check": "openings_lie_on_walls",
                "detail": f"opening {opening.id} references non-existent wall {opening.wall_id}",
                "entity_ids": [opening.id],
            })
            continue

        true_length = wall_linestring(wall).length
        far_edge = opening.offset_ft + opening.width_ft
        if far_edge > true_length + COORDINATE_TOLERANCE_FT:
            failures.append({
                "check": "openings_lie_on_walls",
                "detail": (
                    f"opening {opening.id} spans [{opening.offset_ft:.4f}, {far_edge:.4f}] "
                    f"which exceeds wall {wall.id}'s true geometric length "
                    f"({true_length:.6f}ft) by more than {COORDINATE_TOLERANCE_FT}ft"
                ),
                "entity_ids": sorted([opening.id, wall.id]),
            })
    return failures


def check_openings_do_not_overlap_on_same_wall(layout: Layout) -> list[dict]:
    """No two openings on the same wall may overlap in their
    [offset, offset + width] intervals."""
    failures = []
    by_wall: dict[str, list] = defaultdict(list)
    for opening in layout.openings:
        by_wall[opening.wall_id].append(opening)

    for wall_id, openings in by_wall.items():
        for i in range(len(openings)):
            for j in range(i + 1, len(openings)):
                a, b = openings[i], openings[j]
                a_end = a.offset_ft + a.width_ft
                b_end = b.offset_ft + b.width_ft
                overlap = min(a_end, b_end) - max(a.offset_ft, b.offset_ft)
                if overlap > COORDINATE_TOLERANCE_FT:
                    failures.append({
                        "check": "openings_do_not_overlap_on_same_wall",
                        "detail": (
                            f"openings {a.id} [{a.offset_ft:.4f},{a_end:.4f}] and "
                            f"{b.id} [{b.offset_ft:.4f},{b_end:.4f}] overlap by "
                            f"{overlap:.4f}ft on wall {wall_id}"
                        ),
                        "entity_ids": sorted([a.id, b.id]),
                    })
    return failures


def check_walls_meet_at_endpoints(layout: Layout) -> list[dict]:
    """Every wall endpoint must coincide with at least one other wall
    endpoint (within tolerance) unless it's a freestanding wall.

    Global check across ALL walls in the layout. A degree-1 ("dangling")
    node is only a problem if the single wall touching it actually bounds a
    room: bounds_rooms length is the signal (0 = freestanding, 1 = exterior,
    2 = interior). Dangling is allowed iff bounds_rooms == [] (freestanding
    walls aren't required to connect to anything); it's a failure if
    bounds_rooms has length 1 or 2 (a dangling exterior wall is a gap in the
    envelope; a dangling interior wall is a disconnected partition).

    Nodes of degree >= 3 (T-junctions) are normal and not flagged here.
    """
    failures = []
    graph = wall_endpoint_graph(layout.walls, ENDPOINT_ROUND_DECIMALS)
    for node_key, incident_walls in graph.items():
        if len(incident_walls) != 1:
            continue
        wall = incident_walls[0]
        if len(wall.bounds_rooms) == 0:
            continue
        failures.append({
            "check": "walls_meet_at_endpoints",
            "detail": (
                f"wall {wall.id} has a dangling endpoint at {node_key} not "
                f"shared by any other wall (bounds_rooms={wall.bounds_rooms})"
            ),
            "entity_ids": [wall.id],
        })
    return failures


def _rings_match_within_tolerance(
    ring_a: list[tuple[float, float]],
    ring_b: list[tuple[float, float]],
    tol: float,
) -> bool:
    """Both rings are closed (first == last) and CCW. Try every rotation of
    ring_a against ring_b's fixed order — no reflection needed, both are
    already canonically CCW."""
    a, b = ring_a[:-1], list(ring_b[:-1])
    if len(a) != len(b):
        return False
    n = len(a)
    for offset in range(n):
        if all(
            abs(a[i][0] - b[(i + offset) % n][0]) <= tol
            and abs(a[i][1] - b[(i + offset) % n][1]) <= tol
            for i in range(n)
        ):
            return True
    return False


def check_walls_form_closed_room_boundaries(layout: Layout) -> list[dict]:
    """Every room's boundary_wall_ids must form a closed loop matching its
    vertices.

    Rooms authored with extra collinear wall-split points will legitimately
    fail this strict topology check even if geometrically equivalent — that
    is intentional; check_room_polygons_match_boundary_walls is the
    permissive, area-based counterpart for that case.
    """
    failures = []
    walls_by_id = {wall.id: wall for wall in layout.walls}

    for room in layout.rooms:
        ring, reason = order_wall_ids_into_ring(
            room.boundary_wall_ids, walls_by_id, ENDPOINT_ROUND_DECIMALS
        )
        entity_ids = sorted({room.id, *room.boundary_wall_ids})
        if ring is None:
            failures.append({
                "check": "walls_form_closed_room_boundaries",
                "detail": f"boundary_wall_ids for {room.id} do not form a single closed cycle ({reason})",
                "entity_ids": entity_ids,
            })
            continue

        if not _rings_match_within_tolerance(ring, room.vertices, COORDINATE_TOLERANCE_FT):
            failures.append({
                "check": "walls_form_closed_room_boundaries",
                "detail": (
                    f"the wall ring for {room.id} does not match room.vertices "
                    f"within {COORDINATE_TOLERANCE_FT}ft under any rotation"
                ),
                "entity_ids": entity_ids,
            })
    return failures


def check_room_polygons_match_boundary_walls(layout: Layout) -> list[dict]:
    """Each room's vertices polygon must be spatially consistent with the
    polygon formed by its boundary_wall_ids.

    Reuses order_wall_ids_into_ring (same helper as check 6). Compares via
    Shapely's symmetric_difference area, scaled by the room polygon's
    perimeter so the tolerance is a uniform per-foot-of-boundary allowance.
    """
    failures = []
    walls_by_id = {wall.id: wall for wall in layout.walls}

    for room in layout.rooms:
        ring, reason = order_wall_ids_into_ring(
            room.boundary_wall_ids, walls_by_id, ENDPOINT_ROUND_DECIMALS
        )
        entity_ids = sorted({room.id, *room.boundary_wall_ids})
        if ring is None:
            failures.append({
                "check": "room_polygons_match_boundary_walls",
                "detail": f"cannot build a wall-boundary polygon for {room.id} ({reason})",
                "entity_ids": entity_ids,
            })
            continue

        wall_poly = Polygon(ring)
        room_poly = room_polygon(room)
        if not wall_poly.is_valid or not room_poly.is_valid:
            failures.append({
                "check": "room_polygons_match_boundary_walls",
                "detail": f"wall-boundary polygon or room polygon for {room.id} is not a valid simple polygon",
                "entity_ids": entity_ids,
            })
            continue

        sym_diff_area = room_poly.symmetric_difference(wall_poly).area
        threshold_area = POLYGON_MATCH_TOLERANCE_FT * room_poly.length
        if sym_diff_area > threshold_area:
            failures.append({
                "check": "room_polygons_match_boundary_walls",
                "detail": (
                    f"room {room.id}'s vertices polygon and its wall-boundary "
                    f"polygon differ by {sym_diff_area:.4f} sqft "
                    f"(threshold {threshold_area:.4f} sqft = "
                    f"{POLYGON_MATCH_TOLERANCE_FT}ft x perimeter "
                    f"{room_poly.length:.4f}ft)"
                ),
                "entity_ids": entity_ids,
            })
    return failures


def check_exterior_envelope_is_single_closed_polygon(layout: Layout) -> list[dict]:
    """The union of all room polygons must form a single connected
    component (no islands) whose exterior boundary is a single closed
    polygon (no holes).

    Rooms whose own polygon is invalid are excluded before the union (their
    invalidity is already reported by check_no_negative_room_areas). If
    there are zero valid room polygons, there is no envelope invariant to
    violate, so this reports no failures.
    """
    failures = []
    all_room_ids = sorted(room.id for room in layout.rooms)
    valid_polys = [poly for _, poly in all_room_polygons(layout) if poly.is_valid]
    if not valid_polys:
        return failures

    merged = unary_union(valid_polys)

    if merged.geom_type == "MultiPolygon":
        failures.append({
            "check": "exterior_envelope_is_single_closed_polygon",
            "detail": f"room union is disconnected into {len(merged.geoms)} separate pieces (islands)",
            "entity_ids": all_room_ids,
        })
    elif merged.geom_type == "Polygon":
        if not merged.is_valid:
            failures.append({
                "check": "exterior_envelope_is_single_closed_polygon",
                "detail": (
                    "room union forms a degenerate polygon (e.g. rooms touching "
                    "at a single point rather than sharing an edge)"
                ),
                "entity_ids": all_room_ids,
            })
        elif len(merged.interiors) > 0:
            failures.append({
                "check": "exterior_envelope_is_single_closed_polygon",
                "detail": f"room union has {len(merged.interiors)} interior hole(s) — gap(s) fully enclosed by rooms",
                "entity_ids": all_room_ids,
            })
    else:
        failures.append({
            "check": "exterior_envelope_is_single_closed_polygon",
            "detail": (
                f"room union produced an unexpected geometry type "
                f"'{merged.geom_type}' (expected Polygon or MultiPolygon)"
            ),
            "entity_ids": all_room_ids,
        })
    return failures


_CHECKS = [
    check_no_negative_room_areas,
    check_rooms_do_not_overlap,
    check_openings_lie_on_walls,
    check_openings_do_not_overlap_on_same_wall,
    check_walls_meet_at_endpoints,
    check_walls_form_closed_room_boundaries,
    check_room_polygons_match_boundary_walls,
    check_exterior_envelope_is_single_closed_polygon,
]


def verify_layer_a(layout: Layout) -> VerifierResult:
    """Run all Layer A geometry checks against a Layout.

    Runs ALL checks even after the first failure — the caller wants the
    full failure manifest, not just the first bug. Layer A is a hard gate:
    it never scores (score=None) and never warns (warnings=[]), only passes
    or fails.
    """
    start = time.perf_counter()

    checks_run = [fn.__name__[len("check_"):] for fn in _CHECKS]
    all_failures: list[dict] = []
    for fn in _CHECKS:
        all_failures.extend(fn(layout))

    # Deterministic ordering. `detail` is the tiebreaker for two failures
    # that share the same check + entity_ids but differ only in message
    # text (e.g. a wall dangling at both ends produces two
    # walls_meet_at_endpoints entries distinguished only by which endpoint
    # coordinate is in the detail string). Python's list.sort() is stable,
    # so fully identical dicts retain the order _CHECKS already produced
    # them in.
    all_failures.sort(key=lambda f: (f["check"], tuple(sorted(f["entity_ids"])), f["detail"]))

    elapsed_ms = (time.perf_counter() - start) * 1000.0

    return VerifierResult(
        verifier_name="layer_a_geometry",
        passed=len(all_failures) == 0,
        checks_run=checks_run,
        failures=all_failures,
        warnings=[],
        score=None,
        elapsed_ms=elapsed_ms,
    )
