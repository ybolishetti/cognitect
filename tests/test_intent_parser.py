"""
Tests for engine/intent_parser/

Tests:
- Schema validation with valid JSON → FloorPlanOp
- Schema validation with invalid JSON → raises ValidationError
- IntentParser is importable
- Live API test (skipped unless COGNITECT_CLAUDE_API_KEY is set)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.intent_parser.schemas import (
    ConnectionSpec,
    ConstraintSpec,
    FloorPlanOp,
    FloorPlanState,
    RoomSpec,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Schema validation tests ───────────────────────────────────────────────────

class TestFloorPlanOpSchema:
    """Test that FloorPlanOp schema validates correctly."""

    def test_add_room_valid(self):
        op = FloorPlanOp(
            op_type="add_room",
            room_spec=RoomSpec(
                name="Master Bedroom",
                room_type="bedroom",
                area_sqft=200.0,
                adjacency_requirements=["main_bathroom"],
            ),
            metadata={"confidence": 0.9},
        )
        assert op.op_type == "add_room"
        assert op.room_spec.name == "Master Bedroom"
        assert op.room_spec.area_sqft == 200.0

    def test_remove_room_valid(self):
        op = FloorPlanOp(
            op_type="remove_room",
            target_room_id="office",
            metadata={},
        )
        assert op.op_type == "remove_room"
        assert op.target_room_id == "office"

    def test_set_constraint_valid(self):
        op = FloorPlanOp(
            op_type="set_constraint",
            target_room_id="kitchen",
            constraint_spec=ConstraintSpec(
                constraint_type="min_area",
                room_id="kitchen",
                value=150.0,
                strength="strong",
            ),
            metadata={},
        )
        assert op.constraint_spec.value == 150.0

    def test_add_connection_valid(self):
        op = FloorPlanOp(
            op_type="add_connection",
            connection_spec=ConnectionSpec(
                room_a_id="living_room",
                room_b_id="kitchen",
                connection_type="door",
                width_ft=3.0,
            ),
            metadata={},
        )
        assert op.connection_spec.connection_type == "door"

    def test_add_room_without_room_spec_raises(self):
        with pytest.raises(ValidationError):
            FloorPlanOp(op_type="add_room", metadata={})

    def test_remove_room_without_target_raises(self):
        with pytest.raises(ValidationError):
            FloorPlanOp(op_type="remove_room", metadata={})

    def test_set_constraint_without_spec_raises(self):
        with pytest.raises(ValidationError):
            FloorPlanOp(op_type="set_constraint", metadata={})

    def test_add_connection_same_room_raises(self):
        with pytest.raises(ValidationError):
            ConnectionSpec(
                room_a_id="kitchen",
                room_b_id="kitchen",
                connection_type="door",
            )

    def test_room_spec_area_bounds_invalid(self):
        with pytest.raises(ValidationError):
            RoomSpec(
                name="Test",
                room_type="bedroom",
                min_area_sqft=300.0,
                max_area_sqft=100.0,  # min > max — should fail
            )

    def test_all_op_types_accepted(self):
        valid_ops = [
            "add_room", "remove_room", "resize_room",
            "move_room", "add_connection", "set_constraint",
        ]
        for op_type in valid_ops:
            # Just check the Literal accepts each value — don't full-validate
            data = {"op_type": op_type, "metadata": {}}
            if op_type == "add_room":
                data["room_spec"] = {
                    "name": "Test", "room_type": "other",
                    "adjacency_requirements": [],
                }
            elif op_type in ("remove_room", "resize_room", "move_room"):
                data["target_room_id"] = "test_room"
            elif op_type == "set_constraint":
                data["constraint_spec"] = {
                    "constraint_type": "min_area",
                    "room_id": "test_room",
                    "value": 100.0,
                    "strength": "strong",
                }
            elif op_type == "add_connection":
                data["connection_spec"] = {
                    "room_a_id": "room_a",
                    "room_b_id": "room_b",
                    "connection_type": "door",
                }
            op = FloorPlanOp.model_validate(data)
            assert op.op_type == op_type


class TestFixtureSchemas:
    """Validate all sample_op_schema.json fixtures parse correctly."""

    def test_sample_op_schema_fixture(self):
        fixture_path = FIXTURES_DIR / "sample_op_schema.json"
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"
        with open(fixture_path) as f:
            ops_data = json.load(f)
        assert len(ops_data) > 0
        for i, op_data in enumerate(ops_data):
            op = FloorPlanOp.model_validate(op_data)
            assert op.op_type in [
                "add_room", "remove_room", "resize_room",
                "move_room", "add_connection", "set_constraint",
            ], f"Op {i} has invalid op_type: {op.op_type}"


class TestFloorPlanState:
    """Test FloorPlanState creation and serialization."""

    def test_empty_state(self):
        state = FloorPlanState(plan_id="test_001")
        assert state.plan_id == "test_001"
        assert len(state.rooms) == 0
        assert state.coordinate_matrix is None

    def test_state_with_rooms(self):
        state = FloorPlanState(
            plan_id="test_001",
            rooms={
                "living_room": RoomSpec(
                    name="Living Room",
                    room_type="living",
                    area_sqft=300.0,
                    adjacency_requirements=[],
                ),
                "kitchen": RoomSpec(
                    name="Kitchen",
                    room_type="kitchen",
                    area_sqft=150.0,
                    adjacency_requirements=[],
                ),
            },
        )
        assert len(state.rooms) == 2
        assert "living_room" in state.rooms

    def test_state_serialization(self):
        state = FloorPlanState(plan_id="test_001")
        dumped = state.model_dump()
        assert dumped["plan_id"] == "test_001"
        assert isinstance(dumped["rooms"], dict)


# ── Import test ───────────────────────────────────────────────────────────────

class TestIntentParserImport:
    def test_parser_module_importable(self):
        from engine.intent_parser import parser  # noqa: F401
        from engine.intent_parser.parser import IntentParser, APIError, SchemaValidationError
        assert IntentParser is not None
        assert APIError is not None

    def test_parser_requires_api_key(self):
        from engine.intent_parser.parser import IntentParser
        import os
        old_key = os.environ.pop("COGNITECT_CLAUDE_API_KEY", None)
        try:
            with pytest.raises(ValueError, match="COGNITECT_CLAUDE_API_KEY"):
                IntentParser(api_key=None)
        finally:
            if old_key:
                os.environ["COGNITECT_CLAUDE_API_KEY"] = old_key


# ── Live API tests (skipped unless key is set) ────────────────────────────────

LIVE_API_AVAILABLE = bool(os.environ.get("COGNITECT_CLAUDE_API_KEY"))


@pytest.mark.skipif(not LIVE_API_AVAILABLE, reason="COGNITECT_CLAUDE_API_KEY not set")
class TestIntentParserLive:
    """Live tests against Claude API. Only run when key is available."""

    def test_parse_add_room(self):
        from engine.intent_parser.parser import IntentParser
        parser = IntentParser()
        state = FloorPlanState(plan_id="live_test_001")
        op = parser.parse(
            "Add a master bedroom of about 200 square feet",
            state,
        )
        assert op.op_type == "add_room"
        assert op.room_spec is not None
        assert op.room_spec.room_type == "bedroom"
        assert op.room_spec.area_sqft == pytest.approx(200.0, rel=0.1)

    def test_parse_remove_room(self):
        from engine.intent_parser.parser import IntentParser
        parser = IntentParser()
        state = FloorPlanState(
            plan_id="live_test_002",
            rooms={
                "office": RoomSpec(name="Office", room_type="office", adjacency_requirements=[])
            },
        )
        op = parser.parse("Remove the office", state)
        assert op.op_type == "remove_room"
        assert op.target_room_id == "office"

    def test_parse_set_constraint(self):
        from engine.intent_parser.parser import IntentParser
        parser = IntentParser()
        state = FloorPlanState(
            plan_id="live_test_003",
            rooms={
                "kitchen": RoomSpec(name="Kitchen", room_type="kitchen", adjacency_requirements=[])
            },
        )
        op = parser.parse("Make the kitchen at least 150 square feet", state)
        assert op.op_type in ("set_constraint", "resize_room")

    def test_sla_under_2s(self):
        import time
        from engine.intent_parser.parser import IntentParser
        parser = IntentParser()
        state = FloorPlanState(plan_id="sla_test")
        t0 = time.perf_counter()
        parser.parse("Add a bathroom", state)
        elapsed = time.perf_counter() - t0
        assert elapsed < 2.0, f"SLA violated: {elapsed:.2f}s > 2.0s"
