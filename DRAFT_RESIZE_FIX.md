# DRAFT: Fix `resize_room` full-rebalance bug — preserve layout on pure resize ops

## Task
Fix a bug where `resize_room` ops (e.g. "expand room 10 by 25%") cause a complete layout repack of all rooms instead of only resizing the target room. The expected behavior is: all other rooms stay exactly where they are; only the target room's dimensions change and it is re-placed to minimize disruption.

---

## Root Cause

Two overlapping bugs in `engine/constraint_solver/solver.py`, method `_run_solver_pass` (lines ~365–386).

### Bug 1 (primary) — `has_new_rooms` false-positive: second `any()` clause

```python
# CURRENT (BROKEN):
has_new_rooms = any(
    graph.nodes[rid].pinned_position is None
    and graph.nodes[rid].is_flexible_pin is False
    for rid in room_ids
) or any(
    graph.nodes[rid].is_flexible_pin          # ← always True for neighbors of mutated room
    for rid in room_ids
    if graph.nodes[rid].pinned_position is not None
)
```

When `resize_room` fires:
- Mutated room (e.g. `room_10`): `pinned_position=None`, `is_flexible_pin=False`
- **Neighbors of `room_10`**: `pinned_position=SET`, `is_flexible_pin=True` (set in `_build_graph` line ~212)

The second `any()` is always `True` on any resize with neighbors → `has_new_rooms=True` → `_tile_rooms(..., pinned={})` → **full repack from scratch**.

### Bug 2 (secondary) — first `any()` clause also fires

`room_10` itself has `pinned_position=None` and `is_flexible_pin=False`, so even without neighbors the first clause would make `has_new_rooms=True`. A pure-resize op can never reach the pinned-preservation path.

### What `is_flexible_pin` was supposed to mean
`is_flexible_pin=True` means "this room has a prior position, but can shift slightly if needed" (e.g. to accommodate a neighbor that grew). It was never meant to signal "treat this like a brand-new plan." The full rebalance should only happen when rooms have **no** prior position at all (i.e., they are genuinely new rooms being added for the first time).

---

## Fix

### Approach: Store `requires_full_rebalance` on `ConstraintSolver` during `_build_graph`

`_build_graph` already computes the correct signal (line ~192):
```python
has_new_rooms = any(rid not in prior_matrix for rid in mutated)
```
...plus `prior_matrix is None` for the very first solve. We stash this on the solver instance so `_run_solver_pass` reads the authoritative value instead of re-deriving it incorrectly from graph node state.

The mutated room is *intentionally* unpinned (`pinned_position=None`), so any check on graph nodes will always see at least one `None` and trigger full rebalance. The only correct source of truth is whether rooms in `mutated` existed in `prior_matrix`.

**No changes to `graph.py`** — `requires_full_rebalance` lives on `ConstraintSolver` as a transient instance attribute, reset each call to `solve()`.

---

### Step 1 — `ConstraintSolver.__init__` (add instance attribute)

Add to `__init__` (or just set it in `solve()` before calling `_build_graph`):

```python
# In solve(), before _build_graph call — reset the flag each solve
self._requires_full_rebalance: bool = True  # default True (safe for first solve)
```

### Step 2 — `_build_graph` — compute and store the flag

At the end of `_build_graph`, after the position-locks block (after line ~213), add:

```python
# Store the rebalance decision so _run_solver_pass doesn't have to re-derive it.
# Full rebalance = first solve (no prior_matrix) OR any mutated room is genuinely new.
if prior_matrix is None:
    self._requires_full_rebalance = True
else:
    self._requires_full_rebalance = any(rid not in prior_matrix for rid in mutated)

return graph
```

(Move/add the `return graph` if needed — currently it's already there.)

### Step 3 — `_run_solver_pass` — replace the broken `has_new_rooms` block

```python
# CURRENT (BROKEN) — replace this entire block:
has_new_rooms = any(
    graph.nodes[rid].pinned_position is None
    and graph.nodes[rid].is_flexible_pin is False
    for rid in room_ids
) or any(
    graph.nodes[rid].is_flexible_pin
    for rid in room_ids
    if graph.nodes[rid].pinned_position is not None
)

if has_new_rooms:
    positions = self._tile_rooms(room_ids, sizes, graph, pinned={})
else:
    pinned_positions = {
        rid: graph.nodes[rid].pinned_position
        for rid in room_ids
        if graph.nodes[rid].pinned_position is not None
        and not graph.nodes[rid].is_flexible_pin
    }
    positions = self._tile_rooms(room_ids, sizes, graph, pinned=pinned_positions)
```

```python
# FIXED — replace with this:
if self._requires_full_rebalance:
    # First solve, or a genuinely new room was added — full rebalance.
    positions = self._tile_rooms(room_ids, sizes, graph, pinned={})
else:
    # Pure edit (resize/move) — preserve all rooms that have a prior position.
    # This includes both rigid pins (untouched rooms) and flexible pins
    # (neighbors of the mutated room). The mutated room itself has
    # pinned_position=None and will be placed freely with its new size.
    pinned_positions = {
        rid: graph.nodes[rid].pinned_position
        for rid in room_ids
        if graph.nodes[rid].pinned_position is not None
    }
    positions = self._tile_rooms(room_ids, sizes, graph, pinned=pinned_positions)
```

**Key changes:**
1. `has_new_rooms` is replaced entirely by `self._requires_full_rebalance`, computed in `_build_graph` from the authoritative `prior_matrix` + `mutated` sets.
2. The pure-edit `pinned_positions` dict now includes **all** rooms with prior positions (rigid and flexible), so the tiler starts from the real prior layout.
3. The mutated room (`room_10`) has `pinned_position=None` → remains unpinned → tiler places it freely with its new size.
4. `is_flexible_pin` is no longer involved in the rebalance decision at all.

---

## Files to Modify

| File | Change |
|---|---|
| `engine/constraint_solver/solver.py` | Fix `has_new_rooms` detection and pinned dict in `_run_solver_pass` (lines ~365–386) |

**Do NOT touch:**
- `engine/constraint_solver/graph.py` — `is_flexible_pin` field stays on `RoomNode`; it's still set correctly in `_build_graph` and used by other logic
- `engine/plan_manager.py` — `_apply_op`, `scale_factor` logic, `_last_mutated_rooms` are all correct
- `api/routes/plan.py` — the `instruct` → `solve()` call chain is correct
- `engine/intent_parser/` — Claude's `scale_factor` output is correct
- Any test files (tests will be updated as part of this fix, see below)

---

## Tests to Add / Update

In `tests/test_constraint_solver.py` (or create `tests/test_resize_layout.py`):

### Test 1: Resize does not repack untouched rooms
```python
def test_resize_preserves_layout():
    """
    Add 3 rooms, solve to get stable positions.
    Resize room_2 by 25%. Verify room_1 and room_3 x/y are unchanged.
    """
    from engine.plan_manager import PlanManager
    from engine.intent_parser.schemas import FloorPlanOp, RoomSpec

    manager = PlanManager(plan_id="test")

    # Add 3 rooms manually (bypass Claude)
    manager.apply_op(FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name="Room 1", room_type="bedroom", area_sqft=200)))
    manager.apply_op(FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name="Room 2", room_type="living_room", area_sqft=300)))
    manager.apply_op(FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name="Room 3", room_type="kitchen", area_sqft=150)))

    # First solve — establish baseline positions
    matrix1 = manager.solve()
    x1_before = matrix1["room_1"]["x"]
    y1_before = matrix1["room_1"]["y"]
    x3_before = matrix1["room_3"]["x"]
    y3_before = matrix1["room_3"]["y"]

    # Resize room_2 by 25%
    manager.apply_op(FloorPlanOp(
        op_type="resize_room",
        target_room_id="room_2",
        room_spec=RoomSpec(area_sqft=375),  # 300 * 1.25
    ))
    matrix2 = manager.solve()

    # room_1 and room_3 must not have moved
    assert abs(matrix2["room_1"]["x"] - x1_before) < 0.1, "room_1 x shifted unexpectedly"
    assert abs(matrix2["room_1"]["y"] - y1_before) < 0.1, "room_1 y shifted unexpectedly"
    assert abs(matrix2["room_3"]["x"] - x3_before) < 0.1, "room_3 x shifted unexpectedly"
    assert abs(matrix2["room_3"]["y"] - y3_before) < 0.1, "room_3 y shifted unexpectedly"

    # room_2 must have grown
    area2 = matrix2["room_2"]["width"] * matrix2["room_2"]["height"]
    assert area2 > 300 * 0.85, "room_2 did not grow after resize"
```

### Test 2: Add_room still triggers full rebalance (regression guard)
```python
def test_add_room_triggers_rebalance():
    """
    After solving a 2-room plan, adding a third room should produce
    a fresh layout (all 3 rooms visible, no leftover stale positions).
    """
    from engine.plan_manager import PlanManager
    from engine.intent_parser.schemas import FloorPlanOp, RoomSpec

    manager = PlanManager(plan_id="test2")
    manager.apply_op(FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name="Room A", room_type="bedroom", area_sqft=200)))
    manager.apply_op(FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name="Room B", room_type="kitchen", area_sqft=150)))
    manager.solve()

    manager.apply_op(FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name="Room C", room_type="bathroom", area_sqft=80)))
    matrix = manager.solve()

    assert "room_a" in matrix
    assert "room_b" in matrix
    assert "room_c" in matrix
    # All 3 rooms must have non-zero dimensions
    for rid, coords in matrix.items():
        assert coords["width"] > 0
        assert coords["height"] > 0
```

### Test 3: scale_factor resize with 13-room plan (the original bug)
```python
def test_scale_factor_resize_large_plan():
    """
    Regression: 13-room plan, resize room_10 by 25%.
    All other 12 rooms must keep their approximate prior positions.
    """
    from engine.plan_manager import PlanManager
    from engine.intent_parser.schemas import FloorPlanOp, RoomSpec

    manager = PlanManager(plan_id="test3")
    rooms = [
        ("Room 1", "bedroom", 200), ("Room 2", "bathroom", 80),
        ("Room 3", "kitchen", 180), ("Room 4", "living_room", 300),
        ("Room 5", "bedroom", 200), ("Room 6", "bathroom", 75),
        ("Room 7", "dining_room", 160), ("Room 8", "bedroom", 220),
        ("Room 9", "office", 120), ("Room 10", "living_room", 280),
        ("Room 11", "bedroom", 190), ("Room 12", "bathroom", 85),
        ("Room 13", "kitchen", 170),
    ]
    for name, rtype, area in rooms:
        manager.apply_op(FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name=name, room_type=rtype, area_sqft=area)))

    matrix1 = manager.solve()

    # Capture positions of all rooms except room_10
    positions_before = {
        rid: (coords["x"], coords["y"])
        for rid, coords in matrix1.items()
        if rid != "room_10"
    }

    # Resize room_10 by 25%
    manager.apply_op(FloorPlanOp(
        op_type="resize_room",
        target_room_id="room_10",
        room_spec=RoomSpec(area_sqft=round(280 * 1.25, 2)),
    ))
    matrix2 = manager.solve()

    # All other rooms should be at approximately the same position
    for rid, (x_before, y_before) in positions_before.items():
        x_after = matrix2[rid]["x"]
        y_after = matrix2[rid]["y"]
        assert abs(x_after - x_before) < 2.0, f"{rid} x moved too much: {x_before} → {x_after}"
        assert abs(y_after - y_before) < 2.0, f"{rid} y moved too much: {y_before} → {y_after}"
```

---

## Expected Behavior After Fix

1. **Resize op**: Target room changes size. All other rooms stay within ~2ft of their original positions (slight nudge acceptable if the resized room grew into a neighbor's space).
2. **Add-room op**: Full rebalance still happens — new room gets a slot, all existing rooms may shift.
3. **`scale_factor` resize via NL**: Same as (1) — "expand room 10 by 25%" → room 10 grows, rooms 1–9 and 11–13 don't move.

---

## Architecture Rules (do not violate)
- LLM never touches geometry
- Constraint solver never calls LLM
- CAD kernel never reads conversational state
- `RoomSpec` is a delta struct for non-`add_room` ops — only `add_room` requires `name` + `room_type`
- `name` and `room_type` must remain `Optional` on `RoomSpec`

---

## Verification

After implementing:
```bash
cd /data/workspace/cognitect
source .venv/bin/activate
python -m pytest tests/ -m "not slow and not live" -v
# All existing tests must still pass
# New resize tests must pass
```
