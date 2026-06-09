"""
Phase 5 batch parsing tests.

Covers multi-op decomposition, architectural bundles, and backward compat.
Mock tests patch IntentParser._client.messages.create with fixture JSON.
Live tests gated with @pytest.mark.live.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from engine.intent_parser.parser import IntentParser, SchemaValidationError
from engine.intent_parser.schemas import (
    ConstraintSpec,
    FloorPlanOp,
    FloorPlanOpBatch,
    FloorPlanState,
    RoomSpec,
)
from engine.plan_manager import PlanManager


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_claude_response(batch_json: dict) -> MagicMock:
    """Build a mock anthropic messages.create response."""
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(batch_json))]
    return response


def _make_parser_with_batch(batch_json: dict) -> IntentParser:
    """Create an IntentParser whose Claude client returns the given batch JSON."""
    with patch("engine.intent_parser.parser.anthropic.Anthropic"):
        parser = IntentParser(api_key="test-key")
    parser._client.messages.create = MagicMock(
        return_value=_mock_claude_response(batch_json)
    )
    return parser


def _living_room_batch(area: float = 300.0) -> dict:
    return {
        "ops": [
            {
                "op_type": "add_room",
                "room_spec": {
                    "name": "Living Room",
                    "room_type": "living",
                    "area_sqft": area,
                    "adjacency_requirements": [],
                },
                "metadata": {"confidence": 0.9},
            }
        ],
        "batch_description": "Add a living room",
        "metadata": {"confidence": 0.9},
    }


def _three_bedrooms_batch() -> dict:
    return {
        "ops": [
            {
                "op_type": "add_room",
                "room_spec": {
                    "name": f"Bedroom {i}",
                    "room_type": "bedroom",
                    "area_sqft": 150.0,
                    "adjacency_requirements": [],
                },
                "metadata": {"confidence": 0.85},
            }
            for i in range(1, 4)
        ],
        "batch_description": "Add 3 bedrooms",
        "metadata": {"confidence": 0.85},
    }


def _master_suite_batch() -> dict:
    return {
        "ops": [
            {
                "op_type": "add_room",
                "room_spec": {
                    "name": "Master Bedroom",
                    "room_type": "bedroom",
                    "area_sqft": 200.0,
                    "adjacency_requirements": [],
                },
                "metadata": {"confidence": 0.9},
            },
            {
                "op_type": "add_room",
                "room_spec": {
                    "name": "Master Bathroom",
                    "room_type": "bathroom",
                    "area_sqft": 80.0,
                    "adjacency_requirements": ["Master Bedroom"],
                },
                "metadata": {"confidence": 0.9},
            },
        ],
        "batch_description": "Add a master suite",
        "metadata": {"confidence": 0.9},
    }


def _resize_kitchen_batch() -> dict:
    return {
        "ops": [
            {
                "op_type": "resize_room",
                "target_room_id": "kitchen",
                "room_spec": {
                    "name": "Kitchen",
                    "room_type": "kitchen",
                    "area_sqft": 220.0,
                    "adjacency_requirements": ["dining_room"],
                },
                "metadata": {"confidence": 0.85},
            },
            {
                "op_type": "set_constraint",
                "constraint_spec": {
                    "constraint_type": "adjacency",
                    "room_id": "kitchen",
                    "value": "dining_room",
                    "strength": "strong",
                },
                "metadata": {"confidence": 0.85},
            },
        ],
        "batch_description": "Enlarge kitchen and require adjacency to dining room",
        "metadata": {"confidence": 0.85},
    }


def _open_concept_batch() -> dict:
    return {
        "ops": [
            {
                "op_type": "add_room",
                "room_spec": {
                    "name": "Living Room",
                    "room_type": "living",
                    "area_sqft": 300.0,
                    "adjacency_requirements": ["Kitchen"],
                },
                "metadata": {"confidence": 0.9},
            },
            {
                "op_type": "add_room",
                "room_spec": {
                    "name": "Kitchen",
                    "room_type": "kitchen",
                    "area_sqft": 200.0,
                    "adjacency_requirements": ["Living Room"],
                },
                "metadata": {"confidence": 0.9},
            },
        ],
        "batch_description": "Add open concept kitchen and living area",
        "metadata": {"confidence": 0.9},
    }


# ── Mocked batch parsing tests ────────────────────────────────────────────────

class TestParseBatchMocked:
    def test_single_room_instruction(self):
        parser = _make_parser_with_batch(_living_room_batch())
        batch = parser.parse_batch("Add a living room of 300 sqft", FloorPlanState(plan_id="t1"))
        assert len(batch.ops) == 1
        assert batch.ops[0].op_type == "add_room"

    def test_multi_room_three_bedrooms(self):
        parser = _make_parser_with_batch(_three_bedrooms_batch())
        batch = parser.parse_batch("Add 3 bedrooms", FloorPlanState(plan_id="t2"))
        assert len(batch.ops) >= 3
        assert all(op.op_type == "add_room" for op in batch.ops)

    def test_master_suite_expansion(self):
        parser = _make_parser_with_batch(_master_suite_batch())
        batch = parser.parse_batch("Add a master suite", FloorPlanState(plan_id="t3"))
        op_types = [op.op_type for op in batch.ops]
        assert "add_room" in op_types
        room_types = [op.room_spec.room_type for op in batch.ops if op.room_spec]
        assert "bedroom" in room_types
        assert "bathroom" in room_types

    def test_resize_plus_adjacency(self):
        parser = _make_parser_with_batch(_resize_kitchen_batch())
        state = FloorPlanState(
            plan_id="t4",
            rooms={
                "kitchen": RoomSpec(name="Kitchen", room_type="kitchen", area_sqft=150.0),
                "dining_room": RoomSpec(name="Dining Room", room_type="dining", area_sqft=150.0),
            },
        )
        batch = parser.parse_batch(
            "Make the kitchen bigger to fit the dining room", state
        )
        op_types = [op.op_type for op in batch.ops]
        assert "resize_room" in op_types
        assert "set_constraint" in op_types

    def test_open_concept_with_adjacency(self):
        parser = _make_parser_with_batch(_open_concept_batch())
        batch = parser.parse_batch(
            "Add an open concept kitchen and living area",
            FloorPlanState(plan_id="t5"),
        )
        assert len(batch.ops) >= 2
        add_ops = [op for op in batch.ops if op.op_type == "add_room"]
        assert len(add_ops) >= 2
        assert any(
            op.room_spec and op.room_spec.adjacency_requirements
            for op in add_ops
        )

    def test_backward_compat_single_op_json(self):
        """If Claude returns a single FloorPlanOp (no ops array), wrap it."""
        single_op = {
            "op_type": "add_room",
            "room_spec": {
                "name": "Office",
                "room_type": "office",
                "area_sqft": 120.0,
                "adjacency_requirements": [],
            },
            "metadata": {"confidence": 0.8, "raw_nl": "Add an office"},
        }
        parser = _make_parser_with_batch(single_op)
        batch = parser.parse_batch("Add an office", FloorPlanState(plan_id="t6"))
        assert len(batch.ops) == 1
        assert batch.ops[0].op_type == "add_room"

    def test_parse_returns_first_op(self):
        parser = _make_parser_with_batch(_three_bedrooms_batch())
        op = parser.parse("Add 3 bedrooms", FloorPlanState(plan_id="t7"))
        assert op.op_type == "add_room"

    def test_summarize_state_includes_constraints(self):
        with patch("engine.intent_parser.parser.anthropic.Anthropic"):
            parser = IntentParser(api_key="test-key")
        state = FloorPlanState(
            plan_id="sum1",
            rooms={
                "kitchen": RoomSpec(
                    name="Kitchen",
                    room_type="kitchen",
                    area_sqft=150.0,
                    adjacency_requirements=["dining_room"],
                ),
            },
            constraints=[
                ConstraintSpec(
                    constraint_type="min_area",
                    room_id="kitchen",
                    value=140.0,
                ),
            ],
        )
        summary = json.loads(parser._summarize_state(state))
        assert summary["total_area_sqft"] == 150.0
        assert summary["room_count"] == 1
        assert summary["rooms"]["kitchen"]["adjacency_requirements"] == ["dining_room"]
        assert len(summary["constraints"]) == 1
        assert summary["constraints"][0]["type"] == "min_area"


class TestInstructBatchMocked:
    def test_instruct_applies_all_ops_in_batch(self):
        batch = FloorPlanOpBatch(
            ops=[
                FloorPlanOp(
                    op_type="add_room",
                    room_spec=RoomSpec(name="Bedroom 1", room_type="bedroom", area_sqft=150.0),
                ),
                FloorPlanOp(
                    op_type="add_room",
                    room_spec=RoomSpec(name="Bedroom 2", room_type="bedroom", area_sqft=150.0),
                ),
                FloorPlanOp(
                    op_type="add_room",
                    room_spec=RoomSpec(name="Bedroom 3", room_type="bedroom", area_sqft=150.0),
                ),
            ],
            batch_description="Add 3 bedrooms",
        )
        with patch("engine.plan_manager.IntentParser") as MockParser:
            MockParser.return_value.parse_batch.return_value = batch
            manager = PlanManager(api_key="test-key")

        applied = manager.instruct("Add 3 bedrooms")
        assert len(applied) == 3
        assert manager.room_count == 3
        assert len(manager.history()) == 3


# ── Live API tests ────────────────────────────────────────────────────────────

@pytest.mark.live
class TestParseBatchLive:
    @pytest.fixture
    def parser(self):
        if not os.environ.get("COGNITECT_CLAUDE_API_KEY"):
            pytest.skip("COGNITECT_CLAUDE_API_KEY not set")
        return IntentParser()

    def test_live_single_room(self, parser):
        batch = parser.parse_batch(
            "Add a living room of 300 sqft",
            FloorPlanState(plan_id="live1"),
        )
        assert len(batch.ops) == 1
        assert batch.ops[0].op_type == "add_room"

    def test_live_three_bedrooms(self, parser):
        batch = parser.parse_batch(
            "Add 3 bedrooms",
            FloorPlanState(plan_id="live2"),
        )
        assert len(batch.ops) >= 3
        assert all(op.op_type == "add_room" for op in batch.ops)
