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
from engine.intent_parser.schemas import FloorPlanOp, FloorPlanOpBatch, RoomSpec


# ── Mocked pipeline tests ─────────────────────────────────────────────────────

class TestNLPipelineMocked:
    """Full NL → DXF pipeline with Claude mocked."""

    def _make_manager_with_mock_parser(self, ops: list[FloorPlanOp]) -> PlanManager:
        """Create a PlanManager whose parser returns ops in sequence."""
        with patch("engine.plan_manager.IntentParser") as MockParser:
            mock_instance = MockParser.return_value
            mock_instance.parse_batch.side_effect = [
                FloorPlanOpBatch(
                    ops=[op],
                    batch_description=f"mock batch for {op.op_type}",
                )
                for op in ops
            ]
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
        applied = manager.instruct("Add a bedroom")
        assert applied[0].op_type == "add_room"


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
        ops = manager.instruct("Add a living room of about 300 square feet")
        assert ops[0].op_type == "add_room"
        assert manager.room_count == 1

    def test_live_two_room_plan_exports(self, manager):
        manager.instruct("Add a living room of 300 sqft")
        manager.instruct("Add a kitchen of 150 sqft adjacent to the living room")
        dxf_path = manager.export_dxf()
        assert dxf_path.exists()
        assert dxf_path.stat().st_size > 500
