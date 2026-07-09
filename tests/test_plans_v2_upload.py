"""
Integration tests for POST /v2/plans/upload.

Mirrors tests/test_plans_v2.py's structure and reuses its shared fixtures
(fake Supabase client, JWT/device-id header helpers) from tests/conftest.py.
"""
from __future__ import annotations

import json
import uuid

from tests.conftest import _auth_header, _device_header
from tests.test_dxf_importer import _build_dxf_bytes


def test_upload_json_creates_plan(client):
    device_id = str(uuid.uuid4())
    payload = {
        "plan_id": "uploaded01",
        "rooms": {
            "room_1": {"name": "Living Room", "room_type": "living", "area_sqft": 200},
        },
        "coordinate_matrix": {
            "room_1": {"x": 0, "y": 0, "width": 20, "height": 10},
        },
    }
    resp = client.post(
        "/v2/plans/upload",
        files={"file": ("plan.json", json.dumps(payload).encode(), "application/json")},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["plan_id"]

    resp = client.get(f"/v2/plans/{plan_id}", headers=_device_header(device_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["room_count"] == 1
    assert "room_1" in resp.json()["rooms"]


def test_upload_dxf_creates_plan(client):
    device_id = str(uuid.uuid4())
    raw = _build_dxf_bytes([([(0, 0), (20, 0), (20, 15), (0, 15)], True)])
    resp = client.post(
        "/v2/plans/upload",
        files={"file": ("plan.dxf", raw, "application/dxf")},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["plan_id"]

    resp = client.get(f"/v2/plans/{plan_id}", headers=_device_header(device_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["room_count"] == 1


def test_upload_rejects_wrong_extension(client):
    device_id = str(uuid.uuid4())
    resp = client.post(
        "/v2/plans/upload",
        files={"file": ("plan.png", b"not a real image", "image/png")},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 400, resp.text


def test_upload_rejects_oversized_file(client):
    device_id = str(uuid.uuid4())
    oversized = b"\x00" * (11 * 1024 * 1024)
    resp = client.post(
        "/v2/plans/upload",
        files={"file": ("plan.json", oversized, "application/json")},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 413, resp.text


def test_upload_requires_owner(client):
    payload = {"plan_id": "noowner01", "rooms": {}, "coordinate_matrix": {}}
    resp = client.post(
        "/v2/plans/upload",
        files={"file": ("plan.json", json.dumps(payload).encode(), "application/json")},
    )
    assert resp.status_code == 400, resp.text
