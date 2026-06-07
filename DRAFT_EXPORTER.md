# DRAFT: Plan Exporter — Cursor Composer Implementation Spec

## 1. Task

Implement `engine/exporter/exporter.py::PlanExporter` — all three export methods (`export_dxf`, `export_pdf`, `export_from_matrix`) — using ezdxf to produce DXF files with regulatory annotations (room labels, dimensions, area callouts, title block) and PDF output.

---

## 2. Repo Context

```
engine/
├── exporter/
│   ├── __init__.py
│   └── exporter.py    ← IMPLEMENT THIS (stub exists, all methods raise NotImplementedError)
├── intent_parser/
│   └── schemas.py     ← FloorPlanState — read-only reference
```

**Do not touch:** `engine/intent_parser/`, `engine/constraint_solver/`, `engine/cad_generator/`, `api/`, `tests/`.

---

## 3. Data Shapes

### Input to `export_dxf()`

```python
freecad_model_path: Path  # path to .FCStd file (may not exist if called from export_from_matrix)
metadata: dict
# Required keys:
{
    "plan_id": str,           # e.g. "abc123"
    "project_name": str,      # e.g. "Smith Residence"
    "rooms": {                # coordinate_matrix from constraint solver
        "living_room": {"x": 0.0, "y": 0.0, "width": 20.0, "height": 15.0},
        "kitchen": {"x": 20.0, "y": 0.0, "width": 12.0, "height": 12.5},
        # ... all values in FEET
    },
    "room_labels": {          # Optional: human-readable names
        "living_room": "Living Room",
        "kitchen": "Kitchen",
    },
    "scale": "1/4\" = 1'",    # Optional: display scale string
    "date": "2026-06-07",     # Optional: ISO date
    "north_arrow": True,      # Optional: draw north arrow
}
```

### Output

```python
Path  # absolute path to .dxf file
# Convention: same directory as freecad_model_path, same stem, .dxf extension
# Or for export_from_matrix: /tmp/cognitect/plans/{plan_id}.dxf
```

### `export_pdf()` output

```python
Path  # absolute path to .pdf file
# Convention: same as DXF but .pdf extension
```

---

## 4. Constraints

### SLA
- Total export (DXF + PDF): < 60s
- DXF alone: < 10s for a 10-room plan
- PDF rendering: < 30s

### ezdxf version
- Use `ezdxf >= 1.1.0` (already installed)
- Target DXF version: `R2010` (broad AutoCAD/BricsCAD compatibility)
- Do NOT use `R2000` (missing MTEXT support for multi-line annotations)

### Architecture rules
- The exporter reads ONLY from `coordinate_matrix` (in metadata["rooms"]) and optional room labels.
- It does NOT read FloorPlanState directly (no LLM state).
- It does NOT call any external APIs.

---

## 5. DXF Layer Structure

Use these exact layer names (architects and regulators expect them):

| Layer Name      | Color Index | Description                                |
|-----------------|-------------|--------------------------------------------|
| `A-WALL`        | 7 (white)   | Room perimeter walls (LWPOLYLINE)          |
| `A-ROOM`        | 3 (green)   | Room fill / hatch (optional)               |
| `A-DIMS`        | 2 (yellow)  | Linear dimensions (room width/height)      |
| `A-TEXT`        | 7 (white)   | Room labels (MTEXT)                        |
| `A-ANNOT`       | 1 (red)     | Area callouts (MTEXT: "300 SF")            |
| `A-TITLE`       | 7 (white)   | Title block (lower-right corner)           |
| `A-NORTH`       | 7 (white)   | North arrow                                |

---

## 6. DXF Content Requirements

### 6.1 Room walls
Each room in `coordinate_matrix` → one closed `LWPOLYLINE` on `A-WALL`:
```python
msp.add_lwpolyline(
    [(x, y), (x+w, y), (x+w, y+h), (x, y+h)],
    close=True,
    dxfattribs={"layer": "A-WALL", "lineweight": 50}  # 0.5mm wall line
)
```
All coordinates in feet (ezdxf treats units as "architectural feet" — 1 unit = 1 foot).

### 6.2 Room labels
Centered MTEXT in each room:
```python
label_x = x + w / 2
label_y = y + h / 2 + 0.5  # slightly above center
msp.add_mtext(
    room_labels.get(room_id, room_id.replace("_", " ").title()),
    dxfattribs={"layer": "A-TEXT", "char_height": 0.75, "insert": (label_x, label_y)}
)
```

### 6.3 Area callouts
Below the label, add area in sqft:
```python
area = w * h
area_text = f"{area:.0f} SF"
msp.add_mtext(area_text, dxfattribs={
    "layer": "A-ANNOT", "char_height": 0.5,
    "insert": (label_x, label_y - 0.8)
})
```

### 6.4 Linear dimensions
Add horizontal and vertical dimensions for each room:
```python
# Horizontal (width)
msp.add_linear_dim(
    base=(x, y - 3),     # dimension line position (3 ft below room)
    p1=(x, y),
    p2=(x + w, y),
    dimstyle="Standard",
).render()

# Vertical (height)  
msp.add_linear_dim(
    base=(x - 3, y),     # 3 ft to left of room
    p1=(x, y),
    p2=(x, y + h),
    angle=90,
    dimstyle="Standard",
).render()
```

### 6.5 Title block
Simple title block in lower-right corner of the drawing extents:

```python
# Compute drawing extents
all_x = [c["x"] + c["width"] for c in rooms.values()]
all_y = [c["y"] + c["height"] for c in rooms.values()]
max_x, max_y = max(all_x), max(all_y)

# Title block at (max_x + 2, 0) → (max_x + 22, 8)
tb_x, tb_y = max_x + 2, 0
msp.add_lwpolyline(
    [(tb_x, tb_y), (tb_x+20, tb_y), (tb_x+20, tb_y+8), (tb_x, tb_y+8)],
    close=True, dxfattribs={"layer": "A-TITLE"}
)
msp.add_mtext(metadata.get("project_name", "Untitled"), dxfattribs={
    "layer": "A-TITLE", "char_height": 1.0, "insert": (tb_x+1, tb_y+6)
})
msp.add_mtext(f"Plan ID: {metadata['plan_id']}", dxfattribs={
    "layer": "A-TITLE", "char_height": 0.6, "insert": (tb_x+1, tb_y+4.5)
})
msp.add_mtext(f"Date: {metadata.get('date', 'N/A')}", dxfattribs={
    "layer": "A-TITLE", "char_height": 0.6, "insert": (tb_x+1, tb_y+3.5)
})
msp.add_mtext(f"Scale: {metadata.get('scale', '1/4\" = 1-0\"')}", dxfattribs={
    "layer": "A-TITLE", "char_height": 0.6, "insert": (tb_x+1, tb_y+2.5)
})
```

### 6.6 Document units
```python
doc = ezdxf.new("R2010")
doc.units = 1  # 1 = inches... actually use:
from ezdxf.enums import InsertUnits
doc.header["$INSUNITS"] = 2  # feet
doc.header["$MEASUREMENT"] = 0  # imperial
```

---

## 7. PDF Export

Two approaches (try in order):

### Option A: ezdxf matplotlib backend (preferred, no extra deps)
```python
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

fig = plt.figure(figsize=(24, 18), dpi=150)
ax = fig.add_axes([0, 0, 1, 1])
ctx = RenderContext(doc)
backend = MatplotlibBackend(ax)
Frontend(ctx, backend).draw_layout(doc.modelspace(), finalize=True)
pdf_path = dxf_path.with_suffix(".pdf")
with PdfPages(str(pdf_path)) as pdf:
    pdf.savefig(fig, bbox_inches="tight")
plt.close(fig)
return pdf_path
```

### Option B: ezdxf svg + reportlab (fallback)
```python
# Only if matplotlib approach fails
from ezdxf.addons.drawing import svg
# ... render to SVG then convert via reportlab or cairosvg
```

---

## 8. Known Pitfalls

### 8.1 ezdxf MTEXT encoding
- `MTEXT` requires content to be `str`, not `bytes`. Always `str(text)`.
- Multi-line text: use `\P` as newline separator in MTEXT content (not `\n`).

### 8.2 Dimension style
- `add_linear_dim()` defaults to "Standard" dimstyle. This exists in a new ezdxf doc by default.
- Annotative dimensions require `doc.dimstyles.get("Standard").dxf.dimscale = 0`.
- Always call `.render()` on the dim object to generate the actual geometry.

### 8.3 Coordinate system
- ezdxf uses the same coordinate space as the coordinate matrix (both in feet here).
- DXF Y-axis: positive is UP. The coordinate matrix also uses bottom-left origin.
- No coordinate transform needed.

### 8.4 Output directory
```python
import os
output_path = Path(os.environ.get("COGNITECT_OUTPUT_DIR", "/tmp/cognitect/plans"))
output_path.mkdir(parents=True, exist_ok=True)
dxf_path = output_path / f"{metadata['plan_id']}.dxf"
```

### 8.5 matplotlib not in requirements
- matplotlib is needed for PDF export. Add to requirements.txt: `matplotlib>=3.8.0`
- Or make PDF export optional: catch `ImportError` and raise `ExportError("matplotlib required for PDF")`

### 8.6 Large plans and dimension clutter
- For plans > 10 rooms, dimension every-other room only (or use a flag `add_dims=True/False`).
- Overlapping dimensions are a known issue — dimension offset should scale with plan size.

---

## 9. Expected Output — Files to Modify

1. **`engine/exporter/exporter.py`** — implement all three methods:
   - `export_dxf(freecad_model_path, metadata) → Path`
   - `export_pdf(freecad_model_path, metadata) → Path`
   - `export_from_matrix(coordinate_matrix, metadata) → Path` (shortcut, no FCStd needed)

2. **`requirements.txt`** — add `matplotlib>=3.8.0` for PDF rendering

---

## 10. Do Not Touch

- `engine/intent_parser/` — any file
- `engine/constraint_solver/` — any file
- `engine/cad_generator/` — any file (separate spec)
- `api/` — any file
- `tests/` — don't modify existing tests (add `tests/test_exporter_integration.py` if desired)
- `pyproject.toml`, `Dockerfile`, `docker-compose.yml`
