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

    def instruct(self, nl_input: str) -> list[FloorPlanOp]:
        """
        Parse a natural-language instruction and apply ALL resulting ops.

        Args:
            nl_input: e.g. "Add a master bedroom of 200 sqft adjacent to the bathroom"

        Returns:
            List of FloorPlanOps that were applied (1 or more).

        Raises:
            IntentParseError: If Claude fails to parse the instruction.
            PlanManagerError: If the op can't be applied to the current state.
        """
        batch = self._parser.parse_batch(nl_input, self._state)
        applied = []
        for op in batch.ops:
            self._apply_op(op)
            self._history.append(op)
            applied.append(op)
        logger.info(
            "Applied %d op(s) from batch '%s' | rooms=%d | v=%d",
            len(applied), batch.batch_description,
            len(self._state.rooms), self._state.version,
        )
        return applied

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
