"""
Plan API routes — Phase 2.

POST /plan/new                          → create a new plan session
POST /plan/{plan_id}/instruct           → send NL instruction
GET  /plan/{plan_id}/export             → export current plan to DXF
GET  /plan/{plan_id}/state              → inspect current plan state
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from engine.plan_manager import PlanManager, PlanManagerError, UnknownRoomError
from engine.intent_parser.parser import IntentParseError, SchemaValidationError
from engine.constraint_solver.solver import ConstraintUnsatisfiableError
from engine.cad_generator.generator import CADGenerationError
from engine.exporter.exporter import ExportError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plan", tags=["plan"])

# In-memory session store (Phase 3 replaces with PostgreSQL)
_PLANS: dict[str, PlanManager] = {}


# ── Request / Response models ─────────────────────────────────────────────────

class NewPlanRequest(BaseModel):
    plan_id: Optional[str] = None
    project_name: Optional[str] = None


class NewPlanResponse(BaseModel):
    plan_id: str
    message: str


class InstructRequest(BaseModel):
    instruction: str


class InstructResponse(BaseModel):
    plan_id: str
    op_type: str
    room_count: int
    version: int
    message: str


class PlanStateResponse(BaseModel):
    plan_id: str
    version: int
    room_count: int
    rooms: dict  # room_id → {name, room_type, area_sqft}
    connections: list


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/new", response_model=NewPlanResponse)
async def new_plan(request: NewPlanRequest = NewPlanRequest()):
    """Create a new plan session. Returns a plan_id for subsequent calls."""
    manager = PlanManager(plan_id=request.plan_id)
    _PLANS[manager.plan_id] = manager
    logger.info("Created plan: %s", manager.plan_id)
    return NewPlanResponse(
        plan_id=manager.plan_id,
        message=f"Plan '{manager.plan_id}' created. Send instructions to /plan/{manager.plan_id}/instruct",
    )


@router.post("/{plan_id}/instruct", response_model=InstructResponse)
async def instruct(plan_id: str, request: InstructRequest):
    """
    Send a natural-language instruction to the plan.
    The instruction is parsed by Claude, validated, and applied to the state.
    """
    manager = _get_plan(plan_id)

    try:
        op = manager.instruct(request.instruction)
    except SchemaValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Claude returned invalid schema: {exc}. Raw: {exc.raw_response[:200]}",
        )
    except IntentParseError as exc:
        raise HTTPException(status_code=502, detail=f"Intent parse failed: {exc}")
    except UnknownRoomError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PlanManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return InstructResponse(
        plan_id=plan_id,
        op_type=op.op_type,
        room_count=manager.room_count,
        version=manager.state.version,
        message=f"Applied '{op.op_type}'. Plan now has {manager.room_count} room(s).",
    )


@router.get("/{plan_id}/export")
async def export_plan(plan_id: str, mode: str = "2d"):
    """
    Export the current plan.

    Query params:
      mode=2d (default) — fast DXF from coordinate matrix (no FreeCAD)
      mode=3d           — full FreeCAD 3D model then DXF (5–15s)

    Returns the DXF file as a download.
    """
    manager = _get_plan(plan_id)

    if manager.room_count == 0:
        raise HTTPException(status_code=400, detail="Plan has no rooms. Add rooms first.")

    try:
        if mode == "3d":
            dxf_path = manager.export_dxf_with_3d()
        else:
            dxf_path = manager.export_dxf()
    except ConstraintUnsatisfiableError as exc:
        raise HTTPException(status_code=422, detail=f"Layout solver failed: {exc}")
    except CADGenerationError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"CAD generation failed: {exc}. stderr: {exc.stderr[:200]}",
        )
    except ExportError as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}")

    return FileResponse(
        path=str(dxf_path),
        media_type="application/dxf",
        filename=f"{plan_id}.dxf",
    )


@router.get("/{plan_id}/state", response_model=PlanStateResponse)
async def get_state(plan_id: str):
    """Return the current plan state (room list, version, connections)."""
    manager = _get_plan(plan_id)
    state = manager.state
    return PlanStateResponse(
        plan_id=plan_id,
        version=state.version,
        room_count=manager.room_count,
        rooms={
            room_id: {
                "name": spec.name,
                "room_type": spec.room_type,
                "area_sqft": spec.area_sqft,
            }
            for room_id, spec in state.rooms.items()
        },
        connections=[
            {
                "room_a": c.room_a_id,
                "room_b": c.room_b_id,
                "type": c.connection_type,
            }
            for c in state.connections
        ],
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_plan(plan_id: str) -> PlanManager:
    manager = _PLANS.get(plan_id)
    if not manager:
        raise HTTPException(
            status_code=404,
            detail=f"Plan '{plan_id}' not found. Create one with POST /plan/new",
        )
    return manager
