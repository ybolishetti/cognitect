"""
Unit tests for the robust DXF importer.

Covers both the shared engine.importers module (parse_dxf_to_state /
parse_json_to_state) directly, and the legacy v1 route (_load_from_dxf) that
wraps it — the latter is the regression proof that extracting the shared
module didn't change v1 behavior.
"""
from __future__ import annotations

import json
import os
import tempfile

import ezdxf
import pytest
from fastapi import HTTPException

from api.routes.load import _load_from_dxf
from api.routes.plan import _PLANS
from engine.importers import parse_dxf_to_state, parse_json_to_state


def _save_dxf(doc: ezdxf.document.Drawing) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp_path = tmp.name
    doc.saveas(tmp_path)
    with open(tmp_path, "rb") as fh:
        data = fh.read()
    os.unlink(tmp_path)
    return data


def _build_dxf_bytes(
    polylines: list[tuple[list[tuple[float, float]], bool]],
    *,
    insunits: int = 2,
) -> bytes:
    """Create DXF bytes from (points, closed) polyline specs."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for points, closed in polylines:
        msp.add_lwpolyline(points, close=closed)
    return _save_dxf(doc)


def _add_rect_lines(msp, x0: float, y0: float, w: float, h: float) -> None:
    corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    for i in range(4):
        start = corners[i]
        end = corners[(i + 1) % 4]
        msp.add_line(start, end)


def _build_line_dxf_bytes(
    rooms: list[tuple[float, float, float, float]],
    *,
    insunits: int = 2,
) -> bytes:
    """Create DXF bytes from rectangular rooms drawn as LINE wall segments."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for x0, y0, w, h in rooms:
        _add_rect_lines(msp, x0, y0, w, h)
    return _save_dxf(doc)


def _build_hatch_dxf_bytes(
    rooms: list[tuple[float, float, float, float]],
    *,
    insunits: int = 2,
) -> bytes:
    """Create DXF bytes from rectangular rooms drawn as HATCH fills."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for x0, y0, w, h in rooms:
        hatch = msp.add_hatch(color=1)
        hatch.paths.add_polyline_path(
            [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
            is_closed=True,
        )
    return _save_dxf(doc)


async def _load(raw: bytes):
    resp = await _load_from_dxf(raw, "test.dxf")
    state = _PLANS[resp.plan_id].state
    return resp, state


@pytest.mark.asyncio
async def test_dxf_feet_coordinates_unchanged():
    """DXF drawn in feet passes through with correct dimensions."""
    raw = _build_dxf_bytes([([(0, 0), (20, 0), (20, 15), (0, 15)], True)])
    resp, state = await _load(raw)
    assert resp.room_count == 1
    coords = state.coordinate_matrix["room_1"]
    assert coords["width"] == pytest.approx(20.0, abs=0.1)
    assert coords["height"] == pytest.approx(15.0, abs=0.1)
    assert state.rooms["room_1"].area_sqft == pytest.approx(300.0, abs=1.0)


@pytest.mark.asyncio
async def test_dxf_mm_insunits_scales_to_feet():
    """DXF with $INSUNITS=4 (mm) scales bounding boxes to feet."""
    # 6096 mm × 4572 mm ≈ 20 ft × 15 ft
    raw = _build_dxf_bytes(
        [([(0, 0), (6096, 0), (6096, 4572), (0, 4572)], True)],
        insunits=4,
    )
    resp, state = await _load(raw)
    assert resp.room_count == 1
    coords = state.coordinate_matrix["room_1"]
    assert coords["width"] == pytest.approx(20.0, abs=0.2)
    assert coords["height"] == pytest.approx(15.0, abs=0.2)


@pytest.mark.asyncio
async def test_dxf_inches_heuristic_scales_to_feet():
    """DXF in inches without $INSUNITS is detected via global max-dimension heuristic."""
    # 720 × 480 inches → 60 ft × 40 ft after /12
    raw = _build_dxf_bytes([([(0, 0), (720, 0), (720, 480), (0, 480)], True)], insunits=0)
    resp, state = await _load(raw)
    assert resp.room_count == 1
    coords = state.coordinate_matrix["room_1"]
    assert coords["width"] == pytest.approx(60.0, abs=0.5)
    assert coords["height"] == pytest.approx(40.0, abs=0.5)


@pytest.mark.asyncio
async def test_dxf_outlier_room_filtered():
    """Spatially isolated shape is removed when enough rooms exist to cluster."""
    polylines: list[tuple[list[tuple[float, float]], bool]] = []
    for row in range(5):
        for col in range(7):
            x0, y0 = col * 22.0, row * 17.0
            polylines.append(
                ([(x0, y0), (x0 + 20, y0), (x0 + 20, y0 + 15), (x0, y0 + 15)], True)
            )
    # Stray title-block rectangle far from the cluster
    polylines.append(
        ([(5000, 5000), (5020, 5000), (5020, 5015), (5000, 5015)], True)
    )
    raw = _build_dxf_bytes(polylines)
    resp, state = await _load(raw)
    assert resp.room_count == 35


@pytest.mark.asyncio
async def test_dxf_non_room_shapes_filtered():
    """Tiny annotation boxes and giant borders are excluded; real rooms kept."""
    polylines = [
        ([(0, 0), (12, 0), (12, 10), (0, 10)], True),       # 120 sqft
        ([(14, 0), (28, 0), (28, 12), (14, 12)], True),     # 168 sqft
        ([(0, 14), (15, 14), (15, 26), (0, 26)], True),    # 180 sqft
        ([(50, 50), (52, 50), (52, 52), (50, 52)], True),   # 4 sqft — too small
        ([(0, 0), (300, 0), (300, 300), (0, 300)], True),  # 90k sqft — site border
    ]
    raw = _build_dxf_bytes(polylines)
    resp, state = await _load(raw)
    assert resp.room_count == 3


@pytest.mark.asyncio
async def test_dxf_all_shapes_too_large_raises_422():
    """Only oversized shapes produce a helpful 422 after all extraction passes fail."""
    raw = _build_dxf_bytes([([(0, 0), (200, 0), (200, 200), (0, 200)], True)])
    with pytest.raises(HTTPException) as exc_info:
        await _load_from_dxf(raw, "test.dxf")
    assert exc_info.value.status_code == 422
    assert "no room geometry found" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_dxf_no_geometry_raises_422():
    """Empty DXF with no extractable geometry → 422 with remediation tips."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 2
    raw = _save_dxf(doc)
    with pytest.raises(HTTPException) as exc_info:
        await _load_from_dxf(raw, "test.dxf")
    assert exc_info.value.status_code == 422
    assert "BOUNDARY" in exc_info.value.detail


@pytest.mark.asyncio
async def test_dxf_coordinates_normalized_to_origin():
    """After import, the minimum x and y across all rooms is 0.0."""
    polylines = [
        ([(100, 200), (120, 200), (120, 215), (100, 215)], True),
        ([(130, 200), (150, 200), (150, 215), (130, 215)], True),
        ([(100, 220), (120, 220), (120, 235), (100, 235)], True),
    ]
    raw = _build_dxf_bytes(polylines)
    _, state = await _load(raw)
    xs = [c["x"] for c in state.coordinate_matrix.values()]
    ys = [c["y"] for c in state.coordinate_matrix.values()]
    assert min(xs) == pytest.approx(0.0, abs=0.01)
    assert min(ys) == pytest.approx(0.0, abs=0.01)


@pytest.mark.asyncio
async def test_dxf_shared_wall_lines_extract_rooms():
    """Shared wall segments split at intersections still yield individual rooms."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 2
    msp = doc.modelspace()
    # Three adjacent rooms using shared horizontal wall lines
    msp.add_line((0, 0), (66, 0))
    msp.add_line((0, 15), (66, 15))
    msp.add_line((0, 0), (0, 15))
    msp.add_line((22, 0), (22, 15))
    msp.add_line((44, 0), (44, 15))
    msp.add_line((66, 0), (66, 15))
    raw = _save_dxf(doc)
    resp, _state = await _load(raw)
    assert resp.room_count == 3


@pytest.mark.asyncio
async def test_dxf_line_wins_over_incidental_polylines():
    """When incidental polylines survive but LINE walls define more rooms, prefer LINE pass."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 2
    msp = doc.modelspace()
    for i in range(5):
        x = 5000 + i * 25
        msp.add_lwpolyline([(x, 5000), (x + 20, 5000), (x + 20, 5015), (x, 5015)], close=True)
    for row in range(5):
        for col in range(7):
            x0, y0 = col * 22.0, row * 17.0
            corners = [(x0, y0), (x0 + 20, y0), (x0 + 20, y0 + 15), (x0, y0 + 15)]
            for i in range(4):
                msp.add_line(corners[i], corners[(i + 1) % 4])
    raw = _save_dxf(doc)
    resp, _state = await _load(raw)
    assert resp.room_count == 35


@pytest.mark.asyncio
async def test_dxf_line_entities_extract_three_rooms():
    """LINE wall segments forming three rectangles are reconstructed into rooms."""
    raw = _build_line_dxf_bytes([
        (0, 0, 12, 10),
        (14, 0, 12, 10),
        (0, 14, 12, 10),
    ])
    resp, state = await _load(raw)
    assert resp.room_count == 3
    areas = sorted(spec.area_sqft for spec in state.rooms.values())
    assert areas[0] == pytest.approx(120.0, abs=1.0)
    assert areas[1] == pytest.approx(120.0, abs=1.0)
    assert areas[2] == pytest.approx(120.0, abs=1.0)


@pytest.mark.asyncio
async def test_dxf_hatch_entities_fallback_extraction():
    """HATCH-filled room boundaries are extracted when polyline/LINE passes are insufficient."""
    raw = _build_hatch_dxf_bytes([
        (0, 0, 12, 10),
        (14, 0, 12, 10),
        (0, 14, 12, 10),
    ])
    resp, state = await _load(raw)
    assert resp.room_count == 3
    assert all(spec.area_sqft == pytest.approx(120.0, abs=1.0) for spec in state.rooms.values())


# --- engine.importers module (extracted shared parser) ---


def test_parse_dxf_to_state_returns_floor_plan_state():
    """parse_dxf_to_state returns a FloorPlanState directly, with no PlanManager/_PLANS wiring."""
    raw = _build_dxf_bytes([([(0, 0), (20, 0), (20, 15), (0, 15)], True)])
    state = parse_dxf_to_state(raw, "test.dxf")
    assert len(state.rooms) == 1
    coords = state.coordinate_matrix["room_1"]
    assert coords["width"] == pytest.approx(20.0, abs=0.1)
    assert coords["height"] == pytest.approx(15.0, abs=0.1)
    assert state.plan_id  # a fresh plan_id was generated


def test_parse_dxf_to_state_no_geometry_raises_422():
    """Same error contract as the legacy route for unsupported/empty DXFs."""
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 2
    raw = _save_dxf(doc)
    with pytest.raises(HTTPException) as exc_info:
        parse_dxf_to_state(raw, "test.dxf")
    assert exc_info.value.status_code == 422
    assert "BOUNDARY" in exc_info.value.detail


def test_parse_json_to_state_roundtrips_floor_plan_state():
    """parse_json_to_state parses a native FloorPlanState JSON payload."""
    payload = {
        "plan_id": "abc123",
        "rooms": {
            "room_1": {"name": "Living Room", "room_type": "living", "area_sqft": 200},
        },
        "coordinate_matrix": {
            "room_1": {"x": 0, "y": 0, "width": 20, "height": 10},
        },
    }
    state = parse_json_to_state(json.dumps(payload).encode())
    assert state.plan_id == "abc123"
    assert len(state.rooms) == 1
    assert state.rooms["room_1"].name == "Living Room"


def test_parse_json_to_state_invalid_json_raises_422():
    """Malformed JSON bytes raise the same 422 contract as the legacy route."""
    with pytest.raises(HTTPException) as exc_info:
        parse_json_to_state(b"not valid json")
    assert exc_info.value.status_code == 422
