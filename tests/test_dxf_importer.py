"""Unit tests for the robust DXF importer (_load_from_dxf)."""
from __future__ import annotations

import os
import tempfile

import ezdxf
import pytest
from fastapi import HTTPException

from api.routes.load import _load_from_dxf
from api.routes.plan import _PLANS


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
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp_path = tmp.name
    doc.saveas(tmp_path)
    with open(tmp_path, "rb") as fh:
        data = fh.read()
    os.unlink(tmp_path)
    return data


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
    """Only oversized shapes produce a helpful 422 after area filtering."""
    raw = _build_dxf_bytes([([(0, 0), (200, 0), (200, 200), (0, 200)], True)])
    with pytest.raises(HTTPException) as exc_info:
        await _load_from_dxf(raw, "test.dxf")
    assert exc_info.value.status_code == 422
    assert "no plausible room shapes" in exc_info.value.detail.lower()
    assert "5000" in exc_info.value.detail


@pytest.mark.asyncio
async def test_dxf_no_closed_polylines_raises_422():
    """Open polylines only → 422 with BOUNDARY command tip."""
    raw = _build_dxf_bytes([([(0, 0), (20, 0), (20, 15), (0, 15)], False)])
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
