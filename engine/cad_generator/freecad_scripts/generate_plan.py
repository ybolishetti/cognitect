"""
FreeCAD headless script — runs inside the FreeCAD Python environment.

This script is invoked by engine/cad_generator/generator.py via subprocess.
It receives the coordinate matrix via stdin (JSON) and writes a .FCStd file.

Architecture note: This script has NO access to the main Python environment.
It runs isolated inside the FreeCAD AppImage's own Python 3.11 interpreter.
All data exchange must be via stdin/stdout/files.

STUB: Full implementation in DRAFT_CAD_GENERATOR.md
"""

import json
import sys

def generate_floor_plan(coordinate_matrix: dict, output_path: str) -> None:
    """
    Generate a 3D BRep floor plan model from a coordinate matrix.

    Args:
        coordinate_matrix: {room_id: {x, y, width, height}} in feet
        output_path: Path to write the .FCStd file

    Raises:
        RuntimeError: If FreeCAD generation fails.
    """
    try:
        import FreeCAD
        import Part
        import Draft
        import Arch
    except ImportError as e:
        print(f"FREECAD_ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    WALL_HEIGHT_FT = 9.0
    FT_TO_MM = 304.8  # FreeCAD uses mm internally

    doc = FreeCAD.newDocument("FloorPlan")

    for room_id, coords in coordinate_matrix.items():
        x = coords["x"] * FT_TO_MM
        y = coords["y"] * FT_TO_MM
        w = coords["width"] * FT_TO_MM
        h = coords["height"] * FT_TO_MM
        wall_h = WALL_HEIGHT_FT * FT_TO_MM

        # Create room slab (floor)
        slab = doc.addObject("Part::Box", f"{room_id}_slab")
        slab.Placement = FreeCAD.Placement(
            FreeCAD.Vector(x, y, 0),
            FreeCAD.Rotation()
        )
        slab.Length = w
        slab.Width = h
        slab.Height = 100  # 100mm slab thickness

        # Label
        label = doc.addObject("App::DocumentObjectGroup", f"{room_id}_group")
        label.Label = room_id

    doc.recompute()
    doc.saveAs(output_path)
    print(f"FREECAD_OK: {output_path}")


if __name__ == "__main__":
    data = json.load(sys.stdin)
    generate_floor_plan(data["coordinate_matrix"], data["output_path"])
