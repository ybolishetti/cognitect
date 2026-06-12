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


def make_resize_room_op(room_id: str, area: float) -> FloorPlanOp:
    return FloorPlanOp(
        op_type="resize_room",
        target_room_id=room_id,
        room_spec=RoomSpec(name="unused", room_type="other", area_sqft=area),
    )


def make_scale_resize_op(room_id: str, scale_factor: float) -> FloorPlanOp:
    return FloorPlanOp(
        op_type="resize_room",
        target_room_id=room_id,
        room_spec=RoomSpec(name="unused", room_type="other", scale_factor=scale_factor),
    )


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

    def test_coordinate_matrix_persisted_on_op(self):
        """Adding a room should keep the cached coordinate matrix until next solve."""
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        matrix_before = m.solve()
        assert m.state.coordinate_matrix is not None
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        assert m.state.coordinate_matrix == matrix_before


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


# ── Tests: resize_room scale_factor ───────────────────────────────────────────

class TestResizeRoomScaleFactor:
    def test_scale_factor_expands_area(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Room 10", "other", 200.0))
        m.apply_op(make_scale_resize_op("room_10", 1.25))
        assert m.state.rooms["room_10"].area_sqft == 250.0

    def test_scale_factor_shrinks_area(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Room 10", "other", 200.0))
        m.apply_op(make_scale_resize_op("room_10", 0.8))
        assert m.state.rooms["room_10"].area_sqft == 160.0

    def test_scale_factor_no_area_no_matrix_is_noop(self, caplog):
        """If room has no area_sqft AND no coordinate_matrix, scale is skipped."""
        m = make_manager()
        m.apply_op(FloorPlanOp(
            op_type="add_room",
            room_spec=RoomSpec(name="Room 10", room_type="other"),
        ))
        # No solve() called — coordinate_matrix is None
        with caplog.at_level("WARNING"):
            m.apply_op(make_scale_resize_op("room_10", 1.25))
        assert m.state.rooms["room_10"].area_sqft is None
        assert "scale_factor" in caplog.text

    def test_scale_factor_uses_coordinate_matrix_when_no_area_sqft(self):
        """If room has no area_sqft but solver has run, derive base area from coordinates."""
        m = make_manager()
        m.apply_op(FloorPlanOp(
            op_type="add_room",
            room_spec=RoomSpec(name="Room 10", room_type="other"),  # no area_sqft
        ))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 100.0))
        coords = m.solve()
        base_w = coords["room_10"]["width"]
        base_h = coords["room_10"]["height"]
        base_area = base_w * base_h

        m.apply_op(make_scale_resize_op("room_10", 1.25))
        expected_area = round(base_area * 1.25, 2)
        assert m.state.rooms["room_10"].area_sqft == expected_area

    def test_scale_factor_preserves_name_and_room_type(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 200.0))
        m.apply_op(make_scale_resize_op("living_room", 1.25))
        assert m.state.rooms["living_room"].name == "Living Room"
        assert m.state.rooms["living_room"].room_type == "living"

    def test_scale_factor_then_solve_updates_coordinate_matrix(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 200.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        before = m.solve()
        before_area = (
            before["living_room"]["width"] * before["living_room"]["height"]
        )

        m.apply_op(make_scale_resize_op("living_room", 1.25))
        after = m.solve()
        after_area = (
            after["living_room"]["width"] * after["living_room"]["height"]
        )

        assert after_area > before_area
        assert m.state.rooms["living_room"].area_sqft == 250.0


# ── Tests: layout continuity ──────────────────────────────────────────────────

class TestPlanManagerContinuity:
    def test_coordinate_matrix_not_cleared_on_op(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        first_matrix = m.solve()
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        assert m.state.coordinate_matrix is not None
        assert m.state.coordinate_matrix == first_matrix

    def test_mutated_rooms_accumulate_across_ops(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        m.apply_op(make_resize_room_op("living_room", 375.0))
        m.apply_op(make_resize_room_op("kitchen", 180.0))
        assert m._last_mutated_rooms == {"living_room", "kitchen"}

    def test_resize_then_solve_preserves_other_rooms(self):
        m = make_manager()
        m.apply_op(make_add_room_op("Living Room", "living", 300.0))
        m.apply_op(make_add_room_op("Kitchen", "kitchen", 150.0))
        m.apply_op(make_add_room_op("Bedroom", "bedroom", 200.0))
        m.apply_op(make_add_room_op("Office", "office", 120.0))
        prior = m.solve()

        m.apply_op(make_resize_room_op("living_room", 375.0))
        after = m.solve()

        for rid in ("kitchen", "bedroom", "office"):
            assert after[rid]["x"] == pytest.approx(prior[rid]["x"], abs=1.0)
            assert after[rid]["y"] == pytest.approx(prior[rid]["y"], abs=1.0)
