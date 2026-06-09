# DRAFT: Robust DXF Importer — Phase 6

## Problem Statement

The current DXF importer (`api/routes/load.py: _load_from_dxf`) has three confirmed bugs
visible in production (screenshot: Plan 7321ba66):

1. **Outlier rooms destroy the viewport.** One room ("Room 44") lands far from the rest
   (bottom-left of canvas while all others are top-right). This stretches the matplotlib
   axis to cover the entire span, making every other room microscopically small.

2. **Coordinate units are wrong.** Dimension ticks show values like 189', 41', 68' for
   rooms in a residential floor plan — impossible at human scale. The DXF is almost
   certainly in **millimeters or inches**, not feet. The current heuristic
   (`if w > 500 or h > 500: divide by 12`) only fires on individual room dimensions,
   misses rooms just under 500, and uses the wrong divisor for mm (should be 304.8).

3. **No coordinate normalization.** Rooms are stored at raw DXF world coordinates
   (e.g. x=4200, y=1800 in mm). After unit conversion they end up scattered around
   arbitrary positions instead of starting at (0, 0). The previewer's auto-scale handles
   this partially but any outlier or near-zero-size artifact breaks it.

### Root Causes

- `_load_from_dxf` checks `$INSUNITS` header field only implicitly (not at all — it's absent)
- Unit detection heuristic is per-room, not global
- No outlier filtering — dimension lines, annotation borders, and title block rectangles in
  the DXF are extracted as "rooms"
- No coordinate normalization after extraction
- Parser only handles `LWPOLYLINE` / `POLYLINE` with `closed=True` — many architectural DXFs
  use `LINE` segments for walls

---

## Goals

- Correctly import real-world architectural DXF files (AutoCAD, Revit export, SketchUp export)
- Render rooms in the correct relative layout at human-readable scale (feet)
- Filter out non-room geometry (dimension lines, title blocks, annotation boxes)
- Graceful fallback with a clear error message when the DXF can't be parsed

## Non-Goals

- Full DXF spec compliance (e.g. ACAD_PROXY_ENTITY, 3D solids, xrefs)
- Extracting room *names* from DXF text entities (rooms will still be named "Room N"; user
  renames via NL instructions afterward)
- Modifying the previewer or exporter (those are correct; the bug is in the importer)

---

## Implementation Plan

All changes are confined to **`api/routes/load.py`**, specifically `_load_from_dxf()`.
No other files should need to change.

### Step 1 — Read `$INSUNITS` from DXF header

ezdxf exposes the `$INSUNITS` header variable. Map it to a feet conversion factor:

```python
INSUNITS_TO_FEET = {
    0:  None,    # unitless — fall back to heuristic
    1:  1/12,    # inches → feet
    2:  1.0,     # feet (no conversion)
    4:  1/304.8, # mm → feet
    5:  1/30.48, # cm → feet
    6:  1/1000 * 3.28084,  # meters → feet
    13: 1/25.4,  # microinches — unlikely, ignore
    14: 1/304.8, # microns — treat as mm
}
```

Read it once at the top of `_load_from_dxf`:

```python
insunits = doc.header.get("$INSUNITS", 0)
scale = INSUNITS_TO_FEET.get(insunits)  # None = unknown
```

### Step 2 — Collect ALL candidate shapes first, then decide scale globally

**Problem with current approach:** scale is applied per-room, so the heuristic can fire on
some rooms but not others depending on their individual size.

**Fix:** collect all bounding boxes first (raw units), compute the global bounding box of
the entire drawing, then decide the scale factor once:

```python
candidates = []  # list of (x_min, y_min, w, h) in raw DXF units

for entity in msp:
    # ... extract xs, ys as before ...
    candidates.append((x_min, y_min, w, h))

# Global scale detection (if $INSUNITS was 0/unknown)
if scale is None and candidates:
    all_widths = [c[2] for c in candidates]
    all_heights = [c[3] for c in candidates]
    max_dim = max(max(all_widths), max(all_heights))
    if max_dim > 10_000:
        scale = 1 / 304.8   # almost certainly mm
    elif max_dim > 1_000:
        scale = 1 / 25.4    # likely inches with fine detail
    elif max_dim > 200:
        scale = 1 / 12      # inches
    else:
        scale = 1.0         # assume feet already

# Apply scale to all candidates
scaled = [
    (x * scale, y * scale, w * scale, h * scale)
    for (x, y, w, h) in candidates
]
```

### Step 3 — Filter outliers and non-room geometry

Real rooms in a residential floor plan are roughly **50–2000 sqft** (4.6–185 m²).
Anything outside this range is almost certainly a dimension line rectangle, title block
border, or annotation bounding box.

```python
MIN_ROOM_SQFT = 20    # smaller than a closet = probably not a room
MAX_ROOM_SQFT = 5000  # larger than a ballroom = probably a site boundary or title block

room_candidates = []
for (x, y, w, h) in scaled:
    area = w * h
    if MIN_ROOM_SQFT <= area <= MAX_ROOM_SQFT:
        room_candidates.append((x, y, w, h))
```

**Additional outlier check — IQR-based spatial filter:**

If one room is far away from the cluster (like "Room 44" in the screenshot), it's likely
a stray entity (title block, legend box, north arrow boundary). Filter by centroid distance:

```python
import statistics

if len(room_candidates) >= 4:
    cx_list = [x + w/2 for (x, y, w, h) in room_candidates]
    cy_list = [y + h/2 for (x, y, w, h) in room_candidates]
    median_cx = statistics.median(cx_list)
    median_cy = statistics.median(cy_list)
    
    # Compute median absolute deviation as spread estimate
    spread = max(
        statistics.median([abs(cx - median_cx) for cx in cx_list]) or 1,
        statistics.median([abs(cy - median_cy) for cy in cy_list]) or 1,
    )
    threshold = max(spread * 5, 30)  # 5× MAD, minimum 30ft
    
    room_candidates = [
        (x, y, w, h) for (x, y, w, h) in room_candidates
        if abs((x + w/2) - median_cx) <= threshold
        and abs((y + h/2) - median_cy) <= threshold
    ]
```

### Step 4 — Normalize coordinates to origin

After filtering, shift so the bottom-left of the bounding box is at (0, 0):

```python
if room_candidates:
    origin_x = min(x for (x, y, w, h) in room_candidates)
    origin_y = min(y for (x, y, w, h) in room_candidates)
    room_candidates = [
        (x - origin_x, y - origin_y, w, h)
        for (x, y, w, h) in room_candidates
    ]
```

### Step 5 — Assemble RoomSpec objects (same as today)

```python
rooms: dict[str, RoomSpec] = {}
coordinate_matrix: dict[str, dict] = {}

for i, (x, y, w, h) in enumerate(room_candidates, start=1):
    room_id = f"room_{i}"
    area = round(w * h, 1)
    rooms[room_id] = RoomSpec(
        name=f"Room {i}",
        room_type="other",
        area_sqft=area,
    )
    coordinate_matrix[room_id] = {
        "x": round(x, 2),
        "y": round(y, 2),
        "width": round(w, 2),
        "height": round(h, 2),
    }
```

---

## Full Revised `_load_from_dxf` Function

```python
INSUNITS_TO_FEET = {
    1:  1/12,       # inches
    2:  1.0,        # feet
    4:  1/304.8,    # mm
    5:  1/30.48,    # cm
    6:  3.28084,    # meters
}

async def _load_from_dxf(raw: bytes, filename: str) -> LoadResponse:
    import os, tempfile, statistics

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        doc = ezdxf.readfile(tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid DXF file: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    msp = doc.modelspace()

    # --- Step 1: Read unit scale from header ---
    insunits = doc.header.get("$INSUNITS", 0)
    scale = INSUNITS_TO_FEET.get(insunits)  # None = unknown, detect heuristically

    # --- Step 2: Extract all candidate closed-polyline bounding boxes ---
    candidates = []
    for entity in msp:
        if entity.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
            continue
        if not getattr(entity.dxf, "closed", False) and not getattr(entity, "is_closed", False):
            continue
        try:
            if entity.dxftype() == "LWPOLYLINE":
                points = list(entity.get_points())
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
            else:
                verts = list(entity.vertices)
                xs = [v.dxf.location.x for v in verts]
                ys = [v.dxf.location.y for v in verts]
        except Exception:
            continue
        if len(xs) < 3:
            continue
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        w, h = x_max - x_min, y_max - y_min
        if w < 0.01 or h < 0.01:
            continue
        candidates.append((x_min, y_min, w, h))

    if not candidates:
        raise HTTPException(
            status_code=422,
            detail=(
                "No closed polylines found in DXF. "
                "Ensure rooms are drawn as closed LWPOLYLINE entities. "
                "Tip: in AutoCAD, use BOUNDARY command to convert wall lines to closed polylines."
            ),
        )

    # --- Step 2b: Global scale detection (fallback when $INSUNITS is missing) ---
    if scale is None:
        max_dim = max(max(c[2], c[3]) for c in candidates)
        if max_dim > 10_000:
            scale = 1 / 304.8    # mm
        elif max_dim > 500:
            scale = 1 / 12       # inches
        else:
            scale = 1.0          # assume feet

    scaled = [(x * scale, y * scale, w * scale, h * scale) for (x, y, w, h) in candidates]

    # --- Step 3: Filter by area (remove dimension lines, title blocks, site borders) ---
    MIN_ROOM_SQFT = 20
    MAX_ROOM_SQFT = 5000
    filtered = [(x, y, w, h) for (x, y, w, h) in scaled if MIN_ROOM_SQFT <= w * h <= MAX_ROOM_SQFT]

    if not filtered:
        raise HTTPException(
            status_code=422,
            detail=(
                f"DXF parsed but no plausible room shapes found after unit conversion "
                f"(scale={scale:.6f}). All {len(scaled)} shapes were outside the "
                f"{MIN_ROOM_SQFT}–{MAX_ROOM_SQFT} sqft range. "
                "Check that the DXF contains closed polylines sized as rooms, not site boundaries."
            ),
        )

    # --- Step 3b: Outlier filter (remove spatially isolated shapes) ---
    if len(filtered) >= 4:
        cx_list = [x + w / 2 for (x, y, w, h) in filtered]
        cy_list = [y + h / 2 for (x, y, w, h) in filtered]
        med_cx = statistics.median(cx_list)
        med_cy = statistics.median(cy_list)
        mad_x = statistics.median([abs(cx - med_cx) for cx in cx_list]) or 1
        mad_y = statistics.median([abs(cy - med_cy) for cy in cy_list]) or 1
        thresh_x = max(mad_x * 5, 30)
        thresh_y = max(mad_y * 5, 30)
        filtered = [
            (x, y, w, h) for (x, y, w, h) in filtered
            if abs((x + w / 2) - med_cx) <= thresh_x
            and abs((y + h / 2) - med_cy) <= thresh_y
        ]

    # --- Step 4: Normalize to origin ---
    origin_x = min(x for (x, y, w, h) in filtered)
    origin_y = min(y for (x, y, w, h) in filtered)
    normalized = [(x - origin_x, y - origin_y, w, h) for (x, y, w, h) in filtered]

    # --- Step 5: Build RoomSpec objects ---
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
        plan_id=plan_id,
        rooms=rooms,
        coordinate_matrix=coordinate_matrix,
    )
    manager = PlanManager(plan_id=plan_id, api_key=_FALLBACK_KEY)
    manager._state = state
    _PLANS[plan_id] = manager

    logger.info(
        "Loaded DXF plan '%s' — %d rooms (scale=%.6f, insunits=%d, raw=%d, filtered=%d)",
        plan_id, len(rooms), scale, insunits, len(candidates), len(filtered),
    )
    return LoadResponse(
        plan_id=plan_id,
        room_count=len(rooms),
        format="dxf",
        message=(
            f"Extracted {len(rooms)} room(s) from DXF "
            f"(unit scale: {scale:.4f} ft/unit). "
            "Rooms are named 'Room 1', 'Room 2', etc. "
            "Use NL instructions to rename, resize, or rearrange them."
        ),
    )
```

---

## Tests to Add (`tests/test_dxf_importer.py`)

```python
# 1. Happy path: DXF in feet — coordinates pass through unchanged
# 2. Happy path: DXF in mm ($INSUNITS=4) — correctly scales to feet
# 3. Happy path: DXF in inches (no $INSUNITS, large coords) — heuristic detects and scales
# 4. Outlier room is filtered: 35 rooms clustered + 1 far outlier → 35 rooms returned
# 5. Non-room shapes filtered: mix of 10–300 sqft rooms + tiny polylines + giant border → only rooms returned
# 6. All shapes too large: raises 422 with helpful message
# 7. No closed polylines at all: raises 422 with BOUNDARY command tip
# 8. Coordinate normalization: min x/y of output is always 0.0
```

---

## Files Changed

| File | Change |
|---|---|
| `api/routes/load.py` | Replace `_load_from_dxf()` with revised implementation above |
| `tests/test_dxf_importer.py` | New file — 8 unit tests (create synthetic DXFs via ezdxf) |

No changes to: `engine/previewer.py`, `engine/exporter/exporter.py`, `api/routes/preview.py`, frontend.

---

## Acceptance Criteria

- [ ] Uploading the DXF from the screenshot (Plan 7321ba66 repro) renders all rooms in a
      clean grid layout with no outliers
- [ ] Dimension labels show reasonable values (< 100' for residential rooms)
- [ ] `Room 44` outlier is excluded or placed correctly within the cluster
- [ ] All 8 tests pass
- [ ] Existing tests (`pytest -m "not slow and not live"`) continue to pass
