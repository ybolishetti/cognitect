"""
FreeCAD headless script — runs inside the FreeCAD Python environment.

This script is invoked by engine/cad_generator/generator.py via subprocess.
It receives the coordinate matrix via stdin (JSON) and writes a .FCStd file.

Architecture note: This script has NO access to the main Python environment.
It runs isolated inside the FreeCAD AppImage's own Python 3.11 interpreter.
All data exchange must be via stdin/stdout/files.
"""

import json
import sys


def generate_floor_plan(
    coordinate_matrix: dict, output_path: str, metadata: dict = None
) -> None:
    """
    Generate a 3D BRep floor plan model from a coordinate matrix.

    coordinate_matrix: {room_id: {x, y, width, height}} in feet
    output_path: Path to write the .FCStd file
    metadata: optional dict with room names/types (from payload)
    """
    try:
        import FreeCAD
        import Part
        import Draft
        import Arch
    except ImportError as e:
        print(f"FREECAD_ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    FT_TO_MM = 304.8
    WALL_HEIGHT_MM = 9.0 * FT_TO_MM  # 9ft ceiling = 2743.2mm
    WALL_WIDTH_MM = 200.0  # 200mm wall thickness (standard)

    doc = FreeCAD.newDocument("FloorPlan")

    room_names = {}
    if metadata and "rooms" in metadata:
        room_names = {
            rid: info.get("name", rid) for rid, info in metadata["rooms"].items()
        }

    for room_id, coords in coordinate_matrix.items():
        # Convert feet to mm
        x_mm = coords["x"] * FT_TO_MM
        y_mm = coords["y"] * FT_TO_MM
        w_mm = coords["width"] * FT_TO_MM
        h_mm = coords["height"] * FT_TO_MM

        # Room perimeter as a closed wire (exterior face of walls)
        p1 = FreeCAD.Vector(x_mm, y_mm, 0)
        p2 = FreeCAD.Vector(x_mm + w_mm, y_mm, 0)
        p3 = FreeCAD.Vector(x_mm + w_mm, y_mm + h_mm, 0)
        p4 = FreeCAD.Vector(x_mm, y_mm + h_mm, 0)

        # Create the wall boundary wire
        wire = Draft.makeWire([p1, p2, p3, p4], closed=True)
        wire.Label = f"{room_id}_outline"

        # Create Arch Wall
        wall = Arch.makeWall(wire, height=WALL_HEIGHT_MM, width=WALL_WIDTH_MM)
        wall.Label = room_names.get(room_id, room_id)

        # Create floor slab using Part::Box for solid geometry
        slab = doc.addObject("Part::Box", f"{room_id}_slab")
        slab.Placement = FreeCAD.Placement(
            FreeCAD.Vector(x_mm, y_mm, -100),  # 100mm below floor level
            FreeCAD.Rotation(),
        )
        slab.Length = w_mm
        slab.Width = h_mm
        slab.Height = 100  # 100mm slab
        slab.Label = f"{room_names.get(room_id, room_id)} Floor"

    doc.recompute()
    doc.saveAs(output_path)
    print(f"FREECAD_OK: {output_path}")


if __name__ == "__main__":
    data = json.load(sys.stdin)
    generate_floor_plan(
        data["coordinate_matrix"],
        data["output_path"],
        metadata=data,
    )
