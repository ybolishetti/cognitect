"""
Plan Load API route — seed a PlanManager from an uploaded file.

POST /plan/load
  Accepts multipart/form-data with a single file field: "file"
  Supported formats:
    - .json  — Cognitect FloorPlanState JSON (our native format)
    - .dxf   — AutoCAD DXF (parsed via ezdxf, best-effort room extraction)

  Returns: {plan_id, room_count, format, message}
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import tempfile
import uuid
from pathlib import Path

import ezdxf
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from engine.plan_manager import PlanManager
from engine.intent_parser.schemas import FloorPlanState, RoomSpec
from api.routes.plan import _PLANS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plan", tags=["load"])

# Loading a plan only requires the parser later (during /instruct). Fall back to a
# placeholder key so a plan can be loaded in a keyless demo/test environment; the
# real key is read from the env when an actual instruction is parsed.
_FALLBACK_KEY = os.environ.get("COGNITECT_CLAUDE_API_KEY") or "demo-placeholder-key"

INSUNITS_TO_FEET = {
    1: 1 / 12,       # inches
    2: 1.0,          # feet
    4: 1 / 304.8,    # mm
    5: 1 / 30.48,    # cm
    6: 3.28084,      # meters
}


class LoadResponse(BaseModel):
    plan_id: str
    room_count: int
    format: str
    message: str


@router.post("/load", response_model=LoadResponse)
async def load_plan(file: UploadFile = File(...)):
    """
    Upload an existing floor plan and create an editable session from it.

    Supported:
      - .json — native FloorPlanState JSON export from Cognitect
      - .dxf  — AutoCAD DXF (rooms extracted from closed polylines)
    """
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()

    if suffix not in (".json", ".dxf"):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Supported: .json, .dxf"
        )

    raw = await file.read()

    if suffix == ".json":
        return await _load_from_json(raw)
    else:
        return await _load_from_dxf(raw, filename)


async def _load_from_json(raw: bytes) -> LoadResponse:
    """Load from native Cognitect FloorPlanState JSON."""
    try:
        data = json.loads(raw)
        state = FloorPlanState(**data)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid FloorPlanState JSON: {exc}"
        )

    plan_id = state.plan_id or str(uuid.uuid4())[:8]
    manager = PlanManager(plan_id=plan_id, api_key=_FALLBACK_KEY)
    manager._state = state  # seed with loaded state
    _PLANS[plan_id] = manager

    logger.info("Loaded JSON plan '%s' — %d rooms", plan_id, len(state.rooms))
    return LoadResponse(
        plan_id=plan_id,
        room_count=len(state.rooms),
        format="json",
        message=f"Loaded plan '{plan_id}' with {len(state.rooms)} room(s). "
                f"Send instructions to /plan/{plan_id}/instruct",
    )


async def _load_from_dxf(raw: bytes, filename: str) -> LoadResponse:
    """
    Parse a DXF file and extract rooms from closed polylines.

    Each closed polyline is treated as a room boundary.
    Rooms are named 'Room 1', 'Room 2', etc. and typed as 'other'.
    The user can then rename/retype them via NL instructions.

    Uses $INSUNITS when present, otherwise global heuristics, then filters
    non-room geometry and normalizes coordinates to the origin in feet.
    """
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

    insunits = doc.header.get("$INSUNITS", 0)
    scale = INSUNITS_TO_FEET.get(insunits)

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

    if scale is None:
        max_dim = max(max(c[2], c[3]) for c in candidates)
        if max_dim > 10_000:
            scale = 1 / 304.8
        elif max_dim > 500:
            scale = 1 / 12
        else:
            scale = 1.0

    scaled = [(x * scale, y * scale, w * scale, h * scale) for (x, y, w, h) in candidates]

    min_room_sqft = 20
    max_room_sqft = 5000
    filtered = [
        (x, y, w, h) for (x, y, w, h) in scaled
        if min_room_sqft <= w * h <= max_room_sqft
    ]

    if not filtered:
        raise HTTPException(
            status_code=422,
            detail=(
                f"DXF parsed but no plausible room shapes found after unit conversion "
                f"(scale={scale:.6f}). All {len(scaled)} shapes were outside the "
                f"{min_room_sqft}–{max_room_sqft} sqft range. "
                "Check that the DXF contains closed polylines sized as rooms, not site boundaries."
            ),
        )

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

    if not filtered:
        raise HTTPException(
            status_code=422,
            detail=(
                "DXF parsed but all room shapes were spatial outliers after filtering. "
                "Check that closed polylines represent rooms in a single floor-plan cluster."
            ),
        )

    origin_x = min(x for (x, y, w, h) in filtered)
    origin_y = min(y for (x, y, w, h) in filtered)
    normalized = [(x - origin_x, y - origin_y, w, h) for (x, y, w, h) in filtered]

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
