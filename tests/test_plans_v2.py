"""
Integration tests for /v2/plans — the persistent, multi-tenant plan routes.

Mocks the Supabase client (api.storage.plan_store._client) with a small
in-memory fake rather than hitting the real DB. Mocks IntentParser the same
way tests/test_plan_manager.py does, so /instruct never calls Claude.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.storage import plan_store
from engine.intent_parser.schemas import FloorPlanOp, RoomSpec


# ── Fake Supabase client ─────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    """Minimal stand-in for supabase-py's fluent postgrest query builder."""

    def __init__(self, rows: list):
        self._rows = rows
        self._filters: list[tuple[str, str, object]] = []
        self._op: str | None = None
        self._payload = None
        self._count_mode = None
        self._order = None
        self._limit = None

    def select(self, *_cols, count=None):
        self._op = "select"
        self._count_mode = count
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload if isinstance(payload, list) else [payload]
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row) -> bool:
        for op, col, val in self._filters:
            if op == "eq" and row.get(col) != val:
                return False
            if op == "gte" and (row.get(col) is None or row.get(col) < val):
                return False
        return True

    def execute(self) -> _FakeResult:
        if self._op == "insert":
            inserted = []
            now = datetime.now(timezone.utc).isoformat()
            for r in self._payload:
                row = dict(r)
                row.setdefault("id", str(uuid.uuid4()))
                row.setdefault("archived", False)
                row.setdefault("created_at", now)
                row.setdefault("updated_at", now)
                row.setdefault("last_opened_at", now)
                self._rows.append(row)
                inserted.append(row)
            return _FakeResult(inserted)

        if self._op == "update":
            matched = [r for r in self._rows if self._matches(r)]
            for r in matched:
                r.update(self._payload)
            return _FakeResult(matched)

        matched = [dict(r) for r in self._rows if self._matches(r)]
        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: r.get(col) or "", reverse=desc)
        count = len(matched) if self._count_mode else None
        if self._limit is not None:
            matched = matched[: self._limit]
        return _FakeResult(matched, count=count)


class _FakeRpc:
    def __init__(self, result: _FakeResult):
        self._result = result

    def execute(self) -> _FakeResult:
        return self._result


class FakeSupabaseClient:
    def __init__(self):
        self.tables = {"plans": [], "plan_versions": [], "llm_call_log": []}

    def table(self, name):
        return _FakeQuery(self.tables.setdefault(name, []))

    def rpc(self, name, params):
        if name == "claim_anonymous_plans":
            device_id, user_id = params["p_device_id"], params["p_user_id"]
            claimed = []
            for row in self.tables["plans"]:
                if row.get("device_id") == device_id and row.get("user_id") is None:
                    row["user_id"] = user_id
                    row["device_id"] = None
                    claimed.append(row["id"])
            return _FakeRpc(_FakeResult([{"claimed_count": len(claimed), "plan_ids": claimed}]))
        if name == "trim_plan_versions":
            return _FakeRpc(_FakeResult([]))
        raise ValueError(f"Unknown rpc: {name}")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fake_supabase(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(plan_store, "_client", fake)
    return fake


@pytest.fixture(autouse=True)
def mock_intent_parser():
    """No Claude calls: canned single add_room op, same pattern as test_plan_manager.py."""
    op = FloorPlanOp(
        op_type="add_room",
        room_spec=RoomSpec(name="Living Room", room_type="living", area_sqft=200),
    )
    batch = MagicMock()
    batch.ops = [op]
    batch.batch_description = "add a living room"
    with patch("engine.plan_manager.IntentParser") as MockParser:
        MockParser.return_value.parse_batch.return_value = batch
        yield


@pytest.fixture
def client():
    return TestClient(app)


def _make_jwt(user_id: str, email: str = "test@example.com") -> str:
    secret = os.environ["SUPABASE_JWT_SECRET"]
    return jwt.encode({"sub": user_id, "email": email, "role": "authenticated"}, secret, algorithm="HS256")


def _auth_header(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_make_jwt(user_id)}"}


def _device_header(device_id: str) -> dict:
    return {"X-Device-Id": device_id}


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
