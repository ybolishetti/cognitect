"""Layer B — structural sanity advisory scorer (Architecture C).

Advisory only. Never rejects. Emits a VerifierResult with:
  - passed = True (always — the Layout is not rejected by Layer B)
  - warnings = [...] — soft issues found
  - score in [0, 1] — 1 = "no concerns", 0 = "many concerns"

Best-of-N uses Layer B's score as a tiebreaker among candidates that both
survived Layer A + C. It does NOT gate anything.

Checks (all advisory):
  1. Long unsupported walls          — walls > 30ft without an intersecting
                                        perpendicular near mid-span
  2. Aspect ratio outliers            — rooms with bounding-box side ratio
                                        > 4:1 (hard to frame)
  3. Column-free spans                — living/dining/office rooms with a
                                        short-axis bounding-box span > 20ft
  4. Bathroom / kitchen stacking      — bonus if wet rooms share walls with
                                        each other (plumbing efficiency)
"""

from __future__ import annotations

import time

from shapely.geometry import Point

from engine.layout import FloorPlanSpec, Layout, VerifierResult
from engine.verifiers.layer_a_helpers import room_polygon, wall_linestring

LONG_WALL_THRESHOLD_FT = 30.0
ASPECT_RATIO_OUTLIER_THRESHOLD = 4.0
COLUMN_FREE_SPAN_THRESHOLD_FT = 20.0
COLUMN_FREE_ELIGIBLE_TYPES = {"living", "dining", "office"}
WET_ROOM_TYPES = {"bathroom", "kitchen", "utility"}

# Perpendicular-distance tolerance for "does this point lie on this wall's
# line" — an authoring-scale allowance, matching Layer A's COORDINATE_TOLERANCE_FT.
POINT_ON_LINE_TOLERANCE_FT = 0.01

# A candidate support point within this distance of either of the long
# wall's own endpoints doesn't count as mid-span support — it's just another
# wall meeting at the same corner.
CORNER_EXCLUSION_FT = 0.5


def _check_long_walls(layout: Layout) -> tuple[float, list[dict]]:
    """Long walls (> 30ft) need a perpendicular intersecting near mid-span.

    For each long wall, checks whether any *other* wall's endpoint lies on
    the long wall's line strictly between its own two endpoints (not at a
    shared corner). Sub-score = 1 - (unsupported / total long walls).
    """
    warnings: list[dict] = []
    long_walls = [w for w in layout.walls if wall_linestring(w).length > LONG_WALL_THRESHOLD_FT]
    if not long_walls:
        return 1.0, warnings

    unsupported = 0
    for wall in long_walls:
        line = wall_linestring(wall)
        length = line.length
        supported = False
        for other in layout.walls:
            if other.id == wall.id:
                continue
            for pt in (other.start, other.end):
                point = Point(pt)
                if line.distance(point) > POINT_ON_LINE_TOLERANCE_FT:
                    continue
                along = line.project(point)
                if CORNER_EXCLUSION_FT <= along <= length - CORNER_EXCLUSION_FT:
                    supported = True
                    break
            if supported:
                break
        if not supported:
            unsupported += 1
            warnings.append({
                "check": "long_walls",
                "detail": (
                    f"wall {wall.id} ({length:.2f}ft) exceeds {LONG_WALL_THRESHOLD_FT}ft "
                    f"with no perpendicular support intersecting its mid-span"
                ),
                "entity_ids": [wall.id],
            })

    score = 1.0 - (unsupported / len(long_walls))
    return score, warnings


def _check_room_aspect_ratios(layout: Layout) -> tuple[float, list[dict]]:
    """Rooms with a bounding-box long/short side ratio > 4:1 are flagged.

    Sub-score = 1 - (outlier_rooms / total_rooms). No rooms → 1.0.
    """
    warnings: list[dict] = []
    if not layout.rooms:
        return 1.0, warnings

    outliers = 0
    for room in layout.rooms:
        minx, miny, maxx, maxy = room_polygon(room).bounds
        width, height = maxx - minx, maxy - miny
        short_side, long_side = min(width, height), max(width, height)
        ratio = long_side / short_side if short_side > 0 else float("inf")
        if ratio > ASPECT_RATIO_OUTLIER_THRESHOLD:
            outliers += 1
            warnings.append({
                "check": "room_aspect_ratios",
                "detail": (
                    f"room {room.id} bounding-box aspect ratio {ratio:.2f}:1 exceeds "
                    f"{ASPECT_RATIO_OUTLIER_THRESHOLD}:1 (hard to frame)"
                ),
                "entity_ids": [room.id],
            })

    score = 1.0 - (outliers / len(layout.rooms))
    return score, warnings


def _check_column_free_spans(layout: Layout) -> tuple[float, list[dict]]:
    """living/dining/office rooms with a short-axis bounding-box span > 20ft
    plausibly want a column-free span. Sub-score = 1 - (offenders / eligible).

    No eligible rooms (e.g. all bedrooms) → 1.0.
    """
    warnings: list[dict] = []
    eligible = [r for r in layout.rooms if r.room_type in COLUMN_FREE_ELIGIBLE_TYPES]
    if not eligible:
        return 1.0, warnings

    offenders = 0
    for room in eligible:
        minx, miny, maxx, maxy = room_polygon(room).bounds
        short_side = min(maxx - minx, maxy - miny)
        if short_side > COLUMN_FREE_SPAN_THRESHOLD_FT:
            offenders += 1
            warnings.append({
                "check": "column_free_spans",
                "detail": (
                    f"room {room.id} ({room.room_type}) has a {short_side:.2f}ft "
                    f"short-axis span exceeding {COLUMN_FREE_SPAN_THRESHOLD_FT}ft "
                    f"without an implied column"
                ),
                "entity_ids": [room.id],
            })

    score = 1.0 - (offenders / len(eligible))
    return score, warnings


def _check_wet_room_stacking(layout: Layout) -> tuple[float, list[dict]]:
    """Bonus for bathroom/kitchen/utility rooms sharing a wall with each
    other (plumbing efficiency).

    Sub-score = min(1.0, adjacent_wet_room_pairs / max(1, wet_rooms - 1)).
    0 wet rooms → 1.0 (nothing to stack, don't penalize).
    """
    warnings: list[dict] = []
    wet_rooms = [r for r in layout.rooms if r.room_type in WET_ROOM_TYPES]
    if not wet_rooms:
        return 1.0, warnings

    wet_ids = {r.id for r in wet_rooms}
    adjacent_pairs: set[tuple[str, str]] = set()
    for wall in layout.walls:
        if len(wall.bounds_rooms) == 2:
            a, b = wall.bounds_rooms
            if a in wet_ids and b in wet_ids:
                adjacent_pairs.add(tuple(sorted((a, b))))

    denom = max(1, len(wet_rooms) - 1)
    score = min(1.0, len(adjacent_pairs) / denom)
    if score < 1.0:
        warnings.append({
            "check": "wet_room_stacking",
            "detail": (
                f"only {len(adjacent_pairs)} of an expected {denom} wet-room "
                f"adjacency pair(s) found among {len(wet_rooms)} wet room(s) "
                f"— plumbing efficiency opportunity"
            ),
            "entity_ids": sorted(wet_ids),
        })
    return score, warnings


def verify_layer_b(layout: Layout, spec: FloorPlanSpec | None = None) -> VerifierResult:
    """Score a Layout on structural sanity heuristics.

    Args:
      layout: the Layout to check
      spec:   optional FloorPlanSpec — unused today (kept in signature for
              future rules that need user intent, e.g. "user asked for
              open floor plan" would soften column-free-span penalty).
    """
    start = time.perf_counter()

    warnings: list[dict] = []
    sub_scores: list[float] = []

    long_walls_score, long_walls_warnings = _check_long_walls(layout)
    warnings.extend(long_walls_warnings)
    sub_scores.append(long_walls_score)

    aspect_score, aspect_warnings = _check_room_aspect_ratios(layout)
    warnings.extend(aspect_warnings)
    sub_scores.append(aspect_score)

    span_score, span_warnings = _check_column_free_spans(layout)
    warnings.extend(span_warnings)
    sub_scores.append(span_score)

    stacking_score, stacking_warnings = _check_wet_room_stacking(layout)
    warnings.extend(stacking_warnings)
    sub_scores.append(stacking_score)

    score = sum(sub_scores) / len(sub_scores) if sub_scores else 1.0
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    return VerifierResult(
        verifier_name="layer_b_structural",
        passed=True,  # ADVISORY — always passes
        checks_run=["long_walls", "aspect_ratios", "column_free_spans", "wet_room_stacking"],
        failures=[],  # Layer B never fails; use warnings
        warnings=warnings,
        score=score,
        elapsed_ms=elapsed_ms,
    )
