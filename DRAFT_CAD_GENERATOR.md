# DRAFT: CAD Generator — Cursor Composer Implementation Spec

## 1. Task

Implement `engine/cad_generator/generator.py::CADGenerator.generate()` to invoke FreeCAD headless via AppImage subprocess, converting a coordinate matrix into a `.FCStd` 3D BRep floor plan model using the FreeCAD Draft + Arch workbenches.

---

## 2. Repo Context

```
engine/
├── cad_generator/
│   ├── __init__.py
│   ├── generator.py              ← IMPLEMENT THIS (stub exists, raise NotImplementedError)
│   └── freecad_scripts/
│       └── generate_plan.py      ← ALSO IMPLEMENT THIS (runs inside FreeCAD Python env)
├── intent_parser/
│   └── schemas.py                ← FloorPlanState, RoomSpec — read-only reference
```

**Do not touch:** `engine/intent_parser/`, `engine/constraint_solver/`, `api/`, `tests/`.

---

## 3. Data Shapes

### Input to `CADGenerator.generate()`

```python
coordinate_matrix: dict[str, dict[str, float]]
# Example:
{
    "living_room": {"x": 0.0,  "y": 0.0,  "width": 20.0, "height": 15.0},
    "kitchen":     {"x": 20.0, "y": 0.0,  "width": 12.0, "height": 12.5},
    "master_bedroom": {"x": 0.0, "y": 15.0, "width": 14.14, "height": 14.14},
}
# All values in FEET

plan_state: FloorPlanState
# Used only for room labels / metadata. Do NOT use for geometry.
# Architecture rule: CAD kernel never reads conversational state.
# Only read: plan_state.rooms[room_id].name, plan_state.rooms[room_id].room_type
```

### Output

```python
Path  # absolute path to .FCStd file
# e.g. /tmp/cognitect/plans/{plan_id}/{timestamp}.FCStd
```

### FreeCAD script I/O (stdin/stdout protocol)

```python
# generator.py → subprocess stdin (JSON):
{
    "coordinate_matrix": { ... },  # same as above
    "output_path": "/tmp/cognitect/plans/abc123/plan.FCStd",
    "room_labels": {"living_room": "Living Room", ...},
    "wall_height_ft": 9.0
}

# subprocess stdout signals:
"FREECAD_OK: /path/to/file.FCStd"    # success
"FREECAD_ERROR: <message>"           # failure (also stderr)
```

---

## 4. Constraints

### SLA
- Target: 5–15 seconds for a 5-room plan
- Hard timeout: 30 seconds (subprocess.run timeout=30)
- If timeout exceeded → raise `CADGenerationError("FreeCAD timed out after 30s")`

### Architecture rules (STRICT)
- `generator.py` is a subprocess wrapper. It must NOT import FreeCAD.
- `generate_plan.py` runs inside the FreeCAD AppImage's isolated Python 3.11.
  It has NO access to the main venv. Do NOT use `import anthropic`, `import pydantic`, etc.
- All data exchange between generator.py and generate_plan.py MUST be via stdin JSON.
- Do NOT use `--appimage-mount` — use `--appimage-extract-and-run` for headless use.

### FreeCAD invocation pattern

```python
import subprocess, json, tempfile, os
from pathlib import Path

script_path = Path(__file__).parent / "freecad_scripts" / "generate_plan.py"
appimage = self.appimage_path

result = subprocess.run(
    [str(appimage), "--appimage-extract-and-run", "--headless",
     "-c", f"import sys; exec(open('{script_path}').read())"],
    input=json.dumps(payload),
    capture_output=True,
    text=True,
    timeout=30,
)
```

### Output path convention
```python
import tempfile, os
out_dir = Path(os.environ.get("COGNITECT_OUTPUT_DIR", "/tmp/cognitect/plans"))
out_dir.mkdir(parents=True, exist_ok=True)
output_path = out_dir / f"{plan_state.plan_id}_{int(time.time())}.FCStd"
```

---

## 5. Known Pitfalls

### 5.1 FreeCAD coordinate system
- FreeCAD uses **millimetres** internally. Convert feet → mm: `mm = feet * 304.8`
- Origin is bottom-left `(0, 0, 0)`. Z=0 is the floor.

### 5.2 Non-orthogonal walls
- The coordinate matrix guarantees axis-aligned rectangles (width/height). No diagonal walls.
- Use `Arch.makeWall()` with `Part::Line` base objects, NOT `Draft.makeRectangle()` for walls.
- `Draft.makeRectangle` creates a flat face with no thickness — walls need thickness (typ. 6 inches = 152.4mm).

### 5.3 AppImage Python isolation
- The FreeCAD AppImage ships its own Python 3.11 with its own `sys.path`.
- `generate_plan.py` cannot `import kiwisolver`, `import pydantic`, etc.
- Only these imports are safe inside the script: `import FreeCAD`, `import Part`, 
  `import Draft`, `import Arch`, `import json`, `import sys`, `import os`, `import math`.

### 5.4 AppImage executable bit
- The AppImage must be `chmod +x` before invocation. Check in `__init__`:
  ```python
  if not os.access(self.appimage_path, os.X_OK):
      os.chmod(self.appimage_path, 0o755)
  ```

### 5.5 `--headless` flag availability
- FreeCAD 1.0 AppImage supports `--headless` for no-GUI mode.
- If `--headless` is NOT supported (older builds), use `DISPLAY=""` env var instead.
- Always set env: `env = {**os.environ, "DISPLAY": "", "QT_QPA_PLATFORM": "offscreen"}`

### 5.6 Document save path
- `doc.saveAs(path)` requires the path to NOT already exist or FreeCAD will silently overwrite.
- Always generate a unique output path using `time.time()` or `uuid`.

### 5.7 Arch workbench availability
- `import Arch` in FreeCAD 1.0 requires: `FreeCADGui` to be imported first OR use `Part` only.
- For headless mode, `FreeCADGui` is NOT available. Use `Part::Box` for room geometry.
- Safe headless approach: build floor slabs as `Part::Box` objects (thin boxes, 100mm height).

### 5.8 stdout parsing
- FreeCAD prints startup messages to stdout before script output.
- Parse for the `FREECAD_OK:` token anywhere in stdout, not just first line:
  ```python
  for line in result.stdout.splitlines():
      if line.startswith("FREECAD_OK:"):
          return Path(line.split(":", 1)[1].strip())
  ```

---

## 6. Expected Output

### Files to modify/create:
1. **`engine/cad_generator/generator.py`** — implement `CADGenerator.generate()`
2. **`engine/cad_generator/freecad_scripts/generate_plan.py`** — implement `generate_floor_plan()`

### `generator.py` — full implementation outline:
```python
def generate(self, coordinate_matrix: dict, plan_state: FloorPlanState) -> Path:
    t0 = time.perf_counter()
    
    # 1. Verify AppImage exists and is executable
    if not self.appimage_path.exists():
        raise CADGenerationError(f"FreeCAD AppImage not found: {self.appimage_path}")
    if not os.access(self.appimage_path, os.X_OK):
        os.chmod(self.appimage_path, 0o755)
    
    # 2. Prepare output path
    out_dir = Path(os.environ.get("COGNITECT_OUTPUT_DIR", "/tmp/cognitect/plans"))
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{plan_state.plan_id}_{int(time.time())}.FCStd"
    
    # 3. Build payload
    room_labels = {rid: spec.name for rid, spec in plan_state.rooms.items()}
    payload = json.dumps({
        "coordinate_matrix": coordinate_matrix,
        "output_path": str(output_path),
        "room_labels": room_labels,
        "wall_height_ft": 9.0,
    })
    
    # 4. Run FreeCAD subprocess
    script_path = Path(__file__).parent / "freecad_scripts" / "generate_plan.py"
    env = {**os.environ, "DISPLAY": "", "QT_QPA_PLATFORM": "offscreen"}
    
    try:
        result = subprocess.run(
            [str(self.appimage_path), "--appimage-extract-and-run", "--headless",
             "-c", f"import sys; exec(open('{script_path}').read())"],
            input=payload, capture_output=True, text=True,
            timeout=30, env=env,
        )
    except subprocess.TimeoutExpired:
        raise CADGenerationError("FreeCAD timed out after 30s", exit_code=-1)
    
    # 5. Parse output
    for line in result.stdout.splitlines():
        if line.startswith("FREECAD_OK:"):
            output_file = Path(line.split(":", 1)[1].strip())
            elapsed = time.perf_counter() - t0
            logger.info("CAD generation complete: %s (%.1fs)", output_file, elapsed)
            return output_file
    
    # 6. Error handling
    raise CADGenerationError(
        f"FreeCAD generation failed (exit {result.returncode})",
        stderr=result.stderr[-2000:],
        exit_code=result.returncode,
    )
```

### `generate_plan.py` — full implementation outline:
```python
import FreeCAD, Part, json, sys, math

FT_TO_MM = 304.8
WALL_THICKNESS_MM = 152.4  # 6 inches
WALL_HEIGHT_FT = 9.0

data = json.loads(sys.stdin.read())
coordinate_matrix = data["coordinate_matrix"]
output_path = data["output_path"]
room_labels = data.get("room_labels", {})
wall_height_ft = data.get("wall_height_ft", WALL_HEIGHT_FT)

doc = FreeCAD.newDocument("FloorPlan")

for room_id, coords in coordinate_matrix.items():
    x, y = coords["x"] * FT_TO_MM, coords["y"] * FT_TO_MM
    w, h = coords["width"] * FT_TO_MM, coords["height"] * FT_TO_MM
    wall_h = wall_height_ft * FT_TO_MM
    
    # Floor slab (thin box)
    slab = doc.addObject("Part::Box", f"{room_id}_floor")
    slab.Placement = FreeCAD.Placement(FreeCAD.Vector(x, y, 0), FreeCAD.Rotation())
    slab.Length, slab.Width, slab.Height = w, h, 50  # 50mm slab
    slab.Label = f"{room_labels.get(room_id, room_id)} (floor)"
    
    # Four perimeter walls (as thin Part::Box objects)
    # S wall: y=y, x=x to x+w
    # N wall: y=y+h-thickness
    # W wall: x=x, y=y to y+h
    # E wall: x=x+w-thickness
    for wall_id, wx, wy, wlen, wdep in [
        (f"{room_id}_S", x, y, w, WALL_THICKNESS_MM),
        (f"{room_id}_N", x, y+h-WALL_THICKNESS_MM, w, WALL_THICKNESS_MM),
        (f"{room_id}_W", x, y, WALL_THICKNESS_MM, h),
        (f"{room_id}_E", x+w-WALL_THICKNESS_MM, y, WALL_THICKNESS_MM, h),
    ]:
        wall = doc.addObject("Part::Box", wall_id)
        wall.Placement = FreeCAD.Placement(FreeCAD.Vector(wx, wy, 50), FreeCAD.Rotation())
        wall.Length, wall.Width, wall.Height = wlen, wdep, wall_h

doc.recompute()
doc.saveAs(output_path)
print(f"FREECAD_OK: {output_path}")
```

---

## 7. Do Not Touch

- `engine/intent_parser/` — any file
- `engine/constraint_solver/` — any file
- `api/` — any file
- `tests/` — any file (add new test cases if desired but don't modify existing ones)
- `engine/exporter/` — separate spec in DRAFT_EXPORTER.md
- `pyproject.toml`, `requirements.txt`, `Dockerfile`
