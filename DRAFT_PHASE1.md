# DRAFT: Cognitect Phase 1 — CAD Generator + Exporter + E2E Pipeline

> **For:** Cursor Composer  
> **Repo:** `/data/workspace/cognitect` (ybolishetti/cognitect on GitHub)  
> **Phase 1 goal:** Working headless FreeCAD + kiwisolver + ezdxf pipeline — from NL input to a DXF file on disk  
> **Do NOT touch:** `engine/intent_parser/`, `engine/constraint_solver/`, `engine/intent_parser/schemas.py`, `tests/test_intent_parser.py`, `tests/test_constraint_solver.py`

---

## What You're Building

Three things, in this order:

1. **`engine/cad_generator/generator.py`** — implement `CADGenerator.generate()`
2. **`engine/cad_generator/freecad_scripts/generate_plan.py`** — implement `generate_floor_plan()` (runs inside FreeCAD's Python env)
3. **`engine/exporter/exporter.py`** — implement all three methods: `export_dxf()`, `export_pdf()`, `export_from_matrix()`
4. **`tests/test_phase1_e2e.py`** — integration test that chains solver → CAD generator → exporter
5. **`engine/setup_env.py`** — one-time env setup script that extracts libGL if not present

---

## Environment Context (Critical)

This server is a headless EC2 instance with **no system libGL.so.1**. FreeCAD's Arch workbench needs it. Workaround already proven working:

```bash
# Download and extract libgl1 deb (no sudo needed)
apt-get download libgl1 libglx0 libglvnd0
dpkg -x libgl1_*.deb /tmp/libgl1_extract/
dpkg -x libglx0_*.deb /tmp/libgl1_extract/
dpkg -x libglvnd0_*.deb /tmp/libgl1_extract/
```

Then set env for all FreeCAD calls:
```python
env = {
    **os.environ,
    "LD_LIBRARY_PATH": "/tmp/libgl1_extract/usr/lib/x86_64-linux-gnu",
    "QT_QPA_PLATFORM": "offscreen",
}
```

**FreeCAD binary location:** `/data/workspace/cognitect/squashfs-root/usr/bin/freecadcmd`  
**FreeCAD AppImage:** `/data/workspace/freecad/FreeCAD.AppImage`  
**FreeCAD version:** 1.0.0 (confirmed working headless with Part, Draft, Arch workbenches)

The `/tmp/libgl1_extract/` dir is ephemeral. The `CADGenerator` must check for it and re-extract if missing.

---

## Task 1: `engine/setup_env.py`

Create this new file. It ensures the libGL libs are available before any FreeCAD call.

```python
"""
setup_env.py — ensure FreeCAD runtime dependencies are available.

Called by CADGenerator before any subprocess invocation.
Idempotent: safe to call multiple times.
"""
import os
import subprocess
import logging

LIBGL_TARGET = "/tmp/libgl1_extract/usr/lib/x86_64-linux-gnu/libGL.so.1"
LIBGL_EXTRACT_DIR = "/tmp/libgl1_extract"

logger = logging.getLogger(__name__)

def ensure_libgl() -> str:
    """
    Ensure libGL.so.1 is available for FreeCAD.
    Downloads and extracts libgl1 deb if not present.
    
    Returns:
        Path to the directory containing libGL.so.1.
    
    Raises:
        RuntimeError: If download or extraction fails.
    """
    if os.path.exists(LIBGL_TARGET):
        return LIBGL_EXTRACT_DIR

    logger.info("libGL.so.1 not found — downloading and extracting...")
    
    # Download debs
    debs = ["libgl1", "libglx0", "libglvnd0"]
    for pkg in debs:
        result = subprocess.run(
            ["apt-get", "download", pkg],
            capture_output=True, text=True, cwd="/tmp"
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to download {pkg}: {result.stderr}")
    
    # Extract debs
    import glob
    for deb in glob.glob("/tmp/*.deb"):
        result = subprocess.run(
            ["dpkg", "-x", deb, LIBGL_EXTRACT_DIR],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to extract {deb}: {result.stderr}")
    
    if not os.path.exists(LIBGL_TARGET):
        raise RuntimeError(f"libGL extraction completed but {LIBGL_TARGET} not found")
    
    logger.info("libGL ready at %s", LIBGL_EXTRACT_DIR)
    return LIBGL_EXTRACT_DIR
```

---

## Task 2: `engine/cad_generator/generator.py`

Replace the `NotImplementedError` stub. Keep the class docstring and error classes.

### `CADGenerator.generate()` implementation:

```python
def generate(self, coordinate_matrix: dict, plan_state: FloorPlanState) -> Path:
    """
    Run FreeCAD headless subprocess to generate a 3D BRep model.
    Returns Path to generated .FCStd file.
    SLA: 5–15s
    """
    import json
    import os
    import subprocess
    import tempfile
    from engine.setup_env import ensure_libgl
    
    # 1. Ensure libGL is available
    libgl_dir = ensure_libgl()
    
    # 2. Verify FreeCAD binary exists
    if not self.FREECADCMD.exists():
        raise CADGenerationError(
            f"FreeCAD binary not found: {self.FREECADCMD}. "
            "Is the squashfs-root extracted?",
            exit_code=1,
        )
    
    # 3. Write coordinate_matrix + output path to a temp input JSON file
    output_dir = Path(tempfile.gettempdir()) / "cognitect_output"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{plan_state.plan_id}.FCStd"
    
    payload = {
        "coordinate_matrix": coordinate_matrix,
        "output_path": str(output_path),
        "plan_id": plan_state.plan_id,
        "rooms": {
            room_id: {
                "name": spec.name,
                "room_type": spec.room_type,
            }
            for room_id, spec in plan_state.rooms.items()
        },
    }
    
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(payload, f)
        input_json_path = f.name
    
    # 4. Build the FreeCAD script invocation command
    script_path = Path(__file__).parent / "freecad_scripts" / "generate_plan.py"
    
    env = {
        **os.environ,
        "LD_LIBRARY_PATH": f"{libgl_dir}/usr/lib/x86_64-linux-gnu",
        "QT_QPA_PLATFORM": "offscreen",
    }
    
    cmd = [
        str(self.FREECADCMD),
        "-c",
        f"import json, sys; sys.stdin = open('{input_json_path}'); exec(open('{script_path}').read())"
    ]
    
    # 5. Run FreeCAD subprocess
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=str(self.SQUASHFS_ROOT),
        )
    except subprocess.TimeoutExpired:
        raise CADGenerationError(
            f"FreeCAD subprocess timed out after 30s for plan {plan_state.plan_id}",
            exit_code=-1,
        )
    finally:
        # Clean up temp input file
        try:
            os.unlink(input_json_path)
        except OSError:
            pass
    
    # 6. Check for success sentinel in stdout
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    
    if result.returncode != 0 or "FREECAD_OK:" not in stdout:
        raise CADGenerationError(
            f"FreeCAD subprocess failed for plan {plan_state.plan_id}",
            stderr=stderr,
            exit_code=result.returncode,
        )
    
    # 7. Parse output path from sentinel line
    for line in stdout.splitlines():
        if line.startswith("FREECAD_OK:"):
            out_path = Path(line.split(":", 1)[1].strip())
            if out_path.exists():
                return out_path
    
    raise CADGenerationError(
        f"FreeCAD reported OK but output file not found. stdout: {stdout[:200]}",
        stderr=stderr,
        exit_code=0,
    )
```

---

## Task 3: `engine/cad_generator/freecad_scripts/generate_plan.py`

Implement `generate_floor_plan()`. This runs **inside FreeCAD's Python 3.11 environment** — it has access to `FreeCAD`, `Part`, `Draft`, `Arch` but NOT to the main venv.

Replace the stub body with:

```python
def generate_floor_plan(coordinate_matrix: dict, output_path: str, metadata: dict = None) -> None:
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
    WALL_HEIGHT_MM = 9.0 * FT_TO_MM   # 9ft ceiling = 2743.2mm
    WALL_WIDTH_MM = 200.0              # 200mm wall thickness (standard)

    doc = FreeCAD.newDocument("FloorPlan")
    
    room_names = {}
    if metadata and "rooms" in metadata:
        room_names = {rid: info.get("name", rid) for rid, info in metadata["rooms"].items()}

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
            FreeCAD.Rotation()
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
```

---

## Task 4: `engine/exporter/exporter.py`

Implement all three methods. Remove all `NotImplementedError` raises.

### `export_from_matrix()` — primary method, no FreeCAD dependency:

```python
def export_from_matrix(self, coordinate_matrix: dict, metadata: dict) -> Path:
    """
    Export directly from coordinate matrix to DXF.
    coordinate_matrix: {room_id: {x, y, width, height}} in feet
    metadata: {"plan_id": str, "project_name": str, "rooms": {room_id: {"name": str, "room_type": str}}, ...}
    Returns: Path to .dxf file
    """
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    from pathlib import Path
    import tempfile, datetime
    
    plan_id = metadata.get("plan_id", "plan")
    project_name = metadata.get("project_name", "Cognitect Floor Plan")
    room_info = metadata.get("rooms", {})
    
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 0  # unitless (we use feet)
    
    msp = doc.modelspace()
    
    # Create layers
    doc.layers.add("WALLS", color=7)        # white/black
    doc.layers.add("DIMENSIONS", color=3)   # green
    doc.layers.add("ANNOTATIONS", color=1)  # red
    doc.layers.add("TITLE_BLOCK", color=5)  # blue
    
    for room_id, coords in coordinate_matrix.items():
        x = coords["x"]
        y = coords["y"]
        w = coords["width"]
        h = coords["height"]
        area = w * h
        name = room_info.get(room_id, {}).get("name", room_id)
        
        # Room outline polyline (closed, on WALLS layer)
        msp.add_lwpolyline(
            [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
            close=True,
            dxfattribs={"layer": "WALLS", "lineweight": 50},
        )
        
        # Room label (centered in room, on ANNOTATIONS layer)
        cx = x + w / 2
        cy = y + h / 2
        label_height = min(w, h) * 0.08  # 8% of smallest dimension
        label_height = max(label_height, 0.5)  # at least 0.5ft tall text
        
        msp.add_text(
            name,
            dxfattribs={
                "layer": "ANNOTATIONS",
                "height": label_height,
                "insert": (cx, cy + label_height * 0.5),
                "halign": 1,  # center
                "valign": 2,  # middle
            },
        )
        
        # Area label
        area_text = f"{area:.0f} sqft"
        msp.add_text(
            area_text,
            dxfattribs={
                "layer": "ANNOTATIONS",
                "height": label_height * 0.7,
                "insert": (cx, cy - label_height * 0.5),
                "halign": 1,
                "valign": 2,
            },
        )
        
        # Dimension: width (horizontal)
        msp.add_linear_dim(
            base=(x, y - 2.0),       # dimension line 2ft below
            p1=(x, y),
            p2=(x + w, y),
            angle=0,
            dxfattribs={"layer": "DIMENSIONS"},
        )
        
        # Dimension: height (vertical)
        msp.add_linear_dim(
            base=(x - 2.0, y),       # dimension line 2ft to the left
            p1=(x, y),
            p2=(x, y + h),
            angle=90,
            dxfattribs={"layer": "DIMENSIONS"},
        )
    
    # Title block (simple — bottom left corner)
    tb_y = -8.0  # below plan
    msp.add_text(
        project_name,
        dxfattribs={"layer": "TITLE_BLOCK", "height": 1.5, "insert": (0, tb_y)},
    )
    msp.add_text(
        f"Plan ID: {plan_id}",
        dxfattribs={"layer": "TITLE_BLOCK", "height": 0.8, "insert": (0, tb_y - 2)},
    )
    msp.add_text(
        f"Generated: {datetime.date.today().isoformat()}",
        dxfattribs={"layer": "TITLE_BLOCK", "height": 0.8, "insert": (0, tb_y - 3)},
    )
    
    # Save
    output_dir = Path(tempfile.gettempdir()) / "cognitect_output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / f"{plan_id}.dxf"
    
    doc.saveas(str(out_path))
    logger.info("DXF exported: %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path
```

### `export_dxf()` — delegates to `export_from_matrix()` using FreeCAD model metadata:

```python
def export_dxf(self, freecad_model_path: Path, metadata: dict) -> Path:
    """
    Export from an existing .FCStd file to DXF.
    For now: if coordinate_matrix is in metadata["rooms"], use it directly.
    """
    if "rooms" not in metadata:
        raise ExportError("metadata must contain 'rooms' key with coordinate matrix")
    return self.export_from_matrix(metadata.get("coordinate_matrix", metadata["rooms"]), metadata)
```

### `export_pdf()` — DXF → PDF via ezdxf's matplotlib backend:

```python
def export_pdf(self, freecad_model_path: Path, metadata: dict) -> Path:
    """
    Export floor plan to PDF via DXF → matplotlib → PDF.
    """
    from pathlib import Path
    
    # First get the DXF
    dxf_path = self.export_dxf(freecad_model_path, metadata)
    pdf_path = dxf_path.with_suffix(".pdf")
    
    try:
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        import matplotlib.pyplot as plt
        
        doc = ezdxf.readfile(str(dxf_path))
        msp = doc.modelspace()
        
        fig = plt.figure(figsize=(17, 11))  # ANSI B sheet
        ax = fig.add_axes([0.05, 0.05, 0.90, 0.90])
        
        ctx = RenderContext(doc)
        backend = MatplotlibBackend(ax)
        Frontend(ctx, backend).draw_layout(msp)
        
        fig.savefig(str(pdf_path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        
    except ImportError:
        # matplotlib not available — write a minimal PDF note
        logger.warning("matplotlib not available for PDF export; writing placeholder")
        pdf_path.write_text(f"PDF export requires matplotlib. DXF available at: {dxf_path}")
    
    return pdf_path
```

---

## Task 5: `tests/test_phase1_e2e.py`

Create this new test file. It tests the full chain: solver → CAD generator → exporter.

```python
"""
Phase 1 E2E integration test.

Chain: FloorPlanState → ConstraintSolver → CADGenerator → PlanExporter

Marks:
  @pytest.mark.slow — any test that invokes FreeCAD subprocess (~5-15s each)
  
Run fast tests only: pytest -m "not slow"
Run all: pytest tests/test_phase1_e2e.py
"""
from __future__ import annotations

import json
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
        # Solve
        matrix = solver.solve(two_room_state)
        assert len(matrix) == 2
        
        # CAD
        fcstd_path = cad_generator.generate(matrix, two_room_state)
        assert fcstd_path.exists()
        
        # Export
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
```

---

## Running Tests

```bash
cd /data/workspace/cognitect

# Run fast tests only (no FreeCAD, <30s total):
PYTHONPATH=. /usr/bin/python3.12 -m pytest tests/ -m "not slow" -v

# Run Phase 1 E2E (including FreeCAD, may take ~2min):
PYTHONPATH=. /usr/bin/python3.12 -m pytest tests/test_phase1_e2e.py -v

# Run full suite:
PYTHONPATH=. /usr/bin/python3.12 -m pytest tests/ -v
```

**Note:** The venv's Python binary hangs on import (network init issue). Always use `/usr/bin/python3.12` directly with `PYTHONPATH` set to the venv's site-packages.

The correct way to run tests:
```bash
PYTHONPATH=/data/workspace/cognitect/.venv/lib/python3.12/site-packages:/data/workspace/cognitect \
/usr/bin/python3.12 -m pytest tests/ -v
```

---

## Success Criteria

Phase 1 is done when:

1. `pytest tests/ -m "not slow"` — all pass (includes new exporter tests)
2. `pytest tests/test_phase1_e2e.py::TestFullPipeline::test_solver_to_dxf_no_freecad` — passes
3. `pytest tests/test_phase1_e2e.py -m slow` — all pass (FreeCAD subprocess working)
4. A valid DXF file with room outlines, labels, and dimensions is produced
5. A valid .FCStd file with Arch walls is produced

---

## Pitfalls

1. **FreeCAD Python env is isolated** — `generate_plan.py` runs inside FreeCAD's Python 3.11 and cannot import anything from the main venv. No pydantic, no anthropic, no fastapi. Only stdlib + FreeCAD builtins.

2. **Stdin for FreeCAD** — Don't pipe JSON via stdin to `freecadcmd -c`. Use a temp JSON file on disk and read it inside the script. The `-c` flag's interaction with stdin is unreliable.

3. **LD_LIBRARY_PATH must be set** — every FreeCAD subprocess call needs `LD_LIBRARY_PATH=/tmp/libgl1_extract/usr/lib/x86_64-linux-gnu`. Without it, PySide2 fails to import and Arch/Draft workbenches are unavailable.

4. **`/tmp/libgl1_extract/` is ephemeral** — always call `ensure_libgl()` before any FreeCAD invocation.

5. **`QT_QPA_PLATFORM=offscreen`** — required on headless server. Without it, Qt tries to open a display and crashes.

6. **ezdxf dimension rendering** — ezdxf's `add_linear_dim()` creates associative dimensions that need `doc.entitydb` to be called correctly. If you get errors, fall back to `add_line()` + `add_text()` for dimension lines.

7. **FreeCAD Draft.makeWire** — takes a list of `FreeCAD.Vector` objects, not tuples. Always wrap coordinates in `FreeCAD.Vector()`.

8. **Do not use the venv Python** — the venv's Python binary at `.venv/bin/python` hangs on startup in this environment. Use `/usr/bin/python3.12` with `PYTHONPATH` set instead.

---

## Files to Create/Modify

| File | Action |
|---|---|
| `engine/setup_env.py` | **CREATE** |
| `engine/cad_generator/generator.py` | **MODIFY** — replace `generate()` stub |
| `engine/cad_generator/freecad_scripts/generate_plan.py` | **MODIFY** — replace `generate_floor_plan()` stub |
| `engine/exporter/exporter.py` | **MODIFY** — replace all 3 stubs |
| `tests/test_phase1_e2e.py` | **CREATE** |

Do NOT touch: `engine/intent_parser/`, `engine/constraint_solver/`, `engine/intent_parser/schemas.py`, `tests/test_intent_parser.py`, `tests/test_constraint_solver.py`
