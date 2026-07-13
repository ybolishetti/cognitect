# DRAFT_ARCH_C_3 — LayoutGenerator Interface + StubGenerator

**Status:** Ready to send to Cursor Composer
**Depends on:** DRAFT_ARCH_C_1 (Layout schema — merged in PR #14) — ONLY. Independent of Layer A / PR #15.
**Sends to:** Cursor Composer, one shot
**Est. scope:** ~350 new lines of code + ~250 lines of tests, 0 lines changed in existing files
**Branch name:** `claude/arch-c-draft-3-generator-iface`

---

## Goal

Introduce the `LayoutGenerator` abstract interface from `ARCHITECTURE_C.md` §Key Decision #3, and land the first concrete implementation: `StubGenerator`, which wraps the existing kiwisolver + shelf-packer to emit typed `Layout` objects from a `FloorPlanSpec`.

This DRAFT is a **pure additive scaffold**. It touches no existing pipeline code, no API routes, and no verifiers. `PlanManager` and `/instruct` continue to work exactly as before. The `StubGenerator` becomes the deterministic, zero-API-cost path used by CI/tests until `PromptedGenerator` (DRAFT 4) lands.

**Non-goals:**
- Not calling any LLM (that's DRAFT 4)
- Not wiring anything into API routes (that's DRAFT 8)
- Not running verifiers on generator output (that's DRAFT 6)
- Not deprecating `PlanManager` / `FloorPlanState` (they stay — edit flow keeps using them)

---

## What Ships

### New files

```
engine/generators/
├── __init__.py            # exports LayoutGenerator, StubGenerator, GeneratorFactory
├── base.py                # LayoutGenerator ABC + GeneratorFailure + factory
└── stub.py                # StubGenerator: FloorPlanSpec → Layout via existing solver
tests/
└── test_stub_generator.py # ~15 tests
```

Nothing else is modified. Zero changes to existing files.

---

## Interface Spec

### `engine/generators/base.py`

```python
"""Architecture C — LayoutGenerator interface.

The pluggable seam between "intent" (FloorPlanSpec) and "geometry"
(Layout). Three implementations are planned:

- StubGenerator (this DRAFT): wraps the existing kiwisolver + shelf-packer.
  Deterministic. Zero API cost. Used by CI and as the fallback path.
- PromptedGenerator (DRAFT 4): claude-sonnet with heavy prompting and
  JSON-schema-constrained output. Runtime default until FineTuned lands.
- FineTunedGenerator (DRAFT 7): NotImplementedError placeholder.

Selection happens at construction time via GeneratorFactory (which reads
the LAYOUT_GENERATOR env var). Nothing downstream — Layer A/B/C, best-of-N,
API routes — knows or cares which implementation is behind the ABC.

Architecture rule: this module NEVER touches the LLM directly, NEVER
touches geometry directly. It defines a contract; implementations fulfil
it.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from engine.layout import FloorPlanSpec, Layout


class GeneratorFailure(Exception):
    """Raised when a generator cannot produce ANY candidate Layout.

    Distinct from "produced N candidates but all failed Layer A/C" — that
    is a best-of-N concern (DRAFT 6). This exception means the generator
    itself refused or errored at the pre-verification stage: bad spec,
    unresolvable constraints, LLM timeout, etc.
    """

    def __init__(self, message: str, spec_id: str, generator_name: str, reason_code: str):
        super().__init__(message)
        self.spec_id = spec_id
        self.generator_name = generator_name
        self.reason_code = reason_code  # e.g. "solver_timeout", "invalid_spec", "llm_refused"


@dataclass(frozen=True)
class GeneratorMetadata:
    """Provenance stamp attached to every generated Layout.

    Written to Layout.metadata["generator"] on emission. The audit
    manifest (DRAFT 6) reads this to populate the top-level audit block.
    Keep this dataclass frozen so it's hashable and cheap to compare across
    candidates.
    """

    name: str                      # e.g. "stub", "prompted-claude-sonnet-4-5"
    version: str                   # ISO date or semver — bump on behavioural change
    seed: int | None = None        # RNG seed for reproducibility (Stub uses this; Prompted may not)
    extra: dict = field(default_factory=dict)  # freeform, per-implementation


class LayoutGenerator(ABC):
    """Produce N candidate Layouts from a FloorPlanSpec.

    Implementations MUST:
      - Return a list of length between 1 and spec.n_candidates.
      - Populate Layout.metadata["generator"] on every returned Layout.
      - Raise GeneratorFailure (not a bare exception) if zero candidates
        can be produced. Do NOT return an empty list — that's a contract
        violation caught by the tests.
      - Be re-entrant / stateless per call. State that spans calls (LLM
        conversation, warm caches) is a per-implementation concern.

    Implementations SHOULD:
      - Log elapsed_ms for each candidate.
      - Set Layout.metadata["generator_extra"] with any diagnostics useful
        for post-mortem (prompt token counts, solver iteration counts).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable identifier (e.g. 'stub', 'prompted'). Used by
        GeneratorFactory dispatch AND written to the audit manifest."""

    @abstractmethod
    def generate(self, spec: FloorPlanSpec) -> list[Layout]:
        """Return 1..spec.n_candidates Layouts. Raise GeneratorFailure on
        total failure. Never return an empty list."""


class GeneratorFactory:
    """Constructs a LayoutGenerator from the LAYOUT_GENERATOR env var.

    Valid values: "stub" (default), "prompted", "finetuned".
    Unknown values raise ValueError at construction time — fail fast.
    """

    @staticmethod
    def from_env() -> LayoutGenerator:
        kind = os.environ.get("LAYOUT_GENERATOR", "stub").lower()
        return GeneratorFactory.by_name(kind)

    @staticmethod
    def by_name(kind: str) -> LayoutGenerator:
        if kind == "stub":
            # Local import to avoid a cycle with the stub module's own
            # imports of engine.generators.base symbols.
            from engine.generators.stub import StubGenerator
            return StubGenerator()
        if kind == "prompted":
            raise NotImplementedError(
                "PromptedGenerator ships in DRAFT_ARCH_C_4. Set "
                "LAYOUT_GENERATOR=stub for now."
            )
        if kind == "finetuned":
            raise NotImplementedError(
                "FineTunedGenerator ships in DRAFT_ARCH_C_7. Set "
                "LAYOUT_GENERATOR=stub for now."
            )
        raise ValueError(
            f"Unknown LAYOUT_GENERATOR value {kind!r}. "
            f"Valid: 'stub', 'prompted', 'finetuned'."
        )
```

### `engine/generators/stub.py`

```python
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
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from engine.generators.base import (
    GeneratorFailure,
    GeneratorMetadata,
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
        from engine.constraint_solver.solver import ConstraintSolver
        from engine.intent_parser.schemas import (
            FloorPlanState,
            RoomSpec,
        )

        # Step 1 — build a FloorPlanState the existing solver understands
        rooms_state: list[RoomSpec] = []
        for i, req in enumerate(spec.room_requirements):
            area = (
                req.preferred_area_sqft
                or req.min_area_sqft
                or 150.0  # neutral fallback; Layer B may complain
            )
            rooms_state.append(
                RoomSpec(
                    room_id=f"room_{i:03d}",
                    name=req.name,
                    room_type=req.room_type,
                    area_sqft=area,
                    aspect_ratio=req.aspect_ratio,
                )
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
        except Exception as exc:
            raise GeneratorFailure(
                message=f"underlying solver failed: {exc}",
                spec_id=spec.spec_id,
                generator_name="stub",
                reason_code="solver_infeasible",
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


def _matrix_to_layout(
    plan_state: Any,
    matrix: dict[str, dict],
    spec: FloorPlanSpec,
) -> Layout:
    """Turn a shelf-packed coordinate_matrix into a typed Layout.

    This is where the interesting geometry logic lives. Broken into:
      - _make_rooms: rectangle → Room with CCW vertices
      - _make_walls: dedupe edges shared between rectangles into one
        interior wall bounding two rooms
      - _make_openings: one door per interior wall, centered
    """
    rooms, room_bboxes = _make_rooms(plan_state, matrix)
    walls, wall_bounds_by_edge = _make_walls(rooms, room_bboxes)
    openings = _make_openings(walls)

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
        metadata={},  # generator stamp added by StubGenerator.generate
    )


# The Cursor Composer implementation is expected to fill in:
#   _make_rooms(plan_state, matrix) -> tuple[list[Room], dict[str, tuple[float,float,float,float]]]
#   _make_walls(rooms, room_bboxes) -> tuple[list[Wall], dict[frozenset, list[str]]]
#   _make_openings(walls) -> list[Opening]
#   _attach_boundary_walls(rooms, room_bboxes, wall_bounds_by_edge) -> list[Room]
#
# Each function is small (~30–60 lines) and hits ONE responsibility. See
# the "Translation Rules" section below for the exact geometric contract
# each must satisfy.
```

### `engine/generators/__init__.py`

```python
"""Architecture C — LayoutGenerator interface + implementations."""

from engine.generators.base import (
    GeneratorFactory,
    GeneratorFailure,
    GeneratorMetadata,
    LayoutGenerator,
)
from engine.generators.stub import STUB_VERSION, StubGenerator

__all__ = [
    "GeneratorFactory",
    "GeneratorFailure",
    "GeneratorMetadata",
    "LayoutGenerator",
    "StubGenerator",
    "STUB_VERSION",
]
```

---

## Translation Rules (STRICT — verifiers assume these hold)

### Room construction (`_make_rooms`)

- Each entry in the shelf-packer's `matrix` has `x`, `y`, `width`, `height` in feet, math coords (y increases up).
- Emit exactly one `Room` per entry.
- `Room.id` = the room_id from `plan_state` (matches the coordinate_matrix key).
- Vertices are the 4 corners in CCW order starting bottom-left:
  ```
  [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]  # closed
  ```
- `area_sqft` = `width * height` (matches shoelace within schema's 0.5% tolerance).
- `boundary_wall_ids` is filled in later by `_attach_boundary_walls` — pass `[]` here would fail the schema (min_length=3), so **do not construct the Room object until walls are known**. Two options for Cursor:
  1. Build a `_PartialRoom` dict in `_make_rooms`, defer `Room(...)` construction until `_attach_boundary_walls`.
  2. Build `Room(...)` with a temporary 3-element `boundary_wall_ids` list of wall IDs that don't exist yet, then `model_copy(update=...)` after walls are known.
  Prefer option 1 — no Pydantic churn, no wasted validation.
- Also return `room_bboxes: dict[room_id → (x, y, w, h)]` so `_make_walls` doesn't need to re-parse `matrix`.

### Wall construction (`_make_walls`)

For every rectangle, its 4 edges are: bottom, right, top, left. Each edge is a segment from one corner to the next in CCW order.

**Deduplication rule (this is the whole point):** two adjacent rooms with a shared edge must produce **one** wall, not two. Detection:

- Build a dict keyed by the edge's endpoints, normalized to `frozenset({(x1, y1), (x2, y2)})` so direction doesn't matter.
- The first time an edge is seen, emit `Wall(start=A, end=B, bounds_rooms=[room_id], ...)`.
- If the same edge is seen again from another room, do NOT emit a new wall. Instead: append the second room to that wall's `bounds_rooms`.
  - Because Wall is a Pydantic model, "append" means `walls_by_edge[edge] = walls_by_edge[edge].model_copy(update={"bounds_rooms": [...prev, new_room_id]})`. This preserves immutability discipline.

**Wall ID scheme:** `wall_{index:04d}` where index is the emission order (first wall emitted = `wall_0000`). Stable + trivially unique.

**Wall thickness:** `STUB_WALL_THICKNESS_FT` (0.5 ft) for every wall. Fine-tuned generator will vary this.

**bounds_rooms length invariants:**
- Interior wall (shared by 2 rectangles) → `bounds_rooms` has 2 entries → this is what Layer A's `check_walls_meet_at_endpoints` expects.
- Exterior wall (edge on the plan's outer perimeter, no neighbor) → `bounds_rooms` has 1 entry.
- No wall should end up with 0 or >2 entries. **Assert this before returning** — if you see `len(bounds_rooms) > 2`, something is wrong with the dedupe key (edges are being over-merged).

Also return `wall_bounds_by_edge: dict[frozenset → wall_id]` so `_attach_boundary_walls` can look up which wall corresponds to each edge.

### Opening construction (`_make_openings`)

- One default door per **interior wall** (`len(bounds_rooms) == 2`), centered along the wall.
- No openings on exterior walls in the stub (real generator adds windows; stub keeps it minimal, Layer C will flag missing egress windows if the plan needs them — that's Layer C's job, not the stub's).
- `Opening.id` = `opening_{index:04d}`, emission order.
- `Opening.opening_type = "door"`.
- `Opening.width_ft = DEFAULT_DOOR_WIDTH_FT` (3.0 ft) if the wall is long enough (`length_ft > DEFAULT_DOOR_WIDTH_FT + 2 * DEFAULT_DOOR_OFFSET_MARGIN_FT`); otherwise **skip this door**. Log a warning to `Layout.metadata["generator_extra"]["skipped_doors"]` so audit can see it. It's not a failure — Layer C decides whether that room needs a door.
- `Opening.offset_ft = (wall.length_ft - width_ft) / 2` (centered).
- `swings_into_room_id` = the first room in `wall.bounds_rooms` (arbitrary but deterministic — Layer B may re-score; StubGenerator does not attempt door-swing intelligence).

### `_attach_boundary_walls`

For each room's bbox, compute its 4 edges in CCW order (same rule as `_make_rooms`), look each edge up in `wall_bounds_by_edge`, and set the room's `boundary_wall_ids = [wall_id_bottom, wall_id_right, wall_id_top, wall_id_left]` — CCW starting from the bottom edge.

The order matters for Layer A's `check_walls_form_closed_room_boundaries` check, which expects `boundary_wall_ids` to trace the room's perimeter in CCW order.

---

## Tests (`tests/test_stub_generator.py`)

Structure — 4 groups, ~15 tests total. All fast, no network, no LLM.

### Group 1 — Interface contract (5 tests)

```python
def test_stub_generator_name_is_stable():
    assert StubGenerator().name == "stub"

def test_factory_returns_stub_by_default():
    # No env var set
    monkeypatch.delenv("LAYOUT_GENERATOR", raising=False)
    gen = GeneratorFactory.from_env()
    assert isinstance(gen, StubGenerator)

def test_factory_returns_stub_when_env_set_to_stub():
    monkeypatch.setenv("LAYOUT_GENERATOR", "stub")
    assert isinstance(GeneratorFactory.from_env(), StubGenerator)

def test_factory_raises_not_implemented_for_prompted():
    monkeypatch.setenv("LAYOUT_GENERATOR", "prompted")
    with pytest.raises(NotImplementedError, match="DRAFT_ARCH_C_4"):
        GeneratorFactory.from_env()

def test_factory_raises_value_error_for_unknown_kind():
    with pytest.raises(ValueError, match="Unknown LAYOUT_GENERATOR"):
        GeneratorFactory.by_name("banana")
```

### Group 2 — Happy path (3 tests)

```python
def test_generate_returns_at_least_one_layout_for_valid_spec():
    spec = _make_spec_2_rooms()  # helper in same file
    layouts = StubGenerator().generate(spec)
    assert len(layouts) >= 1
    assert all(isinstance(l, Layout) for l in layouts)

def test_generated_layout_stamps_generator_metadata():
    spec = _make_spec_2_rooms()
    layout = StubGenerator().generate(spec)[0]
    gen = layout.metadata["generator"]
    assert gen["name"] == "stub"
    assert gen["version"] == STUB_VERSION
    assert isinstance(gen["seed"], int)
    assert gen["elapsed_ms"] > 0

def test_same_spec_produces_same_layout_deterministically():
    spec = _make_spec_2_rooms()
    a = StubGenerator().generate(spec)[0]
    b = StubGenerator().generate(spec)[0]
    # Compare geometry, not the elapsed_ms field
    assert [r.vertices for r in a.rooms] == [r.vertices for r in b.rooms]
    assert [w.start for w in a.walls] == [w.start for w in b.walls]
```

### Group 3 — Geometry contract (5 tests)

These validate that the emitted Layout satisfies the assumptions Layer A depends on. If any fail, StubGenerator is emitting Layouts that will fail Layer A downstream — which would be a wasted DRAFT 6 debugging session.

```python
def test_no_two_walls_share_the_same_edge():
    """The dedupe rule — no duplicate walls between adjacent rooms."""
    spec = _make_spec_2_rooms()
    layout = StubGenerator().generate(spec)[0]
    edges = [
        frozenset({tuple(w.start), tuple(w.end)}) for w in layout.walls
    ]
    assert len(edges) == len(set(edges)), "duplicate walls emitted"

def test_interior_walls_bound_exactly_two_rooms():
    """Shared walls should have bounds_rooms of length 2."""
    layout = StubGenerator().generate(_make_spec_2_rooms())[0]
    interior = [w for w in layout.walls if len(w.bounds_rooms) == 2]
    assert len(interior) >= 1, "expected at least one interior wall"
    for wall in interior:
        assert len(set(wall.bounds_rooms)) == 2, \
            "interior wall bounds_rooms must contain 2 distinct rooms"

def test_boundary_wall_ids_reference_existing_walls():
    layout = StubGenerator().generate(_make_spec_3_rooms())[0]
    all_wall_ids = {w.id for w in layout.walls}
    for room in layout.rooms:
        for wid in room.boundary_wall_ids:
            assert wid in all_wall_ids, \
                f"room {room.id} references unknown wall {wid}"

def test_room_vertices_are_ccw_and_closed():
    layout = StubGenerator().generate(_make_spec_3_rooms())[0]
    for room in layout.rooms:
        assert room.vertices[0] == room.vertices[-1], "vertices must close"
        # Schema already enforces CCW; this test guards regressions
        # if Cursor changes the corner-emission order in _make_rooms.

def test_openings_only_on_interior_walls():
    layout = StubGenerator().generate(_make_spec_3_rooms())[0]
    walls_by_id = {w.id: w for w in layout.walls}
    for opening in layout.openings:
        wall = walls_by_id[opening.wall_id]
        assert len(wall.bounds_rooms) == 2, \
            "stub emits openings only on interior walls"
```

### Group 4 — Failure modes (2 tests)

```python
def test_generator_failure_wraps_solver_exceptions():
    """A spec the solver can't handle should raise GeneratorFailure,
    not the underlying ConstraintUnsatisfiableError."""
    spec = _make_impossible_spec()  # e.g. 1 room with area 0.0001 sqft
    with pytest.raises(GeneratorFailure) as exc_info:
        StubGenerator().generate(spec)
    assert exc_info.value.generator_name == "stub"
    assert exc_info.value.spec_id == spec.spec_id
    assert exc_info.value.reason_code in {
        "solver_infeasible", "solver_empty_output", "stub_internal_error",
    }

def test_generator_failure_never_returns_empty_list():
    """Contract: generate() returns list of >= 1 OR raises. Never both."""
    spec = _make_spec_2_rooms()
    result = StubGenerator().generate(spec)
    assert len(result) >= 1
```

### Test helpers (same file, at bottom)

```python
def _make_spec_2_rooms() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_test_2rooms",
        original_nl="a 2-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=150.0),
        ],
        n_candidates=1,
    )

def _make_spec_3_rooms() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_test_3rooms",
        original_nl="a 3-room test plan",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Kitchen", room_type="kitchen", preferred_area_sqft=150.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=180.0),
        ],
        n_candidates=1,
    )

def _make_impossible_spec() -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id="spec_test_impossible",
        original_nl="a physically impossible plan",
        room_requirements=[
            RoomRequirement(name="Impossible", room_type="other", preferred_area_sqft=0.0001),
        ],
        n_candidates=1,
    )
```

---

## Explicitly NOT in this DRAFT

- **No verifier calls.** StubGenerator does NOT run Layer A on its own output. Best-of-N (DRAFT 6) is where verifiers get wired in.
- **No API route changes.** `/plan/generate` ships in DRAFT 8. The generator is importable but not yet exposed to HTTP.
- **No adjacency handling.** `RoomRequirement.adjacencies` is ignored by StubGenerator (documented in the docstring). The prompted generator (DRAFT 4) will honor it. Stub is intentionally dumb — its job is to be a working, deterministic warm-up target for Layer A/C.
- **No windows.** Stub only emits doors on interior walls. Layer C egress-window check will flag bedrooms → that's a scoring signal for best-of-N, not a stub bug.
- **No structural grid.** `Layout.structural_grid` gets the default (empty) `StructuralGrid`. Layer B's column inference (DRAFT 6 territory) will populate it.
- **No exits.** `Layout.exits` stays empty. Adding exits is a Layer C concern coupled with jurisdiction.

---

## Definition of Done

- [ ] `engine/generators/__init__.py`, `base.py`, `stub.py` exist per the specs above
- [ ] `tests/test_stub_generator.py` has all 15 tests; all pass
- [ ] `python3 -m pytest tests/test_stub_generator.py -v` → 15/15 passing in < 1s
- [ ] Full existing suite still passes: `python3 -m pytest -m "not slow and not live"` shows no NEW failures (the pre-existing 15 `sentry_sdk` collection errors on some hosts are unrelated and can stay)
- [ ] `python3 -c "from engine.generators import GeneratorFactory; g = GeneratorFactory.from_env(); print(g.name)"` prints `stub`
- [ ] No changes to any file outside `engine/generators/` or `tests/test_stub_generator.py`
- [ ] Emitted `Layout` from a 2-room spec passes `Layout` schema validation (i.e. the constructor doesn't raise)
- [ ] Wall dedupe test passes: no two walls share the same normalized edge

---

## Review Notes for Hermes (post-merge)

When the branch lands, Hermes should verify:

1. **Layer A doesn't reject the stub's output.** Once DRAFT 6 wires them together this is enforced automatically, but even now, we can eyeball it:
   ```python
   from engine.generators import StubGenerator
   from engine.verifiers import verify_layer_a
   layout = StubGenerator().generate(_make_spec_3_rooms())[0]
   result = verify_layer_a(layout)
   assert result.passed, result.failures
   ```
   If Layer A rejects a stub output, that's a stub bug — the stub is supposed to be the trivially-valid path.

2. **`Room.boundary_wall_ids` traces CCW.** Manually spot-check a 2-room layout: room A's boundary should list `[bottom, right, top, left]` and its right wall should equal room B's left wall.

3. **`_matrix_to_layout` handles the empty-matrix case correctly.** The solver *can* return an empty dict for degenerate specs; StubGenerator must raise `GeneratorFailure`, not return an empty Layout.

If any of the above fails, file a follow-up patch — do not merge with a broken stub, because DRAFTs 4/6/8 all assume it works.

---

## What Comes Next (after this merges)

- **DRAFT_ARCH_C_4_PROMPTED_GEN.md** — the real generator: claude-sonnet with JSON-schema-constrained output.
- **DRAFT_ARCH_C_5_LAYER_C.md** — code checker (5 IRC rules).
- **DRAFT_ARCH_C_6_BEST_OF_N.md** — the loop: generator → Layer A → Layer C → Layer B scoring → return top-K.

Layer B (advisory scorer) intentionally ships as part of best-of-N, not as its own DRAFT, because it only makes sense in the context of scoring survivors.
