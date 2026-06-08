# DRAFT: Cognitect Phase 2 — NL → Intent → Constraint → CAD End-to-End

> **For:** Cursor Composer  
> **Repo:** `/data/workspace/cognitect` (ybolishetti/cognitect on GitHub)  
> **Branch:** Create and work on `cursor/phase2-nl-e2e`  
> **Phase 2 goal:** Wire the Claude intent parser into the full pipeline so a user can type natural language and get a DXF file out the other end — no manual JSON construction.  
> **Do NOT touch:** `engine/intent_parser/parser.py`, `engine/intent_parser/schemas.py`, `engine/constraint_solver/`, `engine/cad_generator/`, `engine/exporter/`  
> **Tests must pass:** `pytest -m "not slow"` — all existing tests must stay green

---

## What Phase 2 Builds

Four things:

1. **`engine/plan_manager.py`** — `PlanManager` class: stateful session controller that holds `FloorPlanState`, applies `FloorPlanOp`s, and orchestrates the full NL → DXF pipeline
2. **`engine/api/routes/plan.py`** — FastAPI router: `/plan/new`, `/plan/{plan_id}/instruct`, `/plan/{plan_id}/export`
3. **`tests/test_phase2_e2e.py`** — E2E tests for the full NL → DXF pipeline (mocked Claude, real solver + exporter)
4. **`tests/test_plan_manager.py`** — Unit tests for `PlanManager` op application logic

---

## Architecture Reminder

```
NL Input
   ↓
IntentParser.parse(nl, state) → FloorPlanOp          ← Claude API (already implemented)
   ↓
PlanManager.apply_op(op) → FloorPlanState             ← YOU BUILD THIS
   ↓
ConstraintSolver.solve(state) → coordinate_matrix     ← already implemented
   ↓
CADGenerator.generate(matrix, state) → .FCStd         ← already implemented
   ↓
PlanExporter.export_dxf(fcstd, metadata) → .dxf       ← already implemented
```

**Key rule (from spec):** LLM never touches geometry. `PlanManager` is the firewall — it applies ops to state, then hands off to solver. Never pass coordinate matrices back to Claude.

---

## Task 1: `engine/plan_manager.py`

Create this new file. `PlanManager` is a stateful object — one instance per user session.

```python
"""
PlanManager — stateful session controller.

Holds FloorPlanState, applies FloorPlanOps, and orchestrates the full
NL → FloorPlanOp → FloorPlanState → coordinate_matrix → CAD → DXF pipeline.

Architecture rule: PlanManager is the ONLY place that calls IntentParser.
The solver and CAD generator never touch the LLM.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from .intent_parser.parser import IntentParser, IntentParseError
from .intent_parser.schemas import (
    FloorPlanOp, FloorPlanState, RoomSpec, ConnectionSpec, ConstraintSpec
)
from .constraint_solver.solver import ConstraintSolver, ConstraintUnsatisfiableError
from .cad_generator.generator import CADGenerator, CADGenerationError
from .exporter.exporter import PlanExporter, ExportError

logger = logging.getLogger(__name__)


class PlanManagerError(Exception):
    """Base error for PlanManager operations."""


class UnknownRoomError(PlanManagerError):
    """Op references a room_id that doesn't exist in the current state."""
    def __init__(self, room_id: str):
        super().__init__(f"Room '{room_id}' not found in current plan state")
        self.room_id = room_id


class PlanManager:
    """
    Stateful session controller for a single floor plan.

    Lifecycle:
      manager = PlanManager()           # new blank plan
      manager.instruct("Add a living room of 300 sqft")
      manager.instruct("Add a kitchen next to it, 150 sqft")
      dxf_path = manager.export_dxf()
    """

    def __init__(
        self,
        plan_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """
        Args:
            plan_id: Optional ID. If None, generates a UUID4.
            api_key: Claude API key. Falls back to COGNITECT_CLAUDE_API_KEY env var.
        """
        self.plan_id = plan_id or str(uuid.uuid4())[:8]
        self._parser = IntentParser(api_key=api_key)
        self._solver = ConstraintSolver()
        self._cad = CADGenerator()
        self._exporter = PlanExporter()
        self._state = FloorPlanState(plan_id=self.plan_id)
        self._history: list[FloorPlanOp] = []

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def state(self) -> FloorPlanState:
        """Read-only access to current state."""
        return self._state

    @property
    def room_count(self) -> int:
        return len(self._state.rooms)

    def instruct(self, nl_input: str) -> FloorPlanOp:
        """
        Parse a natural-language instruction and apply it to the plan.

        Args:
            nl_input: e.g. "Add a master bedroom of 200 sqft adjacent to the bathroom"

        Returns:
            The FloorPlanOp that was applied.

        Raises:
            IntentParseError: If Claude fails to parse the instruction.
            PlanManagerError: If the op can't be applied to the current state.
        """
        op = self._parser.parse(nl_input, self._state)
        self._apply_op(op)
        self._history.append(op)
        logger.info(
            "Applied op: %s | rooms=%d | v=%d",
            op.op_type, len(self._state.rooms), self._state.version
        )
        return op

    def apply_op(self, op: FloorPlanOp) -> None:
        """
        Apply a pre-parsed FloorPlanOp directly (bypasses Claude).
        Useful for testing and replay.
        """
        self._apply_op(op)
        self._history.append(op)

    def solve(self) -> dict:
        """
        Run the constraint solver on the current state.

        Returns:
            coordinate_matrix: {room_id: {x, y, width, height}} in feet.

        Raises:
            ConstraintUnsatisfiableError: If layout can't be resolved.
            PlanManagerError: If plan has no rooms.
        """
        if not self._state.rooms:
            raise PlanManagerError("Cannot solve: plan has no rooms")
        matrix = self._solver.solve(self._state)
        self._state = self._state.model_copy(
            update={"coordinate_matrix": matrix}
        )
        return matrix

    def export_dxf(self, project_name: Optional[str] = None) -> Path:
        """
        Run the full pipeline: solve → CAD → DXF export.

        Args:
            project_name: Optional label for the DXF title block.

        Returns:
            Path to the generated .dxf file.

        Raises:
            PlanManagerError: If plan has no rooms.
            ConstraintUnsatisfiableError: If solver fails.
            CADGenerationError: If FreeCAD subprocess fails.
            ExportError: If DXF export fails.
        """
        if not self._state.rooms:
            raise PlanManagerError("Cannot export: plan has no rooms")

        # 1. Solve constraints
        matrix = self.solve()

        # 2. Build metadata for exporter
        metadata = {
            "plan_id": self.plan_id,
            "project_name": project_name or f"Cognitect Plan {self.plan_id}",
            "coordinate_matrix": matrix,
            "rooms": {
                room_id: {"name": spec.name, "room_type": spec.room_type}
                for room_id, spec in self._state.rooms.items()
            },
        }

        # 3. Export directly from matrix (no FreeCAD dependency for 2D DXF)
        return self._exporter.export_from_matrix(matrix, metadata)

    def export_dxf_with_3d(self, project_name: Optional[str] = None) -> Path:
        """
        Full pipeline including FreeCAD 3D model generation.
        Slower (5–15s). Use export_dxf() for fast 2D-only mode.
        """
        if not self._state.rooms:
            raise PlanManagerError("Cannot export: plan has no rooms")

        matrix = self.solve()

        # Generate .FCStd first
        fcstd_path = self._cad.generate(matrix, self._state)

        metadata = {
            "plan_id": self.plan_id,
            "project_name": project_name or f"Cognitect Plan {self.plan_id}",
            "coordinate_matrix": matrix,
            "rooms": {
                room_id: {"name": spec.name, "room_type": spec.room_type}
                for room_id, spec in self._state.rooms.items()
            },
        }

        return self._exporter.export_dxf(fcstd_path, metadata)

    def history(self) -> list[FloorPlanOp]:
        """Return the list of all ops applied in this session."""
        return list(self._history)

    def reset(self) -> None:
        """Clear all rooms and start fresh (keep plan_id)."""
        self._state = FloorPlanState(plan_id=self.plan_id)
        self._history.clear()
        logger.info("Plan %s reset", self.plan_id)

    # ── Op application ────────────────────────────────────────────────────────

    def _apply_op(self, op: FloorPlanOp) -> None:
        """
        Apply a FloorPlanOp to self._state. Mutates _state in place.
        Raises PlanManagerError for invalid ops (unknown room IDs, etc.).
        """
        rooms = dict(self._state.rooms)
        constraints = list(self._state.constraints)
        connections = list(self._state.connections)

        if op.op_type == "add_room":
            room_id = self._slugify(op.room_spec.name)
            # Handle duplicate IDs by appending a counter
            base = room_id
            i = 2
            while room_id in rooms:
                room_id = f"{base}_{i}"
                i += 1
            rooms[room_id] = op.room_spec

        elif op.op_type == "remove_room":
            self._assert_room_exists(op.target_room_id, rooms)
            rooms.pop(op.target_room_id)
            # Remove any constraints/connections referencing this room
            constraints = [c for c in constraints if c.room_id != op.target_room_id]
            connections = [
                c for c in connections
                if c.room_a_id != op.target_room_id and c.room_b_id != op.target_room_id
            ]

        elif op.op_type == "resize_room":
            self._assert_room_exists(op.target_room_id, rooms)
            existing = rooms[op.target_room_id]
            if op.room_spec:
                # Merge: only update fields that are explicitly set in the new spec
                updated = existing.model_copy(update={
                    k: v for k, v in op.room_spec.model_dump(exclude_none=True).items()
                    if k not in ("name", "room_type")  # preserve identity fields
                })
                rooms[op.target_room_id] = updated
            elif op.constraint_spec:
                constraints.append(op.constraint_spec)

        elif op.op_type == "move_room":
            # move_room is a semantic op — in our 2D solver, position is
            # determined by constraints, not direct placement.
            # Encode as an adjacency/orientation constraint if metadata hints exist.
            self._assert_room_exists(op.target_room_id, rooms)
            logger.info(
                "move_room received for %s — no direct coordinate override; "
                "use set_constraint with orientation for positioning",
                op.target_room_id,
            )

        elif op.op_type == "add_connection":
            conn = op.connection_spec
            self._assert_room_exists(conn.room_a_id, rooms)
            self._assert_room_exists(conn.room_b_id, rooms)
            # Avoid duplicate connections
            existing_pair = {(c.room_a_id, c.room_b_id) for c in connections}
            if (conn.room_a_id, conn.room_b_id) not in existing_pair and \
               (conn.room_b_id, conn.room_a_id) not in existing_pair:
                connections.append(conn)

        elif op.op_type == "set_constraint":
            cs = op.constraint_spec
            self._assert_room_exists(cs.room_id, rooms)
            # Replace any existing constraint of the same type on the same room
            constraints = [
                c for c in constraints
                if not (c.room_id == cs.room_id and c.constraint_type == cs.constraint_type)
            ]
            constraints.append(cs)

        # Rebuild state (Pydantic models are immutable — use model_copy)
        self._state = self._state.model_copy(update={
            "rooms": rooms,
            "constraints": constraints,
            "connections": connections,
            "version": self._state.version + 1,
            "coordinate_matrix": None,  # invalidate on any mutation
        })

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _slugify(name: str) -> str:
        """Convert 'Master Bedroom' → 'master_bedroom'."""
        return name.lower().strip().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _assert_room_exists(room_id: str, rooms: dict) -> None:
        if room_id not in rooms:
            raise UnknownRoomError(room_id)
```

---

## Task 2: `engine/api/routes/plan.py`

Create this new file. Three endpoints that expose `PlanManager` over HTTP.

**Important:** Use an in-memory store (`_PLANS: dict[str, PlanManager]`) for now — no DB yet (Phase 3 adds PostgreSQL). This is intentionally simple.

```python
"""
Plan API routes — Phase 2.

POST /plan/new                          → create a new plan session
POST /plan/{plan_id}/instruct           → send NL instruction
GET  /plan/{plan_id}/export             → export current plan to DXF
GET  /plan/{plan_id}/state              → inspect current plan state
"""
from __future__ import annotations

import logging
from pathlib import Path
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
```

Also update `engine/api/main.py` to register this new router. Find the existing `app = FastAPI(...)` and add:

```python
from engine.api.routes.plan import router as plan_router
app.include_router(plan_router)
```

---

## Task 3: `tests/test_plan_manager.py`

Create this new test file. All tests are **fast** — no Claude, no FreeCAD. Claude is mocked.

```python
"""
Unit tests for PlanManager op application logic.

All tests mock the IntentParser (no Claude API calls).
Fast: no FreeCAD, no external services.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from engine.plan_manager import PlanManager, PlanManagerError, UnknownRoomError
from engine.intent_parser.schemas import (
    FloorPlanOp, RoomSpec, ConstraintSpec, ConnectionSpec
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_manager() -> PlanManager:
    """Create a PlanManager with a mocked IntentParser."""
    with patch("engine.plan_manager.IntentParser"):
        manager = PlanManager(api_key="test-key")
    return manager


def make_add_room_op(name: str, room_type: str, area: float) -> FloorPlanOp:
    return FloorPlanOp(
        op_type="add_room",
        room_spec=RoomSpec(name=name, room_type=room_type, area_sqft=area),
    )


def make_remove_room_op(room_id: str) -> FloorPlanOp:
    return FloorPlanOp(op_type="remove_room", target_room_id=room_id)


def make_connection_op(room_a: str, room_b: str) -> FloorPlanOp:
    return FloorPlanOp(
        op_type="add_connection",
        connection_spec=ConnectionSpec(
            room_a_id=room_a, room_b_id=room_b, connection_type="door"
        ),
    )


def make_constraint_op(room_id: str, constraint_type: str, value: float) -> FloorPlanOp:
    return FloorPlanOp(
        op_type="set_constraint",
        constraint_spec=ConstraintSpec(
            constraint_type=constraint_type, room_id=room_id, value=value
        ),
    )


# ── Tests: add_room ───────────────────────────────────────────────────────────

class TestAddRoom:
    def test_add_single_room(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        assert "living_room" in m.state.rooms
        assert m.room_count == 1

    def test_add_multiple_rooms(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        m.apply_op(make_add_room_op("Master Bedroom", "bedroom", 200.0))
        assert m.room_count == 3

    def test_duplicate_room_name_gets_suffix(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Bedroom", "bedroom", 150.0))
        m.apply_op(make_add_room_op("Bedroom", "bedroom", 120.0))
        assert "bedroom" in m.state.rooms
        assert "bedroom_2" in m.state.rooms

    def test_version_increments(self):
        m = make_manager()
        assert m.state.version == 0
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        assert m.state.version == 1
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        assert m.state.version == 2

    def test_coordinate_matrix_invalidated_on_add(self):
        """Adding a room should clear any cached coordinate matrix."""
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        _ = m.solve()
        assert m.state.coordinate_matrix is not None
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        assert m.state.coordinate_matrix is None


# ── Tests: remove_room ────────────────────────────────────────────────────────

class TestRemoveRoom:
    def test_remove_existing_room(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_remove_room_op("living_room"))
        assert m.room_count == 0

    def test_remove_nonexistent_room_raises(self):
        m = make_manager()
        with pytest.raises(UnknownRoomError):
            m.apply_op(make_remove_room_op("nonexistent_room"))

    def test_remove_also_removes_connections(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        m.apply_op(make_connection_op("living_room", "kitchen"))
        assert len(m.state.connections) == 1
        m.apply_op(make_remove_room_op("kitchen"))
        assert len(m.state.connections) == 0

    def test_remove_also_removes_constraints(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_constraint_op("living_room", "min_area", 250.0))
        assert len(m.state.constraints) == 1
        m.apply_op(make_remove_room_op("living_room"))
        assert len(m.state.constraints) == 0


# ── Tests: add_connection ────────────────────────────────────────────────────

class TestAddConnection:
    def test_add_door_between_rooms(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        m.apply_op(make_connection_op("living_room", "kitchen"))
        assert len(m.state.connections) == 1
        assert m.state.connections[0].connection_type == "door"

    def test_duplicate_connection_not_added(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        m.apply_op(make_connection_op("living_room", "kitchen"))
        m.apply_op(make_connection_op("living_room", "kitchen"))
        assert len(m.state.connections) == 1

    def test_connection_to_unknown_room_raises(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        with pytest.raises(UnknownRoomError):
            m.apply_op(make_connection_op("living_room", "ghost_room"))


# ── Tests: set_constraint ─────────────────────────────────────────────────────

class TestSetConstraint:
    def test_add_min_area_constraint(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_constraint_op("living_room", "min_area", 250.0))
        assert len(m.state.constraints) == 1

    def test_duplicate_constraint_type_replaced(self):
        """Setting the same constraint type twice should replace, not append."""
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_constraint_op("living_room", "min_area", 200.0))
        m.apply_op(make_constraint_op("living_room", "min_area", 250.0))
        min_area_constraints = [
            c for c in m.state.constraints if c.constraint_type == "min_area"
        ]
        assert len(min_area_constraints) == 1
        assert min_area_constraints[0].value == 250.0

    def test_constraint_on_unknown_room_raises(self):
        m = make_manager()
        with pytest.raises(UnknownRoomError):
            m.apply_op(make_constraint_op("ghost_room", "min_area", 100.0))


# ── Tests: solve ──────────────────────────────────────────────────────────────

class TestSolve:
    def test_solve_two_rooms(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        matrix = m.solve()
        assert len(matrix) == 2
        for room_id, coords in matrix.items():
            assert "x" in coords
            assert "y" in coords
            assert "width" in coords
            assert "height" in coords

    def test_solve_empty_plan_raises(self):
        m = make_manager()
        with pytest.raises(PlanManagerError, match="no rooms"):
            m.solve()

    def test_solve_caches_matrix_in_state(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.solve()
        assert m.state.coordinate_matrix is not None


# ── Tests: export ─────────────────────────────────────────────────────────────

class TestExportDxf:
    def test_export_produces_dxf(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        dxf_path = m.export_dxf()
        assert dxf_path.exists()
        assert dxf_path.suffix == ".dxf"
        assert dxf_path.stat().st_size > 500

    def test_export_empty_plan_raises(self):
        m = make_manager()
        with pytest.raises(PlanManagerError, match="no rooms"):
            m.export_dxf()

    def test_export_dxf_is_valid(self):
        """DXF output can be parsed by ezdxf."""
        import ezdxf
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        dxf_path = m.export_dxf()
        doc = ezdxf.readfile(str(dxf_path))
        assert doc is not None


# ── Tests: reset + history ────────────────────────────────────────────────────

class TestResetAndHistory:
    def test_reset_clears_rooms(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.reset()
        assert m.room_count == 0
        assert m.state.version == 0

    def test_history_tracks_ops(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        assert len(m.history()) == 2
        assert m.history()[0].op_type == "add_room"

    def test_reset_clears_history(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.reset()
        assert len(m.history()) == 0
```

---

## Task 4: `tests/test_phase2_e2e.py`

Create this new test file. Tests the **full pipeline** with Claude mocked (no API key needed).

```python
"""
Phase 2 E2E tests.

Full NL → op → state → solve → DXF pipeline.
Claude API is mocked — these are fast tests (no FreeCAD, no API).

Marks:
  @pytest.mark.live — tests that call real Claude API (skipped by default)
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.plan_manager import PlanManager, PlanManagerError, UnknownRoomError
from engine.intent_parser.schemas import FloorPlanOp, RoomSpec


# ── Mocked pipeline tests ─────────────────────────────────────────────────────

class TestNLPipelineMocked:
    """Full NL → DXF pipeline with Claude mocked."""

    def _make_manager_with_mock_parser(self, ops: list[FloorPlanOp]) -> PlanManager:
        """Create a PlanManager whose parser returns ops in sequence."""
        with patch("engine.plan_manager.IntentParser") as MockParser:
            mock_instance = MockParser.return_value
            mock_instance.parse.side_effect = ops
            manager = PlanManager(api_key="mock-key")
        return manager

    def test_add_two_rooms_and_export(self):
        """Simulate: user says 'add living room' then 'add kitchen', then exports."""
        ops = [
            FloorPlanOp(
                op_type="add_room",
                room_spec=RoomSpec(name="Living Room", room_type="living", area_sqft=300.0),
            ),
            FloorPlanOp(
                op_type="add_room",
                room_spec=RoomSpec(name="Kitchen", room_type="kitchen", area_sqft=150.0),
            ),
        ]
        manager = self._make_manager_with_mock_parser(ops)

        manager.instruct("Add a living room of 300 sqft")
        manager.instruct("Add a kitchen of 150 sqft")

        assert manager.room_count == 2
        dxf_path = manager.export_dxf()
        assert dxf_path.exists()
        assert dxf_path.suffix == ".dxf"

    def test_add_then_remove_room(self):
        ops = [
            FloorPlanOp(
                op_type="add_room",
                room_spec=RoomSpec(name="Office", room_type="office", area_sqft=100.0),
            ),
            FloorPlanOp(
                op_type="remove_room",
                target_room_id="office",
            ),
        ]
        manager = self._make_manager_with_mock_parser(ops)
        manager.instruct("Add an office")
        assert manager.room_count == 1
        manager.instruct("Remove the office")
        assert manager.room_count == 0

    def test_five_room_plan_dxf_has_all_rooms(self):
        """Verify DXF output contains all 5 rooms on the WALLS layer."""
        import ezdxf
        ops = [
            FloorPlanOp(op_type="add_room", room_spec=RoomSpec(name=name, room_type=rt, area_sqft=area))
            for name, rt, area in [
                ("Living Room", "living", 300.0),
                ("Kitchen", "kitchen", 150.0),
                ("Master Bedroom", "bedroom", 200.0),
                ("Bathroom", "bathroom", 60.0),
                ("Office", "office", 120.0),
            ]
        ]
        manager = self._make_manager_with_mock_parser(ops)
        for _ in ops:
            manager.instruct("(mocked)")

        dxf_path = manager.export_dxf()
        doc = ezdxf.readfile(str(dxf_path))
        walls = [e for e in doc.modelspace() if e.dxf.layer == "WALLS"]
        assert len(walls) == 5, f"Expected 5 rooms, got {len(walls)}"

    def test_instruct_returns_op(self):
        ops = [
            FloorPlanOp(
                op_type="add_room",
                room_spec=RoomSpec(name="Bedroom", room_type="bedroom", area_sqft=180.0),
            )
        ]
        manager = self._make_manager_with_mock_parser(ops)
        op = manager.instruct("Add a bedroom")
        assert op.op_type == "add_room"


# ── Live API tests (skipped unless COGNITECT_CLAUDE_API_KEY is set) ───────────

@pytest.mark.live
class TestNLPipelineLive:
    """
    Live tests against the real Claude API.
    Skipped unless COGNITECT_CLAUDE_API_KEY is set in the environment.
    Run with: pytest -m live tests/test_phase2_e2e.py
    """

    @pytest.fixture
    def manager(self):
        import os
        if not os.environ.get("COGNITECT_CLAUDE_API_KEY"):
            pytest.skip("COGNITECT_CLAUDE_API_KEY not set")
        return PlanManager()

    def test_live_add_living_room(self, manager):
        op = manager.instruct("Add a living room of about 300 square feet")
        assert op.op_type == "add_room"
        assert manager.room_count == 1

    def test_live_two_room_plan_exports(self, manager):
        manager.instruct("Add a living room of 300 sqft")
        manager.instruct("Add a kitchen of 150 sqft adjacent to the living room")
        dxf_path = manager.export_dxf()
        assert dxf_path.exists()
        assert dxf_path.stat().st_size > 500
```

---

## API registration — update `engine/api/main.py`

Find the existing FastAPI app and add the plan router. The file likely looks like:

```python
from fastapi import FastAPI
app = FastAPI(title="Cognitect Engine API")
```

Add below it:

```python
from engine.api.routes.plan import router as plan_router
app.include_router(plan_router)
```

---

## pyproject.toml — add `live` marker

Find the `[tool.pytest.ini_options]` section and add `"live: marks tests requiring real Claude API"` to the markers list.

---

## What NOT to do

- Do **not** modify `engine/intent_parser/parser.py` or any schemas
- Do **not** modify `engine/constraint_solver/`
- Do **not** modify `engine/cad_generator/` or `engine/exporter/`
- Do **not** add database models — that's Phase 3
- Do **not** add authentication — that's Phase 4
- All existing tests in `tests/test_constraint_solver.py`, `tests/test_exporter.py`, `tests/test_intent_parser.py`, `tests/test_cad_generator.py`, `tests/test_phase1_e2e.py` must stay green

---

## Verification

After implementing, run:

```bash
pytest -m "not slow and not live" -v
```

Expected: all existing tests + new `test_plan_manager.py` + new `test_phase2_e2e.py` (non-live) pass.
