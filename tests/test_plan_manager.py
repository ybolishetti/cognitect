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
