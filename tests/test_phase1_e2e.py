"""
Phase 1 E2E integration test.

Chain: FloorPlanState → ConstraintSolver → CADGenerator → PlanExporter

Marks:
  @pytest.mark.slow — any test that invokes FreeCAD subprocess (~5-15s each)

Run fast tests only: pytest -m "not slow"
Run all: pytest tests/test_phase1_e2e.py
"""
from __future__ import annotations

import pytest
from pathlib import Path

from engine.constraint_solver.solver import ConstraintSolver
from engine.cad_generator.generator import CADGenerator, CADGenerationError
from engine.exporter.exporter import PlanExporter, ExportError
from engine.intent_parser.schemas import FloorPlanState, RoomSpec


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def two_room_state():
    return FloorPlanState(
        plan_id="e2e_test_two_room",
        rooms={
            "living_room": RoomSpec(
                name="Living Room", room_type="living", area_sqft=300.0
            ),
            "kitchen": RoomSpec(
                name="Kitchen", room_type="kitchen", area_sqft=150.0
            ),
        },
    )


@pytest.fixture
def five_room_state():
    return FloorPlanState(
        plan_id="e2e_test_five_room",
        rooms={
            "living_room": RoomSpec(name="Living Room", room_type="living", area_sqft=300.0),
            "kitchen": RoomSpec(name="Kitchen", room_type="kitchen", area_sqft=150.0),
            "master_bedroom": RoomSpec(name="Master Bedroom", room_type="bedroom", area_sqft=200.0),
            "bathroom": RoomSpec(name="Bathroom", room_type="bathroom", area_sqft=60.0),
            "office": RoomSpec(name="Office", room_type="office", area_sqft=120.0),
        },
    )


@pytest.fixture
def solver():
    return ConstraintSolver()


@pytest.fixture
def exporter():
    return PlanExporter()


@pytest.fixture
def cad_generator():
    return CADGenerator()


# ── Exporter tests (fast — no FreeCAD) ───────────────────────────────────────

class TestExporterFromMatrix:
    def test_export_from_matrix_produces_dxf(self, exporter, two_room_state, solver):
        matrix = solver.solve(two_room_state)
        metadata = {
            "plan_id": two_room_state.plan_id,
            "project_name": "Test Plan",
            "rooms": {
                room_id: {"name": spec.name, "room_type": spec.room_type}
                for room_id, spec in two_room_state.rooms.items()
            },
        }
        dxf_path = exporter.export_from_matrix(matrix, metadata)

        assert dxf_path.exists(), f"DXF not found at {dxf_path}"
        assert dxf_path.suffix == ".dxf"
        assert dxf_path.stat().st_size > 1000, "DXF file suspiciously small"

    def test_dxf_is_valid_ezdxf(self, exporter, two_room_state, solver):
        """Verify the output DXF can be read back by ezdxf."""
        import ezdxf
        matrix = solver.solve(two_room_state)
        metadata = {
            "plan_id": two_room_state.plan_id,
            "project_name": "Readback Test",
            "rooms": {
                rid: {"name": spec.name, "room_type": spec.room_type}
                for rid, spec in two_room_state.rooms.items()
            },
        }
        dxf_path = exporter.export_from_matrix(matrix, metadata)

        doc = ezdxf.readfile(str(dxf_path))
        assert doc is not None
        layers = [layer.dxf.name for layer in doc.layers]
        assert "WALLS" in layers
        assert "ANNOTATIONS" in layers
        assert "DIMENSIONS" in layers

    def test_dxf_contains_all_rooms(self, exporter, five_room_state, solver):
        """Each room should produce at least one entity on the WALLS layer."""
        import ezdxf
        matrix = solver.solve(five_room_state)
        metadata = {
            "plan_id": five_room_state.plan_id,
            "project_name": "Five Room Test",
            "rooms": {
                rid: {"name": spec.name, "room_type": spec.room_type}
                for rid, spec in five_room_state.rooms.items()
            },
        }
        dxf_path = exporter.export_from_matrix(matrix, metadata)

        doc = ezdxf.readfile(str(dxf_path))
        msp = doc.modelspace()
        walls = [e for e in msp if e.dxf.layer == "WALLS"]
        assert len(walls) == 5, f"Expected 5 wall entities, got {len(walls)}"


# ── CAD Generator tests (slow — invokes FreeCAD) ─────────────────────────────

class TestCADGeneratorE2E:
    @pytest.mark.slow
    def test_generate_two_room_fcstd(self, cad_generator, two_room_state, solver):
        """FreeCAD subprocess should produce a valid .FCStd file."""
        matrix = solver.solve(two_room_state)
        fcstd_path = cad_generator.generate(matrix, two_room_state)

        assert fcstd_path.exists(), f".FCStd not found: {fcstd_path}"
        assert fcstd_path.suffix == ".FCStd"
        assert fcstd_path.stat().st_size > 100, "FCStd file suspiciously small"

    @pytest.mark.slow
    def test_generate_five_room_fcstd(self, cad_generator, five_room_state, solver):
        matrix = solver.solve(five_room_state)
        fcstd_path = cad_generator.generate(matrix, five_room_state)

        assert fcstd_path.exists()
        assert fcstd_path.stat().st_size > 100

    @pytest.mark.slow
    def test_generate_within_sla(self, cad_generator, two_room_state, solver):
        """FreeCAD generation should complete within 15s SLA."""
        import time
        matrix = solver.solve(two_room_state)
        t0 = time.perf_counter()
        cad_generator.generate(matrix, two_room_state)
        elapsed = time.perf_counter() - t0
        assert elapsed < 15.0, f"CAD generation SLA violated: {elapsed:.1f}s > 15s"


# ── Full chain test ───────────────────────────────────────────────────────────

class TestFullPipeline:
    @pytest.mark.slow
    def test_solver_to_dxf_pipeline(self, solver, cad_generator, exporter, two_room_state):
        """Full chain: state → solver → CAD → DXF."""
        matrix = solver.solve(two_room_state)
        assert len(matrix) == 2

        fcstd_path = cad_generator.generate(matrix, two_room_state)
        assert fcstd_path.exists()

        metadata = {
            "plan_id": two_room_state.plan_id,
            "project_name": "E2E Pipeline Test",
            "coordinate_matrix": matrix,
            "rooms": {
                rid: {"name": spec.name, "room_type": spec.room_type}
                for rid, spec in two_room_state.rooms.items()
            },
        }
        dxf_path = exporter.export_dxf(fcstd_path, metadata)
        assert dxf_path.exists()
        assert dxf_path.stat().st_size > 1000

    def test_solver_to_dxf_no_freecad(self, solver, exporter, two_room_state):
        """Shortcut path: solver → export_from_matrix (no FreeCAD needed)."""
        matrix = solver.solve(two_room_state)
        metadata = {
            "plan_id": two_room_state.plan_id,
            "project_name": "No-FreeCAD Path Test",
            "rooms": {
                rid: {"name": spec.name, "room_type": spec.room_type}
                for rid, spec in two_room_state.rooms.items()
            },
        }
        dxf_path = exporter.export_from_matrix(matrix, metadata)
        assert dxf_path.exists()
        assert dxf_path.stat().st_size > 1000
