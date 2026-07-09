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

import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from engine.plan_manager import PlanManager
from engine.importers import parse_dxf_to_state, parse_json_to_state
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
    state = parse_json_to_state(raw)

    plan_id = state.plan_id or str(uuid.uuid4())[:8]
    if state.plan_id != plan_id:
        state = state.model_copy(update={"plan_id": plan_id})
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
    Parse a DXF file and extract rooms from closed polylines, LINE walls, or HATCH fills.

    Each detected room boundary becomes a RoomSpec named 'Room 1', 'Room 2', etc.
    Uses $INSUNITS when present, otherwise global heuristics, then filters
    non-room geometry and normalizes coordinates to the origin in feet.
    """
    state = parse_dxf_to_state(raw, filename)
    plan_id = state.plan_id

    manager = PlanManager(plan_id=plan_id, api_key=_FALLBACK_KEY)
    manager._state = state
    _PLANS[plan_id] = manager

    logger.info("Loaded DXF plan '%s' — %d rooms", plan_id, len(state.rooms))
    return LoadResponse(
        plan_id=plan_id,
        room_count=len(state.rooms),
        format="dxf",
        message=(
            f"Extracted {len(state.rooms)} room(s) from DXF. "
            "Rooms are named 'Room 1', 'Room 2', etc. "
            "Use NL instructions to rename, resize, or rearrange them."
        ),
    )
