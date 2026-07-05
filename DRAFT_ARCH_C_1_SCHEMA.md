# DRAFT — Architecture C, Phase 1: Typed Layout Schema

**Status:** Ready to send to Cursor Composer
**Scope:** New `engine/layout/schemas.py` — the typed Layout ground truth for Architecture C. No changes to existing files.
**Estimated size:** ~500 lines new code + ~300 lines tests
**Depends on:** Nothing (foundation DRAFT)
**Blocks:** All other Architecture C DRAFTs

---

## Context (READ FIRST)

Cognitect is pivoting to Architecture C — see `ARCHITECTURE_C.md` at repo root for the full plan. Currently the pipeline's ground truth is `coordinate_matrix: dict[str, {x,y,w,h}]`, produced by the kiwisolver+shelf-packer. This is fragile: it can't express walls-as-first-class-objects, openings, structural grids, or exits. The whole "verifier gate" strategy from the spec is impossible without a proper typed model.

This DRAFT introduces the **typed Layout schema** — a complete plan-state snapshot — plus the **FloorPlanSpec** (whole-plan intent, input to LayoutGenerator).

**Do not modify** the existing `engine/intent_parser/schemas.py` (FloorPlanOp). It remains valid for the edit flow. This DRAFT adds a *parallel* schema module for the generate flow.

---

## Goal

Add `engine/layout/` module with:
- `Layout`, `Room`, `Wall`, `Opening`, `StructuralGrid`, `Exit` Pydantic models
- `FloorPlanSpec` — the intent input for LayoutGenerator
- `GenerationFailure` — the error type when best-of-N produces no survivors
- `LayoutAuditManifest` — the per-Layout provenance record
- Round-trip serialization (Pydantic model_dump / model_validate)
- Full test coverage for validation rules

**NO** verifiers in this DRAFT (that's Phase 2). **NO** generators in this DRAFT (that's Phase 3). Schema only.

---

## Files to Create

```
engine/layout/
├── __init__.py                    # exports the public types
├── schemas.py                     # all Pydantic models (main file, ~500 lines)
├── audit.py                       # LayoutAuditManifest + helpers (~80 lines)
└── errors.py                      # GenerationFailure + verifier error types (~50 lines)

tests/
└── test_layout_schemas.py         # ~300 lines, ~40 tests
```

---

## Schema Design

### Coordinate System (STRICT)

- Units: **feet** (float, 2 decimal places)
- Origin: (0, 0) at the **bottom-left** of the plan
- X axis: increases to the right
- Y axis: increases upward
- Wall coordinates are line segments `(x1, y1) → (x2, y2)`
- Openings sit on walls, parameterized by `wall_id + offset_ft + width_ft`

**This is math coords, not screen coords.** The previewer flips y at render time. Downstream consumers (exporter, verifiers) all use math coords.

### Room

```python
class Room(BaseModel):
    id: str = Field(..., pattern=r"^room_[a-z0-9_]+$")
    name: str = Field(..., min_length=1, max_length=64)
    room_type: Literal[
        "bedroom", "bathroom", "kitchen", "living", "dining",
        "hallway", "office", "garage", "closet", "utility", "other"
    ]
    # Polygon vertices — must be closed (first == last), CCW ordering
    vertices: list[tuple[float, float]] = Field(..., min_length=4)
    area_sqft: float = Field(..., gt=0)
    # Wall IDs that bound this room (references walls[].id)
    boundary_wall_ids: list[str] = Field(..., min_length=3)
    # Optional metadata
    ceiling_height_ft: float = Field(default=9.0, gt=0)
    metadata: dict = Field(default_factory=dict)
```

**Validators:**
- `vertices` must be closed: `vertices[0] == vertices[-1]`
- `vertices` must be CCW (positive shoelace area)
- `area_sqft` must match shoelace area of `vertices` within 0.5% tolerance
- `boundary_wall_ids` must be unique
- Every vertex coordinate must be non-negative (positive quadrant)

### Wall

```python
class Wall(BaseModel):
    id: str = Field(..., pattern=r"^wall_[a-z0-9_]+$")
    start: tuple[float, float]  # (x, y) in feet
    end: tuple[float, float]
    thickness_ft: float = Field(default=0.5, gt=0)
    # Which rooms this wall bounds (0, 1, or 2 rooms — 0 = free-standing, 1 = exterior, 2 = interior)
    bounds_rooms: list[str] = Field(..., max_length=2)
    # Wall load-bearing status (advisory, populated by Layer B)
    is_load_bearing: Optional[bool] = None
    metadata: dict = Field(default_factory=dict)
```

**Validators:**
- `start != end` (no zero-length walls)
- `length_ft` property returns euclidean distance
- All coordinates non-negative

### Opening

```python
class Opening(BaseModel):
    id: str = Field(..., pattern=r"^opening_[a-z0-9_]+$")
    opening_type: Literal["door", "window", "archway", "wall_opening"]
    wall_id: str  # references walls[].id
    # Position along the wall, measured from wall.start
    offset_ft: float = Field(..., ge=0)
    width_ft: float = Field(..., gt=0)
    # Height of the opening (bottom edge) from floor
    sill_height_ft: float = Field(default=0.0, ge=0)
    # Height of the opening itself
    height_ft: float = Field(default=6.67, gt=0)  # 6'8" standard door
    # Swing direction for doors: which room the door opens into
    swings_into_room_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

**Validators (enforced at Layout-level, not Opening-level, since we need wall context):**
- `offset_ft + width_ft <= wall.length_ft` (opening fits on wall)
- If `opening_type == "door"`: `swings_into_room_id` should be in `wall.bounds_rooms`
- If `opening_type == "window"`: opening is on an exterior wall (`len(wall.bounds_rooms) == 1`)

### StructuralGrid

```python
class GridLine(BaseModel):
    id: str = Field(..., pattern=r"^grid_[a-z0-9_]+$")
    axis: Literal["x", "y"]
    position_ft: float = Field(..., ge=0)
    label: str = Field(..., min_length=1, max_length=4)  # "A", "B", "1", "2"

class StructuralGrid(BaseModel):
    lines: list[GridLine] = Field(default_factory=list)
    # Advisory — advisory column positions inferred by Layer B, not authoritative
    inferred_column_positions: list[tuple[float, float]] = Field(default_factory=list)
```

Grid is optional. Empty grid is valid (no structural claim). Populated grid is used by Layer B for structural sanity checks.

### Exit

```python
class Exit(BaseModel):
    id: str = Field(..., pattern=r"^exit_[a-z0-9_]+$")
    opening_id: str  # references an Opening of type "door"
    exit_type: Literal["primary", "emergency", "egress_window"]
    # Egress path in feet (from farthest interior point to this exit)
    max_egress_distance_ft: Optional[float] = Field(None, gt=0)
    metadata: dict = Field(default_factory=dict)
```

**Rationale:** Exits are first-class because Layer C needs them for egress compliance. Every habitable plan needs at least one primary exit.

### Layout (the top-level object)

```python
class Layout(BaseModel):
    """Complete typed floor plan state — the ground truth of Architecture C."""
    
    plan_id: str = Field(..., pattern=r"^plan_[a-z0-9_]+$")
    schema_version: Literal["1.0"] = "1.0"
    
    rooms: list[Room]
    walls: list[Wall]
    openings: list[Opening] = Field(default_factory=list)
    structural_grid: StructuralGrid = Field(default_factory=StructuralGrid)
    exits: list[Exit] = Field(default_factory=list)
    
    # Overall plan extent (bounding box)
    extent_x_ft: float = Field(..., gt=0)
    extent_y_ft: float = Field(..., gt=0)
    
    # Provenance (populated by best-of-N; None on raw generation output)
    audit: Optional["LayoutAuditManifest"] = None
    
    metadata: dict = Field(default_factory=dict)
```

**Layout-level validators (@model_validator mode="after"):**
1. All `Room.boundary_wall_ids` reference existing walls
2. All `Wall.bounds_rooms` reference existing rooms  
3. All `Opening.wall_id` reference existing walls
4. All `Exit.opening_id` reference existing openings of type "door"
5. All IDs are unique within their type
6. `extent_x_ft` >= max x-coordinate across all vertices
7. `extent_y_ft` >= max y-coordinate across all vertices

**Explicitly NOT validated in the schema:**
- Geometric non-overlap between rooms (that's Layer A's job)
- Wall connectivity (that's Layer A)
- Code compliance (that's Layer C)
- Structural sanity (that's Layer B)

The schema catches *type* and *reference* errors. The verifiers catch *semantic* errors. Keep them separate.

### FloorPlanSpec

```python
class RoomRequirement(BaseModel):
    """A single room the user wants in the plan."""
    name: str
    room_type: Literal[...]  # same as Room.room_type
    min_area_sqft: Optional[float] = Field(None, gt=0)
    max_area_sqft: Optional[float] = Field(None, gt=0)
    preferred_area_sqft: Optional[float] = Field(None, gt=0)
    aspect_ratio: Optional[float] = Field(None, gt=0)
    adjacencies: list[str] = Field(default_factory=list)  # names of other rooms this must adjoin
    metadata: dict = Field(default_factory=dict)


class SiteConstraints(BaseModel):
    """Lot/site constraints (setbacks, orientation, jurisdiction)."""
    lot_width_ft: Optional[float] = Field(None, gt=0)
    lot_depth_ft: Optional[float] = Field(None, gt=0)
    setback_front_ft: Optional[float] = Field(None, ge=0)
    setback_rear_ft: Optional[float] = Field(None, ge=0)
    setback_side_ft: Optional[float] = Field(None, ge=0)
    max_footprint_sqft: Optional[float] = Field(None, gt=0)
    # Jurisdiction for Layer C code checking
    jurisdiction: str = Field(default="IRC-2021")
    # North direction (degrees from +Y axis, CW)
    north_bearing_deg: float = Field(default=0.0, ge=0.0, lt=360.0)


class FloorPlanSpec(BaseModel):
    """Whole-plan intent — the input to LayoutGenerator.
    
    Produced from NL by the intent layer. Consumed by LayoutGenerator to produce
    N candidate Layouts.
    """
    spec_id: str = Field(..., pattern=r"^spec_[a-z0-9_]+$")
    room_requirements: list[RoomRequirement] = Field(..., min_length=1)
    site_constraints: SiteConstraints = Field(default_factory=SiteConstraints)
    # Free-form user prose (kept for audit trail — LLMs may re-read this)
    original_nl: str = Field(..., min_length=1)
    # Number of candidates to generate (default 8, capped at 32)
    n_candidates: int = Field(default=8, ge=1, le=32)
    metadata: dict = Field(default_factory=dict)
```

### LayoutAuditManifest

`engine/layout/audit.py`:

```python
class VerifierResult(BaseModel):
    verifier_name: Literal["layer_a_geometry", "layer_b_structural", "layer_c_code"]
    passed: bool  # For Layer B: always True (advisory); use `warnings` for issues
    checks_run: list[str]  # Names of individual checks executed
    failures: list[dict] = Field(default_factory=list)  # {"check": str, "detail": str, "citation": Optional[str]}
    warnings: list[dict] = Field(default_factory=list)
    score: Optional[float] = Field(None, ge=0.0, le=1.0)  # For Layer B ranking
    elapsed_ms: float = Field(..., ge=0)


class LayoutAuditManifest(BaseModel):
    """Provenance record attached to every Layout returned by best-of-N."""
    generator: str  # e.g. "prompted-claude-sonnet-4-5"
    generator_version: str  # date string or model hash
    spec_hash: str  # sha256 of FloorPlanSpec JSON
    verifier_results: list[VerifierResult]
    generated_at: datetime
    selection_rank: int  # rank in best-of-N (0 = top)
    total_candidates: int  # how many candidates were generated before filtering
    survived_layer_a: int
    survived_layer_c: int
    metadata: dict = Field(default_factory=dict)
```

### GenerationFailure

`engine/layout/errors.py`:

```python
class GenerationFailure(Exception):
    """Raised when best-of-N produces zero valid Layouts.
    
    Do NOT fall back to invalid geometry. Callers must handle this and either
    re-prompt the user, retry with more candidates, or surface the error.
    """
    def __init__(
        self,
        spec_id: str,
        total_candidates: int,
        layer_a_failures: int,
        layer_c_failures: int,
        details: list[dict],
    ):
        self.spec_id = spec_id
        self.total_candidates = total_candidates
        self.layer_a_failures = layer_a_failures
        self.layer_c_failures = layer_c_failures
        self.details = details
        super().__init__(
            f"Generation failed for spec {spec_id}: "
            f"{layer_a_failures}/{total_candidates} failed Layer A, "
            f"{layer_c_failures}/{total_candidates} failed Layer C"
        )


class SchemaViolation(ValueError):
    """Raised when a Layout fails schema-level validation (before verifiers)."""
    pass
```

---

## `engine/layout/__init__.py`

```python
"""Architecture C — typed Layout schema.

The ground truth of the generate flow. Produced by LayoutGenerator, verified
by Layer A / B / C, ranked by best-of-N, exported by the exporter.
"""

from engine.layout.schemas import (
    Room,
    Wall,
    Opening,
    GridLine,
    StructuralGrid,
    Exit,
    Layout,
    RoomRequirement,
    SiteConstraints,
    FloorPlanSpec,
)
from engine.layout.audit import LayoutAuditManifest, VerifierResult
from engine.layout.errors import GenerationFailure, SchemaViolation

__all__ = [
    "Room", "Wall", "Opening", "GridLine", "StructuralGrid", "Exit",
    "Layout", "RoomRequirement", "SiteConstraints", "FloorPlanSpec",
    "LayoutAuditManifest", "VerifierResult",
    "GenerationFailure", "SchemaViolation",
]
```

---

## Tests to Write (`tests/test_layout_schemas.py`)

Group tests by model. Aim for ~40 tests, all passing.

### Room tests
1. Valid rectangular room passes
2. Valid L-shaped room passes
3. Non-closed vertices raises
4. CW-ordered vertices raises (must be CCW)
5. area_sqft mismatches shoelace by >0.5% raises
6. Empty boundary_wall_ids raises (min_length=3)
7. Duplicate boundary_wall_ids raises
8. Negative vertex coordinate raises
9. Invalid ID pattern raises (e.g. `Room1` not `room_1`)

### Wall tests
10. Valid wall passes
11. Zero-length wall (start==end) raises
12. Negative coordinate raises
13. `length_ft` computed property is correct (compute for a known example, assert ~sqrt(2)*10 for a 10x10 diagonal)
14. bounds_rooms with 3 entries raises (max_length=2)

### Opening tests
15. Valid door passes
16. Invalid ID pattern raises
17. Negative offset_ft raises
18. Zero width raises

### Layout-level cross-reference tests
19. Room.boundary_wall_ids references non-existent wall → raises
20. Opening.wall_id references non-existent wall → raises
21. Exit.opening_id references non-existent opening → raises
22. Exit.opening_id references an opening of type "window" → raises
23. Duplicate room IDs → raises
24. Duplicate wall IDs → raises
25. extent_x_ft smaller than max vertex x → raises

### Layout-level positive tests
26. Minimal valid layout (1 room, 4 walls, 0 openings) passes
27. 3-room layout with shared walls passes
28. Round-trip: `Layout(...).model_dump()` → `Layout.model_validate(...)` recovers identical object
29. JSON round-trip via `model_dump_json` / `model_validate_json`

### FloorPlanSpec tests
30. Valid spec passes
31. Empty room_requirements raises
32. n_candidates > 32 raises
33. Invalid jurisdiction default is "IRC-2021" 
34. north_bearing_deg == 360 raises (lt=360)

### LayoutAuditManifest tests
35. Valid manifest passes
36. Attaching manifest to Layout roundtrips
37. Verifier score outside [0,1] raises

### GenerationFailure tests
38. Constructing raises with correct message
39. Attributes accessible after raise

### Helper tests
40. Shoelace area helper (extract as `_shoelace_area(vertices)` in schemas.py) returns correct area for known polygons

---

## Implementation Notes

### Shoelace formula (for Room area validation)

```python
def _shoelace_area(vertices: list[tuple[float, float]]) -> float:
    """Return signed area (positive if CCW, negative if CW)."""
    n = len(vertices) - 1  # last vertex == first
    s = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[i + 1]
        s += (x1 * y2 - x2 * y1)
    return s / 2.0
```

Use `abs(_shoelace_area(...))` to compare to `area_sqft`; use sign to check CCW.

### Wall.length_ft as computed field

```python
@computed_field
@property
def length_ft(self) -> float:
    dx = self.end[0] - self.start[0]
    dy = self.end[1] - self.start[1]
    return round((dx * dx + dy * dy) ** 0.5, 4)
```

### ID prefixing convention (STRICT)

Every ID must be prefixed with its type. This makes IDs self-describing in logs and audit trails.
- Rooms: `room_<slug>` (e.g. `room_master_bedroom`, `room_1`)
- Walls: `wall_<slug>` or `wall_<uuid8>` 
- Openings: `opening_<slug>`
- Exits: `exit_<slug>`
- Grid lines: `grid_a`, `grid_b`, `grid_1`, `grid_2`
- Plans: `plan_<uuid8>`
- Specs: `spec_<uuid8>`

Enforce with Pydantic `Field(pattern=...)`. Do NOT accept bare IDs like `1` or `bedroom` — Cursor tends to slip on this if not told explicitly.

### Serialization

All models use Pydantic v2. Use `model_dump()` / `model_dump_json()` / `model_validate()` / `model_validate_json()`. Do NOT use `.dict()` or `.json()` (deprecated in v2).

Tuples serialize as lists in JSON. On roundtrip, Pydantic coerces list-of-2-floats back to tuple. Test this explicitly.

---

## Anti-Patterns (do NOT do)

- Do NOT add verifier logic to schema validators. Schema catches type/reference errors; verifiers catch semantic errors. Keep them separate.
- Do NOT add methods that mutate a Layout in place (Layout is immutable-ish; edits happen via FloorPlanOp on a separate flow).
- Do NOT add methods that call the LLM (schema is model-agnostic).
- Do NOT add methods that call Shapely (that lives in `engine/verifiers/layer_a.py`, next DRAFT).
- Do NOT touch `engine/intent_parser/schemas.py`. FloorPlanOp stays where it is.
- Do NOT rename `coordinate_matrix` anywhere. It stays in the edit flow.
- Do NOT wire this into `PlanManager` or any API route. This DRAFT is schema-only.

---

## Verification Checklist

Before you consider this DRAFT complete:

- [ ] `engine/layout/schemas.py` exists with all models above
- [ ] `engine/layout/__init__.py` exports the public types
- [ ] `engine/layout/audit.py` and `engine/layout/errors.py` exist
- [ ] `tests/test_layout_schemas.py` has ≥ 40 tests, all passing
- [ ] `python3 -m pytest tests/test_layout_schemas.py -v` shows all green
- [ ] `python3 -m pytest tests/ -m "not slow and not live" -v` shows all existing tests still pass (no regressions)
- [ ] No new imports of Shapely, anthropic, or FreeCAD in this DRAFT
- [ ] `mypy engine/layout/` (if configured) has zero errors
- [ ] `git diff --stat` shows only new files + one new directory

---

## Commit Message

```
Architecture C, Phase 1: Typed Layout schema

Adds engine/layout/ with the typed ground-truth models (Room, Wall,
Opening, StructuralGrid, Exit, Layout) and the FloorPlanSpec intent.
Includes audit manifest and error types. Schema-only — no verifiers,
no generators, no wiring into PlanManager yet.

- engine/layout/schemas.py: 12 Pydantic models with validators
- engine/layout/audit.py: LayoutAuditManifest + VerifierResult
- engine/layout/errors.py: GenerationFailure + SchemaViolation  
- tests/test_layout_schemas.py: 40 tests covering all models
- No changes to existing files

See ARCHITECTURE_C.md for the full plan.
```

---

## After This DRAFT Lands

Next DRAFT will be `DRAFT_ARCH_C_2_LAYER_A.md`: Shapely-based geometry verifier. It will consume the `Layout` type defined here.
