# DRAFT: Cognitect Phase 4 — Demo Frontend (Canvas UI + Plan Upload + NL Edit)

> **For:** Cursor Composer
> **Repo:** `/data/workspace/cognitect` (ybolishetti/cognitect on GitHub)
> **Branch:** Create and work on `cursor/phase4-demo-frontend`
> **Phase 4 goal:** A working demo UI — type natural language, watch a floor plan draw itself. Upload an existing plan and edit it with NL. Export to DXF/PDF. Demo-ready, not production-ready.
> **Do NOT touch:** `engine/`, `tests/` (existing tests must stay green)
> **Tests must pass:** `pytest -m "not slow"` — all 79 existing tests must stay green

---

## What You're Building

Six things, in this order:

1. **`engine/previewer.py`** — `PlanPreviewer` class: renders a coordinate matrix to PNG using matplotlib
2. **`api/routes/preview.py`** — `GET /plan/{id}/preview` endpoint returning a PNG image
3. **`api/routes/load.py`** — `POST /plan/load` endpoint: seed a plan from uploaded JSON or DXF file
4. **`frontend/`** — Next.js single-page app: canvas + NL input + upload button + export buttons
5. **`tests/test_previewer.py`** — unit tests for PlanPreviewer
6. **`tests/test_load.py`** — unit tests for the load endpoint

---

## Architecture Overview

```
Browser
  │
  ├── POST /plan/new              → creates blank session, returns plan_id
  ├── POST /plan/load             → seeds session from uploaded file, returns plan_id
  ├── POST /plan/{id}/instruct    → NL instruction → updates plan state
  ├── GET  /plan/{id}/preview     → returns PNG image of current plan (for canvas)
  ├── GET  /plan/{id}/state       → returns room list for sidebar
  └── GET  /plan/{id}/export      → returns DXF file download
```

The frontend is a **single HTML page** with a React component tree. No auth. No multi-user. No database persistence (in-memory sessions, same as Phase 2). The goal is a convincing demo, not a production system.

---

## Task 1: `engine/previewer.py`

Create this new file. `PlanPreviewer` renders a `coordinate_matrix` dict to a PNG in memory.

```python
"""
PlanPreviewer — renders a floor plan coordinate matrix to a PNG image.

Uses matplotlib patches to draw rooms as colored rectangles with labels
and dimension annotations.

Architecture rule: PlanPreviewer never calls the LLM, solver, or CAD kernel.
It only reads coordinate_matrix + room metadata.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# One color per room type — warm, readable palette
ROOM_COLORS = {
    "bedroom":  "#AED6F1",  # soft blue
    "bathroom": "#A9DFBF",  # soft green
    "kitchen":  "#FAD7A0",  # warm orange
    "living":   "#F9E79F",  # warm yellow
    "dining":   "#F5CBA7",  # peach
    "hallway":  "#D7DBDD",  # light grey
    "office":   "#D2B4DE",  # lavender
    "garage":   "#BFC9CA",  # cool grey
    "other":    "#EAEDED",  # near-white
}
DEFAULT_COLOR = "#EAEDED"


class PlanPreviewer:
    """
    Renders a floor plan to PNG bytes.

    Usage:
        previewer = PlanPreviewer()
        png_bytes = previewer.render(coordinate_matrix, room_metadata)
    """

    def render(
        self,
        coordinate_matrix: dict,
        room_metadata: dict,
        width_px: int = 900,
        height_px: int = 700,
        dpi: int = 100,
        title: Optional[str] = None,
    ) -> bytes:
        """
        Render the floor plan to PNG bytes.

        Args:
            coordinate_matrix: {room_id: {x, y, width, height}} in feet
            room_metadata: {room_id: {name, room_type}} — for labels and colors
            width_px: Output image width in pixels
            height_px: Output image height in pixels
            dpi: Render DPI
            title: Optional plan title shown at top

        Returns:
            PNG image as bytes
        """
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend — must be before pyplot import
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyBboxPatch

        fig_w = width_px / dpi
        fig_h = height_px / dpi
        fig, ax = plt.subplots(1, 1, figsize=(fig_w, fig_h))

        if not coordinate_matrix:
            ax.text(0.5, 0.5, "No rooms yet.\nType an instruction below.",
                    ha="center", va="center", fontsize=14, color="#888",
                    transform=ax.transAxes)
            ax.set_axis_off()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                        facecolor="#F8F9FA")
            plt.close(fig)
            buf.seek(0)
            return buf.read()

        # Compute bounding box for auto-scaling
        all_x = [c["x"] for c in coordinate_matrix.values()]
        all_y = [c["y"] for c in coordinate_matrix.values()]
        all_r = [c["x"] + c["width"] for c in coordinate_matrix.values()]
        all_t = [c["y"] + c["height"] for c in coordinate_matrix.values()]
        min_x, min_y = min(all_x), min(all_y)
        max_x, max_y = max(all_r), max(all_t)
        span_x = max_x - min_x or 1
        span_y = max_y - min_y or 1
        pad = max(span_x, span_y) * 0.08

        ax.set_xlim(min_x - pad, max_x + pad)
        ax.set_ylim(min_y - pad, max_y + pad)
        ax.set_aspect("equal")
        ax.set_facecolor("#F8F9FA")
        ax.grid(False)

        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

        for room_id, coords in coordinate_matrix.items():
            x, y, w, h = coords["x"], coords["y"], coords["width"], coords["height"]
            meta = room_metadata.get(room_id, {})
            room_type = meta.get("room_type", "other")
            name = meta.get("name", room_id.replace("_", " ").title())
            color = ROOM_COLORS.get(room_type, DEFAULT_COLOR)

            # Room rectangle
            rect = mpatches.FancyBboxPatch(
                (x, y), w, h,
                boxstyle="square,pad=0",
                linewidth=1.8,
                edgecolor="#2C3E50",
                facecolor=color,
                alpha=0.85,
                zorder=2,
            )
            ax.add_patch(rect)

            # Room name label
            font_size = max(6, min(11, min(w, h) * 1.8))
            cx, cy = x + w / 2, y + h / 2
            ax.text(cx, cy + 0.15, name,
                    ha="center", va="center",
                    fontsize=font_size, fontweight="bold",
                    color="#1A252F", zorder=3)

            # Area label (smaller, below name)
            area = w * h
            ax.text(cx, cy - 0.35, f"{area:.0f} sqft",
                    ha="center", va="center",
                    fontsize=max(5, font_size - 2),
                    color="#5D6D7E", zorder=3)

            # Dimension ticks: width along bottom edge, height along left edge
            tick_color = "#7F8C8D"
            tick_fs = max(5, font_size - 3)
            ax.annotate(
                "", xy=(x + w, y - pad * 0.4), xytext=(x, y - pad * 0.4),
                arrowprops=dict(arrowstyle="<->", color=tick_color, lw=0.8),
                zorder=1,
            )
            ax.text(cx, y - pad * 0.55, f"{w:.0f}'",
                    ha="center", va="top", fontsize=tick_fs, color=tick_color)

        if title:
            fig.suptitle(title, fontsize=13, fontweight="bold",
                         color="#2C3E50", y=0.98)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                    facecolor="white")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
```

**requirements.txt additions** (append these lines):
```
matplotlib>=3.8.0
Pillow>=10.0.0
```

---

## Task 2: `api/routes/preview.py`

Create this new file.

```python
"""
Preview API route — renders floor plan to PNG.

GET /plan/{plan_id}/preview
  Query params:
    width  (int, default 900) — image width in pixels
    height (int, default 700) — image height in pixels
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from engine.previewer import PlanPreviewer
from api.routes.plan import _PLANS  # shared in-memory session store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plan", tags=["preview"])

_previewer = PlanPreviewer()


@router.get("/{plan_id}/preview")
async def preview_plan(plan_id: str, width: int = 900, height: int = 700):
    """
    Render the current plan state as a PNG image.
    Returns an empty canvas with a prompt message if the plan has no rooms.
    """
    if plan_id not in _PLANS:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")

    manager = _PLANS[plan_id]
    state = manager.state

    coordinate_matrix = state.coordinate_matrix or {}

    # If no coordinate_matrix yet but rooms exist, run solver first
    if not coordinate_matrix and state.rooms:
        try:
            coordinate_matrix = manager.solve()
        except Exception as exc:
            logger.warning("Solver failed during preview: %s", exc)
            coordinate_matrix = {}

    room_metadata = {
        room_id: {"name": spec.name, "room_type": spec.room_type}
        for room_id, spec in state.rooms.items()
    }

    png_bytes = _previewer.render(
        coordinate_matrix=coordinate_matrix,
        room_metadata=room_metadata,
        width_px=width,
        height_px=height,
        title=f"Plan {plan_id}",
    )

    return Response(content=png_bytes, media_type="image/png")
```

---

## Task 3: `api/routes/load.py`

Create this new file. Handles uploading an existing plan (JSON or DXF) and seeding a `PlanManager` session from it.

```python
"""
Plan Load API route — seed a PlanManager from an uploaded file.

POST /plan/load
  Accepts multipart/form-data with a single file field: "file"
  Supported formats:
    - .json  — Cognitect FloorPlanState JSON (our native format)
    - .dxf   — AutoCAD DXF (parsed via ezdxf, best-effort room extraction)

  Returns: {plan_id, room_count, message}
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import ezdxf
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from engine.plan_manager import PlanManager
from engine.intent_parser.schemas import FloorPlanState, RoomSpec, RoomCoordinates
from api.routes.plan import _PLANS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plan", tags=["load"])


class LoadResponse(BaseModel):
    plan_id: str
    room_count: int
    format: str
    message: str


@router.post("/load", response_model=LoadResponse)
async def load_plan(file: UploadFile = File(...)):
    """
    Upload an existing floor plan and create an editable session from it.

    Supported:
      - .json — native FloorPlanState JSON export from Cognitect
      - .dxf  — AutoCAD DXF (rooms extracted from WALLS layer polylines)
    """
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()

    if suffix not in (".json", ".dxf"):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Supported: .json, .dxf"
        )

    raw = await file.read()

    if suffix == ".json":
        return await _load_from_json(raw)
    else:
        return await _load_from_dxf(raw, filename)


async def _load_from_json(raw: bytes) -> LoadResponse:
    """Load from native Cognitect FloorPlanState JSON."""
    try:
        data = json.loads(raw)
        state = FloorPlanState(**data)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid FloorPlanState JSON: {exc}"
        )

    plan_id = state.plan_id or str(uuid.uuid4())[:8]
    manager = PlanManager(plan_id=plan_id)
    manager._state = state  # seed with loaded state
    _PLANS[plan_id] = manager

    logger.info("Loaded JSON plan '%s' — %d rooms", plan_id, len(state.rooms))
    return LoadResponse(
        plan_id=plan_id,
        room_count=len(state.rooms),
        format="json",
        message=f"Loaded plan '{plan_id}' with {len(state.rooms)} room(s). "
                f"Send instructions to /plan/{plan_id}/instruct",
    )


async def _load_from_dxf(raw: bytes, filename: str) -> LoadResponse:
    """
    Parse a DXF file and extract rooms from WALLS layer closed polylines.

    Each closed polyline on the WALLS layer is treated as a room boundary.
    Rooms are named 'Room 1', 'Room 2', etc. and typed as 'other'.
    The user can then rename/retype them via NL instructions.

    Coordinate units: assumed to be feet. If bounding boxes look implausibly
    large (> 500ft on a side), divide by 12 (inches → feet).
    """
    import io

    try:
        doc = ezdxf.read(io.BytesIO(raw))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid DXF file: {exc}")

    msp = doc.modelspace()

    rooms: dict[str, RoomSpec] = {}
    coordinate_matrix: dict[str, dict] = {}
    room_index = 1

    for entity in msp:
        # Only process closed LWPOLYLINEs (room outlines)
        if entity.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
            continue
        if not getattr(entity.dxf, "closed", False) and not getattr(entity, "is_closed", False):
            continue

        try:
            if entity.dxftype() == "LWPOLYLINE":
                points = list(entity.get_points())
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
            else:
                verts = list(entity.vertices)
                xs = [v.dxf.location.x for v in verts]
                ys = [v.dxf.location.y for v in verts]
        except Exception:
            continue

        if len(xs) < 3:
            continue

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        w = x_max - x_min
        h = y_max - y_min

        # Skip degenerate shapes
        if w < 0.1 or h < 0.1:
            continue

        # Heuristic: if any dimension > 500, assume inches and convert to feet
        if w > 500 or h > 500:
            x_min /= 12; x_max /= 12
            y_min /= 12; y_max /= 12
            w /= 12; h /= 12

        room_id = f"room_{room_index}"
        area = round(w * h, 1)

        rooms[room_id] = RoomSpec(
            name=f"Room {room_index}",
            room_type="other",
            area_sqft=area,
        )
        coordinate_matrix[room_id] = {
            "x": round(x_min, 2),
            "y": round(y_min, 2),
            "width": round(w, 2),
            "height": round(h, 2),
        }
        room_index += 1

    if not rooms:
        raise HTTPException(
            status_code=422,
            detail="No closed polylines found in DXF. "
                   "Ensure rooms are drawn as closed LWPOLYLINE entities."
        )

    plan_id = str(uuid.uuid4())[:8]
    state = FloorPlanState(
        plan_id=plan_id,
        rooms=rooms,
        coordinate_matrix=coordinate_matrix,
    )
    manager = PlanManager(plan_id=plan_id)
    manager._state = state
    _PLANS[plan_id] = manager

    logger.info("Loaded DXF plan '%s' — %d rooms extracted", plan_id, len(rooms))
    return LoadResponse(
        plan_id=plan_id,
        room_count=len(rooms),
        format="dxf",
        message=f"Extracted {len(rooms)} room(s) from DXF. "
                f"Rooms are named 'Room 1', 'Room 2', etc. "
                f"Use NL instructions to rename, resize, or rearrange them.",
    )
```

---

## Task 4: Register new routes in `api/main.py`

Open `api/main.py` and add the two new routers. Find the existing router registrations and add:

```python
from api.routes import preview, load

app.include_router(preview.router)
app.include_router(load.router)
```

---

## Task 5: `frontend/` — Next.js Single-Page Demo App

Create a new `frontend/` directory at the repo root. This is a standard Next.js project.

### 5a. `frontend/package.json`

```json
{
  "name": "cognitect-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev --port 3000",
    "build": "next build",
    "start": "next start --port 3000"
  },
  "dependencies": {
    "next": "14.2.3",
    "react": "^18",
    "react-dom": "^18"
  },
  "devDependencies": {
    "@types/node": "^20",
    "@types/react": "^18",
    "typescript": "^5"
  }
}
```

### 5b. `frontend/next.config.js`

```js
/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
```

This proxies all `/api/*` calls from the frontend to the FastAPI backend on port 8000. No CORS config needed.

### 5c. `frontend/app/page.tsx` — The entire demo UI

This is the main page. Build it as a single React component. Here's the complete spec:

**Layout (full screen, two columns):**
- Left column (35%): Controls panel
- Right column (65%): Canvas (floor plan image)

**Left column contains (top to bottom):**

1. **Header** — "Cognitect" in large bold text, subtitle "AI Floor Plan Engine"

2. **New Plan / Upload section:**
   - "New Plan" button — calls `POST /api/plan/new`, stores `plan_id` in state
   - "Upload Plan" button — opens a file picker (`.json, .dxf`), calls `POST /api/plan/load` with the file as `multipart/form-data`, stores returned `plan_id`
   - Show current plan ID in small grey text once a plan is active

3. **Instruction input (shown only when a plan is active):**
   - Textarea with placeholder: `"Describe your floor plan or give an instruction..."`
   - Examples shown below in small grey text:
     - "Add a 300 sqft living room"
     - "Add a kitchen next to the living room, 150 sqft"
     - "Add a master bedroom of 200 sqft"
     - "Make the kitchen bigger"
     - "Remove the hallway"
   - "Send" button (or press Cmd/Ctrl+Enter) — calls `POST /api/plan/{id}/instruct`, then refreshes the preview
   - Show a loading spinner while the request is in flight
   - Show error messages in red if the API returns an error

4. **Room list (shown only when plan has rooms):**
   - Fetched from `GET /api/plan/{id}/state`
   - Simple list: colored dot (matching canvas color per room_type) + room name + area in sqft
   - Updates after each instruction

5. **Export buttons (shown only when plan has rooms):**
   - "Download DXF" — opens `GET /api/plan/{id}/export` in a new tab
   - "Download PDF" — opens `GET /api/plan/{id}/export?format=pdf` in a new tab (note: add format param to export endpoint — see Task 6)

**Right column:**
- Displays `<img>` tag pointing to `GET /api/plan/{id}/preview?width=800&height=600`
- Refreshes the image URL (append `?t=timestamp` to bust cache) after every instruct call
- Shows a placeholder when no plan is active: centered grey text "Start a new plan or upload an existing one"
- Show a subtle loading shimmer while preview is loading

**Styling:**
- Clean, minimal — white background, dark text (#1A252F), accent color #2980B9 (blue)
- Font: system-ui or Inter
- No CSS frameworks — use CSS modules or inline styles
- The canvas image should have a subtle drop shadow
- Responsive down to 900px wide minimum

**State management:** Use React `useState` and `useEffect` only. No Redux, no Zustand.

**Error handling:**
- API errors → show a dismissable red banner at top of left panel
- Network errors → "Could not connect to server. Is the backend running?"

---

## Task 6: Add PDF export support to `api/routes/plan.py`

In the existing export endpoint, add `format` query param support:

Find the existing `export_plan` function and update its signature and logic:

```python
@router.get("/{plan_id}/export")
async def export_plan(plan_id: str, mode: str = "2d", format: str = "dxf"):
    """
    Export the current plan.

    Query params:
      mode=2d (default) — fast DXF from coordinate matrix (no FreeCAD)
      mode=3d           — full FreeCAD 3D model then DXF (5–15s)
      format=dxf        — DXF file (default)
      format=pdf        — PDF via matplotlib rendering
    """
```

For `format=pdf`, call `manager._exporter.export_pdf(Path("/dev/null"), metadata)` (the exporter already has `export_pdf` implemented — it generates DXF first then renders via matplotlib).

Return `FileResponse` with `media_type="application/pdf"` and `filename=f"{plan_id}.pdf"` for PDF.

---

## Task 7: `tests/test_previewer.py`

```python
"""Tests for PlanPreviewer."""
import pytest
from engine.previewer import PlanPreviewer


@pytest.fixture
def previewer():
    return PlanPreviewer()


@pytest.fixture
def sample_matrix():
    return {
        "living_room": {"x": 0, "y": 0, "width": 20, "height": 15},
        "kitchen":     {"x": 20, "y": 0, "width": 12, "height": 15},
        "bedroom":     {"x": 0, "y": 15, "width": 16, "height": 12},
    }


@pytest.fixture
def sample_meta():
    return {
        "living_room": {"name": "Living Room", "room_type": "living"},
        "kitchen":     {"name": "Kitchen",     "room_type": "kitchen"},
        "bedroom":     {"name": "Bedroom",     "room_type": "bedroom"},
    }


def test_render_returns_png_bytes(previewer, sample_matrix, sample_meta):
    """render() should return non-empty bytes starting with PNG magic bytes."""
    png = previewer.render(sample_matrix, sample_meta)
    assert isinstance(png, bytes)
    assert len(png) > 0
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_empty_plan(previewer):
    """render() with no rooms should return a valid PNG (empty canvas placeholder)."""
    png = previewer.render({}, {})
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_custom_dimensions(previewer, sample_matrix, sample_meta):
    """render() should respect width_px and height_px."""
    png = previewer.render(sample_matrix, sample_meta, width_px=400, height_px=300)
    assert isinstance(png, bytes)
    assert len(png) > 0


def test_render_single_room(previewer):
    """render() with a single room should not raise."""
    matrix = {"living_room": {"x": 0, "y": 0, "width": 20, "height": 15}}
    meta = {"living_room": {"name": "Living Room", "room_type": "living"}}
    png = previewer.render(matrix, meta)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_all_room_types(previewer):
    """All room_type values should render without error."""
    from engine.previewer import ROOM_COLORS
    matrix = {}
    meta = {}
    for i, rt in enumerate(ROOM_COLORS.keys()):
        rid = f"room_{i}"
        matrix[rid] = {"x": i * 12, "y": 0, "width": 10, "height": 10}
        meta[rid] = {"name": rt.title(), "room_type": rt}
    png = previewer.render(matrix, meta)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
```

---

## Task 8: `tests/test_load.py`

```python
"""Tests for POST /plan/load endpoint."""
import json
import pytest
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_load_json_valid():
    """Load a valid FloorPlanState JSON."""
    state = {
        "plan_id": "test01",
        "rooms": {
            "living_room": {
                "name": "Living Room",
                "room_type": "living",
                "area_sqft": 300,
                "adjacency_requirements": []
            }
        },
        "constraints": [],
        "connections": [],
        "coordinate_matrix": None,
        "version": 1,
    }
    content = json.dumps(state).encode()
    response = client.post(
        "/plan/load",
        files={"file": ("plan.json", content, "application/json")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["plan_id"] == "test01"
    assert data["room_count"] == 1
    assert data["format"] == "json"


def test_load_json_invalid():
    """Load invalid JSON should return 422."""
    response = client.post(
        "/plan/load",
        files={"file": ("plan.json", b"not valid json", "application/json")},
    )
    assert response.status_code == 422


def test_load_unsupported_format():
    """Unsupported file type should return 415."""
    response = client.post(
        "/plan/load",
        files={"file": ("plan.pdf", b"%PDF...", "application/pdf")},
    )
    assert response.status_code == 415


def test_load_dxf_basic():
    """Load a minimal DXF with one closed polyline room."""
    import ezdxf
    import io

    doc = ezdxf.new()
    msp = doc.modelspace()
    # Add a closed 20x15 ft room outline
    msp.add_lwpolyline(
        [(0, 0), (20, 0), (20, 15), (0, 15)],
        close=True,
        dxfattribs={"layer": "WALLS"},
    )
    buf = io.BytesIO()
    doc.write(buf)
    buf.seek(0)

    response = client.post(
        "/plan/load",
        files={"file": ("plan.dxf", buf.read(), "application/dxf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["room_count"] >= 1
    assert data["format"] == "dxf"


def test_load_then_instruct(monkeypatch):
    """Load a JSON plan then send an NL instruction (mocked Claude)."""
    from engine.intent_parser import parser as intent_parser_module
    from engine.intent_parser.schemas import FloorPlanOp, RoomSpec

    def mock_parse(self, nl_input, state):
        return FloorPlanOp(
            op_type="add_room",
            room_spec=RoomSpec(name="Office", room_type="office", area_sqft=120),
        )

    monkeypatch.setattr(intent_parser_module.IntentParser, "parse", mock_parse)

    # First load a plan
    state = {
        "plan_id": "edit01",
        "rooms": {
            "living_room": {
                "name": "Living Room", "room_type": "living",
                "area_sqft": 300, "adjacency_requirements": []
            }
        },
        "constraints": [], "connections": [],
        "coordinate_matrix": None, "version": 1,
    }
    load_resp = client.post(
        "/plan/load",
        files={"file": ("plan.json", json.dumps(state).encode(), "application/json")},
    )
    assert load_resp.status_code == 200
    plan_id = load_resp.json()["plan_id"]

    # Then instruct
    instruct_resp = client.post(
        f"/plan/{plan_id}/instruct",
        json={"instruction": "Add a home office of 120 sqft"},
    )
    assert instruct_resp.status_code == 200
    assert instruct_resp.json()["room_count"] == 2
```

---

## Running the Full Stack Locally

After implementation, start both services:

```bash
# Terminal 1 — Backend
cd /path/to/cognitect
pip install -r requirements.txt
COGNITECT_CLAUDE_API_KEY=your_key uvicorn api.main:app --reload --port 8000

# Terminal 2 — Frontend
cd /path/to/cognitect/frontend
npm install
npm run dev
```

Then open `http://localhost:3000`.

---

## Acceptance Criteria

Phase 4 is complete when ALL of the following work:

- [ ] `GET /plan/{id}/preview` returns a valid PNG for a plan with rooms
- [ ] `GET /plan/{id}/preview` returns a valid PNG (empty canvas) for a plan with no rooms
- [ ] `POST /plan/load` with a valid `.json` FloorPlanState file loads the plan and returns a `plan_id`
- [ ] `POST /plan/load` with a valid `.dxf` file extracts rooms and returns a `plan_id`
- [ ] After loading a plan, `POST /plan/{id}/instruct` successfully edits it
- [ ] `GET /plan/{id}/export?format=pdf` returns a PDF file
- [ ] Frontend: "New Plan" button creates a session and shows the canvas
- [ ] Frontend: Typing an instruction and hitting Send updates the canvas
- [ ] Frontend: "Upload Plan" button accepts `.json` and `.dxf`, loads the plan, shows it on canvas
- [ ] Frontend: Room list updates after each instruction
- [ ] Frontend: "Download DXF" and "Download PDF" buttons trigger file downloads
- [ ] All existing 79 tests still pass: `pytest -m "not slow"`
- [ ] New tests pass: `pytest tests/test_previewer.py tests/test_load.py`
