"""Tests for engine/generators/prompted.py — PromptedGenerator (Architecture C, DRAFT 4).

20 mocked tests (no network) + 1 live test (skipped unless
COGNITECT_CLAUDE_API_KEY is set). Mocks the Anthropic client via
constructor injection (PromptedGenerator(client=...)) rather than
patching anthropic.Anthropic, matching the DI-friendly constructor.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import anthropic

from engine.generators import GeneratorFactory, GeneratorFailure, PromptedGenerator
from engine.generators.prompts import LAYOUT_JSON_SCHEMA, PROMPTED_VERSION
from engine.layout import FloorPlanSpec, Layout, RoomRequirement


# ── Mock Anthropic client ───────────────────────────────────────────────────


@dataclass
class _MockToolUseBlock:
    type: str = "tool_use"
    name: str = "emit_layout"
    input: Any = None


@dataclass
class _MockTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _MockMessage:
    content: list


class _MockMessagesAPI:
    def __init__(self, responses):
        # responses: list of dicts OR exceptions. Consumed in order per call.
        self._responses = list(responses)
        self.calls = []  # captured create() kwargs

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("_MockMessagesAPI: ran out of canned responses")
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, dict) and r.get("__no_tool_use__"):
            return _MockMessage(content=[_MockTextBlock(text=r.get("text", ""))])
        return _MockMessage(content=[_MockToolUseBlock(input=r)])


class _MockAnthropic:
    def __init__(self, responses):
        self.messages = _MockMessagesAPI(responses)


# ── Test helpers ─────────────────────────────────────────────────────────────


def _make_spec(n_candidates: int = 1, spec_id: str = "spec_test") -> FloorPlanSpec:
    return FloorPlanSpec(
        spec_id=spec_id,
        original_nl="A 2-room plan: 200 sqft living room and 150 sqft bedroom.",
        room_requirements=[
            RoomRequirement(name="Living", room_type="living", preferred_area_sqft=200.0),
            RoomRequirement(name="Bedroom", room_type="bedroom", preferred_area_sqft=150.0),
        ],
        n_candidates=n_candidates,
    )


def _valid_layout_dict(suffix: str = "a") -> dict:
    """A hand-verified valid Layout dict (2 rooms, shared wall, one door)."""
    return {
        "plan_id": f"plan_test_{suffix}",
        "schema_version": "1.0",
        "rooms": [
            {
                "id": "room_living",
                "name": "Living",
                "room_type": "living",
                "vertices": [[0, 0], [11.5, 0], [11.5, 17.5], [0, 17.5], [0, 0]],
                "area_sqft": 201.25,
                "boundary_wall_ids": [
                    "wall_living_south", "wall_shared", "wall_living_north", "wall_living_west",
                ],
            },
            {
                "id": "room_bedroom",
                "name": "Bedroom",
                "room_type": "bedroom",
                "vertices": [[11.5, 0], [20, 0], [20, 17.5], [11.5, 17.5], [11.5, 0]],
                "area_sqft": 148.75,
                "boundary_wall_ids": [
                    "wall_shared", "wall_bedroom_south", "wall_bedroom_east", "wall_bedroom_north",
                ],
            },
        ],
        "walls": [
            {"id": "wall_living_south", "start": [0, 0], "end": [11.5, 0], "bounds_rooms": ["room_living"]},
            {"id": "wall_living_west", "start": [0, 0], "end": [0, 17.5], "bounds_rooms": ["room_living"]},
            {"id": "wall_living_north", "start": [0, 17.5], "end": [11.5, 17.5], "bounds_rooms": ["room_living"]},
            {"id": "wall_shared", "start": [11.5, 0], "end": [11.5, 17.5], "bounds_rooms": ["room_living", "room_bedroom"]},
            {"id": "wall_bedroom_south", "start": [11.5, 0], "end": [20, 0], "bounds_rooms": ["room_bedroom"]},
            {"id": "wall_bedroom_east", "start": [20, 0], "end": [20, 17.5], "bounds_rooms": ["room_bedroom"]},
            {"id": "wall_bedroom_north", "start": [11.5, 17.5], "end": [20, 17.5], "bounds_rooms": ["room_bedroom"]},
        ],
        "openings": [
            {
                "id": "opening_door_1",
                "opening_type": "door",
                "wall_id": "wall_shared",
                "offset_ft": 7.0,
                "width_ft": 3.0,
                "swings_into_room_id": "room_bedroom",
            }
        ],
        "extent_x_ft": 20.0,
        "extent_y_ft": 17.5,
    }


def _api_status_error() -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(500, request=request)
    return anthropic.APIStatusError("simulated API error", response=response, body=None)


def _rate_limit_error() -> anthropic.RateLimitError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)
    return anthropic.RateLimitError("simulated rate limit", response=response, body=None)


def _connection_error() -> anthropic.APIConnectionError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


# ── Group 1 — Interface contract ────────────────────────────────────────────


def test_name_is_prompted():
    mock = _MockAnthropic(responses=[])
    assert PromptedGenerator(client=mock).name == "prompted"


def test_missing_api_key_raises_generator_failure(monkeypatch):
    monkeypatch.delenv("COGNITECT_CLAUDE_API_KEY", raising=False)
    with pytest.raises(GeneratorFailure) as exc_info:
        PromptedGenerator()
    assert exc_info.value.reason_code == "missing_api_key"


# ── Group 2 — Happy path ─────────────────────────────────────────────────────


def test_generate_single_candidate_happy_path():
    mock = _MockAnthropic(responses=[_valid_layout_dict("a")])
    gen = PromptedGenerator(client=mock)
    layouts = gen.generate(_make_spec(n_candidates=1))
    assert len(layouts) == 1
    assert isinstance(layouts[0], Layout)
    assert layouts[0].metadata["generator"]["name"] == "prompted"


def test_generate_n_candidates_happy_path():
    mock = _MockAnthropic(responses=[
        _valid_layout_dict("a"), _valid_layout_dict("b"), _valid_layout_dict("c"),
    ])
    gen = PromptedGenerator(client=mock)
    layouts = gen.generate(_make_spec(n_candidates=3))
    assert len(layouts) == 3
    indices = {l.metadata["generator"]["extra"]["candidate_index"] for l in layouts}
    assert indices == {0, 1, 2}


def test_generator_metadata_populated():
    mock = _MockAnthropic(responses=[_valid_layout_dict("a")])
    gen = PromptedGenerator(client=mock)
    layout = gen.generate(_make_spec(n_candidates=1))[0]
    meta = layout.metadata["generator"]
    assert meta["name"] == "prompted"
    assert meta["version"]
    assert meta["extra"]["model"] == "claude-sonnet-4-5"
    assert meta["extra"]["elapsed_ms"] >= 0


# ── Group 3 — Model refusal / partial failure ───────────────────────────────


def test_model_refusal_hatch():
    mock = _MockAnthropic(responses=[
        {"error": "cannot_generate", "reason": "conflicting adjacencies"},
    ])
    gen = PromptedGenerator(client=mock)
    with pytest.raises(GeneratorFailure) as exc_info:
        gen.generate(_make_spec(n_candidates=1))
    assert exc_info.value.reason_code == "all_candidates_failed"


def test_model_refusal_partial():
    mock = _MockAnthropic(responses=[
        {"error": "cannot_generate", "reason": "conflicting adjacencies"},
        _valid_layout_dict("b"),
    ])
    gen = PromptedGenerator(client=mock)
    layouts = gen.generate(_make_spec(n_candidates=2))
    assert len(layouts) == 1
    assert layouts[0].metadata["generator"]["extra"]["candidate_index"] == 1


def test_schema_validation_failure_is_generator_failure():
    bad = _valid_layout_dict("a")
    del bad["plan_id"]

    mock_single = _MockAnthropic(responses=[bad])
    gen_single = PromptedGenerator(client=mock_single)
    with pytest.raises(GeneratorFailure) as exc_info:
        gen_single.generate(_make_spec(n_candidates=1))
    assert exc_info.value.reason_code == "all_candidates_failed"

    bad2 = _valid_layout_dict("a")
    del bad2["plan_id"]
    mock_multi = _MockAnthropic(responses=[bad2, _valid_layout_dict("b")])
    gen_multi = PromptedGenerator(client=mock_multi)
    layouts = gen_multi.generate(_make_spec(n_candidates=2))
    assert len(layouts) == 1
    assert layouts[0].metadata["generator"]["extra"]["candidate_index"] == 1


def test_no_tool_use_block_is_generator_failure():
    mock = _MockAnthropic(responses=[{"__no_tool_use__": True, "text": "I refuse to use tools."}])
    gen = PromptedGenerator(client=mock)
    with pytest.raises(GeneratorFailure) as exc_info:
        gen._generate_one(_make_spec(n_candidates=1), candidate_index=0)
    assert exc_info.value.reason_code == "no_tool_use"


# ── Group 4 — Anthropic API error translation ───────────────────────────────


def test_api_status_error_translated():
    mock = _MockAnthropic(responses=[_api_status_error()])
    gen = PromptedGenerator(client=mock)
    with pytest.raises(GeneratorFailure) as exc_info:
        gen._generate_one(_make_spec(n_candidates=1), candidate_index=0)
    assert exc_info.value.reason_code == "api_status_error"

    mock2 = _MockAnthropic(responses=[_api_status_error()])
    gen2 = PromptedGenerator(client=mock2)
    with pytest.raises(GeneratorFailure) as exc_info2:
        gen2.generate(_make_spec(n_candidates=1))
    assert exc_info2.value.reason_code == "all_candidates_failed"


def test_rate_limit_error_translated():
    mock = _MockAnthropic(responses=[_rate_limit_error()])
    gen = PromptedGenerator(client=mock)
    with pytest.raises(GeneratorFailure) as exc_info:
        gen._generate_one(_make_spec(n_candidates=1), candidate_index=0)
    assert exc_info.value.reason_code == "rate_limit"


def test_connection_error_translated():
    mock = _MockAnthropic(responses=[_connection_error()])
    gen = PromptedGenerator(client=mock)
    with pytest.raises(GeneratorFailure) as exc_info:
        gen._generate_one(_make_spec(n_candidates=1), candidate_index=0)
    assert exc_info.value.reason_code == "api_connection_error"


def test_all_candidates_fail_raises_generator_failure():
    mock = _MockAnthropic(responses=[
        _api_status_error(), _rate_limit_error(), _connection_error(),
    ])
    gen = PromptedGenerator(client=mock)
    with pytest.raises(GeneratorFailure) as exc_info:
        gen.generate(_make_spec(n_candidates=3))
    assert exc_info.value.reason_code == "all_candidates_failed"
    message = str(exc_info.value)
    assert "candidate 0" in message
    assert "candidate 1" in message
    assert "candidate 2" in message


# ── Group 5 — User message content ──────────────────────────────────────────


def test_user_message_includes_original_nl():
    mock = _MockAnthropic(responses=[_valid_layout_dict("a")])
    gen = PromptedGenerator(client=mock)
    spec = _make_spec(n_candidates=1)
    gen.generate(spec)
    content = mock.messages.calls[0]["messages"][0]["content"]
    assert spec.original_nl in content


def test_user_message_includes_candidate_index():
    mock = _MockAnthropic(responses=[_valid_layout_dict("a"), _valid_layout_dict("b")])
    gen = PromptedGenerator(client=mock)
    gen.generate(_make_spec(n_candidates=2))
    first_content = mock.messages.calls[0]["messages"][0]["content"]
    second_content = mock.messages.calls[1]["messages"][0]["content"]
    assert "Candidate 1 of 2" in first_content
    assert "Candidate 2 of 2" in second_content


# ── Group 6 — Schema / version sanity ───────────────────────────────────────


def test_layout_json_schema_is_valid_json_schema():
    json.dumps(LAYOUT_JSON_SCHEMA)  # must not raise
    assert LAYOUT_JSON_SCHEMA["type"] == "object"
    props = LAYOUT_JSON_SCHEMA["properties"]
    for key in ("rooms", "walls", "openings", "plan_id", "schema_version", "extent_x_ft", "extent_y_ft"):
        assert key in props


def test_wall_length_ft_excluded_from_schema():
    """Regression: mode='validation' (not 'serialization') must exclude
    computed fields like Wall.length_ft — see plan Correction #1."""
    defs = LAYOUT_JSON_SCHEMA.get("$defs", {})
    wall_schema = defs["Wall"]
    assert "length_ft" not in wall_schema["properties"]
    assert "length_ft" not in wall_schema.get("required", [])


def test_prompted_version_is_iso_date_or_semver():
    assert re.match(r"^\d{4}-\d{2}-\d{2}$|^\d+\.\d+\.\d+$", PROMPTED_VERSION)


# ── Group 7 — Factory wiring ─────────────────────────────────────────────────


def test_factory_by_name_prompted_returns_prompted_generator(monkeypatch):
    monkeypatch.setenv("COGNITECT_CLAUDE_API_KEY", "test-key")
    gen = GeneratorFactory.by_name("prompted")
    assert isinstance(gen, PromptedGenerator)


def test_factory_from_env_prompted(monkeypatch):
    monkeypatch.setenv("LAYOUT_GENERATOR", "prompted")
    monkeypatch.setenv("COGNITECT_CLAUDE_API_KEY", "test-key")
    assert GeneratorFactory.from_env().name == "prompted"


def test_factory_unknown_kind_still_raises_value_error():
    with pytest.raises(ValueError, match="Unknown LAYOUT_GENERATOR"):
        GeneratorFactory.by_name("nonsense")


def test_factory_finetuned_still_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="DRAFT_ARCH_C_7"):
        GeneratorFactory.by_name("finetuned")


# ── Live test (skipped by default) ──────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("COGNITECT_CLAUDE_API_KEY"),
    reason="COGNITECT_CLAUDE_API_KEY not set",
)
def test_live_generate_2room_plan():
    """Real Anthropic API call. Costs ~$0.03. Not run in CI."""
    gen = PromptedGenerator()
    spec = _make_spec(n_candidates=1, spec_id="spec_live_smoketest")
    layouts = gen.generate(spec)
    assert len(layouts) == 1
    layout = layouts[0]
    assert len(layout.rooms) == 2
    assert {r.room_type for r in layout.rooms} == {"living", "bedroom"}
    assert layout.metadata["generator"]["name"] == "prompted"
