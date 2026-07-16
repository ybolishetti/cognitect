"""Tests for GET ?include=layout and POST /v2/plans/generate/{id}/materialize.

Reuses SPEC/_make_result/_mock_run_best_of_n from test_plans_v2_generate.py and
the fake-Supabase/auth-header fixtures shared via tests/conftest.py.
"""
from __future__ import annotations

import uuid

import pytest

from engine.layout import Layout, Opening, Room, Wall
from engine.materialize import layout_to_plan_state

from tests.conftest import _auth_header, _device_header
from tests.test_plans_v2_generate import SPEC, _make_result, _mock_run_best_of_n


def _generate(client, fake_supabase, headers, top_k: int = 1):
    with _mock_run_best_of_n(return_value=_make_result(top_k=top_k)):
        resp = client.post(
            "/v2/plans/generate", json={"spec": SPEC, "top_k": top_k}, headers=headers
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── GET ?include=layout ─────────────────────────────────────────────────────────

def test_get_with_include_layout_returns_full_geometry(client, fake_supabase):
    device_id = str(uuid.uuid4())
    body = _generate(client, fake_supabase, _device_header(device_id))
    gpid = body["generated_plan_id"]

    resp = client.get(
        f"/v2/plans/generate/{gpid}?include=layout", headers=_device_header(device_id)
    )
    assert resp.status_code == 200, resp.text
    layouts_full = resp.json()["layouts_full"]
    assert layouts_full is not None
    assert len(layouts_full) == 1
    assert layouts_full[0]["selection_rank"] == 0
    assert layouts_full[0]["layout"]["rooms"][0]["id"] == "room_1"

    resp_no_include = client.get(f"/v2/plans/generate/{gpid}", headers=_device_header(device_id))
    assert resp_no_include.json()["layouts_full"] is None


def test_get_with_bogus_include_param_returns_summary_only(client, fake_supabase):
    device_id = str(uuid.uuid4())
    body = _generate(client, fake_supabase, _device_header(device_id))
    gpid = body["generated_plan_id"]

    resp = client.get(
        f"/v2/plans/generate/{gpid}?include=nonsense", headers=_device_header(device_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["layouts_full"] is None


# ── POST .../materialize ─────────────────────────────────────────────────────────

def test_materialize_creates_new_plan(client, fake_supabase):
    device_id = str(uuid.uuid4())
    body = _generate(client, fake_supabase, _device_header(device_id))
    gpid = body["generated_plan_id"]

    resp = client.post(
        f"/v2/plans/generate/{gpid}/materialize",
        json={"selection_rank": 0},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["created"] is True
    assert data["plan_id"]
    assert len(fake_supabase.tables["plans"]) == 1
    assert fake_supabase.tables["plans"][0]["materialized_from_layout_id"] == data["materialized_from_layout_id"]


def test_materialize_is_idempotent(client, fake_supabase):
    device_id = str(uuid.uuid4())
    body = _generate(client, fake_supabase, _device_header(device_id))
    gpid = body["generated_plan_id"]

    first = client.post(
        f"/v2/plans/generate/{gpid}/materialize",
        json={"selection_rank": 0},
        headers=_device_header(device_id),
    )
    second = client.post(
        f"/v2/plans/generate/{gpid}/materialize",
        json={"selection_rank": 0},
        headers=_device_header(device_id),
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["plan_id"] == second.json()["plan_id"]
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert len(fake_supabase.tables["plans"]) == 1


def test_materialize_wrong_owner_returns_403(client, fake_supabase):
    device_a, device_b = str(uuid.uuid4()), str(uuid.uuid4())
    body = _generate(client, fake_supabase, _device_header(device_a))
    gpid = body["generated_plan_id"]

    resp = client.post(
        f"/v2/plans/generate/{gpid}/materialize",
        json={"selection_rank": 0},
        headers=_device_header(device_b),
    )
    assert resp.status_code == 403


def test_materialize_bad_selection_rank_returns_404(client, fake_supabase):
    device_id = str(uuid.uuid4())
    body = _generate(client, fake_supabase, _device_header(device_id))
    gpid = body["generated_plan_id"]

    resp = client.post(
        f"/v2/plans/generate/{gpid}/materialize",
        json={"selection_rank": 99},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 404
    assert "99" in resp.text


def test_materialize_derives_default_name_from_spec(client, fake_supabase):
    device_id = str(uuid.uuid4())
    body = _generate(client, fake_supabase, _device_header(device_id))
    gpid = body["generated_plan_id"]

    resp = client.post(
        f"/v2/plans/generate/{gpid}/materialize",
        json={"selection_rank": 0},
        headers=_device_header(device_id),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Living + others (candidate 1)"


# ── engine.materialize.layout_to_plan_state (unit) ───────────────────────────────

def _make_two_room_layout() -> Layout:
    walls = [
        Wall(id="wall_a_s", start=(0.0, 0.0), end=(10.0, 0.0), bounds_rooms=["room_a"]),
        Wall(id="wall_a_n", start=(10.0, 10.0), end=(0.0, 10.0), bounds_rooms=["room_a"]),
        Wall(id="wall_a_w", start=(0.0, 10.0), end=(0.0, 0.0), bounds_rooms=["room_a"]),
        Wall(id="wall_mid", start=(10.0, 0.0), end=(10.0, 10.0), bounds_rooms=["room_a", "room_b"]),
        Wall(id="wall_b_s", start=(10.0, 0.0), end=(20.0, 0.0), bounds_rooms=["room_b"]),
        Wall(id="wall_b_n", start=(20.0, 10.0), end=(10.0, 10.0), bounds_rooms=["room_b"]),
        Wall(id="wall_b_e", start=(20.0, 0.0), end=(20.0, 10.0), bounds_rooms=["room_b"]),
    ]
    room_a = Room(
        id="room_a", name="Living Room", room_type="living",
        vertices=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
        area_sqft=100.0,
        boundary_wall_ids=["wall_a_s", "wall_mid", "wall_a_n", "wall_a_w"],
    )
    room_b = Room(
        id="room_b", name="Coat Closet", room_type="closet",
        vertices=[(10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0), (10.0, 0.0)],
        area_sqft=100.0,
        boundary_wall_ids=["wall_b_s", "wall_b_e", "wall_b_n", "wall_mid"],
    )
    opening = Opening(
        id="opening_1", opening_type="door", wall_id="wall_mid", offset_ft=4.0, width_ft=3.0
    )
    return Layout(
        plan_id="plan_test1", rooms=[room_a, room_b], walls=walls, openings=[opening],
        extent_x_ft=20.0, extent_y_ft=10.0,
    )


def test_layout_to_plan_state_roundtrip():
    layout_json = _make_two_room_layout().model_dump(mode="json")
    plan_state = layout_to_plan_state(layout_json)

    assert set(plan_state["rooms"].keys()) == {"room_a", "room_b"}
    assert plan_state["rooms"]["room_a"]["name"] == "Living Room"
    assert plan_state["rooms"]["room_a"]["room_type"] == "living"
    assert plan_state["rooms"]["room_a"]["area_sqft"] == pytest.approx(100.0)
    # closet isn't a valid RoomSpec.room_type — must be mapped to "other"
    assert plan_state["rooms"]["room_b"]["room_type"] == "other"

    assert plan_state["coordinate_matrix"]["room_a"] == {"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0}
    assert plan_state["coordinate_matrix"]["room_b"] == {"x": 10.0, "y": 0.0, "width": 10.0, "height": 10.0}

    assert len(plan_state["connections"]) == 1
    conn = plan_state["connections"][0]
    assert {conn["room_a_id"], conn["room_b_id"]} == {"room_a", "room_b"}
    assert conn["connection_type"] == "door"
    assert conn["width_ft"] == pytest.approx(3.0)

    # Materialized state must validate against the legacy FloorPlanState schema —
    # this is what plan_store._deserialize runs on every /plans/{id} load.
    from engine.intent_parser.schemas import FloorPlanState
    FloorPlanState.model_validate(plan_state)
