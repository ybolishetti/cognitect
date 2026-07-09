"""
Integration tests for /v2/plans — the persistent, multi-tenant plan routes.

Mocks the Supabase client (api.storage.plan_store._client) with a small
in-memory fake rather than hitting the real DB. Mocks IntentParser the same
way tests/test_plan_manager.py does, so /instruct never calls Claude.

The fake Supabase client, `fake_supabase`/`mock_intent_parser`/`client`
fixtures, and JWT/device-id header helpers live in tests/conftest.py so
tests/test_plans_v2_upload.py can share them without duplication.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tests.conftest import _auth_header, _device_header

# ── Tests ─────────────────────────────────────────────────────────────────────

def test_anonymous_create_and_persist(client):
    device_id = str(uuid.uuid4())
    resp = client.post("/v2/plans", json={"name": "My Plan"}, headers=_device_header(device_id))
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["plan_id"]

    resp = client.post(
        f"/v2/plans/{plan_id}/instruct",
        json={"instruction": "Add a living room of 200 sqft"},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["room_count"] == 1
    assert "living_room" in body["coordinate_matrix"]

    # A fresh GET rehydrates a brand-new PlanManager from Supabase state_json —
    # this is what actually proves persistence rather than in-process caching.
    resp = client.get(f"/v2/plans/{plan_id}", headers=_device_header(device_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["room_count"] == 1
    assert "living_room" in resp.json()["rooms"]


def test_anonymous_create_requires_device_id(client):
    resp = client.post("/v2/plans", json={"name": "No Owner"})
    assert resp.status_code == 400


def test_authenticated_create_and_list(client):
    user_id = str(uuid.uuid4())
    headers = _auth_header(user_id)
    resp = client.post("/v2/plans", json={"name": "Auth Plan"}, headers=headers)
    assert resp.status_code == 201, resp.text

    resp = client.get("/v2/plans", headers=headers)
    assert resp.status_code == 200
    assert "Auth Plan" in [p["name"] for p in resp.json()]


def test_cross_user_access_denied(client):
    owner_id, other_id = str(uuid.uuid4()), str(uuid.uuid4())
    resp = client.post("/v2/plans", json={"name": "Private"}, headers=_auth_header(owner_id))
    plan_id = resp.json()["plan_id"]

    resp = client.get(f"/v2/plans/{plan_id}", headers=_auth_header(other_id))
    assert resp.status_code == 403

    resp = client.patch(
        f"/v2/plans/{plan_id}", json={"name": "Hijacked"}, headers=_auth_header(other_id)
    )
    assert resp.status_code == 403


def test_cross_device_access_denied(client):
    device_a, device_b = str(uuid.uuid4()), str(uuid.uuid4())
    resp = client.post("/v2/plans", json={"name": "Anon Plan"}, headers=_device_header(device_a))
    plan_id = resp.json()["plan_id"]

    resp = client.get(f"/v2/plans/{plan_id}", headers=_device_header(device_b))
    assert resp.status_code == 403


def test_claim_flow(client):
    device_id = str(uuid.uuid4())
    resp = client.post(
        "/v2/plans", json={"name": "Pre-signup Plan"}, headers=_device_header(device_id)
    )
    plan_id = resp.json()["plan_id"]

    user_id = str(uuid.uuid4())
    resp = client.post(
        "/v2/plans/claim", json={"device_id": device_id}, headers=_auth_header(user_id)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claimed_count"] == 1
    assert plan_id in body["plan_ids"]

    # Anonymous access no longer works once claimed...
    resp = client.get(f"/v2/plans/{plan_id}", headers=_device_header(device_id))
    assert resp.status_code == 403

    # ...but the claiming user can access it.
    resp = client.get(f"/v2/plans/{plan_id}", headers=_auth_header(user_id))
    assert resp.status_code == 200


def test_rate_limit_anonymous_429(client):
    """ANON_RATE_LIMIT_PER_HOUR defaults to 1 — a second /instruct within the
    hour must be rejected with a Retry-After telling the caller how long."""
    device_id = str(uuid.uuid4())
    resp = client.post("/v2/plans", json={"name": "Rate Limited"}, headers=_device_header(device_id))
    plan_id = resp.json()["plan_id"]

    resp = client.post(
        f"/v2/plans/{plan_id}/instruct",
        json={"instruction": "Add a living room of 200 sqft"},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        f"/v2/plans/{plan_id}/instruct",
        json={"instruction": "Add a bedroom of 150 sqft"},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 429, resp.text
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) == 3600  # full 1-hour window


def test_rate_limit_authenticated_429(client, fake_supabase):
    """
    USER_RATE_LIMIT_PER_DAY defaults to 20. Pre-seed 19 llm_call_log rows
    directly (rather than firing 19 real /instruct calls through the full
    parse->solve->save pipeline) to hit the boundary fast: the 20th real call
    must still succeed (count=19 < 20), the 21st must be rejected (count=20 >= 20).
    """
    user_id = str(uuid.uuid4())
    headers = _auth_header(user_id)
    resp = client.post("/v2/plans", json={"name": "Daily Limit"}, headers=headers)
    plan_id = resp.json()["plan_id"]

    now = datetime.now(timezone.utc).isoformat()
    for _ in range(19):
        fake_supabase.tables["llm_call_log"].append(
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "device_id": None,
                "plan_id": plan_id,
                "model": "claude-haiku-4-5",
                "status": "ok",
                "created_at": now,
            }
        )

    resp = client.post(
        f"/v2/plans/{plan_id}/instruct",
        json={"instruction": "Add a living room of 200 sqft"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text  # 20th call: count was 19, allowed

    resp = client.post(
        f"/v2/plans/{plan_id}/instruct",
        json={"instruction": "Add a bedroom of 150 sqft"},
        headers=headers,
    )
    assert resp.status_code == 429, resp.text  # 21st call: count now 20, rejected
    assert int(resp.headers["Retry-After"]) == 86400  # full 1-day window


def test_rename_happy_path(client):
    user_id = str(uuid.uuid4())
    headers = _auth_header(user_id)
    resp = client.post("/v2/plans", json={"name": "Old Name"}, headers=headers)
    plan_id = resp.json()["plan_id"]

    resp = client.patch(f"/v2/plans/{plan_id}", json={"name": "New Name"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Name"

    resp = client.get(f"/v2/plans/{plan_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_delete_hides_plan(client, fake_supabase):
    """DELETE is a soft-delete (archived=true): the row and its plan_versions
    history are preserved for a possible future restore, but the plan must
    become invisible through every /v2/plans read path immediately."""
    user_id = str(uuid.uuid4())
    headers = _auth_header(user_id)
    resp = client.post("/v2/plans", json={"name": "To Delete"}, headers=headers)
    plan_id = resp.json()["plan_id"]

    resp = client.delete(f"/v2/plans/{plan_id}", headers=headers)
    assert resp.status_code == 204, resp.text

    resp = client.get(f"/v2/plans/{plan_id}", headers=headers)
    assert resp.status_code == 404

    row = next(r for r in fake_supabase.tables["plans"] if r["id"] == plan_id)
    assert row["archived"] is True
