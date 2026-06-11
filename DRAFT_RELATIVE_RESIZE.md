# DRAFT: Relative Resize — scale_factor support for percentage-based room commands

## Problem

Commands like `"expand room 10 by 25%"` or `"make the kitchen 50% larger"` silently do nothing today.

**Root cause:** Two bugs compound:
1. `RoomSpec` has no `scale_factor` field — Claude has nowhere to encode relative sizing.
2. The system prompt has no rule for percentage/relative resize, so Claude either invents an absolute sqft (losing the relative semantics) or returns a `resize_room` with `room_spec=None` AND `constraint_spec=None`, which causes `_apply_op` to hit the dead branch and silently skip.

## Scope

Four files: `schemas.py`, `parser.py` (prompt), `plan_manager.py`, and one test file.

---

## Step 1 — Add `scale_factor` to `RoomSpec`

File: `engine/intent_parser/schemas.py`

Add an optional field to `RoomSpec`:

```python
scale_factor: Optional[float] = Field(
    None,
    gt=0,
    description=(
        "Multiplicative scale factor for relative resize. "
        "1.25 = expand by 25%, 0.8 = shrink by 20%. "
        "Applied to the current room area at solve time. "
        "Mutually exclusive with area_sqft for resize ops."
    ),
)
```

Place it after `max_area_sqft` and before `aspect_ratio`.

---

## Step 2 — Update the system prompt

File: `engine/intent_parser/parser.py`

In `FLOOR_PLAN_OP_SCHEMA`, update the `RoomSpec` block to include the new field:

```
"scale_factor": float or null,   // relative resize: 1.25 = +25%, 0.8 = -20%
```

In `SYSTEM_PROMPT_BATCH`, add a new numbered rule after rule 16 (the area_sqft strength rules):

```
17. For percentage / relative resize requests, use scale_factor instead of area_sqft:
    - "Expand X by 25%" → resize_room(target=X, room_spec={scale_factor: 1.25})
    - "Make X 50% larger" → resize_room(target=X, room_spec={scale_factor: 1.50})
    - "Shrink X by 10%" → resize_room(target=X, room_spec={scale_factor: 0.90})
    - "Double the size of X" → resize_room(target=X, room_spec={scale_factor: 2.0})
    - "Reduce X to half" → resize_room(target=X, room_spec={scale_factor: 0.5})
    Do NOT use area_sqft when the user specifies a relative change. Use scale_factor.
    Do NOT leave room_spec null for resize_room ops — always populate it.
```

---

## Step 3 — Apply scale_factor in `_apply_op`

File: `engine/plan_manager.py`

In the `resize_room` branch of `_apply_op`, after the existing `if op.room_spec:` merge block, handle `scale_factor`:

Current code (simplified):
```python
elif op.op_type == "resize_room":
    self._assert_room_exists(op.target_room_id, rooms)
    self._last_mutated_rooms.add(op.target_room_id)
    existing = rooms[op.target_room_id]
    if op.room_spec:
        updated = existing.model_copy(update={
            k: v for k, v in op.room_spec.model_dump(exclude_none=True).items()
            if k not in ("name", "room_type")
        })
        rooms[op.target_room_id] = updated
    elif op.constraint_spec:
        constraints.append(op.constraint_spec)
```

Replace `if op.room_spec:` block with:
```python
    if op.room_spec:
        spec_data = op.room_spec.model_dump(exclude_none=True)

        # Handle relative resize via scale_factor
        if op.room_spec.scale_factor is not None:
            current_area = existing.area_sqft or 0.0
            if current_area > 0:
                new_area = round(current_area * op.room_spec.scale_factor, 2)
                spec_data["area_sqft"] = new_area
            # scale_factor is a derived instruction — don't store it on the room
            spec_data.pop("scale_factor", None)

        updated = existing.model_copy(update={
            k: v for k, v in spec_data.items()
            if k not in ("name", "room_type")
        })
        rooms[op.target_room_id] = updated
    elif op.constraint_spec:
        constraints.append(op.constraint_spec)
```

**Important:** If `current_area == 0` (room has no area yet), log a warning and skip the scale — don't silently set `area_sqft = 0`.

Add this import at the top if not already present: nothing new needed.

---

## Step 4 — Tests

File: `tests/test_plan_manager.py`

Add tests covering:
1. `scale_factor=1.25` on a room with `area_sqft=200` → `area_sqft=250` after `apply_op`
2. `scale_factor=0.8` on a room with `area_sqft=200` → `area_sqft=160`
3. `scale_factor` on a room with no area → no-op (no crash, warning logged)
4. `scale_factor` does not overwrite `name` or `room_type`
5. After `apply_op` with `scale_factor`, calling `solve()` returns an updated `coordinate_matrix` (integration)

---

## Acceptance Criteria

- `PlanManager.instruct("expand room_10 by 25%")` where `room_10` has `area_sqft=200` → after `solve()`, `coordinate_matrix["room_10"]` has a larger `width`/`height` footprint than before.
- `PlanManager.instruct("make the living room 50% larger")` → `area_sqft` of `living_room` increases by 50%.
- `PlanManager.instruct("shrink the office by 10%")` → `area_sqft` of `office` decreases by 10%.
- No crash when `scale_factor` is applied to a room with `area_sqft=None`.
- All existing tests continue to pass.

---

## Notes

- `scale_factor` is consumed at apply-time and converted to an absolute `area_sqft`. It is NOT stored on the persisted `RoomSpec`. This keeps the state model clean — the coordinate matrix is always derived from absolute areas.
- The fix in `plan.py` (solve-after-instruct) was applied separately on `main`. This DRAFT only covers the `scale_factor` feature.
- Room IDs in Cognitect use slugified names (e.g. `room_10`, `living_room`). The intent parser must match the exact ID from the plan state — never invent IDs.
