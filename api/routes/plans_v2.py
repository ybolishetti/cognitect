"""
Floor plan routes — v2 (persistent, multi-tenant).

Backed by Supabase (api/storage/plan_store.py) instead of the in-memory
`_PLANS` dict used by api/routes/plan.py, which stays untouched for backward
compat. Supports anonymous plan creation (X-Device-Id header) and claiming
anonymous plans after sign-in.

POST   /v2/plans                    — create a new plan
GET    /v2/plans                    — list the authenticated user's plans
POST   /v2/plans/claim              — reassign anonymous plans to the caller
GET    /v2/plans/{plan_id}          — load plan state
POST   /v2/plans/{plan_id}/instruct — NL instruction -> constraint solve -> persist
GET    /v2/plans/{plan_id}/preview  — render PNG
GET    /v2/plans/{plan_id}/export   — download DXF/PDF
PATCH  /v2/plans/{plan_id}          — rename
DELETE /v2/plans/{plan_id}          — soft-delete (archive)
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from api.auth import AuthedUser, optional_user, require_user, validate_device_id
from api.storage import plan_store
from api.storage.plan_store import PlanAccessDeniedError, PlanNotFoundError
from engine.cad_generator.generator import CADGenerationError
from engine.constraint_solver.solver import ConstraintUnsatisfiableError
from engine.exporter.exporter import ExportError
from engine.intent_parser.parser import IntentParseError, IntentParser, SchemaValidationError
from engine.plan_manager import PlanManagerError, UnknownRoomError
from engine.previewer import PlanPreviewer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/plans", tags=["plans_v2"])

_previewer = PlanPreviewer()

_ANON_RATE_LIMIT_PER_HOUR = int(os.environ.get("ANON_RATE_LIMIT_PER_HOUR", "1"))
_USER_RATE_LIMIT_PER_DAY = int(os.environ.get("USER_RATE_LIMIT_PER_DAY", "20"))


# ── Request / response models ─────────────────────────────────────────────────

class CreatePlanRequest(BaseModel):
    name: str = "Untitled Plan"


class CreatePlanResponse(BaseModel):
    plan_id: str
    name: str
    message: str


class InstructRequest(BaseModel):
    instruction: str = Field(..., min_length=1)


class InstructResponse(BaseModel):
    plan_id: str
    ops_applied: int
    op_types: list[str]
    room_count: int
    version: int
    coordinate_matrix: dict
    message: str


class PlanStateResponse(BaseModel):
    plan_id: str
    name: str
    version: int
    room_count: int
    rooms: dict
    connections: list


class RenamePlanRequest(BaseModel):
    name: str = Field(..., min_length=1)


class ClaimRequest(BaseModel):
    device_id: str


class ClaimResponse(BaseModel):
    claimed_count: int
    plan_ids: list[str]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _require_owner(user: Optional[AuthedUser], device_id: Optional[str]) -> None:
    if not user and not device_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide an Authorization bearer token or an X-Device-Id header",
        )


def _load_owned(
    plan_id: str, user: Optional[AuthedUser], device_id: Optional[str]
) -> tuple:
    try:
        return plan_store.load_plan(
            plan_id, user_id=user.id if user else None, device_id=device_id
        )
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except PlanAccessDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))


def _check_rate_limit(user: Optional[AuthedUser], device_id: Optional[str]) -> None:
    """
    Throttles the only endpoint that actually calls Claude (/instruct):
    1/hour for anonymous devices, 20/day for authenticated users. Counted via
    llm_call_log rows logged by that same endpoint.
    """
    if user:
        window, limit = timedelta(days=1), _USER_RATE_LIMIT_PER_DAY
    else:
        window, limit = timedelta(hours=1), _ANON_RATE_LIMIT_PER_HOUR
    since_iso = (datetime.now(timezone.utc) - window).isoformat()
    count = plan_store.count_llm_calls_since(
        user_id=user.id if user else None, device_id=device_id, since_iso=since_iso
    )
    if count >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit exceeded: {limit} instruction(s) per "
            f"{'day' if user else 'hour'}. Try again later.",
            headers={"Retry-After": str(int(window.total_seconds()))},
        )


def _room_metadata(state) -> dict:
    return {
        room_id: {"name": spec.name, "room_type": spec.room_type}
        for room_id, spec in state.rooms.items()
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=CreatePlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    body: CreatePlanRequest, owner: tuple = Depends(optional_user)
) -> CreatePlanResponse:
    user, device_id = owner
    _require_owner(user, device_id)
    plan_id, _ = plan_store.create_plan(
        user_id=user.id if user else None, device_id=device_id, name=body.name
    )
    logger.info("Created plan %s", plan_id)
    return CreatePlanResponse(
        plan_id=plan_id, name=body.name, message=f"Plan {plan_id} created."
    )


@router.get("")
async def list_plans(user: AuthedUser = Depends(require_user)) -> list[dict]:
    return plan_store.list_plans(user_id=user.id)


@router.post("/claim", response_model=ClaimResponse)
async def claim(
    body: ClaimRequest, user: AuthedUser = Depends(require_user)
) -> ClaimResponse:
    device_id = validate_device_id(body.device_id)
    plan_ids = plan_store.claim_anonymous_plans(device_id, user.id)
    logger.info("User %s claimed %d plan(s)", user.id, len(plan_ids))
    return ClaimResponse(claimed_count=len(plan_ids), plan_ids=plan_ids)


@router.get("/{plan_id}", response_model=PlanStateResponse)
async def get_plan(plan_id: str, owner: tuple = Depends(optional_user)) -> PlanStateResponse:
    user, device_id = owner
    _require_owner(user, device_id)
    manager, name = _load_owned(plan_id, user, device_id)
    state = manager.state
    return PlanStateResponse(
        plan_id=plan_id,
        name=name,
        version=state.version,
        room_count=manager.room_count,
        rooms={
            room_id: {"name": s.name, "room_type": s.room_type, "area_sqft": s.area_sqft}
            for room_id, s in state.rooms.items()
        },
        connections=[
            {"room_a": c.room_a_id, "room_b": c.room_b_id, "type": c.connection_type}
            for c in state.connections
        ],
    )


@router.post("/{plan_id}/instruct", response_model=InstructResponse)
async def instruct(
    plan_id: str, body: InstructRequest, owner: tuple = Depends(optional_user)
) -> InstructResponse:
    user, device_id = owner
    _require_owner(user, device_id)
    _check_rate_limit(user, device_id)
    manager, _name = _load_owned(plan_id, user, device_id)
    log_kwargs = dict(
        user_id=user.id if user else None, device_id=device_id, plan_id=plan_id,
        model=IntentParser.MODEL,
    )

    try:
        ops = manager.instruct(body.instruction)
    except SchemaValidationError as exc:
        plan_store.log_llm_call(**log_kwargs, status="error", error_message=str(exc))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Claude returned invalid schema: {exc}. Raw: {exc.raw_response[:200]}",
        )
    except IntentParseError as exc:
        plan_store.log_llm_call(**log_kwargs, status="error", error_message=str(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Intent parse failed: {exc}")
    except UnknownRoomError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except PlanManagerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    try:
        matrix = manager.solve()
    except ConstraintUnsatisfiableError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Constraint solver failed after applying op(s): {exc}",
        )

    plan_store.log_llm_call(**log_kwargs, status="ok")
    plan_store.save_plan(manager, instruction=body.instruction)

    op_types = [op.op_type for op in ops]
    return InstructResponse(
        plan_id=plan_id,
        ops_applied=len(ops),
        op_types=op_types,
        room_count=manager.room_count,
        version=manager.state.version,
        coordinate_matrix=matrix,
        message=f"Applied {len(ops)} op(s): {', '.join(op_types)}.",
    )


@router.get("/{plan_id}/preview")
async def preview_plan(
    plan_id: str, width: int = 900, height: int = 700, owner: tuple = Depends(optional_user)
):
    user, device_id = owner
    _require_owner(user, device_id)
    manager, _name = _load_owned(plan_id, user, device_id)
    state = manager.state

    coordinate_matrix = state.coordinate_matrix or {}
    if not coordinate_matrix and state.rooms:
        try:
            coordinate_matrix = manager.solve()
        except Exception as exc:
            logger.warning("Solver failed during preview: %s", exc)
            coordinate_matrix = {}

    png_bytes = _previewer.render(
        coordinate_matrix=coordinate_matrix,
        room_metadata=_room_metadata(state),
        width_px=width,
        height_px=height,
        title=f"Plan {plan_id}",
    )
    return Response(content=png_bytes, media_type="image/png")


@router.get("/{plan_id}/export")
async def export_plan(
    plan_id: str, mode: str = "2d", format: str = "dxf", owner: tuple = Depends(optional_user)
):
    user, device_id = owner
    _require_owner(user, device_id)
    manager, _name = _load_owned(plan_id, user, device_id)

    if manager.room_count == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Plan has no rooms. Add rooms first.")

    fmt = format.lower()
    if fmt not in ("dxf", "pdf"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported format '{format}'.")

    try:
        if fmt == "pdf":
            matrix = manager.state.coordinate_matrix or manager.solve()
            pdf_bytes = _previewer.render(
                coordinate_matrix=matrix,
                room_metadata=_room_metadata(manager.state),
                title=f"Cognitect Plan {plan_id}",
                fmt="pdf",
            )
            output_dir = Path(tempfile.gettempdir()) / "cognitect_output"
            output_dir.mkdir(exist_ok=True)
            pdf_path = output_dir / f"{plan_id}.pdf"
            pdf_path.write_bytes(pdf_bytes)
            return FileResponse(
                path=str(pdf_path), media_type="application/pdf", filename=f"{plan_id}.pdf"
            )

        dxf_path = manager.export_dxf_with_3d() if mode == "3d" else manager.export_dxf()
    except ConstraintUnsatisfiableError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Layout solver failed: {exc}")
    except CADGenerationError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"CAD generation failed: {exc}"
        )
    except ExportError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Export failed: {exc}")

    return FileResponse(path=str(dxf_path), media_type="application/dxf", filename=f"{plan_id}.dxf")


@router.patch("/{plan_id}")
async def rename_plan(
    plan_id: str, body: RenamePlanRequest, user: AuthedUser = Depends(require_user)
) -> dict:
    try:
        plan_store.rename_plan(plan_id, body.name, user_id=user.id)
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except PlanAccessDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    return {"plan_id": plan_id, "name": body.name}


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_plan(plan_id: str, user: AuthedUser = Depends(require_user)) -> None:
    try:
        plan_store.delete_plan(plan_id, user_id=user.id)
    except PlanNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except PlanAccessDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
