"""
Tests for engine/exporter/

Verifies:
- PlanExporter is importable
- export_from_matrix() produces valid DXF
- export_dxf() delegates to export_from_matrix()
- ExportError is importable
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.exporter.exporter import ExportError, PlanExporter


class TestPlanExporterImport:
    def test_plan_exporter_importable(self):
        assert PlanExporter is not None

    def test_export_error_importable(self):
        assert ExportError is not None

    def test_export_error_is_exception(self):
        with pytest.raises(ExportError):
            raise ExportError("test export failure")


class TestPlanExporter:
    def setup_method(self):
        self.exporter = PlanExporter()
        self.model_path = Path("/tmp/test_plan.FCStd")
        self.coordinate_matrix = {
            "living_room": {"x": 0.0, "y": 0.0, "width": 20.0, "height": 15.0}
        }
        self.metadata = {
            "plan_id": "test_001",
            "project_name": "Test Project",
            "coordinate_matrix": self.coordinate_matrix,
            "rooms": {
                "living_room": {"name": "Living Room", "room_type": "living"}
            },
        }

    def test_export_from_matrix_produces_dxf(self):
        dxf_path = self.exporter.export_from_matrix(
            self.coordinate_matrix, self.metadata
        )
        assert dxf_path.exists()
        assert dxf_path.suffix == ".dxf"
        assert dxf_path.stat().st_size > 100

    def test_export_dxf_delegates_to_matrix(self):
        dxf_path = self.exporter.export_dxf(self.model_path, self.metadata)
        assert dxf_path.exists()
        assert dxf_path.suffix == ".dxf"

    def test_export_dxf_requires_rooms_key(self):
        with pytest.raises(ExportError):
            self.exporter.export_dxf(self.model_path, {"plan_id": "x"})
