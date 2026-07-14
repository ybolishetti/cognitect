"""Tests for api/storage/generated_plan_store.py.

Uses the same `fake_supabase` autouse fixture (tests/conftest.py) that
test_plans_v2.py relies on — it monkeypatches plan_store._client, which
generated_plan_store reads dynamically via `plan_store._client` (not a
static `from ... import _client`, which would freeze the pre-patch client).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from api.storage import generated_plan_store as store
from engine.layout import Layout, LayoutAuditManifest, Room, VerifierResult, Wall


def _make_layout(plan_id: str = "plan_test", user_score: float | None = 0.75) -> Layout:
    room_id, wall_prefix = "room_1", "wall_1"
    x, y, width, height = 0.0, 0.0, 10.0, 10.0
    x1, y1 = x + width, y + height
    walls = [
        Wall(id=f"{wall_prefix}_s", start=(x, y), end=(x1, y), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_e", start=(x1, y), end=(x1, y1), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_n", start=(x1, y1), end=(x, y1), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_w", start=(x, y1), end=(x, y), bounds_rooms=[room_id]),
    ]
    room = Room(
        id=room_id,
        name="Living",
        room_type="living",
        vertices=[(x, y), (x1, y), (x1, y1), (x, y1), (x, y)],
        area_sqft=width * height,
        boundary_wall_ids=[w.id for w in walls],
    )
    layout = Layout(
        plan_id=plan_id, rooms=[room], walls=walls, extent_x_ft=1000.0, extent_y_ft=1000.0
    )
    layout.audit = LayoutAuditManifest(
        generator="stub",
        generator_version="v1",
        spec_hash="abc123",
        verifier_results=[
            VerifierResult(verifier_name="layer_a_geometry", passed=True, elapsed_ms=1.0),
            VerifierResult(verifier_name="layer_c_code", passed=True, elapsed_ms=1.0),
            VerifierResult(verifier_name="layer_b_structural", passed=True, elapsed_ms=1.0, score=0.9),
        ],
        generated_at=datetime.now(timezone.utc),
        selection_rank=0,
        total_candidates=3,
        survived_layer_a=2,
        survived_layer_c=2,
        user_score=user_score,
    )
    return layout


# ── _require_exactly_one ──────────────────────────────────────────────────────

def test_require_exactly_one_rejects_both_none():
    with pytest.raises(store.GeneratedPlanStoreError):
        store._require_exactly_one(None, None)


def test_require_exactly_one_rejects_both_set():
    with pytest.raises(store.GeneratedPlanStoreError):
        store._require_exactly_one("user-1", "device-1")


# ── create_generated_plan ─────────────────────────────────────────────────────

def test_create_generated_plan_inserts_header_and_layouts(fake_supabase):
    layouts = [_make_layout(), _make_layout()]
    plan_id = store.create_generated_plan(
        user_id="user-1",
        spec_json={"spec_id": "spec_x"},
        spec_hash="hash-1",
        generator_name="stub",
        generator_version="v1",
        total_candidates=3,
        survived_layer_a=2,
        survived_layer_c=2,
        elapsed_ms=42,
        top_layouts=layouts,
    )
    header_rows = fake_supabase.tables["generated_plans"]
    assert len(header_rows) == 1
    assert header_rows[0]["id"] == plan_id
    assert header_rows[0]["spec_hash"] == "hash-1"

    layout_rows = fake_supabase.tables["generated_layout_versions"]
    assert len(layout_rows) == 2
    assert {r["selection_rank"] for r in layout_rows} == {0, 1}


def test_create_generated_plan_with_empty_top_layouts_inserts_only_header(fake_supabase):
    plan_id = store.create_generated_plan(
        device_id=str(uuid.uuid4()),
        spec_json={"spec_id": "spec_x"},
        spec_hash="hash-1",
        generator_name="stub",
        generator_version="v1",
        total_candidates=3,
        survived_layer_a=0,
        survived_layer_c=0,
        elapsed_ms=10,
        top_layouts=[],
    )
    assert len(fake_supabase.tables["generated_plans"]) == 1
    assert fake_supabase.tables.get("generated_layout_versions", []) == []
    assert plan_id


# ── load_generated_plan ────────────────────────────────────────────────────────

def test_load_generated_plan_returns_full_row_with_layouts(fake_supabase):
    plan_id = store.create_generated_plan(
        user_id="user-1",
        spec_json={"spec_id": "spec_x"},
        spec_hash="hash-1",
        generator_name="stub",
        generator_version="v1",
        total_candidates=1,
        survived_layer_a=1,
        survived_layer_c=1,
        elapsed_ms=10,
        top_layouts=[_make_layout()],
    )
    row = store.load_generated_plan(plan_id, user_id="user-1")
    assert row["id"] == plan_id
    assert row["spec_hash"] == "hash-1"
    assert len(row["layouts"]) == 1
    assert row["layouts"][0]["selection_rank"] == 0
    assert row["layouts"][0]["user_score"] == 0.75


def test_load_generated_plan_missing_raises_not_found(fake_supabase):
    with pytest.raises(store.GeneratedPlanNotFoundError):
        store.load_generated_plan(str(uuid.uuid4()), user_id="user-1")


def test_load_generated_plan_archived_raises_not_found(fake_supabase):
    plan_id = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="h", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    store.archive_generated_plan(plan_id, user_id="user-1")
    with pytest.raises(store.GeneratedPlanNotFoundError):
        store.load_generated_plan(plan_id, user_id="user-1")


def test_load_generated_plan_wrong_owner_raises_access_denied(fake_supabase):
    plan_id = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="h", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    with pytest.raises(store.GeneratedPlanAccessDeniedError):
        store.load_generated_plan(plan_id, user_id="user-2")


# ── list_generated_plans_for_user ─────────────────────────────────────────────

def test_list_generated_plans_for_user_excludes_archived_by_default(fake_supabase):
    keep = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="h1", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    archived = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="h2", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    store.archive_generated_plan(archived, user_id="user-1")

    plans = store.list_generated_plans_for_user(user_id="user-1")
    ids = [p["id"] for p in plans]
    assert keep in ids
    assert archived not in ids


def test_list_generated_plans_for_user_includes_archived_when_flag_set(fake_supabase):
    archived = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="h2", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    store.archive_generated_plan(archived, user_id="user-1")

    plans = store.list_generated_plans_for_user(user_id="user-1", include_archived=True)
    assert archived in [p["id"] for p in plans]


# ── archive_generated_plan ────────────────────────────────────────────────────

def test_archive_generated_plan_sets_archived_flag(fake_supabase):
    plan_id = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="h", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    store.archive_generated_plan(plan_id, user_id="user-1")
    row = next(r for r in fake_supabase.tables["generated_plans"] if r["id"] == plan_id)
    assert row["archived"] is True


# ── find_cached_generation ─────────────────────────────────────────────────────

def test_find_cached_generation_hits_within_window(fake_supabase):
    plan_id = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="hash-x", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    cached = store.find_cached_generation(spec_hash="hash-x", user_id="user-1")
    assert cached is not None
    assert cached["id"] == plan_id


def test_find_cached_generation_misses_outside_window(fake_supabase):
    plan_id = store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="hash-old", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    stale_iso = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    row = next(r for r in fake_supabase.tables["generated_plans"] if r["id"] == plan_id)
    row["created_at"] = stale_iso

    cached = store.find_cached_generation(spec_hash="hash-old", user_id="user-1", max_age_hours=24)
    assert cached is None


def test_find_cached_generation_scoped_to_user_id(fake_supabase):
    store.create_generated_plan(
        user_id="user-1", spec_json={}, spec_hash="hash-shared", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    cached = store.find_cached_generation(spec_hash="hash-shared", user_id="user-2")
    assert cached is None


def test_find_cached_generation_scoped_to_device_id(fake_supabase):
    device_a, device_b = str(uuid.uuid4()), str(uuid.uuid4())
    store.create_generated_plan(
        device_id=device_a, spec_json={}, spec_hash="hash-dev", generator_name="stub",
        generator_version="v1", total_candidates=1, survived_layer_a=1,
        survived_layer_c=1, elapsed_ms=1, top_layouts=[],
    )
    assert store.find_cached_generation(spec_hash="hash-dev", device_id=device_b) is None
    assert store.find_cached_generation(spec_hash="hash-dev", device_id=device_a) is not None
