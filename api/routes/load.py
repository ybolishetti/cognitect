"""
Plan Load API route — seed a PlanManager from an uploaded file.

POST /plan/load
  Accepts multipart/form-data with a single file field: "file"
  Supported formats:
    - .json  — Cognitect FloorPlanState JSON (our native format)
    - .dxf   — AutoCAD DXF (parsed via ezdxf, best-effort room extraction)

  Returns: {plan_id, room_count, format, message}
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

import ezdxf
from ezdxf.path import from_hatch
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from engine.plan_manager import PlanManager
from engine.intent_parser.schemas import FloorPlanState, RoomSpec
from api.routes.plan import _PLANS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plan", tags=["load"])

# Loading a plan only requires the parser later (during /instruct). Fall back to a
# placeholder key so a plan can be loaded in a keyless demo/test environment; the
# real key is read from the env when an actual instruction is parsed.
_FALLBACK_KEY = os.environ.get("COGNITECT_CLAUDE_API_KEY") or "demo-placeholder-key"

INSUNITS_TO_FEET = {
    1: 1 / 12,       # inches
    2: 1.0,          # feet
    4: 1 / 304.8,    # mm
    5: 1 / 30.48,    # cm
    6: 3.28084,      # meters
}

MIN_ROOM_SQFT = 20
MAX_ROOM_SQFT = 5000
LINE_SNAP_TOL = 0.1
MAX_LINE_CYCLE_LEN = 20
_PASS_PRIORITY = {"line_reconstruction": 3, "hatch": 2, "polyline": 1}
_WALL_LAYER_MARKERS = ("WALL", "ROOM")


def _is_wall_layer(layer: str) -> bool:
    upper = layer.upper()
    return any(marker in upper for marker in _WALL_LAYER_MARKERS)


def _is_room_hatch_layer(layer: str) -> bool:
    upper = layer.upper()
    return "WALL" in upper or "ROOM" in upper

_DXF_UNSUPPORTED_MSG = (
    "No room geometry found. This DXF uses an unsupported entity type. "
    "Try: (1) in AutoCAD run BOUNDARY on each room to create closed polylines, "
    "(2) export with 'room' layers as filled hatches, or "
    "(3) use Cognitect's JSON format instead."
)


def _detect_scale(insunits: int, candidates: list[tuple[float, float, float, float]]) -> float:
    scale = INSUNITS_TO_FEET.get(insunits)
    if scale is not None or not candidates:
        return scale if scale is not None else 1.0
    max_dim = max(max(c[2], c[3]) for c in candidates)
    if max_dim > 10_000:
        return 1 / 304.8
    if max_dim > 500:
        return 1 / 12
    return 1.0


def _filter_and_normalize(
    candidates: list[tuple[float, float, float, float]],
    scale: float,
) -> list[tuple[float, float, float, float]]:
    if not candidates:
        return []

    scaled = [(x * scale, y * scale, w * scale, h * scale) for (x, y, w, h) in candidates]
    filtered = [
        (x, y, w, h) for (x, y, w, h) in scaled
        if MIN_ROOM_SQFT <= w * h <= MAX_ROOM_SQFT
    ]

    if len(filtered) >= 4:
        cx_list = [x + w / 2 for (x, y, w, h) in filtered]
        cy_list = [y + h / 2 for (x, y, w, h) in filtered]
        med_cx = statistics.median(cx_list)
        med_cy = statistics.median(cy_list)
        mad_x = statistics.median([abs(cx - med_cx) for cx in cx_list]) or 1
        mad_y = statistics.median([abs(cy - med_cy) for cy in cy_list]) or 1
        thresh_x = max(mad_x * 5, 30)
        thresh_y = max(mad_y * 5, 30)
        filtered = [
            (x, y, w, h) for (x, y, w, h) in filtered
            if abs((x + w / 2) - med_cx) <= thresh_x
            and abs((y + h / 2) - med_cy) <= thresh_y
        ]

    if not filtered:
        return []

    origin_x = min(x for (x, y, w, h) in filtered)
    origin_y = min(y for (x, y, w, h) in filtered)
    return [(x - origin_x, y - origin_y, w, h) for (x, y, w, h) in filtered]


def _bbox_from_xy(
    xs: list[float],
    ys: list[float],
) -> tuple[float, float, float, float] | None:
    if len(xs) < 3 or len(ys) < 3:
        return None
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    w, h = x_max - x_min, y_max - y_min
    if w < 0.01 or h < 0.01:
        return None
    return (x_min, y_min, w, h)


def _extract_polyline_candidates(msp) -> list[tuple[float, float, float, float]]:
    candidates: list[tuple[float, float, float, float]] = []
    for entity in msp:
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
        bbox = _bbox_from_xy(xs, ys)
        if bbox is not None:
            candidates.append(bbox)
    return candidates


def _snap_line_node(
    x: float,
    y: float,
    nodes: list[tuple[float, float]],
    snap_tol: float,
) -> int:
    for i, (nx, ny) in enumerate(nodes):
        if abs(x - nx) <= snap_tol and abs(y - ny) <= snap_tol:
            return i
    nodes.append((x, y))
    return len(nodes) - 1


def _compute_snap_tolerance(segments: list[tuple[float, float, float, float]]) -> float:
    if not segments:
        return LINE_SNAP_TOL
    xs = [x1 for x1, _, _, _ in segments] + [x2 for _, _, x2, _ in segments]
    ys = [y1 for _, y1, _, _ in segments] + [y2 for _, _, _, y2 in segments]
    extent = max(max(xs) - min(xs), max(ys) - min(ys))
    return max(extent * 1e-4, LINE_SNAP_TOL)


def _iter_wall_segments(msp) -> list[tuple[float, float, float, float]]:
    all_segments: list[tuple[float, float, float, float]] = []
    wall_segments: list[tuple[float, float, float, float]] = []

    for entity in msp.query("LINE"):
        try:
            seg = (
                entity.dxf.start.x, entity.dxf.start.y,
                entity.dxf.end.x, entity.dxf.end.y,
            )
        except Exception:
            continue
        all_segments.append(seg)
        if _is_wall_layer(entity.dxf.layer):
            wall_segments.append(seg)

    for entity in msp.query("LWPOLYLINE"):
        if getattr(entity.dxf, "closed", False) or getattr(entity, "is_closed", False):
            continue
        try:
            points = [(p[0], p[1]) for p in entity.get_points()]
        except Exception:
            continue
        on_wall_layer = _is_wall_layer(entity.dxf.layer)
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            seg = (x1, y1, x2, y2)
            all_segments.append(seg)
            if on_wall_layer:
                wall_segments.append(seg)

    for entity in msp.query("POLYLINE"):
        if getattr(entity.dxf, "closed", False) or getattr(entity, "is_closed", False):
            continue
        try:
            verts = list(entity.vertices)
            points = [(v.dxf.location.x, v.dxf.location.y) for v in verts]
        except Exception:
            continue
        on_wall_layer = _is_wall_layer(entity.dxf.layer)
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            seg = (x1, y1, x2, y2)
            all_segments.append(seg)
            if on_wall_layer:
                wall_segments.append(seg)

    return wall_segments if wall_segments else all_segments


def _find_line_cycles(
    nodes: list[tuple[float, float]],
    adj: dict[int, list[int]],
) -> list[list[int]]:
    """Find closed face boundaries via planar half-edge walk (handles shared walls)."""
    if not adj:
        return []

    sorted_adj: dict[int, list[int]] = {}
    for node, neighbors in adj.items():
        cx, cy = nodes[node]
        sorted_adj[node] = sorted(
            neighbors,
            key=lambda nb: math.atan2(nodes[nb][1] - cy, nodes[nb][0] - cx),
        )

    visited: set[tuple[int, int]] = set()
    faces: list[list[int]] = []
    seen_faces: set[tuple[int, ...]] = set()

    for start_u in adj:
        for start_v in adj[start_u]:
            if (start_u, start_v) in visited:
                continue
            face = [start_u]
            u, v = start_u, start_v
            while True:
                visited.add((u, v))
                face.append(v)
                neighbors = sorted_adj[v]
                if u not in neighbors:
                    break
                idx = neighbors.index(u)
                w = neighbors[(idx - 1) % len(neighbors)]
                u, v = v, w
                if u == start_u and v == start_v:
                    break
                if len(face) > MAX_LINE_CYCLE_LEN * 4:
                    break
            if len(face) >= 4:
                ring = face[:-1]
                key = tuple(sorted(ring))
                if key not in seen_faces:
                    seen_faces.add(key)
                    faces.append(ring)
    return faces


def _segment_intersection(
    s1: tuple[float, float, float, float],
    s2: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    x1, y1, x2, y2 = s1
    x3, y3, x4, y4 = s2
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-12:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def _point_on_segment(px: float, py: float, seg: tuple[float, float, float, float], tol: float) -> float | None:
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq < tol * tol:
        return None
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    if t < -1e-9 or t > 1 + 1e-9:
        return None
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    if abs(px - proj_x) <= tol and abs(py - proj_y) <= tol:
        return max(0.0, min(1.0, t))
    return None


def _split_segments_at_intersections(
    segments: list[tuple[float, float, float, float]],
    snap_tol: float,
) -> list[tuple[float, float, float, float]]:
    split_params: list[set[float]] = [set() for _ in segments]

    for i, seg in enumerate(segments):
        split_params[i].update({0.0, 1.0})
        x1, y1, x2, y2 = seg
        for px, py in ((x1, y1), (x2, y2)):
            for j, other in enumerate(segments):
                if i == j:
                    continue
                t = _point_on_segment(px, py, other, snap_tol)
                if t is not None:
                    split_params[j].add(t)

    for i, seg in enumerate(segments):
        for j in range(i + 1, len(segments)):
            hit = _segment_intersection(seg, segments[j])
            if hit is None:
                continue
            px, py = hit
            ti = _point_on_segment(px, py, seg, snap_tol)
            tj = _point_on_segment(px, py, segments[j], snap_tol)
            if ti is not None:
                split_params[i].add(ti)
            if tj is not None:
                split_params[j].add(tj)

    split_segments: list[tuple[float, float, float, float]] = []
    for seg, params in zip(segments, split_params):
        x1, y1, x2, y2 = seg
        ordered = sorted(params)
        pts = [(x1 + t * (x2 - x1), y1 + t * (y2 - y1)) for t in ordered]
        for a, b in zip(pts, pts[1:]):
            if abs(a[0] - b[0]) <= snap_tol and abs(a[1] - b[1]) <= snap_tol:
                continue
            split_segments.append((a[0], a[1], b[0], b[1]))
    return split_segments


def _extract_line_candidates(msp) -> list[tuple[float, float, float, float]]:
    segments = _iter_wall_segments(msp)
    if not segments:
        return []

    snap_tol = _compute_snap_tolerance(segments)
    if len(segments) <= 3000:
        segments = _split_segments_at_intersections(segments, snap_tol)

    nodes: list[tuple[float, float]] = []
    adj: dict[int, list[int]] = defaultdict(list)
    seen_edges: set[tuple[int, int]] = set()

    for x1, y1, x2, y2 in segments:
        i = _snap_line_node(x1, y1, nodes, snap_tol)
        j = _snap_line_node(x2, y2, nodes, snap_tol)
        if i == j:
            continue
        edge = (min(i, j), max(i, j))
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        adj[i].append(j)
        adj[j].append(i)

    if not adj:
        return []

    candidates: list[tuple[float, float, float, float]] = []
    seen_boxes: set[tuple[float, float, float, float]] = set()
    for cycle in _find_line_cycles(nodes, adj):
        xs = [nodes[i][0] for i in cycle]
        ys = [nodes[i][1] for i in cycle]
        bbox = _bbox_from_xy(xs, ys)
        if bbox is None:
            continue
        key = tuple(round(v, 1) for v in bbox)
        if key in seen_boxes:
            continue
        seen_boxes.add(key)
        candidates.append(bbox)

    if len(candidates) > 1:
        env_x0 = min(c[0] for c in candidates)
        env_y0 = min(c[1] for c in candidates)
        env_x1 = max(c[0] + c[2] for c in candidates)
        env_y1 = max(c[1] + c[3] for c in candidates)
        env_w, env_h = env_x1 - env_x0, env_y1 - env_y0
        candidates = [
            c for c in candidates
            if not (
                abs(c[0] - env_x0) <= snap_tol
                and abs(c[1] - env_y0) <= snap_tol
                and abs(c[2] - env_w) <= snap_tol
                and abs(c[3] - env_h) <= snap_tol
                and len(candidates) > 2
            )
        ]
    return candidates


def _extract_hatch_candidates(msp) -> list[tuple[float, float, float, float]]:
    all_candidates: list[tuple[float, float, float, float]] = []
    wall_candidates: list[tuple[float, float, float, float]] = []
    seen_boxes: set[tuple[float, float, float, float]] = set()

    for entity in msp.query("HATCH"):
        try:
            paths = from_hatch(entity)
        except Exception:
            continue
        for hatch_path in paths:
            try:
                vertices = list(hatch_path.control_vertices())
            except Exception:
                continue
            if len(vertices) < 3:
                continue
            xs = [v.x for v in vertices]
            ys = [v.y for v in vertices]
            bbox = _bbox_from_xy(xs, ys)
            if bbox is None:
                continue
            key = tuple(round(v, 1) for v in bbox)
            if key in seen_boxes:
                continue
            seen_boxes.add(key)
            all_candidates.append(bbox)
            if _is_room_hatch_layer(entity.dxf.layer):
                wall_candidates.append(bbox)
    return wall_candidates if wall_candidates else all_candidates


def _process_pass(
    candidates: list[tuple[float, float, float, float]],
    insunits: int,
) -> tuple[list[tuple[float, float, float, float]], float]:
    scale = _detect_scale(insunits, candidates)
    return _filter_and_normalize(candidates, scale), scale


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
      - .dxf  — AutoCAD DXF (rooms extracted from closed polylines)
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
    manager = PlanManager(plan_id=plan_id, api_key=_FALLBACK_KEY)
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
    Parse a DXF file and extract rooms from closed polylines, LINE walls, or HATCH fills.

    Each detected room boundary becomes a RoomSpec named 'Room 1', 'Room 2', etc.
    Uses $INSUNITS when present, otherwise global heuristics, then filters
    non-room geometry and normalizes coordinates to the origin in feet.
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        doc = ezdxf.readfile(tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid DXF file: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    msp = doc.modelspace()
    insunits = doc.header.get("$INSUNITS", 0)

    poly_candidates = _extract_polyline_candidates(msp)
    line_candidates = _extract_line_candidates(msp)
    hatch_candidates = _extract_hatch_candidates(msp)

    poly_rooms, poly_scale = _process_pass(poly_candidates, insunits)
    line_rooms, line_scale = _process_pass(line_candidates, insunits)
    hatch_rooms, hatch_scale = _process_pass(hatch_candidates, insunits)

    pass_results = [
        ("polyline", poly_rooms, poly_scale, len(poly_candidates)),
        ("line_reconstruction", line_rooms, line_scale, len(line_candidates)),
        ("hatch", hatch_rooms, hatch_scale, len(hatch_candidates)),
    ]
    source, normalized, scale, raw_count = max(
        pass_results,
        key=lambda item: (len(item[1]), _PASS_PRIORITY[item[0]]),
    )

    logger.info(
        "DXF extraction passes for '%s': polyline=%d/%d, line=%d/%d, hatch=%d/%d → %s",
        filename,
        len(poly_rooms), len(poly_candidates),
        len(line_rooms), len(line_candidates),
        len(hatch_rooms), len(hatch_candidates),
        source,
    )

    if not normalized:
        raise HTTPException(status_code=422, detail=_DXF_UNSUPPORTED_MSG)

    rooms: dict[str, RoomSpec] = {}
    coordinate_matrix: dict[str, dict] = {}
    for i, (x, y, w, h) in enumerate(normalized, start=1):
        room_id = f"room_{i}"
        rooms[room_id] = RoomSpec(
            name=f"Room {i}",
            room_type="other",
            area_sqft=round(w * h, 1),
        )
        coordinate_matrix[room_id] = {
            "x": round(x, 2),
            "y": round(y, 2),
            "width": round(w, 2),
            "height": round(h, 2),
        }

    plan_id = str(uuid.uuid4())[:8]
    state = FloorPlanState(
        plan_id=plan_id,
        rooms=rooms,
        coordinate_matrix=coordinate_matrix,
    )
    manager = PlanManager(plan_id=plan_id, api_key=_FALLBACK_KEY)
    manager._state = state
    _PLANS[plan_id] = manager

    logger.info(
        "Loaded DXF plan '%s' — %d rooms (source=%s, scale=%.6f, insunits=%d, raw=%d, filtered=%d)",
        plan_id, len(rooms), source, scale, insunits, raw_count, len(normalized),
    )
    return LoadResponse(
        plan_id=plan_id,
        room_count=len(rooms),
        format="dxf",
        message=(
            f"Extracted {len(rooms)} room(s) from DXF "
            f"(unit scale: {scale:.4f} ft/unit). "
            "Rooms are named 'Room 1', 'Room 2', etc. "
            "Use NL instructions to rename, resize, or rearrange them."
        ),
    )
