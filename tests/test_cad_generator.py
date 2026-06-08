"""
Tests for engine/cad_generator/

Verifies:
- CADGenerator is importable
- generate() raises NotImplementedError (stub)
- CADGenerationError is importable and has expected attributes
"""

from __future__ import annotations

import pytest

from engine.cad_generator.generator import CADGenerator, CADGenerationError
from engine.intent_parser.schemas import FloorPlanState, RoomSpec


class TestCADGeneratorImport:
    def test_cad_generator_importable(self):
        assert CADGenerator is not None

    def test_cad_generation_error_importable(self):
        assert CADGenerationError is not None

    def test_cad_generation_error_attributes(self):
        err = CADGenerationError("test error", stderr="stderr text", exit_code=1)
        assert str(err) == "test error"
        assert err.stderr == "stderr text"
        assert err.exit_code == 1


class TestCADGenerator:
    def test_generate_raises_when_freecad_missing(self):
        gen = CADGenerator(freecad_appimage_path="/tmp/nonexistent.AppImage")
        state = FloorPlanState(
            plan_id="test",
            rooms={
                "living_room": RoomSpec(
                    name="Living Room",
                    room_type="living",
                    area_sqft=300.0,
                    adjacency_requirements=[],
                )
            },
        )
        coordinate_matrix = {
            "living_room": {"x": 0.0, "y": 0.0, "width": 20.0, "height": 15.0}
        }
        with pytest.raises(CADGenerationError) as exc_info:
            gen.generate(coordinate_matrix, state)
        assert "FreeCAD binary not found" in str(exc_info.value)

    def test_cad_generator_accepts_appimage_path_override(self):
        """CADGenerator should accept a custom AppImage path."""
        from pathlib import Path
        gen = CADGenerator(freecad_appimage_path="/custom/path/FreeCAD.AppImage")
        assert gen.appimage_path == Path("/custom/path/FreeCAD.AppImage")

    def test_freecad_script_exists(self):
        """The FreeCAD Python script that runs inside the AppImage should exist."""
        from pathlib import Path
        script = Path("engine/cad_generator/freecad_scripts/generate_plan.py")
        assert script.exists(), f"FreeCAD script not found: {script}"
