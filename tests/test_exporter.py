"""
Tests for engine/exporter/

Verifies:
- PlanExporter is importable
- export_dxf() raises NotImplementedError (stub)
- export_pdf() raises NotImplementedError (stub)
- export_from_matrix() raises NotImplementedError (stub)
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


class TestPlanExporterStubs:
    """Verify all stub methods raise NotImplementedError with helpful messages."""

    def setup_method(self):
        self.exporter = PlanExporter()
        self.model_path = Path("/tmp/test_plan.FCStd")
        self.metadata = {
            "plan_id": "test_001",
            "project_name": "Test Project",
            "rooms": {
                "living_room": {"x": 0.0, "y": 0.0, "width": 20.0, "height": 15.0}
            },
        }

    def test_export_dxf_raises_not_implemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            self.exporter.export_dxf(self.model_path, self.metadata)
        assert "DRAFT_EXPORTER.md" in str(exc_info.value)

    def test_export_pdf_raises_not_implemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            self.exporter.export_pdf(self.model_path, self.metadata)
        assert "DRAFT_EXPORTER.md" in str(exc_info.value)

    def test_export_from_matrix_raises_not_implemented(self):
        coordinate_matrix = {
            "living_room": {"x": 0.0, "y": 0.0, "width": 20.0, "height": 15.0}
        }
        with pytest.raises(NotImplementedError) as exc_info:
            self.exporter.export_from_matrix(coordinate_matrix, self.metadata)
        assert "DRAFT_EXPORTER.md" in str(exc_info.value)
