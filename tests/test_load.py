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
    import os
    import tempfile

    doc = ezdxf.new()
    msp = doc.modelspace()
    # Add a closed 20x15 ft room outline
    msp.add_lwpolyline(
        [(0, 0), (20, 0), (20, 15), (0, 15)],
        close=True,
        dxfattribs={"layer": "WALLS"},
    )
    # ezdxf serializes to a text stream / file path, so round-trip through a temp file.
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp_path = tmp.name
    doc.saveas(tmp_path)
    with open(tmp_path, "rb") as fh:
        dxf_bytes = fh.read()
    os.unlink(tmp_path)

    response = client.post(
        "/plan/load",
        files={"file": ("plan.dxf", dxf_bytes, "application/dxf")},
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
