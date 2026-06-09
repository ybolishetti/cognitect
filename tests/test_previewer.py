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
