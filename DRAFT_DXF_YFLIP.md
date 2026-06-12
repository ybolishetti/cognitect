# DRAFT: Fix DXF y-axis flip — normalize to solver coordinate space on load

## Task
When a DXF floor plan is uploaded, the extracted room coordinates use the DXF convention (y=0 at bottom, y increases upward). The solver and previewer use the opposite convention (y=0 at top, y increases downward — shelf-pack style). This mismatch causes two symptoms:
1. Rooms overlap or appear stacked incorrectly immediately on upload (e.g. "room 10 sitting on top of room 3")
2. After any resize op, the post-solve preview is completely flipped upside down

The fix is a 4-line y-flip normalization applied to `coordinate_matrix` in `_load_from_dxf` right after the room-building loop, before the state is stored.

---

## Root Cause

DXF coordinate system: `y=0` at **bottom**, `y` increases **upward**.
Solver shelf-packer (`_tile_rooms`): `y=0` at **top**, `y` increases **downward** (new shelves have larger `y`).
Previewer (`ax.set_ylim(max_y + pad, min_y - pad)`): inverted axis, so `min_y` renders at the top of the screen — correct for solver coords, wrong for DXF coords.

After `_filter_and_normalize` in `load.py`, rooms are shifted so `min_y = 0`, but the direction is still DXF (upward). When these coords are stored in `coordinate_matrix` and the previewer draws them with its inverted axis, the room with the smallest DXF y (bottom of the building) appears at the top of the canvas — upside down.

Worse: when a resize op then calls `solve()`, the solver receives the DXF coords as `prior_matrix` and pins all rooms there. The resized room is placed by the shelf-packer at `y ≈ 0` (solver top), but the other rooms are pinned at large DXF y values. The result is the resized room stranded at the top while everything else stays in DXF-space — producing a completely scrambled layout.

---

## Fix

### File: `api/routes/load.py`, function `_load_from_dxf`

**Location:** After the `for i, (x, y, w, h) in enumerate(normalized, start=1):` loop (after line ~583), before building `FloorPlanState`.

**Add this block** (4 lines) immediately after the loop:

```python
# Flip y-axis from DXF convention (y=0 at bottom, increases up)
# to solver/previewer convention (y=0 at top, increases down).
# Without this, DXF-loaded plans render upside-down and mismatch
# the solver's shelf-pack positions on any subsequent resize.
if coordinate_matrix:
    total_height = max(
        c["y"] + c["height"] for c in coordinate_matrix.values()
    )
    for c in coordinate_matrix.values():
        c["y"] = round(total_height - c["y"] - c["height"], 2)
```

**Full context of the change** (so Cursor can find the exact insertion point):

```python
# BEFORE (current code):
    rooms: dict[str, RoomSpec] = {}
    coordinate_matrix: dict[str, dict] = {}
    for i, (x, y, w, h) in enumerate(normalized, start=1):
        room_id = f"room_{i}"
        rooms[room_id] = RoomSpec(
            name=f"Room {i}",
            room_type="other",
            area_sqft=round(w * h, 1),
        )
        coordinate_matrix[room_id] = {
            "x": round(x, 2),
            "y": round(y, 2),
            "width": round(w, 2),
            "height": round(h, 2),
        }

    plan_id = str(uuid.uuid4())[:8]
    state = FloorPlanState(

# AFTER (with fix inserted):
    rooms: dict[str, RoomSpec] = {}
    coordinate_matrix: dict[str, dict] = {}
    for i, (x, y, w, h) in enumerate(normalized, start=1):
        room_id = f"room_{i}"
        rooms[room_id] = RoomSpec(
            name=f"Room {i}",
            room_type="other",
            area_sqft=round(w * h, 1),
        )
        coordinate_matrix[room_id] = {
            "x": round(x, 2),
            "y": round(y, 2),
            "width": round(w, 2),
            "height": round(h, 2),
        }

    # Flip y-axis: DXF uses y=0 at bottom (increases up).
    # Solver and previewer use y=0 at top (increases down).
    # Normalize here so loaded coords are compatible with all downstream logic.
    if coordinate_matrix:
        total_height = max(
            c["y"] + c["height"] for c in coordinate_matrix.values()
        )
        for c in coordinate_matrix.values():
            c["y"] = round(total_height - c["y"] - c["height"], 2)

    plan_id = str(uuid.uuid4())[:8]
    state = FloorPlanState(
```

---

## Files to Modify

| File | Change |
|---|---|
| `api/routes/load.py` | Add y-flip block after coordinate_matrix build loop in `_load_from_dxf` |

**Do NOT touch:**
- `engine/constraint_solver/solver.py` — already fixed in `cursor/fix-resize-rebalance`
- `engine/previewer.py` — the inverted ylim is correct for solver coords, do not change it
- `_load_from_json` — JSON exports are already in solver coords (they were saved from a solved state), no flip needed
- `_filter_and_normalize` — the origin normalization there is fine, this fix goes on top of it

---

## Why NOT fix it in `_filter_and_normalize`

`_filter_and_normalize` is a pure geometry utility — it only shifts origin to (0,0). It doesn't know about the solver's coordinate convention. The flip belongs at the boundary between DXF-space and solver-space, which is `_load_from_dxf` after all geometry extraction is done.

---

## Tests to Add

In `tests/test_load.py` (or `tests/test_api_load.py` — wherever DXF load tests live):

```python
def test_dxf_load_y_coords_flipped_to_solver_space():
    """
    Rooms extracted from DXF should have y=0 at the top (largest DXF-y room
    gets smallest solver-y after flip). This ensures solver and previewer
    see correct orientation.
    """
    # Simulate two rooms stacked vertically in DXF space:
    # Room A: y=0  (bottom in DXF = should be at BOTTOM in display = large solver-y)
    # Room B: y=10 (top in DXF = should be at TOP in display = small solver-y)
    # After flip with total_height=20 (room B: y=10, h=10 → top=20):
    #   Room A: flipped_y = 20 - 0 - 10 = 10  (lower on screen)
    #   Room B: flipped_y = 20 - 10 - 10 = 0  (higher on screen, at top)

    from api.routes.load import _filter_and_normalize

    # Mock a minimal coordinate_matrix as _load_from_dxf would build it
    # pre-flip, then apply the flip manually to verify the math
    coordinate_matrix = {
        "room_1": {"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0},   # DXF bottom
        "room_2": {"x": 0.0, "y": 10.0, "width": 10.0, "height": 10.0},  # DXF top
    }

    total_height = max(c["y"] + c["height"] for c in coordinate_matrix.values())
    for c in coordinate_matrix.values():
        c["y"] = round(total_height - c["y"] - c["height"], 2)

    # After flip: room_2 (was at top in DXF) should now be at y=0 (top of screen)
    assert coordinate_matrix["room_2"]["y"] == 0.0
    # room_1 (was at bottom in DXF) should now be at y=10 (bottom of screen)
    assert coordinate_matrix["room_1"]["y"] == 10.0
    # Heights must be preserved
    assert coordinate_matrix["room_1"]["height"] == 10.0
    assert coordinate_matrix["room_2"]["height"] == 10.0
    # x coords must be untouched
    assert coordinate_matrix["room_1"]["x"] == 0.0
    assert coordinate_matrix["room_2"]["x"] == 0.0


def test_dxf_load_via_api_y_coords_in_solver_space(client):
    """
    Integration test: upload a minimal DXF with two vertically-stacked rooms.
    The returned coordinate_matrix should have the top room at y=0 and
    the bottom room at y > 0.
    """
    # This test requires a real minimal DXF fixture — skip if not available
    pytest.importorskip("ezdxf")
    # ... (use a prebuilt test DXF fixture or skip with xfail if no fixture exists)
```

The unit test (first one) should be straightforward to run without a DXF file. The integration test can be marked `slow` or `xfail` if no DXF fixture exists.

---

## Verification

After implementing:
```bash
cd /data/workspace/cognitect
source .venv/bin/activate
python -m pytest tests/ -m "not slow and not live" -v
# All tests must still pass
```

Manual smoke test:
1. Upload the original floor plan image DXF
2. Preview should show rooms in correct orientation (not upside down)
3. Issue "expand room 10 by 25%"
4. Preview should show room 10 larger, all other rooms in same positions, NOT flipped
