# DRAFT — Layout Solver V2: 2D Spatial Layout with Positional Continuity

**Status:** Ready for Cursor Composer  
**Replaces:** `engine/constraint_solver/solver.py` layout strategy (lines 304–323)  
**Also touches:** `engine/constraint_solver/graph.py`, `engine/plan_manager.py`, tests  
**Architecture rule:** Solver never calls LLM. LLM never touches geometry. No new external deps.

---

## Problem Statement

The current solver uses a 1D strip-pack layout: all rooms are forced to `y = 0` and packed left-to-right in a single row. This causes two critical failures:

1. **Layout explosion on re-solve** — any NL op (resize, add, remove) triggers a full re-solve. Because `y = 0` is forced for all rooms, any previously 2D spatial arrangement (e.g. from a DXF scrape) collapses into a single horizontal strip.

2. **No positional continuity** — `coordinate_matrix` is cleared to `None` on every state mutation, so prior room positions are never carried forward. Even if rooms weren't touched by the op, they get repositioned from scratch.

The fix has two parts:
- **Part 1:** Replace the 1D strip-pack with a 2D bin-packing layout strategy using a grid-based space partition.
- **Part 2:** Carry forward existing room positions as anchors — rooms not mutated by the current op are position-locked with `strong` constraints; only new/mutated rooms get free layout passes.

---

## Scope

### Files to create
- `engine/constraint_solver/layout.py` — new 2D layout engine

### Files to modify
- `engine/constraint_solver/solver.py` — replace `_run_solver_pass` layout section; accept prior coordinates
- `engine/constraint_solver/graph.py` — add `pinned_position` field to `RoomNode`
- `engine/plan_manager.py` — pass prior `coordinate_matrix` into `solve()`; do NOT clear it on op application; preserve it across mutations
- `tests/test_constraint_solver.py` — add layout continuity tests
- `tests/test_plan_manager.py` — add re-solve continuity tests

### Files NOT to touch
- `engine/intent_parser/` — no changes
- `engine/cad_generator/` — no changes
- `engine/exporter/` — no changes
- `api/` — no changes
- `engine/previewer.py` — no changes

---

## Part 1: 2D Layout Engine (`engine/constraint_solver/layout.py`)

Create a new module. This is the only place the 2D placement logic lives.

### Algorithm: Shelf-Based 2D Bin Packing with Adjacency Grouping

The strategy is a modified shelf-first-fit algorithm that respects adjacency edges.

**Step 1 — Partition rooms into adjacency clusters**

Rooms connected by edges in the `CoordinateGraph` are grouped into clusters. Rooms in the same cluster are placed contiguously. Disconnected rooms become single-room clusters.

Use BFS on the edge list to build clusters. Each cluster gets placed as a unit.

**Step 2 — Estimate room dimensions**

For each room, compute estimated `w` and `h` before placement (these will be refined by kiwisolver, but we need an estimate for packing):

```python
def _estimate_dims(node: RoomNode) -> tuple[float, float]:
    area = node.target_area_sqft or 100.0
    aspect = node.aspect_ratio or 1.3   # slightly wider than square by default
    w = math.sqrt(area * aspect)
    h = math.sqrt(area / aspect)
    return max(w, MIN_ROOM_DIM_FT), max(h, MIN_ROOM_DIM_FT)
```

**Step 3 — Place clusters on shelves**

A "shelf" is a horizontal band at a given `y` offset. Rooms are placed left-to-right on the current shelf. When a room doesn't fit (would exceed `MAX_PLAN_DIM_FT` in x), open a new shelf at `y = current_shelf_top + shelf_height`.

Within a cluster, rooms are placed in BFS order from the cluster's root node. The first room in the cluster anchors to the current shelf's `(x, y)`. Subsequent cluster members are placed adjacent to their graph neighbor (share an edge with the previously placed room).

Adjacency placement rule within a cluster:
- If the neighbor was placed to the left, the current room goes to its right (horizontal adjacency).
- If all horizontal space is used, place below the cluster's first room (vertical adjacency) — start a sub-shelf within the cluster.

**Step 4 — Output: `PlacementMap`**

```python
@dataclass
class PlacementMap:
    """Suggested (x, y) origin for each room before kiwisolver runs."""
    positions: dict[str, tuple[float, float]]  # room_id → (x, y)
```

The `PlacementMap` feeds into the solver as `strong` positional suggestions (not `required` — kiwisolver can move them slightly to satisfy dimensional constraints).

### Full `layout.py` interface

```python
class RoomLayoutEngine:
    """
    Computes 2D placement suggestions for rooms prior to kiwisolver.
    
    Pure function — no solver state, no LLM. Only reads CoordinateGraph node/edge data.
    """

    def compute_placement(
        self,
        graph: CoordinateGraph,
        room_ids: list[str],
        pinned: dict[str, tuple[float, float]] | None = None,
    ) -> PlacementMap:
        """
        Args:
            graph: CoordinateGraph with RoomNodes and WallEdges.
            room_ids: Ordered list of room IDs to place.
            pinned: Optional map of room_id → (x, y) for rooms whose position
                    should be preserved (position-locked rooms). These rooms are
                    excluded from the packing algorithm and their positions are
                    returned as-is in the PlacementMap.

        Returns:
            PlacementMap with suggested (x, y) for every room in room_ids.
        """
```

---

## Part 2: Positional Continuity

### `graph.py` — Add `pinned_position` to `RoomNode`

```python
@dataclass
class RoomNode:
    # ... existing fields ...
    pinned_position: tuple[float, float] | None = None
    # (x, y) in feet — if set, solver locks this room's position with a strong constraint.
    # Width/height are still free to vary (used for resize ops).
```

### `solver.py` — Accept prior coordinates; position-lock untouched rooms

#### New method signature for `_build_graph`:

```python
def _build_graph(
    self,
    plan_state: FloorPlanState,
    prior_matrix: dict[str, dict[str, float]] | None = None,
    mutated_rooms: set[str] | None = None,
) -> CoordinateGraph:
```

When `prior_matrix` is provided:
- For each room in `prior_matrix` that is NOT in `mutated_rooms`, set `node.pinned_position = (prior_x, prior_y)`.
- Rooms in `mutated_rooms` get `pinned_position = None` (free to be repositioned).
- New rooms (in `plan_state.rooms` but not in `prior_matrix`) also get `pinned_position = None`.

#### New `solve()` signature:

```python
def solve(
    self,
    plan_state: FloorPlanState,
    prior_matrix: dict[str, dict[str, float]] | None = None,
    mutated_rooms: set[str] | None = None,
) -> dict[str, dict[str, float]]:
```

#### Replace the layout section in `_run_solver_pass`:

**Remove** (lines 304–323 in current `solver.py`):
```python
# ── Layout: pack rooms left-to-right ────────────────────────────────
# All rooms on y=0 row, x-packed sequentially
ordered_rooms = self._topological_order(room_ids, graph)
for i, rid in enumerate(ordered_rooms):
    v = vars_[rid]
    if i == 0:
        solver.addConstraint((v["x"] == 0.0) | "strong")
        solver.addConstraint((v["y"] == 0.0) | "strong")
    else:
        prev_rid = ordered_rooms[i - 1]
        prev_v = vars_[prev_rid]
        solver.addConstraint(
            (v["x"] == prev_v["x"] + prev_v["w"]) | "strong"
        )
        solver.addConstraint((v["y"] == 0.0) | "strong")
```

**Replace with:**
```python
# ── Layout: 2D placement via RoomLayoutEngine ────────────────────────
from .layout import RoomLayoutEngine

# Build pinned map from nodes that have a pinned_position set
pinned = {
    rid: graph.nodes[rid].pinned_position
    for rid in room_ids
    if graph.nodes[rid].pinned_position is not None
}

layout_engine = RoomLayoutEngine()
placement = layout_engine.compute_placement(graph, room_ids, pinned=pinned)

for rid in room_ids:
    v = vars_[rid]
    node = graph.nodes[rid]
    px, py = placement.positions[rid]

    if node.pinned_position is not None:
        # Room is position-locked — use required constraints on x/y
        solver.addConstraint((v["x"] == px) | "required")
        solver.addConstraint((v["y"] == py) | "required")
    else:
        # Free room — suggest position strongly, allow solver to adjust
        solver.addConstraint((v["x"] == px) | "strong")
        solver.addConstraint((v["y"] == py) | "strong")
        solver.addConstraint((v["x"] >= 0.0) | "required")
        solver.addConstraint((v["y"] >= 0.0) | "required")
```

Note: the `(v["x"] >= 0.0) | "required"` constraints that currently live in the "Basic constraints" loop should be removed from there for pinned rooms to avoid conflicting with the `required` equality above (or left as-is since `x == px` where `px >= 0` satisfies `x >= 0` — no conflict, just redundant; either is fine).

### `plan_manager.py` — Preserve coordinate_matrix across ops; track mutated rooms

#### Change 1: Do NOT clear `coordinate_matrix` on op application

In `_apply_op`, the last block currently does:
```python
self._state = self._state.model_copy(update={
    ...
    "coordinate_matrix": None,  # invalidate on any mutation  ← REMOVE THIS
})
```

Remove the `"coordinate_matrix": None` line. The matrix stays valid until a new solve runs. (The solve will always produce a fresh matrix — clearing it pre-emptively just throws away positional information we need.)

#### Change 2: Track which rooms were mutated by the current op

Add a `_last_mutated_rooms: set[str]` instance variable to `PlanManager`. Clear it at the top of `_apply_op`, then populate it based on the op:

```python
def _apply_op(self, op: FloorPlanOp) -> None:
    self._last_mutated_rooms: set[str] = set()

    if op.op_type == "add_room":
        # New room ID won't exist in prior_matrix — no need to add to mutated set,
        # but track the slug so the solver knows it's new.
        room_id = self._slugify(op.room_spec.name)
        # ... existing slug collision logic ...
        rooms[room_id] = op.room_spec
        self._last_mutated_rooms.add(room_id)

    elif op.op_type == "remove_room":
        self._last_mutated_rooms.add(op.target_room_id)
        # ... existing removal logic ...

    elif op.op_type == "resize_room":
        self._last_mutated_rooms.add(op.target_room_id)
        # ... existing resize logic ...

    elif op.op_type == "move_room":
        self._last_mutated_rooms.add(op.target_room_id)
        # ... existing move_room logic ...

    # add_connection and set_constraint don't change room geometry,
    # so they don't add to _last_mutated_rooms.
```

#### Change 3: Pass prior matrix and mutated rooms into `solve()`

In `PlanManager.solve()`:

```python
def solve(self) -> dict:
    if not self._state.rooms:
        raise PlanManagerError("Cannot solve: plan has no rooms")
    
    prior_matrix = self._state.coordinate_matrix  # may be None on first solve
    mutated = getattr(self, "_last_mutated_rooms", set())
    
    matrix = self._solver.solve(
        self._state,
        prior_matrix=prior_matrix,
        mutated_rooms=mutated,
    )
    self._state = self._state.model_copy(
        update={"coordinate_matrix": matrix}
    )
    return matrix
```

---

## Edge Cases to Handle

### New room placement (no prior position)
A newly added room has no entry in `prior_matrix`. The `RoomLayoutEngine` should place it adjacent to its adjacency neighbors if any are already placed, or at the nearest open space on the current shelf if no neighbors exist.

To find "nearest open space": after placing all pinned rooms as occupied regions, run the shelf algorithm only on the free rooms, treating pinned room footprints as obstacles that close off shelf space.

### Remove room — gap handling
When a room is removed, it leaves a gap in the layout. Do NOT try to collapse adjacent rooms to fill it — that would move all the untouched rooms. Just leave the gap. Users can request a "compact layout" via NL later (that becomes a separate op: `op_type = "reflow_layout"`, which is a full re-solve with `prior_matrix=None`).

### Resize room — neighbor collision
If the resized room grows and its new dimensions would overlap a pinned neighbor, the solver should push the neighbor rather than clip the resize. Implement this by making pinned positions `strong` (not `required`) for the neighbors of a mutated room:

In `_build_graph`, after setting `pinned_position`:
```python
# Downgrade neighbors of mutated rooms from required to strong
# so they can shift if needed
for rid in room_ids:
    node = graph.nodes[rid]
    if node.pinned_position is not None:
        neighbors = graph.adjacent_rooms(rid)
        if any(n in mutated_rooms for n in neighbors):
            # This pinned room is adjacent to a mutated room — allow it to shift
            node.is_flexible_pin = True  # new boolean field on RoomNode
```

Add `is_flexible_pin: bool = False` to `RoomNode`. In `_run_solver_pass`, use `"strong"` instead of `"required"` when `node.is_flexible_pin` is True.

### First solve (no prior matrix)
When `prior_matrix is None`, run the full `RoomLayoutEngine.compute_placement()` with `pinned={}`. This produces the initial layout. Behavior is identical to the V1 solver for fresh plans (except the layout is now 2D, not 1D strip-pack).

### `instruct()` called multiple times before `solve()`
Multiple ops can be applied before the user triggers a solve (via export or explicit solve call). `_last_mutated_rooms` should accumulate across all pending ops:

Change `_last_mutated_rooms` to NOT be reset at the top of each `_apply_op`. Instead, reset it only when `solve()` is called:

```python
def solve(self) -> dict:
    ...
    mutated = self._last_mutated_rooms.copy()
    self._last_mutated_rooms.clear()  # reset after consuming
    ...
```

And initialize it in `__init__`:
```python
self._last_mutated_rooms: set[str] = set()
```

---

## Tests to Write

### `tests/test_constraint_solver.py` — new test class `TestLayoutContinuity`

```python
class TestLayoutContinuity:

    def test_resize_preserves_other_room_positions(self):
        """After resizing room A, room B's (x, y) must not change significantly."""
        # Build a plan with 3 rooms, run initial solve, get matrix.
        # Resize room A by 25%. Re-solve.
        # Assert room B and C x/y are within 1 ft of original.

    def test_add_room_places_near_adjacency_neighbor(self):
        """New room added with adjacency to existing room should land next to it."""
        # Build plan with room A. Solve. Add room B with adjacency to A.
        # Re-solve. Assert room B shares an edge with room A (within tolerance).

    def test_remove_room_leaves_others_stable(self):
        """Removing room A should not significantly move rooms B, C."""
        # Build 3-room plan. Solve. Remove room A.
        # Re-solve. Assert B and C positions within 1 ft of original.

    def test_fresh_solve_produces_2d_layout(self):
        """Initial solve with 4+ rooms should produce at least 2 distinct y values."""
        # Build plan with 5 rooms. Run initial solve.
        # Assert len({coords['y'] for coords in matrix.values()}) >= 2

    def test_resize_neighbor_collision_pushes_neighbor(self):
        """Resizing a room to 2x its area should push adjacent rooms, not clip the resize."""
        # Build 2-room plan A-B side by side. Solve.
        # Double A's area. Re-solve.
        # Assert A's actual area >= 1.8x original (not clipped).
        # Assert B has shifted right (x > original_B_x).
```

### `tests/test_plan_manager.py` — new test class `TestPlanManagerContinuity`

```python
class TestPlanManagerContinuity:

    def test_coordinate_matrix_not_cleared_on_op(self):
        """coordinate_matrix should persist after an op until next solve."""
        # Apply ops, solve, apply another op.
        # Assert state.coordinate_matrix is not None after second op.

    def test_mutated_rooms_accumulate_across_ops(self):
        """_last_mutated_rooms should include all rooms touched since last solve."""
        # Apply resize to room A. Apply resize to room B. Check _last_mutated_rooms = {A, B}.

    def test_resize_then_solve_preserves_other_rooms(self):
        """Integration test: resize via instruct() then solve preserves other room positions."""
        # Use apply_op() directly (no Claude required).
        # Build 4-room plan, solve, resize one room, re-solve.
        # Assert 3 untouched rooms have x/y within 1 ft of their pre-resize positions.
```

---

## Acceptance Criteria

1. `"expand room 10 by 25%"` — re-solve produces a coordinate matrix where all rooms NOT named in the op have `x` and `y` within ±2 ft of their pre-op values.
2. A fresh plan with 5 rooms produces at least 2 distinct y-coordinates in the output matrix (2D layout, not 1D strip).
3. All existing 92 tests pass (no regressions).
4. `TestLayoutContinuity` and `TestPlanManagerContinuity` all pass.
5. No new external dependencies added to `requirements.txt`.

---

## Implementation Notes for Cursor

- `layout.py` is new — no existing tests for it. Write tests in `test_constraint_solver.py` alongside the implementation.
- The shelf algorithm in `RoomLayoutEngine.compute_placement()` should use the estimated dimensions from `_estimate_dims()` for packing decisions — actual kiwisolver dimensions come after. This means there will be slight position drift from estimated to actual, which is acceptable.
- `RoomLayoutEngine` should be stateless — no instance variables. All state lives in local variables within `compute_placement()`.
- Do not add `scipy`, `networkx`, or any new packages. Use only stdlib (`collections.deque` for BFS, `dataclasses`, `math`) and what's already in the venv.
- Kiwisolver does not support `required` equality constraints on variables that also appear in `>=` constraints for the same variable — use `strong` strength on the positional constraints for free rooms, `required` only for hard pinned rooms. Test this combination to make sure kiwisolver doesn't reject conflicting constraint sets.
- The `instruct()` path calls `_apply_op()` which accumulates `_last_mutated_rooms`. The `solve()` path consumes and clears it. These two can be called in any order by the API — make sure the accumulator is initialized in `__init__` and the clear happens only in `solve()`.
- Push to branch `cursor/layout-solver-v2`.
