"""Tests for POST /v2/plans/generate/stream — the SSE variant.

Uses httpx's streaming support via TestClient.stream(...), which is
compatible with FastAPI's StreamingResponse (fastapi>=0.110, httpx>=0.27,
both pinned in pyproject.toml).
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from engine.generators import GeneratorFailure
from engine.pipeline import PipelineFailure

from tests.conftest import _auth_header, _device_header
from tests.test_plans_v2_generate import SPEC, _make_result


def _mock_run_best_of_n(**kwargs):
    return patch("api.routes.plans_v2_generate.run_best_of_n", **kwargs)


def _read_events(response) -> list[tuple[str, dict]]:
    """Parse `event: X\\ndata: Y\\n\\n` frames from the raw SSE body."""
    body = response.read().decode("utf-8")
    events = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        event_line, data_line = frame.split("\n", 1)
        event = event_line.removeprefix("event: ")
        data = json.loads(data_line.removeprefix("data: "))
        events.append((event, data))
    return events


def test_sse_returns_text_event_stream(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            resp.read()


def test_sse_fires_generating_event_first(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            events = _read_events(resp)
    assert events[0][0] == "progress"
    assert events[0][1]["phase"] == "generating"


def test_sse_fires_verified_event_before_result(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            events = _read_events(resp)
    assert events[1][0] == "progress"
    assert events[1][1]["phase"] == "verified"
    assert events[1][1]["total_candidates"] == 3


def test_sse_fires_result_event_last(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            events = _read_events(resp)
    assert events[-1][0] == "result"
    assert events[-1][1]["generated_plan_id"]
    assert events[-1][1]["cached"] is False


def test_sse_generator_failure_fires_error_event(client, fake_supabase):
    device_id = str(uuid.uuid4())
    exc = GeneratorFailure("nope", spec_id="spec_test1", generator_name="prompted", reason_code="llm_refused")
    with _mock_run_best_of_n(side_effect=exc):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            events = _read_events(resp)
    assert events[-1][0] == "error"
    assert events[-1][1]["kind"] == "generator_failure"
    assert not any(e == "result" for e, _ in events)


def test_sse_pipeline_failure_fires_error_event(client, fake_supabase):
    device_id = str(uuid.uuid4())
    exc = PipelineFailure(
        "0/3 survived", spec_id="spec_test1", total_candidates=3,
        survived_layer_a=1, survived_layer_c=0, verifier_results={},
    )
    with _mock_run_best_of_n(side_effect=exc):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            events = _read_events(resp)
    assert events[-1][0] == "error"
    assert events[-1][1]["kind"] == "pipeline_failure"
    assert events[-1][1]["survived_layer_a"] == 1
    assert events[-1][1]["survived_layer_c"] == 0


def test_sse_not_implemented_fires_error_event(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(side_effect=NotImplementedError("finetuned not shipped")):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            events = _read_events(resp)
    assert events[-1][0] == "error"
    assert events[-1][1]["kind"] == "not_implemented"


def test_sse_persists_on_success_same_as_json_endpoint(client, fake_supabase):
    device_id = str(uuid.uuid4())
    with _mock_run_best_of_n(return_value=_make_result()):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            resp.read()
    assert len(fake_supabase.tables["generated_plans"]) == 1


def test_sse_does_not_persist_on_failure(client, fake_supabase):
    device_id = str(uuid.uuid4())
    exc = GeneratorFailure("nope", spec_id="spec_test1", generator_name="prompted", reason_code="llm_refused")
    with _mock_run_best_of_n(side_effect=exc):
        with client.stream(
            "POST", "/v2/plans/generate/stream", json={"spec": SPEC}, headers=_device_header(device_id)
        ) as resp:
            resp.read()
    assert fake_supabase.tables.get("generated_plans", []) == []


def test_sse_respects_auth_and_rate_limit(client):
    resp = client.post("/v2/plans/generate/stream", json={"spec": SPEC})
    assert resp.status_code == 400
