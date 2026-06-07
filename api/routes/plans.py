"""
Floor plan routes.

POST /plans                    — create new floor plan
POST /plans/{plan_id}/generate — NL → constraint solve → coordinate matrix
GET  /plans/{plan_id}/status/{task_id} — poll Celery task
GET  /plans/{plan_id}/export/{format}  — download DXF/PDF (stub)
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from engine.intent_parser.parser import (
    APIError,
    IntentParseError,
    IntentParser,
    SchemaValidationError,
)
from engine.intent_parser.schemas import FloorPlanOp, FloorPlanState, RoomSpec
from engine.constraint_solver.solver import ConstraintSolver, ConstraintUnsatisfiableError

logger = logging.getLogger(__name__)
router = APIRouter()

# ── In-memory store (replace with Postgres in production) ────────────────────
_plans: dict[str, FloorPlanState] = {}
_task_results: dict[str, dict] = {}


# ── Request / Response models ─────────────────────────────────────────────────

class CreatePlanRequest(BaseModel):
    project_name: Optional[str] = Field(None, description="Optional human-readable project name")
    initial_rooms: Optional[dict[str, dict]] = Field(
        None,
        description=(
            "Optional initial room seed. Keys are room_id slugs, "
            "values are RoomSpec dicts."
        ),
    )


class CreatePlanResponse(BaseModel):
    plan_id: str
    project_name: Optional[str]
    message: str


class GenerateRequest(BaseModel):
    nl_input: str = Field(..., min_length=1, description="Natural-language floor plan instruction")
    run_async: bool = Field(
        False,
        description="If true, dispatch to Celery and return task_id. If false, run synchronously.",
    )


class GenerateResponse(BaseModel):
    plan_id: str
    task_id: Optional[str] = None
    op: Optional[dict] = None
    coordinate_matrix: Optional[dict] = None
    status: Literal["queued", "complete", "error"]
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    plan_id: str
    status: Literal["pending", "running", "complete", "error"]
    result: Optional[dict] = None
    error: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=CreatePlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(body: CreatePlanRequest) -> CreatePlanResponse:
    """Create a new (empty) floor plan and return its ID."""
    plan_id = str(uuid.uuid4())[:8]

    rooms: dict[str, RoomSpec] = {}
    if body.initial_rooms:
        for room_id, room_dict in body.initial_rooms.items():
            try:
                rooms[room_id] = RoomSpec.model_validate(room_dict)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid room spec for '{room_id}': {exc}",
                )

    plan = FloorPlanState(plan_id=plan_id, rooms=rooms)
    _plans[plan_id] = plan

    logger.info("Created plan %s with %d initial rooms", plan_id, len(rooms))
    return CreatePlanResponse(
        plan_id=plan_id,
        project_name=body.project_name,
        message=f"Floor plan {plan_id} created.",
    )


@router.post("/{plan_id}/generate", response_model=GenerateResponse)
async def generate(plan_id: str, body: GenerateRequest) -> GenerateResponse:
    """
    Run the NL→coordinate matrix pipeline:
      NL Input → Intent Parser (Claude) → FloorPlanOp
      → Apply op to FloorPlanState
      → Constraint Solver (kiwisolver) → coordinate matrix

    CAD generation and export are stubs (pending Cursor Composer implementation).
    """
    plan = _plans.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found.")

    if body.run_async:
        # Celery async path
        try:
            from api.tasks.pipeline import run_pipeline_task
            task = run_pipeline_task.delay(plan_id, body.nl_input)
            _task_results[task.id] = {"status": "pending"}
            return GenerateResponse(
                plan_id=plan_id,
                task_id=task.id,
                status="queued",
                message="Pipeline queued. Poll /status/{task_id} for results.",
            )
        except Exception as exc:
            logger.warning("Celery not available, falling back to sync: %s", exc)
            # Fall through to sync execution

    # Synchronous path (default — easier to test without Redis/Celery)
    try:
        parser = IntentParser()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    # ── Step 1: Parse intent ──────────────────────────────────────────────────
    try:
        op: FloorPlanOp = parser.parse(body.nl_input, plan)
    except SchemaValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Intent parser returned invalid schema: {exc}. Raw: {exc.raw_response[:200]}",
        )
    except APIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Claude API error: {exc}",
        )
    except IntentParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # ── Step 2: Apply op to plan state ────────────────────────────────────────
    _apply_op(plan, op)

    # ── Step 3: Constraint solve ──────────────────────────────────────────────
    solver = ConstraintSolver()
    try:
        matrix = solver.solve(plan)
    except ConstraintUnsatisfiableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Constraint solver failed: {exc}",
        )

    # Update plan with resolved coordinates
    plan.coordinate_matrix = matrix
    plan.version += 1
    _plans[plan_id] = plan

    logger.info(
        "Plan %s: op=%s, %d rooms, matrix solved",
        plan_id, op.op_type, len(plan.rooms),
    )
    return GenerateResponse(
        plan_id=plan_id,
        op=op.model_dump(),
        coordinate_matrix=matrix,
        status="complete",
        message=f"Op '{op.op_type}' applied. Coordinate matrix resolved for {len(matrix)} rooms.",
    )


@router.get("/{plan_id}/status/{task_id}", response_model=TaskStatusResponse)
async def task_status(plan_id: str, task_id: str) -> TaskStatusResponse:
    """Poll the status of an async pipeline task."""
    plan = _plans.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found.")

    # Try Celery first
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)
        celery_status = result.status.lower()
        return TaskStatusResponse(
            task_id=task_id,
            plan_id=plan_id,
            status=_map_celery_status(celery_status),
            result=result.result if result.ready() and not result.failed() else None,
            error=str(result.result) if result.failed() else None,
        )
    except Exception:
        pass

    # Fall back to in-memory store
    stored = _task_results.get(task_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return TaskStatusResponse(
        task_id=task_id,
        plan_id=plan_id,
        status=stored.get("status", "pending"),
        result=stored.get("result"),
        error=stored.get("error"),
    )


@router.get("/{plan_id}/export/{fmt}")
async def export_plan(plan_id: str, fmt: Literal["dxf", "pdf"]) -> dict:
    """
    Download the floor plan as DXF or PDF.
    Stub — returns 501 until Cursor Composer implements PlanExporter.
    """
    plan = _plans.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found.")

    if plan.coordinate_matrix is None:
        raise HTTPException(
            status_code=409,
            detail="Plan has no coordinate matrix yet. Run /generate first.",
        )

    raise HTTPException(
        status_code=501,
        detail=(
            f"Export to {fmt.upper()} is pending implementation. "
            "See DRAFT_EXPORTER.md for the Cursor Composer spec."
        ),
    )


# ── Op application logic ──────────────────────────────────────────────────────

def _apply_op(plan: FloorPlanState, op: FloorPlanOp) -> None:
    """Apply a FloorPlanOp to the plan state in-place."""
    if op.op_type == "add_room":
        room_id = _slugify(op.room_spec.name)
        plan.rooms[room_id] = op.room_spec
        logger.debug("Applied add_room: %s", room_id)

    elif op.op_type == "remove_room":
        plan.rooms.pop(op.target_room_id, None)
        # Clean up constraints and connections referencing this room
        plan.constraints = [c for c in plan.constraints if c.room_id != op.target_room_id]
        plan.connections = [
            c for c in plan.connections
            if c.room_a_id != op.target_room_id and c.room_b_id != op.target_room_id
        ]
        logger.debug("Applied remove_room: %s", op.target_room_id)

    elif op.op_type == "resize_room":
        if op.target_room_id in plan.rooms and op.room_spec:
            # Merge new spec into existing
            existing = plan.rooms[op.target_room_id]
            merged = existing.model_copy(
                update=op.room_spec.model_dump(exclude_none=True)
            )
            plan.rooms[op.target_room_id] = merged
        elif op.target_room_id in plan.rooms and op.constraint_spec:
            plan.constraints.append(op.constraint_spec)
        logger.debug("Applied resize_room: %s", op.target_room_id)

    elif op.op_type == "add_connection":
        plan.connections.append(op.connection_spec)
        logger.debug("Applied add_connection: %s↔%s", op.connection_spec.room_a_id, op.connection_spec.room_b_id)

    elif op.op_type == "set_constraint":
        # Remove existing constraint of same type+room, add new one
        plan.constraints = [
            c for c in plan.constraints
            if not (c.room_id == op.constraint_spec.room_id
                    and c.constraint_type == op.constraint_spec.constraint_type)
        ]
        plan.constraints.append(op.constraint_spec)
        logger.debug("Applied set_constraint: %s on %s", op.constraint_spec.constraint_type, op.constraint_spec.room_id)

    elif op.op_type == "move_room":
        # move_room is handled by the constraint solver; nothing to do here
        logger.debug("move_room noted: %s (solver will handle)", op.target_room_id)


def _slugify(name: str) -> str:
    """Convert 'Master Bedroom' → 'master_bedroom'."""
    return name.lower().strip().replace(" ", "_").replace("-", "_")


def _map_celery_status(celery_status: str) -> Literal["pending", "running", "complete", "error"]:
    mapping = {
        "pending": "pending",
        "started": "running",
        "retry": "running",
        "success": "complete",
        "failure": "error",
        "revoked": "error",
    }
    return mapping.get(celery_status, "pending")
