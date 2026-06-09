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

    Coordinate units: assumed to be feet. If bounding boxes look implausibly
    large (> 500ft on a side), divide by 12 (inches → feet).
    """
    import os
    import tempfile

    # ezdxf reads from a text stream or a file path; write the uploaded bytes to a
    # temp file and use readfile() so encoding detection (ASCII/binary DXF) works.
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

    rooms: dict[str, RoomSpec] = {}
    coordinate_matrix: dict[str, dict] = {}
    room_index = 1

    for entity in msp:
        # Only process closed LWPOLYLINEs (room outlines)
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
        w = x_max - x_min
        h = y_max - y_min

        # Skip degenerate shapes
        if w < 0.1 or h < 0.1:
            continue

        # Heuristic: if any dimension > 500, assume inches and convert to feet
        if w > 500 or h > 500:
            x_min /= 12; x_max /= 12
            y_min /= 12; y_max /= 12
            w /= 12; h /= 12

        room_id = f"room_{room_index}"
        area = round(w * h, 1)

        rooms[room_id] = RoomSpec(
            name=f"Room {room_index}",
            room_type="other",
            area_sqft=area,
        )
        coordinate_matrix[room_id] = {
            "x": round(x_min, 2),
            "y": round(y_min, 2),
            "width": round(w, 2),
            "height": round(h, 2),
        }
        room_index += 1

    if not rooms:
        raise HTTPException(
            status_code=422,
            detail="No closed polylines found in DXF. "
                   "Ensure rooms are drawn as closed LWPOLYLINE entities."
        )

    plan_id = str(uuid.uuid4())[:8]
    state = FloorPlanState(
        plan_id=plan_id,
        rooms=rooms,
        coordinate_matrix=coordinate_matrix,
    )
    manager = PlanManager(plan_id=plan_id, api_key=_FALLBACK_KEY)
    manager._state = state
    _PLANS[plan_id] = manager

    logger.info("Loaded DXF plan '%s' — %d rooms extracted", plan_id, len(rooms))
    return LoadResponse(
        plan_id=plan_id,
        room_count=len(rooms),
        format="dxf",
        message=f"Extracted {len(rooms)} room(s) from DXF. "
                f"Rooms are named 'Room 1', 'Room 2', etc. "
                f"Use NL instructions to rename, resize, or rearrange them.",
    )
