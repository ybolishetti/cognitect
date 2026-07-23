"""Integration tests for POST/GET /v2/plans/generate (Architecture C best-of-N endpoint).

Mocks `run_best_of_n` at its point of use in api.routes.plans_v2_generate (not
at its definition site in engine.pipeline.best_of_n) — the pipeline itself is
exercised by tests/test_pipeline_best_of_n.py, not here. Reuses the
`client`/`fake_supabase`/`_auth_header`/`_device_header` fixtures shared with
tests/test_plans_v2.py via tests/conftest.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from engine.generators import GeneratorFailure, STUB_VERSION
from engine.layout import Layout, LayoutAuditManifest, Room, VerifierResult, Wall
from engine.pipeline import BestOfNResult, PipelineFailure
from engine.pipeline.spec_hash import spec_hash as compute_spec_hash

from tests.conftest import _auth_header, _device_header

SPEC = {
    "spec_id": "spec_test1",
    "original_nl": "a small studio",
    "room_requirements": [{"name": "Living", "room_type": "living", "preferred_area_sqft": 200.0}],
    "n_candidates": 3,
}


def _make_layout(selection_rank: int = 0, user_score: float = 0.8) -> Layout:
    room_id, wall_prefix = "room_1", "wall_1"
    x1, y1 = 10.0, 10.0
    walls = [
        Wall(id=f"{wall_prefix}_s", start=(0.0, 0.0), end=(x1, 0.0), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_e", start=(x1, 0.0), end=(x1, y1), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_n", start=(x1, y1), end=(0.0, y1), bounds_rooms=[room_id]),
        Wall(id=f"{wall_prefix}_w", start=(0.0, y1), end=(0.0, 0.0), bounds_rooms=[room_id]),
    ]
    room = Room(
        id=room_id, name="Living", room_type="living",
        vertices=[(0.0, 0.0), (x1, 0.0), (x1, y1), (0.0, y1), (0.0, 0.0)],
        area_sqft=100.0, boundary_wall_ids=[w.id for w in walls],
    )
    layout = Layout(plan_id="plan_test1", rooms=[room], walls=walls, extent_x_ft=1000.0, extent_y_ft=1000.0)
    layout.audit = LayoutAuditManifest(
        generator="stub", generator_version=STUB_VERSION, spec_hash=compute_spec_hash_dict(),
        verifier_results=[
            VerifierResult(verifier_name="layer_a_geometry", passed=True, elapsed_ms=1.0),
            VerifierResult(verifier_name="layer_c_code", passed=True, elapsed_ms=1.0),
        ],
        generated_at=datetime.now(timezone.utc),
        selection_rank=selection_rank, total_candidates=3, survived_layer_a=2,
        survived_layer_c=2, user_score=user_score,
    )
    return layout


def compute_spec_hash_dict() -> str:
    from engine.layout import FloorPlanSpec
    return compute_spec_hash(FloorPlanSpec(**SPEC))


def _make_result(top_k: int = 1) -> BestOfNResult:
    layouts = [_make_layout(selection_rank=i, user_score=0.9 - i * 0.1) for i in range(top_k)]
    return BestOfNResult(
        layouts=layouts, total_candidates=3, survived_layer_a=2, survived_layer_c=2,
        all_verifier_results={}, elapsed_ms=123.4, generator_name="stub", generator_version=STUB_VERSION,
    )


def _mock_run_best_of_n(**kwargs):
    return patch("api.routes.plans_v2_generate.run_best_of_n", **kwargs)


# ── Happy path ─────────────────────────────────────────────────────────────────

def test_anonymous_generate_creates_row(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        resp = client.post(
            "/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id)
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["generated_plan_id"]
    assert body["cached"] is False
    assert len(body["layouts"]) == 1
    assert fake_supabase.tables["generated_plans"][0]["device_id"] == device_id


def test_anonymous_generate_requires_device_id(client):
    resp = client.post("/v2/plans/generate", json={"spec": SPEC})
    assert resp.status_code == 400


def test_authenticated_generate_uses_user_id(client, fake_supabase):
    user_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        resp = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_auth_header(user_id))
    assert resp.status_code == 201, resp.text
    row = fake_supabase.tables["generated_plans"][0]
    assert row["user_id"] == user_id
    assert row["device_id"] is None


# ── Pipeline failure mapping ───────────────────────────────────────────────────

def test_generator_failure_returns_502(client, fake_supabase):
    device_id = str(uuid.uuid4())
    exc = GeneratorFailure("nope", spec_id="spec_test1", generator_name="prompted", reason_code="llm_refused")
    with _mock_run_best_of_n(side_effect=exc):
        resp = client.post(
            "/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id)
        )
    assert resp.status_code == 502, resp.text
    log_rows = fake_supabase.tables["llm_call_log"]
    assert log_rows[-1]["status"] == "generator_failure"


def test_pipeline_failure_returns_422(client, fake_supabase):
    device_id = str(uuid.uuid4())
    exc = PipelineFailure(
        "0/3 survived", spec_id="spec_test1", total_candidates=3,
        survived_layer_a=1, survived_layer_c=0, verifier_results={},
    )
    with _mock_run_best_of_n(side_effect=exc):
        resp = client.post(
            "/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id)
        )
    assert resp.status_code == 422, resp.text
    log_rows = fake_supabase.tables["llm_call_log"]
    assert log_rows[-1]["status"] == "pipeline_failure"


def test_not_implemented_returns_501(client):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(side_effect=NotImplementedError("finetuned not shipped")):
        resp = client.post(
            "/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id)
        )
    assert resp.status_code == 501, resp.text


# ── Rate limiting (shares llm_call_log with /instruct) ────────────────────────

def test_rate_limit_anonymous_429(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        resp = client.post(
            "/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id)
        )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/v2/plans/generate", json={"spec": {**SPEC, "spec_id": "spec_test2"}},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 429, resp.text
    assert "Retry-After" in resp.headers


def test_rate_limit_authenticated_429(client, fake_supabase):
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(20):
        fake_supabase.tables.setdefault("llm_call_log", []).append(
            {"id": str(uuid.uuid4()), "user_id": user_id, "device_id": None,
             "plan_id": None, "model": "stub", "status": "ok", "created_at": now}
        )
    resp = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_auth_header(user_id))
    assert resp.status_code == 429, resp.text


# ── Persistence detail ──────────────────────────────────────────────────────────

def test_persists_to_generated_plans_and_generated_layout_versions(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result(top_k=2)):
        resp = client.post(
            "/v2/plans/generate", json={"spec": SPEC, "top_k": 2}, headers=_device_header(device_id)
        )
    assert resp.status_code == 201, resp.text
    assert len(fake_supabase.tables["generated_plans"]) == 1
    assert len(fake_supabase.tables["generated_layout_versions"]) == 2


def test_layouts_persisted_with_audit_manifest_intact(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    layout_json = fake_supabase.tables["generated_layout_versions"][0]["layout_json"]
    audit = layout_json["audit"]
    assert audit["selection_rank"] == 0
    assert audit["spec_hash"]
    assert len(audit["verifier_results"]) == 2
    assert audit["user_score"] == pytest.approx(0.9)


def test_user_score_column_populated(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    row = fake_supabase.tables["generated_layout_versions"][0]
    assert row["user_score"] == pytest.approx(row["layout_json"]["audit"]["user_score"])


def test_spec_hash_stored_correctly(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    from engine.layout import FloorPlanSpec
    expected = compute_spec_hash(FloorPlanSpec(**SPEC))
    assert fake_supabase.tables["generated_plans"][0]["spec_hash"] == expected


def test_top_k_defaults_to_1(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result(top_k=1)) as mocked:
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    assert mocked.call_args.kwargs["top_k"] == 1
    assert len(fake_supabase.tables["generated_layout_versions"]) == 1


def test_top_k_clamped_at_pydantic_level(client):
    device_id = str(uuid.uuid4())
    resp = client.post(
        "/v2/plans/generate", json={"spec": SPEC, "top_k": 100}, headers=_device_header(device_id)
    )
    assert resp.status_code == 422, resp.text


# ── Cache behavior ───────────────────────────────────────────────────────────────

def test_cache_hit_returns_cached_true(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        first = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
        assert first.json()["cached"] is False

        second = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    assert second.status_code == 201, second.text
    assert second.json()["cached"] is True
    assert len(fake_supabase.tables["generated_plans"]) == 1


def test_cache_miss_when_force_regenerate_true(client, fake_supabase):
    # Two genuine pipeline runs for the same owner — needs headroom beyond the
    # anonymous 1/hour default, so use an authenticated user (20/day).
    user_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_auth_header(user_id))
        second = client.post(
            "/v2/plans/generate", json={"spec": SPEC, "force_regenerate": True},
            headers=_auth_header(user_id),
        )
    assert second.status_code == 201, second.text
    assert second.json()["cached"] is False
    assert len(fake_supabase.tables["generated_plans"]) == 2


def test_cache_scoped_to_owner(client, fake_supabase):
    device_a, device_b = str(uuid.uuid4()), str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_a))
        second = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_b))
    assert second.json()["cached"] is False
    assert len(fake_supabase.tables["generated_plans"]) == 2


def test_cache_expires_after_24h(client, fake_supabase):
    # Two genuine pipeline runs for the same owner — needs headroom beyond the
    # anonymous 1/hour default, so use an authenticated user (20/day).
    user_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_auth_header(user_id))

    from datetime import timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    fake_supabase.tables["generated_plans"][0]["created_at"] = stale_iso

    with _mock_run_best_of_n(return_value=_make_result()):
        second = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_auth_header(user_id))
    assert second.json()["cached"] is False
    assert len(fake_supabase.tables["generated_plans"]) == 2


# ── GET /v2/plans/generate/{id} ─────────────────────────────────────────────────

def test_get_generated_plan_happy_path(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        created = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    generated_plan_id = created.json()["generated_plan_id"]

    resp = client.get(f"/v2/plans/generate/{generated_plan_id}", headers=_device_header(device_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["generated_plan_id"] == generated_plan_id


def test_get_generated_plan_cross_user_403(client, fake_supabase):
    owner_id, other_id = str(uuid.uuid4()), str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        created = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_auth_header(owner_id))
    generated_plan_id = created.json()["generated_plan_id"]

    resp = client.get(f"/v2/plans/generate/{generated_plan_id}", headers=_auth_header(other_id))
    assert resp.status_code == 403


def test_get_generated_plan_cross_device_403(client, fake_supabase):
    device_a, device_b = str(uuid.uuid4()), str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        created = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_a))
    generated_plan_id = created.json()["generated_plan_id"]

    resp = client.get(f"/v2/plans/generate/{generated_plan_id}", headers=_device_header(device_b))
    assert resp.status_code == 403


def test_get_generated_plan_missing_404(client):
    resp = client.get(
        f"/v2/plans/generate/{uuid.uuid4()}", headers=_device_header(str(uuid.uuid4()))
    )
    assert resp.status_code == 404


def test_get_generated_plan_archived_404(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        created = client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    generated_plan_id = created.json()["generated_plan_id"]
    row = next(r for r in fake_supabase.tables["generated_plans"] if r["id"] == generated_plan_id)
    row["archived"] = True

    resp = client.get(f"/v2/plans/generate/{generated_plan_id}", headers=_device_header(device_id))
    assert resp.status_code == 404


def test_generate_does_not_create_row_in_plans_table(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        client.post("/v2/plans/generate", json={"spec": SPEC}, headers=_device_header(device_id))
    assert fake_supabase.tables["plans"] == []
