"""
Ensures the test suite never depends on real Supabase credentials.

api/auth.py and api/storage/plan_store.py read SUPABASE_URL,
SUPABASE_SERVICE_KEY, and SUPABASE_JWT_SECRET from the environment at *import
time* (fail-fast, matching how engine/intent_parser/parser.py already treats
COGNITECT_CLAUDE_API_KEY). Any test file that imports api.main
(test_load.py, test_plans_v2.py) transitively imports those modules.

This module-level code (not a fixture — fixtures run too late, after test
modules are already imported) runs before pytest imports any test module in
this directory, so dummy values are in place before those imports fire.
os.environ.setdefault() means a real .env (loaded later, inside api/main.py's
load_dotenv() call) never overrides these — dotenv's default is
override=False — so tests are hermetic and never touch real Supabase
credentials, even if a real .env happens to be present locally.
"""
from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-not-for-production")

# ── Shared /v2/plans test infra ─────────────────────────────────────────────
#
# Fake Supabase client + JWT/device-id header helpers, shared by
# test_plans_v2.py and test_plans_v2_upload.py so neither has to duplicate
# this plumbing.

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from api.storage import plan_store
from engine.intent_parser.schemas import FloorPlanOp, RoomSpec


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

    def is_(self, col, val):
        self._filters.append(("is", col, val))
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
            if op == "is" and row.get(col) is not val:
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
            matched.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
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
    from api.main import app

    return TestClient(app)


def _make_jwt(user_id: str, email: str = "test@example.com") -> str:
    secret = os.environ["SUPABASE_JWT_SECRET"]
    return jwt.encode({"sub": user_id, "email": email, "role": "authenticated"}, secret, algorithm="HS256")


def _auth_header(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_make_jwt(user_id)}"}


def _device_header(device_id: str) -> dict:
    return {"X-Device-Id": device_id}
