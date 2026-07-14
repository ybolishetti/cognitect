"""StubGenerator — wraps the existing kiwisolver + shelf-packer to
produce typed Layouts from a FloorPlanSpec.

Purpose: give the rest of Architecture C a working generator TODAY so
Layer A/C/best-of-N can be developed against real Layouts without waiting
on the LLM path. Also: this is what CI runs — deterministic, no API key,
sub-second.

STRICT scope: this module owns the FloorPlanSpec → Layout translation and
NOTHING ELSE. It does NOT:
  - Call the intent parser
  - Mutate any existing PlanManager state
  - Import from api.* (routes are downstream)
  - Emit Layouts that fail schema validation (raise GeneratorFailure
    instead — leaves the audit trail cleaner than Pydantic surfacing
    mid-verifier)

Known Limitations:
  - Multi-row layouts drop interior walls between rows. When the shelf-
    packer wraps to a new row and row widths differ, adjacent-row edges
    partially overlap without sharing endpoints. `_make_walls` uses exact-
    endpoint dedup (frozenset of corners), so partial overlaps produce two
    separate exterior walls rather than one interior wall. Layer A still
    passes on the result (rooms don't overlap, envelope is connected via
    shared corners), but Layer B connectivity checks WILL flag it and
    best-of-N (DRAFT 6) will down-score it. This is intentional: the stub
    is a warm-up target for verifiers, and DRAFT 4's PromptedGenerator
    won't inherit the limitation. To fix properly, `_make_walls` would
    need to split overlapping edges into sub-edges at the overlap
    boundaries — deferred until it becomes a real problem.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from engine.generators.base import (
    GeneratorFailure,
    LayoutGenerator,
)
from engine.layout import (
    FloorPlanSpec,
    Layout,
    Opening,
    Room,
    Wall,
)

# StubGenerator version stamp. BUMP THIS whenever the translation logic
# changes in a way that would produce different Layouts for the same
# FloorPlanSpec — e.g. wall thickness default changes, opening insertion
# heuristics change, room ordering changes.
STUB_VERSION = "2026-07-13"

# Wall thickness for stub output. Real fine-tuned generator will vary
# this per-wall (load-bearing vs partition). Stub uses one value for all
# walls — Layer B may downgrade its score for that, which is fine.
STUB_WALL_THICKNESS_FT = 0.5

# Default door width / offset within each interior wall. Kept in one
# place so Layer C tests can reference the same constants when hand-
# authoring expected failures.
DEFAULT_DOOR_WIDTH_FT = 3.0
DEFAULT_DOOR_OFFSET_MARGIN_FT = 1.0

# RoomSpec.room_type (intent_parser) has a narrower literal than the Arch C
# RoomType used by RoomRequirement — it lacks "utility" and "closet". Map
# any RoomRequirement.room_type value not in this set to "other" when
# constructing the intermediate RoomSpec. The ORIGINAL RoomRequirement type
# is preserved for the emitted Layout.Room (which uses the wider literal).
_ROOM_SPEC_ALLOWED_TYPES = frozenset({
    "bedroom", "bathroom", "kitchen", "living", "dining",
    "hallway", "office", "garage", "other",
})


def _map_room_type_for_solver(rt: str) -> str:
    return rt if rt in _ROOM_SPEC_ALLOWED_TYPES else "other"


# Canonical row height applied to every room whose RoomRequirement doesn't
# specify its own aspect_ratio. The underlying shelf-packer defaults an
# unconstrained room to a square (area**0.5 x area**0.5); two same-row
# rooms of different areas then end up as different-height squares that
# only share a PARTIAL vertical edge, which _make_walls' exact-endpoint
# dedup can't merge into one interior wall (and Room.boundary_wall_ids
# needs exactly one wall per side — see the DRAFT's CCW
# [bottom, right, top, left] contract). Forcing every room onto the same
# row height means adjacent same-row rooms always share a FULL vertical
# edge, so the dedup produces the interior wall Layer A (and
# test_interior_walls_bound_exactly_two_rooms) expects. Rooms with an
# explicit aspect_ratio in the spec keep the user's request instead.
STUB_ROW_HEIGHT_FT = 12.0


class StubGenerator(LayoutGenerator):
    """FloorPlanSpec → Layout via the existing shelf-packing solver.

    Deterministic: seeded from a hash of the spec, so the same spec
    always yields the same list of candidates (in the same order).
    """

    @property
    def name(self) -> str:
        return "stub"

    def generate(self, spec: FloorPlanSpec) -> list[Layout]:
        """Produce 1..spec.n_candidates Layouts.

        The stub currently emits ONE Layout regardless of n_candidates
        (the underlying solver is deterministic). This is intentional and
        called out in the docstring — best-of-N (DRAFT 6) will still
        work because it treats "1 candidate" as a valid input. When the
        prompted generator lands, best-of-N gets its actual variety.
        """
        start = time.perf_counter()

        try:
            layout = self._build_layout_from_spec(spec)
        except GeneratorFailure:
            raise
        except Exception as exc:
            # Wrap ANY unexpected error in GeneratorFailure so the
            # contract holds: callers get either a Layout list or a
            # GeneratorFailure — never a bare traceback out of a
            # LayoutGenerator.
            raise GeneratorFailure(
                message=f"StubGenerator failed: {type(exc).__name__}: {exc}",
                spec_id=spec.spec_id,
                generator_name=self.name,
                reason_code="stub_internal_error",
            ) from exc

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        seed = self._seed_from_spec(spec)
        layout.metadata["generator"] = {
            "name": self.name,
            "version": STUB_VERSION,
            "seed": seed,
            "elapsed_ms": round(elapsed_ms, 3),
        }
        return [layout]

    # ------------------------------------------------------------------
    # Translation helpers
    # ------------------------------------------------------------------

    def _build_layout_from_spec(self, spec: FloorPlanSpec) -> Layout:
        """Translate a FloorPlanSpec into a Layout by:
          1. Resolving each RoomRequirement into a target area + aspect
          2. Running the existing shelf-packer to get (x, y, w, h) per room
          3. Emitting a Room per shelf-packed rectangle
          4. Emitting Walls for every rectangle edge, DEDUPED across
             shared edges (so two adjacent rooms share one interior wall)
          5. Emitting one default door Opening per interior wall

        Everything here is deterministic given the same spec.
        """
        # Import the existing shelf-packer locally to avoid a hard
        # top-level dependency (also keeps this module import-clean when
        # constraint_solver is being refactored).
        from engine.constraint_solver.solver import (
            ConstraintSolver,
            ConstraintUnsatisfiableError,
        )
        from engine.intent_parser.schemas import (
            FloorPlanState,
            RoomSpec,
        )

        # Step 1 — build a FloorPlanState the existing solver understands.
        # NOTE: FloorPlanState.rooms is dict[room_id -> RoomSpec] (NOT a
        # list), and RoomSpec has no room_id field of its own — the room
        # id lives only as the dict key.
        #
        # Also: RoomRequirement.room_type may include "utility" or "closet"
        # which the intent_parser RoomSpec.room_type literal doesn't accept.
        # Map to "other" for the solver's RoomSpec, but keep the ORIGINAL
        # type for the emitted Layout.Room via room_type_by_id.
        rooms_state: dict[str, RoomSpec] = {}
        room_type_by_id: dict[str, str] = {}   # original RoomRequirement.room_type
        room_name_by_id: dict[str, str] = {}   # original RoomRequirement.name
        for i, req in enumerate(spec.room_requirements):
            room_id = f"room_{i:03d}"
            room_type_by_id[room_id] = req.room_type
            room_name_by_id[room_id] = req.name

            area = (
                req.preferred_area_sqft
                or req.min_area_sqft
                or 150.0  # neutral fallback; Layer B may complain
            )
            aspect_ratio = req.aspect_ratio
            if aspect_ratio is None:
                aspect_ratio = area / (STUB_ROW_HEIGHT_FT ** 2)
            rooms_state[room_id] = RoomSpec(
                name=req.name,
                room_type=_map_room_type_for_solver(req.room_type),
                area_sqft=area,
                aspect_ratio=aspect_ratio,
            )

        plan_state = FloorPlanState(
            plan_id=f"plan_{_slug_from_spec_id(spec.spec_id)}",
            rooms=rooms_state,
            constraints=[],  # adjacency requirements are a DRAFT 4 problem
            coordinate_matrix={},
        )

        # Step 2 — run the existing solver to get positions & sizes
        solver = ConstraintSolver()
        try:
            matrix = solver.solve(plan_state=plan_state)
        except ConstraintUnsatisfiableError as exc:
            raise GeneratorFailure(
                message=f"solver could not satisfy constraints: {exc}",
                spec_id=spec.spec_id,
                generator_name="stub",
                reason_code="solver_infeasible",
            ) from exc
        except Exception as exc:
            raise GeneratorFailure(
                message=f"solver raised unexpectedly: {type(exc).__name__}: {exc}",
                spec_id=spec.spec_id,
                generator_name="stub",
                reason_code="solver_internal_error",
            ) from exc

        if not matrix:
            raise GeneratorFailure(
                message="solver produced an empty coordinate matrix",
                spec_id=spec.spec_id,
                generator_name="stub",
                reason_code="solver_empty_output",
            )

        # Steps 3–5 — translate the coordinate_matrix into typed Layout
        return _matrix_to_layout(
            plan_state=plan_state,
            matrix=matrix,
            spec=spec,
            room_type_by_id=room_type_by_id,
            room_name_by_id=room_name_by_id,
        )

    def _seed_from_spec(self, spec: FloorPlanSpec) -> int:
        """Stable seed from the spec — same spec, same seed, same output."""
        h = hashlib.sha256(spec.spec_id.encode("utf-8")).hexdigest()
        return int(h[:8], 16)


# ----------------------------------------------------------------------
# Free functions — factored out so tests can hit them directly without
# instantiating the whole generator.
# ----------------------------------------------------------------------

def _slug_from_spec_id(spec_id: str) -> str:
    """Extract the trailing slug from a spec_id for use in a plan_id."""
    return spec_id.split("spec_", 1)[-1] or "unknown"


@dataclass
class _PartialRoom:
    """Everything needed to build a Room except boundary_wall_ids.

    Room.boundary_wall_ids has min_length=3, so a real Room can't be
    constructed until walls are known. _PartialRoom defers that
    construction to _attach_boundary_walls instead of patching a Room
    after the fact (which would mean either invalid intermediate state or
    a wasted model_copy validation round-trip).
    """

    id: str
    name: str
    room_type: str
    vertices: list[tuple[float, float]]
    area_sqft: float
    ceiling_height_ft: float = 9.0
    metadata: dict = field(default_factory=dict)


def _room_edges_ccw(
    x: float, y: float, w: float, h: float
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return the 4 edges of a rectangle in CCW order: bottom, right, top, left."""
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [
        (corners[0], corners[1]),  # bottom
        (corners[1], corners[2]),  # right
        (corners[2], corners[3]),  # top
        (corners[3], corners[0]),  # left
    ]


def _make_rooms(
    matrix: dict[str, dict],
    room_type_by_id: dict[str, str],
    room_name_by_id: dict[str, str],
) -> tuple[list[_PartialRoom], dict[str, tuple[float, float, float, float]]]:
    """Turn each coordinate_matrix entry into a _PartialRoom + its bbox.

    matrix entries are {x, y, width, height} in feet, math coords (y up).
    Vertices are the 4 corners in CCW order starting bottom-left, closed.
    """
    rooms: list[_PartialRoom] = []
    room_bboxes: dict[str, tuple[float, float, float, float]] = {}

    for room_id, coords in matrix.items():
        x, y, w, h = coords["x"], coords["y"], coords["width"], coords["height"]
        vertices = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        rooms.append(
            _PartialRoom(
                id=room_id,
                name=room_name_by_id[room_id],
                room_type=room_type_by_id[room_id],
                vertices=vertices,
                area_sqft=w * h,
            )
        )
        room_bboxes[room_id] = (x, y, w, h)

    return rooms, room_bboxes


def _make_walls(
    rooms: list[_PartialRoom],
    room_bboxes: dict[str, tuple[float, float, float, float]],
) -> tuple[list[Wall], dict[frozenset, str]]:
    """Emit one Wall per unique rectangle edge, deduped across shared edges.

    Two adjacent rooms sharing an edge produce ONE wall with bounds_rooms
    of length 2 — detected by normalizing each edge to
    frozenset({(x1,y1),(x2,y2)}) so direction doesn't matter.
    """
    walls_by_id: dict[str, Wall] = {}
    wall_bounds_by_edge: dict[frozenset, str] = {}
    wall_index = 0

    for room in rooms:
        x, y, w, h = room_bboxes[room.id]
        for a, b in _room_edges_ccw(x, y, w, h):
            edge = frozenset({a, b})
            existing_wall_id = wall_bounds_by_edge.get(edge)
            if existing_wall_id is None:
                wall_id = f"wall_{wall_index:04d}"
                wall_index += 1
                walls_by_id[wall_id] = Wall(
                    id=wall_id,
                    start=a,
                    end=b,
                    thickness_ft=STUB_WALL_THICKNESS_FT,
                    bounds_rooms=[room.id],
                )
                wall_bounds_by_edge[edge] = wall_id
            else:
                existing = walls_by_id[existing_wall_id]
                walls_by_id[existing_wall_id] = existing.model_copy(
                    update={"bounds_rooms": [*existing.bounds_rooms, room.id]}
                )

    walls = list(walls_by_id.values())
    for wall in walls:
        assert 1 <= len(wall.bounds_rooms) <= 2, (
            f"wall {wall.id} has bounds_rooms of length "
            f"{len(wall.bounds_rooms)} ({wall.bounds_rooms}) — dedupe key is "
            f"over-merging distinct edges"
        )

    return walls, wall_bounds_by_edge


def _make_openings(walls: list[Wall]) -> tuple[list[Opening], list[str]]:
    """One default door per interior wall (bounds_rooms length == 2),
    centered along the wall.

    Returns (openings, skipped_wall_ids) — skipped_wall_ids are interior
    walls too short to fit a door; recorded by the caller into
    Layout.metadata["generator_extra"]["skipped_doors"] rather than
    treated as a failure (Layer C decides code compliance later).
    """
    openings: list[Opening] = []
    skipped_wall_ids: list[str] = []
    min_length = DEFAULT_DOOR_WIDTH_FT + 2 * DEFAULT_DOOR_OFFSET_MARGIN_FT

    opening_index = 0
    for wall in walls:
        if len(wall.bounds_rooms) != 2:
            continue
        if wall.length_ft <= min_length:
            skipped_wall_ids.append(wall.id)
            continue
        offset = (wall.length_ft - DEFAULT_DOOR_WIDTH_FT) / 2
        openings.append(
            Opening(
                id=f"opening_{opening_index:04d}",
                opening_type="door",
                wall_id=wall.id,
                offset_ft=offset,
                width_ft=DEFAULT_DOOR_WIDTH_FT,
                swings_into_room_id=wall.bounds_rooms[0],
            )
        )
        opening_index += 1

    return openings, skipped_wall_ids


def _attach_boundary_walls(
    rooms: list[_PartialRoom],
    room_bboxes: dict[str, tuple[float, float, float, float]],
    wall_bounds_by_edge: dict[frozenset, str],
) -> list[Room]:
    """Construct the real Room objects now that walls (and their ids) exist.

    boundary_wall_ids traces CCW starting from the bottom edge:
    [bottom, right, top, left].
    """
    finished_rooms: list[Room] = []
    for partial in rooms:
        x, y, w, h = room_bboxes[partial.id]
        boundary_wall_ids = [
            wall_bounds_by_edge[frozenset({a, b})]
            for a, b in _room_edges_ccw(x, y, w, h)
        ]
        finished_rooms.append(
            Room(
                id=partial.id,
                name=partial.name,
                room_type=partial.room_type,
                vertices=partial.vertices,
                area_sqft=partial.area_sqft,
                boundary_wall_ids=boundary_wall_ids,
                ceiling_height_ft=partial.ceiling_height_ft,
                metadata=partial.metadata,
            )
        )
    return finished_rooms


def _matrix_to_layout(
    plan_state: Any,
    matrix: dict[str, dict],
    spec: FloorPlanSpec,
    room_type_by_id: dict[str, str],
    room_name_by_id: dict[str, str],
) -> Layout:
    """Turn a shelf-packed coordinate_matrix into a typed Layout.

    This is where the interesting geometry logic lives. Broken into:
      - _make_rooms: rectangle → Room with CCW vertices. Uses
        room_type_by_id/room_name_by_id to preserve the ORIGINAL
        RoomRequirement fields (which may include RoomType values like
        "utility"/"closet" that the intent_parser RoomSpec had to map
        to "other").
      - _make_walls: dedupe edges shared between rectangles into one
        interior wall bounding two rooms
      - _make_openings: one door per interior wall, centered
    """
    rooms, room_bboxes = _make_rooms(matrix, room_type_by_id, room_name_by_id)
    walls, wall_bounds_by_edge = _make_walls(rooms, room_bboxes)
    openings, skipped_doors = _make_openings(walls)

    # Attach boundary_wall_ids to each Room now that walls are known
    rooms_with_boundaries = _attach_boundary_walls(rooms, room_bboxes, wall_bounds_by_edge)

    max_x = max((x for r in rooms_with_boundaries for x, _ in r.vertices), default=0.0)
    max_y = max((y for r in rooms_with_boundaries for _, y in r.vertices), default=0.0)

    return Layout(
        plan_id=plan_state.plan_id,
        rooms=rooms_with_boundaries,
        walls=walls,
        openings=openings,
        extent_x_ft=max(max_x, 1.0),
        extent_y_ft=max(max_y, 1.0),
        metadata={"generator_extra": {"skipped_doors": skipped_doors}},
    )
